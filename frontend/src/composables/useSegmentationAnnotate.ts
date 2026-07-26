/**
 * useSegmentationAnnotate.ts
 * ===================================================
 * 分割任务标注 composable (v2.5.7 拆分自 Annotate.vue)
 *
 * 职责:
 * 1. 管理分割任务 state: initialMaskUrl / initialMaskMeta / segDirty
 * 2. 提供: loadSegmentationMask / saveSegmentationMask / onSegClear
 * 3. 模式/类别切换 handler, 转发到 segAnnotRef
 */
import { ref, nextTick, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { annotationApi, segmentationApi } from '@/api'

export interface SegmentationImage {
  id: number
  task_type: string
  /** v3.0.0: 不合格标记状态 (供保存时自动清理) */
  quality_flag?: string | null
  reject_reason?: string | null
  rejected_by?: number | null
  rejected_at?: string | null
}

export function useSegmentationAnnotate(options: {
  image: Ref<SegmentationImage | null>
  segAnnotRef: Ref<any>
  annotatorSaving: Ref<boolean>
  /**
   * v2.5.15: 保存成功后的副作用钩子
   * - 父组件 (index.vue) 传入 refreshStats, 在后端 image.status 升级后立刻拉新统计
   * - 之前: 保存成功后不刷新, 前端"待标注"数字永远不减
   * - 可选: 不传则只完成"重置 dirty"基础动作
   */
  onSaved?: () => void | Promise<void>
}) {
  const { image, segAnnotRef, annotatorSaving, onSaved } = options

  // state ---------------------------------------------------------------
  /** 已有 mask 的 blob URL (后端 /api/segmentation/masks/{id}?download=true 返回的 PNG) */
  const initialMaskUrl = ref<string | null>(null)
  /** mask 元信息 (id/width/height), 用于保存时决定 update vs create */
  const initialMaskMeta = ref<{ id: number; width: number; height: number } | null>(null)
  /** 画布 dirty 状态 (子组件 dirty-change 同步) */
  const segDirty = ref(false)
  /** 画刷 / 橡皮 / 查看 模式 */
  const segMode = ref<'brush' | 'erase' | 'pan'>('brush')
  /** mask 统计标签 (例如 "已涂 1234 px") */
  const segMaskStatsLabel = ref('—')

  // 加载某图的已有 mask -----------------------------------------------
  const loadSegmentationMask = async (imageId: number) => {
    try {
      // 1) 元数据
      const r: any = await segmentationApi.getMask(imageId)
      if (!r || !r.file_exists || !r.id) {
        initialMaskMeta.value = null
        initialMaskUrl.value = null
        return
      }
      initialMaskMeta.value = { id: r.id, width: r.width, height: r.height }
      // 2) 拉 PNG 二进制 (responseType=blob)
      // http 响应拦截器已 unwrap 为 response.data，故 resp 即为 Blob
      const resp: any = await segmentationApi.getMask(imageId, true)
      const blob: Blob | null = resp instanceof Blob ? resp : null
      if (blob) {
        if (initialMaskUrl.value) URL.revokeObjectURL(initialMaskUrl.value)
        initialMaskUrl.value = URL.createObjectURL(blob)
      } else {
        initialMaskUrl.value = null
      }
    } catch {
      initialMaskMeta.value = null
      initialMaskUrl.value = null
    }
  }

  // 保存 segmentation mask --------------------------------------------
  const saveSegmentationMask = async (file: File) => {
    if (!image.value?.id) return
    // v3.0.0: 保存前若图已被标记不合格, 主动撤销 (与后端正交维度清理对齐)
    const wasUnqualified = image.value?.quality_flag === 'unqualified'
    annotatorSaving.value = true
    try {
      await segmentationApi.uploadMask(image.value.id, file, 'human')
      ElMessage.success('mask 已保存')
      // v3.0.0: 前端同步清空不合格标记 (后端已自动撤销)
      if (wasUnqualified && image.value) {
        try {
          await annotationApi.unmarkUnqualified(image.value.id)
        } catch (e: any) {
          // 409 = 后端认为未标记 (save 时已清空), 静默忽略
          if (e?.response?.status !== 409) {
            console.warn('unmarkUnqualified failed (non-fatal):', e)
          }
        }
        // 就地更新 image 对象, 让 UI 立刻反映
        image.value = {
          ...image.value,
          quality_flag: null,
          reject_reason: null,
          rejected_by: null,
          rejected_at: null,
        }
      }
      await loadSegmentationMask(image.value.id)
      await nextTick()
      segAnnotRef.value?.resetInitial?.()
      // v2.5.15: 保存成功 → 触发父组件的 onSaved 钩子 (典型: refreshStats)
      // - 后端 segmentation.py 已把 image.status 提升到 human_confirmed
      // - 前端需主动重拉 stats, 才能让"待标注"数字减少
      if (onSaved) await onSaved()
    } catch (e: any) {
      ElMessage.error('mask 保存失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  // 模式 / 类别 / 笔刷大小 --------------------------------------------
  const onSegModeChange = (m: 'brush' | 'erase' | 'pan') => {
    segMode.value = m
    segAnnotRef.value?.setMode?.(m)
  }
  const onSegCategoryChange = (id: number) => {
    segAnnotRef.value?.setCategory?.(id)
  }
  const onSegBrushSizeChange = (v: number) => {
    segAnnotRef.value?.setBrushSize?.(v)
  }
  const onSegSave = () => {
    segAnnotRef.value?.save?.()
  }
  const onSegClear = () => {
    segAnnotRef.value?.cancel?.()
  }
  const onSegDirtyChange = (v: boolean) => {
    segDirty.value = v
  }

  // 切图 / 退出时清理 blob URL -----------------------------------------
  const revokeMaskUrl = () => {
    if (initialMaskUrl.value) {
      URL.revokeObjectURL(initialMaskUrl.value)
      initialMaskUrl.value = null
    }
    initialMaskMeta.value = null
  }

  return {
    // state
    initialMaskUrl,
    initialMaskMeta,
    segDirty,
    segMode,
    segMaskStatsLabel,
    // 加载 / 保存
    loadSegmentationMask,
    saveSegmentationMask,
    revokeMaskUrl,
    // 模式 / 类别 / 笔刷
    onSegModeChange,
    onSegCategoryChange,
    onSegBrushSizeChange,
    onSegSave,
    onSegClear,
    onSegDirtyChange,
  }
}
