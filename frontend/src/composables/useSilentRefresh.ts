/**
 * useSilentRefresh - 静默兜底刷新 (Composables)
 * ==============================================
 *
 * 职责:
 * - 每 N 秒拉一次 list, 防止 SSE 断连 / 列表漏更时状态卡住
 * - 有活跃 SSE 流时跳过 (避免冗余 GET, SSE 已经实时更新行)
 * - 暂停/恢复: 详情打开或批量操作时主动 pause, 关闭后 resume
 * - 组件卸载时自动停止
 *
 * v3.1.0 Phase T4 优化 (与 SSE 池联动):
 * - 旧实现每 30s 强制拉整页, 与活跃 SSE 的实时更新完全冗余
 * - 新实现通过 useTrainingSsePool.getActiveTaskIds 探测活跃流, 有活跃流时跳过本次 tick
 * - 默认间隔调至 60s (极端兜底用, 平时 SSE 接管), 高频场景可通过 intervalMs 参数调
 *
 * 使用:
 * `	s
 * const { start, stop, pause, resume } = useSilentRefresh({
 *   intervalMs: 60000,
 *   onTick: () => loadJobs(),
 * })
 * onMounted(start)
 * onBeforeUnmount(stop)
 * `
 */
import { onBeforeUnmount, ref, type Ref } from 'vue'
import { useTrainingSsePool } from './useTrainingSsePool'

export interface UseSilentRefreshOptions {
  /** 刷新间隔 (ms), 默认 60000 */
  intervalMs?: number
  /** 跳过本次 tick 的条件, 返回 true 时跳过 */
  skipWhen?: () => boolean
  /** 每次 tick 时回调 */
  onTick: () => void
  /** 探测"当前是否有任何活跃 SSE 流"以跳过冗余拉取, 默认 true */
  skipWhenSseActive?: boolean
}

export function useSilentRefresh(options: UseSilentRefreshOptions) {
  const { intervalMs = 60000, skipWhen, onTick, skipWhenSseActive = true } = options
  const pool = useTrainingSsePool()

  let timer: ReturnType<typeof setInterval> | null = null
  const active = ref(false)
  // 手动 pause 状态 (临时阻止 onTick, 例如详情打开时)
  const paused = ref(false)

  const tick = () => {
    // 手动暂停中 → 跳过
    if (paused.value) return
    // 用户自定义 skip
    if (skipWhen?.()) return
    // 任意活跃 SSE → 跳过 (SSE 已实时更新列表)
    if (skipWhenSseActive && pool.getActiveTaskIds().length > 0) return
    onTick()
  }

  const start = () => {
    if (timer) clearInterval(timer)
    active.value = true
    timer = setInterval(tick, intervalMs)
  }

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    active.value = false
  }

  /** 临时暂停 (不销毁定时器, 仅阻止本次/后续 onTick) */
  const pause = () => { paused.value = true }
  const resume = () => { paused.value = false }

  onBeforeUnmount(stop)

  return { start, stop, pause, resume, active }
}
