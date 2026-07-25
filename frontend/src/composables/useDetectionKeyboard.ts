/**
 * useDetectionKeyboard.ts
 * ===================================================
 * 目标检测画布 - 键盘快捷键 composable
 *   (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. 注册全局 keydown 事件监听
 * 2. 处理画布强相关快捷键:
 *    - Delete / Backspace: 删除当前选中 bbox
 *    - Ctrl+Z: 撤销
 *    - Ctrl+Shift+Z / Ctrl+Y: 重做
 *    - n: 下一张图 (emit next)
 *    - p: 上一张图 (emit prev)
 *
 * 设计原则:
 * - 输入框 / textarea / contenteditable 内的按键不响应
 * - 组件卸载时自动解绑
 * - 快捷键操作走父组件传的方法, 不直接修改 state
 */
import { onMounted, onBeforeUnmount } from 'vue'

export interface UseDetectionKeyboardOptions {
  /** 撤销 */
  onUndo: () => void
  /** 重做 */
  onRedo: () => void
  /** 删除当前选中 */
  onRemoveSelected: () => void
  /** 当前是否有选中 (用于 Delete 键判断) */
  hasSelection: () => boolean
  /** 下一张 */
  onNext?: () => void
  /** 上一张 */
  onPrev?: () => void
}

export function useDetectionKeyboard(options: UseDetectionKeyboardOptions) {
  const {
    onUndo, onRedo, onRemoveSelected,
    hasSelection, onNext, onPrev,
  } = options

  function onKey(e: KeyboardEvent) {
    // 输入框 / textarea / contenteditable 内不响应
    const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
    if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable) {
      return
    }
    // Ctrl/Meta 组合键
    if (e.ctrlKey || e.metaKey) {
      if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); onUndo(); return }
      if ((e.key === 'z' && e.shiftKey) || e.key === 'y') {
        e.preventDefault(); onRedo(); return
      }
    }
    // Delete / Backspace
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (hasSelection()) { e.preventDefault(); onRemoveSelected() }
      return
    }
    // 单键: n / p 切图
    const k = e.key.toLowerCase()
    if (k === 'n' && onNext) { onNext() }
    else if (k === 'p' && onPrev) { onPrev() }
  }

  onMounted(() => window.addEventListener('keydown', onKey))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

  return { onKey }
}
