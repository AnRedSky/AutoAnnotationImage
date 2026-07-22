/**
 * useDetectionAnnotate.ts
 * ===================================================
 * 检测任务标注 composable (v2.5.7 拆分自 Annotate.vue)
 *
 * 职责:
 * 1. 管理检测任务相关 state: bboxList / copySuggestions / detDirty
 * 2. 提供: loadDetectionAnnotations / saveDetectionBBoxes / cancelDetectionDraft
 *          loadCopySuggestion / applyCopySuggestions
 *          类别调色板 (catColor / catName) 与 popover 交互 (open/change/visible)
 * 3. 维持原 Annotate.vue 中检测任务的全部业务行为, 父组件仅维护 ref 并调用
 *
 * 依赖:
 * - 入参: image (Ref<Image|null>), categories (Ref<Category[]>) 用于查表
 * - 外部 ref: detAnnotRef (画布 ref, 用于 resetInitial / save), annotatorSaving
 * - 入参钩子: onSuccess (保存成功后的副作用, 比如重置 noMore)
 */
import { ref, computed, nextTick, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { detectionApi } from '@/api'

// 类型定义 ---------------------------------------------------------------
export interface BBox {
  id?: number
  x_min: number
  y_min: number
  x_max: number
  y_max: number
  category_id: number
  confidence?: number
}

export interface CopySuggestion {
  category_id: number
  avg_x_min: number
  avg_y_min: number
  avg_x_max: number
  avg_y_max: number
  source_count: number
}

export interface DetectionImage {
  id: number
  task_type: string
}

export interface Category {
  id: number
  name: string
}

// 类别调色板 (与 DetectionAnnotator 一致) ---------------------------------
const DET_PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]

function catColor(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return DET_PALETTE[Math.abs(Number(catId)) % DET_PALETTE.length]
}

