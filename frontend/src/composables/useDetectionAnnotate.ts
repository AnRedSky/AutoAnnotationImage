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
  // v2.5.42: 跟踪「上次加载/保存成功」时的 bbox 列表, 作为 save 短路基准
  // - 不依赖子组件 emit dirty-change 的链式时序, 由 composable 内部独立维护
  // - loadDetectionAnnotations 完成后立即同步
  // - saveDetectionBBoxes 成功后立即同步
  // - 切换图片时由父组件调用 fillImage 重置 (下文)
  const lastSavedBboxes = ref<BBox[]>([])

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
      // v2.5.42: 同步 lastSavedBboxes (本次加载即为「上次保存」状态)
      lastSavedBboxes.value = JSON.parse(JSON.stringify(bboxList.value))
    } catch {
      bboxList.value = []
      lastSavedBboxes.value = []
    }
    // v2.5.43: 加载完成后同步子组件的 dirty 状态, 修复「切图后 dirty 误判」问题
    // 根因:
    //   fillImage 执行顺序是 1) image.value = item  2) bboxList.value = []  3) loadDetectionAnnotations
    //   步骤 1 触发 DetectionAnnotator 的 imageUrl watch → resetInitial,
    //         此时 props.modelValue 已被父组件置为 [], 所以 initial 锁定为 '[]'
    //   步骤 3 完成后, bboxList = [loaded bboxes], modelValue 变为 [loaded bboxes],
    //         但 initial 仍是 '[]', 导致 dirty = '[loaded bboxes]' !== '[]' = true (错误!)
    // 后果:
    //   1) 保存按钮呈现 dirty 高亮态, 用户困惑
    //   2) autoSaveBeforeSwitch (上一张/下一张) 会触发 saveDetectionBBoxes
    //   3) 用户若在加载完成的极短瞬间点「保存」, bboxList 可能还是 [],
    //      而 lastSavedBboxes 是上一次的 [B1,B2], 会触发 clearBBoxes 清空后端
    // 修复:
    //   load 完成后显式调 detAnnotRef.resetInitial, 把 initial 同步为当前 modelValue,
    //   dirty 保持 false, 撤销/重做栈同步清空, 切图后无法 undo 回到旧图状态
    //   await nextTick 确保 bboxList 的 prop 已传到子组件, 再调 resetInitial 才能拿到正确 modelValue
    await nextTick()
    try {
      detAnnotRef.value?.resetInitial?.()
    } catch {
      // resetInitial 失败不应阻塞 load 的整体流程
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
  /**
   * v2.5.42: saveDetectionBBoxes 重构, 完全摆脱对 detDirty 的依赖
   * - 之前: 父组件 DetectionPanel @save 直接链式调 detAnnotRef.save() → 触发 onSave() 再次 emit
   *   整套链路任何环节时序异常 (如子组件 dirty-watch 与父 ref 同步慢) 都会导致「点保存无反应」
   * - 现在: 父组件 @save 直接传 bboxList 进来, 跳过链式 emit
   * - 安全: 函数内部用 composable 内部维护的 lastSavedBboxes (与后端当前状态一致) 做短路基准
   *   - 与 bboxes 字符串一致 → 跳过网络请求 (用户没改)
   *   - 与 bboxes 字符串不一致 → 正常保存 (不论 detDirty 状态如何)
   * - 兜底: 即便 detDirty 时序异常 (例如子组件 emit 丢失) 也不会导致「点保存无反应」,
   *   因为 bboxes 直接来自父组件 bboxList, 反映用户最新画布状态
   * - 兼容旧行为: 「清空全部」场景下 bboxes=[] 与 lastSavedBboxes=[bbox1, ...] 不一致, 正常执行 clearBBoxes
   */
  const saveDetectionBBoxes = async (bboxes: any[]) => {
    if (!image.value?.id) return
    // v2.5.42: 短路 — 仅当 bboxes 与后端当前状态完全一致时跳过
    // - 不依赖 detDirty (避免链式时序问题)
    // - 字符串比较容差: 字段顺序不影响 JSON.stringify 结果 (只要 bboxes 是同一组对象)
    const incoming = JSON.stringify(bboxes || [])
    const lastSaved = JSON.stringify(lastSavedBboxes.value || [])
    if (incoming === lastSaved) {
      // 静默 no-op: 用户没改, 也不弹消息 (避免打断操作流)
      return
    }
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
      // v2.5.38: 根据 bboxes 数量给出不同反馈 — 空列表语义为"清空全部"
      if (bboxes.length === 0) {
        ElMessage.success('已清空全部标注 (0 个 bbox 写入数据库)')
      } else {
        ElMessage.success(`已保存 ${bboxes.length} 个 bbox`)
      }
      // v2.5.42: 同步 lastSavedBboxes (本次保存即为「新的上次保存」状态)
      // 注意: 先同步再 loadDetectionAnnotations, 因为 load 会重置它一次, 但保持顺序让逻辑清晰
      lastSavedBboxes.value = JSON.parse(JSON.stringify(bboxes || []))
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

  /**
   * v2.5.40 修复: dirty-change 事件回调
   * - 之前 index.vue 用 inline lambda `@dirty-change="(v) => detDirty = v"`
   *   在 Vue 3 模板中, ref 会被自动 unwrap, `detDirty = v` 实际是给 unwrap 后的
   *   局部 boolean 变量赋值, ref 永远不会被更新
   * - 表现: 用户点「清空全部」后, 子组件内部 dirty=true 并 emit('dirty-change', true),
   *   但父组件的 detDirty 仍是 false, 右侧「保存」按钮在 handleSaveClick 中
   *   `if (!props.detDirty)` 命中, 弹出「当前无修改, 无需保存」, 实际后端 bboxes 未删除
   * - 现在: 用具名函数 + .value 显式赋值 ref, 与 useSegmentationAnnotate 的
   *   onSegDirtyChange 保持完全一致
   * - 同时把「撤销本次修改」按钮的 disabled 也间接修复 (它之前也是 `!detDirty` 永远 true)
   */
  const onDetDirtyChange = (v: boolean) => {
    detDirty.value = v
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
    // v2.5.40: 画布 dirty 同步 (取代 inline lambda, 修复 ref 不更新问题)
    onDetDirtyChange,
    // 工具
    catColor,
  }
}
