/**
 * useSilentRefresh — 静默兜底刷新 (Composables)
 * ==============================================
 *
 * 职责:
 * - 每 30s 拉一次 list, 防止 SSE 断连后状态卡在 PROGRESS
 * - 仅在 detailVisible.value === false 时执行 (详情 dialog 打开时由详情 SSE 驱动)
 * - 组件卸载时自动停止
 *
 * 抽离动机:
 * - Training/index.vue 2480 行, 静默刷新逻辑 20 行
 * - 抽离后 page 只剩 use + 启动一行
 *
 * 使用:
 * ```ts
 * const { start, stop } = useSilentRefresh({
 *   intervalMs: 30000,
 *   skipWhen: () => detailVisible.value,
 *   onTick: () => loadJobs(),
 * })
 *
 * onMounted(start)
 * onBeforeUnmount(stop)
 * ```
 */
import { onBeforeUnmount, ref, type Ref } from 'vue'

export interface UseSilentRefreshOptions {
  /** 刷新间隔 (ms), 默认 30000 */
  intervalMs?: number
  /** 跳过本次 tick 的条件, 返回 true 时跳过 */
  skipWhen?: () => boolean
  /** 每次 tick 时回调 */
  onTick: () => void
}

export function useSilentRefresh(options: UseSilentRefreshOptions) {
  const { intervalMs = 30000, skipWhen, onTick } = options

  let timer: ReturnType<typeof setInterval> | null = null
  const active = ref(false)

  const start = () => {
    if (timer) clearInterval(timer)
    active.value = true
    timer = setInterval(() => {
      if (skipWhen?.()) return
      onTick()
    }, intervalMs)
  }

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    active.value = false
  }

  // 组件卸载时自动停止
  onBeforeUnmount(stop)

  return { start, stop, active }
}
