/**
 * useAnnotateNav.ts
 * ===================================================
 * 标注工作台 - 图片导航 composable (v3.5.0 新增, 从 Annotate.vue 抽出)
 *
 * 职责 (统一管理标注工作台的图片流转):
 * 1. 维护浏览历史栈: historyIds / historyCursor / noMore
 * 2. 提供切图逻辑: loadNext (下一张) / loadPrev (上一张) / loadSpecificImage
 * 3. 提供切图前的自动保存: autoSaveBeforeSwitch
 * 4. 提供跳转到数据集详情: viewDataset (v3.5.0 修复 index.vue 缺此函数)
 * 5. v3.5.0 新增: loadFirstOfStatus (状态切换后默认跳到该状态第一张)
 *
 * 设计原则:
 * - 切图 race 控制: 用 timer + imageId 比对避免重复 loadNext
 * - 历史栈过滤: loadPrev 跳过已处理图 (status != pending/ai_labeled)
 * - 自动保存: dirty + 是检测/分割任务时, 在切图前先保存
 * - 单向数据流: 父组件传入 ref (image / detAnnotRef / segAnnotRef / segDirty / detDirty / etc.)
 *   composable 只读 ref, 不修改父组件其他 ref
 */
import { ref, computed, watch, type Ref, type ComputedRef } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { imageApi } from '@/api'
import { STATUS_FILTER_LABEL, type StatusFilterValue } from './useAnnotationStatusFilter'

export interface UseAnnotateNavOptions {
  image: Ref<any>
  datasetId: Ref<number | null>
  detAnnotRef: Ref<any>
  segAnnotRef: Ref<any>
  detDirty: Ref<boolean>
  segDirty: Ref<boolean>
  annotatorSaving: Ref<boolean>
  currentTaskType: ComputedRef<'classification' | 'detection' | 'segmentation'>
  statusFilter: Ref<StatusFilterValue>
  apiStatusParam: ComputedRef<string | undefined>
  saveDetectionBBoxes: (bboxes: any[]) => Promise<void>
  saveSegmentationMask: (file: File) => Promise<void>
  revokeMaskUrl: () => void
  fillImage: (item: any) => void
}

