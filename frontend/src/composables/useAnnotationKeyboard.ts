/**
 * useAnnotationKeyboard.ts
 * ===================================================
 * 标注工作台 - 页面级键盘快捷键 composable (v3.5.0 新增)
 *
 * 职责:
 * 1. 注册全局 keydown 监听, 提供工作台通用快捷键
 * 2. 与 useDetectionKeyboard 互补: 本 composable 管页面级 (切图/保存/不合格/历史)
 *    检测画布快捷键 (Delete/Ctrl+Z) 由 useDetectionKeyboard 负责
 *
 * 默认快捷键 (可在 options 中覆盖):
 *   → (ArrowRight) / n  下一张
 *   ← (ArrowLeft)  / p  上一张
 *   Enter / s          保存当前标注 (触发 confirm 当前 top1)
 *   Shift+Enter / x    修正为其他类别
 *   u                  标记当前图为不合格
 *   h                  查看修正历史
 *
 * 设计原则:
 * - 输入框 / textarea / contenteditable 内的按键不响应
 * - 检测画布可能也用箭头键, 通过 tag 区分 (画布通常不是 input)
 * - 组件卸载时自动解绑, 防止内存泄漏
 * - 全部操作走父组件传方法, 不直接修改 state (单向数据流)
 */
import { onMounted, onBeforeUnmount } from 'vue'

export interface UseAnnotationKeyboardOptions {
  /** 下一张 */
  onNext: () => void
  /** 上一张 */
  onPrev: () => void
  /** 确认/保存当前 top1 标签 (分类) 或当前 bbox (检测) */
  onConfirm?: () => void
  /** 切换为修正模式 (弹出其他类别下拉) */
  onCorrect?: () => void
  /** 标记为不合格 */
  onMarkUnqualified?: () => void
  /** 查看修正历史 */
  onShowHistory?: () => void
  /** 是否可上一张 (false 时 ArrowLeft 失效) */
  canPrev?: () => boolean
  /** 是否可下一张 (false 时 ArrowRight 失效) */
  canNext?: () => boolean
}

export function useAnnotationKeyboard(options: UseAnnotationKeyboardOptions) {
  const {
    onNext, onPrev,
    onConfirm, onCorrect,
    onMarkUnqualified, onShowHistory,
    canPrev, canNext,
  } = options

  function isInputTarget(t: EventTarget | null): boolean {
    const el = t as HTMLElement
    if (!el?.tagName) return false
    const tag = el.tagName.toLowerCase()
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
    if (el.isContentEditable) return true
    return false
  }

  function onKey(e: KeyboardEvent) {
    // 输入控件内不响应
    if (isInputTarget(e.target)) return

    // 下一张
    if (e.key === 'ArrowRight' || e.key === 'n') {
      if (canNext && !canNext()) return
      e.preventDefault()
      onNext()
      return
    }
    // 上一张
    if (e.key === 'ArrowLeft' || e.key === 'p') {
      if (canPrev && !canPrev()) return
      e.preventDefault()
      onPrev()
      return
    }
    // 确认 (Enter / s)
    if ((e.key === 'Enter' && !e.shiftKey) || e.key === 's') {
      if (onConfirm) { e.preventDefault(); onConfirm() }
      return
    }
    // 修正 (Shift+Enter / x)
    if ((e.key === 'Enter' && e.shiftKey) || e.key === 'x') {
      if (onCorrect) { e.preventDefault(); onCorrect() }
      return
    }
    // 标记不合格
    if (e.key === 'u') {
      if (onMarkUnqualified) { e.preventDefault(); onMarkUnqualified() }
      return
    }
    // 查看历史
    if (e.key === 'h') {
      if (onShowHistory) { e.preventDefault(); onShowHistory() }
      return
    }
  }

  onMounted(() => window.addEventListener('keydown', onKey))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

  return { onKey }
}