// useDetectionAnnotate ----------------------------------------------------
export function useDetectionAnnotate(options: {
  image: Ref<DetectionImage | null>
  detAnnotRef: Ref<any>
  annotatorSaving: Ref<boolean>
  /**
   * v2.5.15: 保存成功后的副作用钩子
   * - 父组件 (index.vue) 传入 refreshStats, 在后端 image.status 升级后立刻拉新统计
   * - 之前: 保存成功后不刷新, 前端"待标注"数字永远不减
   * - 可选: 不传则只完成"重置 dirty"基础动作
   */
  onSaved?: () => void | Promise<void>
}) {
  const { image, detAnnotRef, annotatorSaving, onSaved } = options

  // state ---------------------------------------------------------------
  /** 当前图的 bbox 列表 (归一化坐标, 与后端 BBoxAnnotation 一致) */
  const bboxList = ref<BBox[]>([])
  /** 跨图复制建议列表 */
  const copySuggestions = ref<CopySuggestion[]>([])
  /** 复制建议的源图数量 */
  const copySuggestionSourceCount = ref(0)
  /** 画布 dirty 状态 (从子组件 dirty-change 同步, 父组件响应式追踪) */
  const detDirty = ref(false)
  /** el-popover 打开的 bbox idx (同时只能一个) */
  const detOpenPopoverIdx = ref<number | null>(null)

  // 加载某图的已有 bbox ------------------------------------------------
  const loadDetectionAnnotations = async (imageId: number) => {
    try {
      const r: any = await detectionApi.listBBoxes(imageId)
      const items = r?.items || r || []
      bboxList.value = items.map((b: any) => ({
        id: b.id,
        x_min: b.x_min, y_min: b.y_min,
        x_max: b.x_max, y_max: b.y_max,
        category_id: b.category_id,
        confidence: b.confidence,
      }))
    } catch {
      bboxList.value = []
    }
  }

  // 跨图复制建议 ------------------------------------------------------
  const loadCopySuggestion = async (imageId: number) => {
    copySuggestions.value = []
    copySuggestionSourceCount.value = 0
    try {
      const r: any = await detectionApi.copySuggestion(imageId)
      copySuggestions.value = r?.suggestions || []
      copySuggestionSourceCount.value = r?.total_source_images || 0
    } catch {
      // 静默失败
    }
  }

  const applyCopySuggestions = () => {
    if (copySuggestions.value.length === 0) return
    const newBoxes = copySuggestions.value.map((s) => ({
      x_min: s.avg_x_min, y_min: s.avg_y_min,
      x_max: s.avg_x_max, y_max: s.avg_y_max,
      category_id: s.category_id,
    }))
    bboxList.value = [...bboxList.value, ...newBoxes]
    ElMessage.success(`已应用 ${newBoxes.length} 个建议 bbox, 可在画布上微调`)
    copySuggestions.value = []
  }

  const ignoreCopySuggestions = () => {
    copySuggestions.value = []
  }

  // 保存 bbox 列表 ----------------------------------------------------
  const saveDetectionBBoxes = async (bboxes: any[]) => {
    if (!image.value?.id) return
    annotatorSaving.value = true
    try {
      // 1) 清空旧 bbox
      await detectionApi.clearBBoxes(image.value.id)
      // 2) 批量写入新 bbox
      for (const b of bboxes) {
        await detectionApi.saveBBox(image.value.id, {
          x_min: b.x_min, y_min: b.y_min,
          x_max: b.x_max, y_max: b.y_max,
          category_id: b.category_id,
          confidence: b.confidence ?? null,
          source: 'human',
        })
      }
      ElMessage.success(`已保存 ${bboxes.length} 个 bbox`)
      await loadDetectionAnnotations(image.value.id)
      // 等待 bboxList 更新传到子组件后, 重置 initial -> dirty=false
      await nextTick()
      detAnnotRef.value?.resetInitial?.()
      // v2.5.15: 保存成功 → 触发父组件的 onSaved 钩子 (典型: refreshStats)
      // - 后端 detection.py 已把 image.status 提升到 human_confirmed
      // - 前端需主动重拉 stats, 才能让"待标注"数字减少
      if (onSaved) await onSaved()
    } catch (e: any) {
      ElMessage.error('bbox 保存失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  // 取消未保存的修改 --------------------------------------------------
  const cancelDetectionDraft = async () => {
    if (!image.value?.id) return
    await loadDetectionAnnotations(image.value.id)
    await nextTick()
    detAnnotRef.value?.resetInitial?.()
    ElMessage.success('已撤销未保存修改')
  }

  // popover 交互 ------------------------------------------------------
  function onDetTagClick(idx: number) {
    detAnnotRef.value?.selectByIndex?.(idx)
    detOpenPopoverIdx.value = detOpenPopoverIdx.value === idx ? null : idx
  }
  function onDetCategoryChange(idx: number, catId: number | null) {
    if (catId == null) return
    detAnnotRef.value?.selectByIndex?.(idx)
    detAnnotRef.value?.changeSelectedCategory?.(catId)
    detOpenPopoverIdx.value = null
  }
  function onPopoverVisibleChange(idx: number, v: boolean) {
    detOpenPopoverIdx.value = v
      ? idx
      : (detOpenPopoverIdx.value === idx ? null : detOpenPopoverIdx.value)
  }
  function onDetTargetCategoryChange(catId: number | null) {
    detAnnotRef.value?.setDefaultCategory?.(catId)
  }
  function removeBboxByIndex(idx: number) {
    detAnnotRef.value?.removeAtWithConfirm?.(idx)
  }

  return {
    // state
    bboxList,
    copySuggestions,
    copySuggestionSourceCount,
    detDirty,
    detOpenPopoverIdx,
    // 加载 / 保存
    loadDetectionAnnotations,
    loadCopySuggestion,
    applyCopySuggestions,
    ignoreCopySuggestions,
    saveDetectionBBoxes,
    cancelDetectionDraft,
    // popover 交互
    onDetTagClick,
    onDetCategoryChange,
    onPopoverVisibleChange,
    onDetTargetCategoryChange,
    removeBboxByIndex,
    // 工具
    catColor,
  }
}