export function useAnnotateNav(options: UseAnnotateNavOptions) {
  const {
    image, datasetId, detAnnotRef, segAnnotRef,
    detDirty, segDirty, annotatorSaving,
    currentTaskType, statusFilter, apiStatusParam,
    saveDetectionBBoxes, saveSegmentationMask,
    revokeMaskUrl, fillImage,
  } = options

  const router = useRouter()

  // ============== 浏览历史栈 ==============
  const historyIds = ref<number[]>([])
  const historyCursor = ref(-1)
  const noMore = ref(false)
  const canGoPrev = computed(() => historyCursor.value > 0)

  // 切图前自动保存定时器 (分割分支兜底)
  let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

  // ============== 切图前自动保存 ==============
  const autoSaveBeforeSwitch = async (): Promise<boolean> => {
    if (currentTaskType.value === 'classification') return true
    if (currentTaskType.value === 'detection') {
      if (!detAnnotRef.value) return true
      if (!detDirty.value) return true
      try {
        annotatorSaving.value = true
        const cur = detAnnotRef.value.modelValue || []
        await saveDetectionBBoxes(cur)
        return true
      } catch (e: any) {
        ElMessage.error('自动保存失败: ' + (e?.response?.data?.detail || e?.message))
        return false
      } finally {
        annotatorSaving.value = false
      }
    }
    if (currentTaskType.value === 'segmentation') {
      if (!segAnnotRef.value) return true
      if (!segDirty.value) return true
      return new Promise<boolean>((resolve) => {
        const stop = watch(
          segDirty,
          (v) => {
            if (!v) { stop(); resolve(true) }
          },
          { flush: 'sync' }
        )
        autoSaveTimer = setTimeout(() => { stop(); resolve(true) }, 5000)
        segAnnotRef.value?.save?.()
      })
    }
    return true
  }

  // ============== 「下一张」逻辑 ==============
  const loadNext = async () => {
    if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
    const ok = await autoSaveBeforeSwitch()
    if (!ok) return
    // 情况 1: 历史栈中间, 直接前进
    if (historyCursor.value < historyIds.value.length - 1) {
      historyCursor.value++
      const id = historyIds.value[historyCursor.value]
      try {
        const detail: any = await imageApi.detail(id)
        fillImage(detail)
      } catch (e: any) {
        ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
      }
      return
    }
    // 情况 2: 栈顶, 拉新图
    try {
      const excludeIdsParam = historyIds.value.length > 0
        ? historyIds.value.join(',')
        : undefined
      const resp: any = await imageApi.list(datasetId.value, {
        status: apiStatusParam.value,
        page: 1,
        page_size: 20,
        exclude_ids: excludeIdsParam,
      })
      const items = resp?.items || []
      const seen = new Set(historyIds.value)
      const safeItem = items.find((it: any) => it && !seen.has(it.id))
      const item = safeItem || null
      if (!item) {
        noMore.value = true
        const statusLabel = STATUS_FILTER_LABEL[statusFilter.value]
        if (historyIds.value.length > 0) {
          ElMessage.warning({
            message: `已经是最后一张了, 没有更多${statusLabel}图片。可点击「启动 AI 预标注」让 AI 继续标注。`,
            duration: 3500,
            showClose: true,
          })
        } else {
          ElMessage.info(`当前数据集没有${statusLabel}的图片`)
        }
        return
      }
      noMore.value = false
      historyIds.value.push(item.id)
      historyCursor.value = historyIds.value.length - 1
      fillImage(item)
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    }
  }

  // ============== 「上一张」逻辑 ==============
  const loadPrev = async () => {
    if (historyCursor.value <= 0) {
      ElMessage.info('已经是第一张了')
      return
    }
    const ok = await autoSaveBeforeSwitch()
    if (!ok) return
    noMore.value = false

    let foundIdx = -1
    let foundDetail: any = null
    const skipIndices: number[] = []

    for (let i = historyCursor.value - 1; i >= 0; i--) {
      const candidateId = historyIds.value[i]
      try {
        const detail: any = await imageApi.detail(candidateId)
        const status = detail?.status
        // 跳过已处理图
        if (status && status !== 'pending' && status !== 'ai_labeled') {
          skipIndices.push(i)
          continue
        }
        foundIdx = i
        foundDetail = detail
        break
      } catch {
        skipIndices.push(i)
        continue
      }
    }

    if (foundIdx === -1) {
      skipIndices.sort((a, b) => b - a)
      for (const idx of skipIndices) {
        historyIds.value.splice(idx, 1)
      }
      historyCursor.value -= skipIndices.length
      ElMessage.info('前面没有未处理的待标注图片了')
      return
    }

    const toRemove = skipIndices.filter(idx => idx > foundIdx)
    toRemove.sort((a, b) => b - a)
    for (const idx of toRemove) {
      historyIds.value.splice(idx, 1)
    }
    historyCursor.value = foundIdx
    fillImage(foundDetail)
  }

  // ============== 加载指定的 imageId (用于「去标注」按钮预选) ==============
  const loadSpecificImage = async (imageId: number): Promise<boolean> => {
    if (!datasetId.value) return false
    try {
      const detail: any = await imageApi.detail(imageId)
      if (detail?.dataset_id && Number(detail.dataset_id) !== Number(datasetId.value)) {
        ElMessage.warning('指定的图片不属于当前数据集, 已回退到默认加载')
        return false
      }
      historyIds.value = [imageId]
      historyCursor.value = 0
      fillImage(detail)
      return true
    } catch (e: any) {
      ElMessage.error(
        '加载指定图片失败, 已回退到默认加载: ' +
        (e?.response?.data?.detail || e?.message)
      )
      return false
    }
  }

  // ============== 跳转到数据集详情 (v3.5.0 修复: 此前 index.vue 缺此函数会运行时错误) ==============
  const viewDataset = () => {
    if (datasetId.value) router.push(`/datasets/${datasetId.value}`)
  }

  // ============== v3.5.0: 「切状态后默认跳到该状态第一张」 ==============
  // 背景: 用户切换 statusFilter (待标注 / AI 已标 / 已人工标注) 时, 期望立即看到该状态的一张图
  //  - 旧实现 watch(statusFilter) → loadNext(), 但 loadNext 会用历史栈做 exclude, 经常跳到第 2/3 张
  //  - 新实现: 先清空历史栈 (新上下文), 再拉该状态排序最前的一张
  //  - 排序沿用后端 list 接口默认 desc (最新优先), 与 list_ids 接口 order=desc 保持一致
  //  - 若该状态无图: noMore=true, image=null, UI 显示「无图」空态
  //  - v3.6.1: 无图时通过 fillImage(null) 让父组件同步清空 candidates / bboxList,
  //    避免右侧 ClassificationPanel 仍渲染上一张图的 Top-K 候选
  const loadFirstOfStatus = async () => {
    if (!datasetId.value) return
    const ok = await autoSaveBeforeSwitch()
    if (!ok) return
    // 清空历史: 新状态是个新上下文, 不能复用旧栈
    historyIds.value = []
    historyCursor.value = -1
    noMore.value = false
    try {
      const resp: any = await imageApi.list(datasetId.value, {
        status: apiStatusParam.value,
        page: 1,
        page_size: 1,
      })
      const items = resp?.items || []
      if (items.length === 0) {
        noMore.value = true
        const statusLabel = STATUS_FILTER_LABEL[statusFilter.value]
        if (historyIds.value.length === 0) {
          ElMessage.info(`当前数据集没有${statusLabel}的图片`)
        }
        // 显式调 fillImage(null) 让父组件清空 candidates / bboxList, 避免残留
        fillImage(null)
        return
      }
      const first = items[0]
      historyIds.value = [first.id]
      historyCursor.value = 0
      fillImage(first)
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    }
  }

  // ============== 切 dataset 时清空浏览历史 ==============
  const resetHistory = () => {
    historyIds.value = []
    historyCursor.value = -1
    noMore.value = false
    image.value = null
    revokeMaskUrl()
  }

  // ============== 组件卸载清理 ==============
  const cleanup = () => {
    if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null }
  }

  return {
    // state
    historyIds,
    historyCursor,
    noMore,
    canGoPrev,
    // 切图
    loadNext,
    loadPrev,
    loadSpecificImage,
    loadFirstOfStatus,
    viewDataset,
    // 维护
    resetHistory,
    cleanup,
  }
}
