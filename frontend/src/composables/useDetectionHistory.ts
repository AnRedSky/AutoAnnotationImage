/**
 * useDetectionHistory.ts
 * ===================================================
 * 目标检测画布历史管理 composable (v3.0.0 Phase M 拆分自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. 维护 undo/redo 栈 (每次 bbox 变更前 snapshot)
 * 2. 提供 undo / redo 方法
 * 3. 跟踪 dirty 状态 (modelValue 与 initial 不一致)
 * 4. 提供 resetInitial (父组件保存后 / 切图时调用)
 * 5. emit dirty-change 事件 (父组件右侧保存按钮 disabled 用)
 *
 * 设计原则:
 * - 撤销栈深度限制 MAX_HISTORY=20, 超过自动丢弃最早项
 * - snapshot 入栈时清空 redo 栈 (新操作会让 redo 失效)
 * - dirty 通过 JSON.stringify(modelValue) 对比, 字段顺序不影响结果
 * - v2.5.5: 撤销栈不暴露给父组件, 内部维护, 切图时 resetInitial 自动清空
 */
import { ref, computed, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'

/** 撤销栈单条记录 */
export interface HistoryEntry {
  bboxes: any[]
}

/** 撤销栈最大深度 */
export const MAX_HISTORY = 20

export interface UseDetectionHistoryOptions {
  /** 当前 bbox 列表 (props.modelValue 透传) */
  modelValue: Ref<any[]>
  /** 切图时清空状态 (由父组件控制, e.g. imageUrl 变化时调用) */
  resetOnChange?: Ref<string>
  /** dirty 变化回调 (用于 emit dirty-change 事件) */
  onDirtyChange?: (dirty: boolean) => void
  /** 是否在 dirty 变化时立即 emit (默认 true, init 时也发) */
  immediate?: boolean
}

export function useDetectionHistory(options: UseDetectionHistoryOptions) {
  const {
    modelValue,
    resetOnChange,
    onDirtyChange,
    immediate = true,
  } = options

  // ============== State ==============
  /** 撤销栈 */
  const undoStack = ref<HistoryEntry[]>([])
  /** 重做栈 */
  const redoStack = ref<HistoryEntry[]>([])

  /** 初始状态快照 (用于 dirty 比对) */
  const initial = ref<string>(JSON.stringify(modelValue.value || []))

  // ============== Computed ==============
  /** 是否可撤销 */
  const canUndo = computed(() => undoStack.value.length > 0)
  /** 是否可重做 */
  const canRedo = computed(() => redoStack.value.length > 0)
  /** dirty 状态 (modelValue 与 initial 序列化后对比) */
  const dirty = computed(
    () => JSON.stringify(modelValue.value || []) !== initial.value
  )

  // ============== Methods ==============
  /**
   * 入栈当前 modelValue 作为可回滚点
   * - 由 add/remove/move/resize/changeCategory/clearAll 等操作前调用
   * - 新操作会清空 redo 栈
   */
  function snapshot() {
    undoStack.value.push({
      bboxes: JSON.parse(JSON.stringify(modelValue.value || [])),
    })
    if (undoStack.value.length > MAX_HISTORY) {
      undoStack.value.shift()
    }
    redoStack.value = []
  }

  /** 撤销 — 弹出 undoStack, 当前 modelValue 入 redoStack */
  function undo() {
    if (!canUndo.value) return
    redoStack.value.push({
      bboxes: JSON.parse(JSON.stringify(modelValue.value || [])),
    })
    const prev = undoStack.value.pop()!
    return prev.bboxes
  }

  /** 重做 — 弹出 redoStack, 当前 modelValue 入 undoStack */
  function redo() {
    if (!canRedo.value) return
    undoStack.value.push({
      bboxes: JSON.parse(JSON.stringify(modelValue.value || [])),
    })
    const next = redoStack.value.pop()!
    return next.bboxes
  }

  /**
   * 重置 initial (父组件保存后 / 切图时调用)
   * - 同步清空 undo/redo 栈, 避免切图后 undo 回到旧图状态
   */
  function resetInitial() {
    initial.value = JSON.stringify(modelValue.value || [])
    undoStack.value = []
    redoStack.value = []
  }

  // ============== Watchers ==============
  // dirty 变化时通知父组件 (保存按钮 disabled 用)
  watch(
    dirty,
    (v) => {
      if (onDirtyChange) onDirtyChange(v)
    },
    { immediate },
  )

  // 切图时重置 dirty (避免新图仍显示有未保存改动)
  if (resetOnChange) {
    watch(resetOnChange, () => {
      resetInitial()
    })
  }

  return {
    // state
    undoStack,
    redoStack,
    initial,
    // computed
    canUndo,
    canRedo,
    dirty,
    // methods
    snapshot,
    undo,
    redo,
    resetInitial,
  }
}
