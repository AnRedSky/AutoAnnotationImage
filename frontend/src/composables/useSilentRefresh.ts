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
 * v3.5.0 Phase T6 优化 (按需激活 + 可见性联动):
 * - 新增 shouldRun: () => boolean, 返回 false 时**彻底停止**定时器 (而非仅 skip 当次)
 *   - 典型用法: 列表无任何 PENDING/PROGRESS 任务时, 后端不可能有状态变化, 不需要兜底
 *   - 子组件状态变化时通过 evaluate() 重新评估, 满足条件则**立即重启**定时器
 * - 新增 enabledByVisibility: boolean (默认 true), 标签页不可见时彻底停止
 *   - 浏览器后台标签 setInterval 仍会触发 (节流到 1Hz) 但用户不可见, 纯浪费
 *   - visibilitychange 切回时 evaluate() 重新评估
 *
 * 使用:
 * `
 * const { start, stop, pause, resume, evaluate } = useSilentRefresh({
 *   intervalMs: 60000,
 *   shouldRun: () => hasActiveJob.value,  // 仅当存在活跃任务时才拉
 *   onTick: () => loadJobs(),
 * })
 * onMounted(() => { evaluate(); start() })
 * // 当业务状态变化时 (例如新建任务后 jobs 列表更新):
 * watch(hasActiveJob, () => evaluate())
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
  /**
   * 决定"是否值得启动/继续定时器"的判定.
   * 返回 false 时彻底停止定时器 (而非仅 skip 单次 tick).
   * 典型用法: 列表全为终态时, 后续不会有状态变化, 停止兜底.
   * 配合 evaluate() 使用, 状态变化时可立即重启.
   */
  shouldRun?: () => boolean
  /** 每次 tick 时回调 */
  onTick: () => void
  /** 探测"当前是否有任何活跃 SSE 流"以跳过冗余拉取, 默认 true */
  skipWhenSseActive?: boolean
  /**
   * 标签页可见性联动: 默认 true.
   * - document.hidden=true 时彻底停止定时器
   * - 切回前台时 evaluate() 重新评估
   */
  enabledByVisibility?: boolean
}

export function useSilentRefresh(options: UseSilentRefreshOptions) {
  const {
    intervalMs = 60000,
    skipWhen,
    shouldRun,
    onTick,
    skipWhenSseActive = true,
    enabledByVisibility = true,
  } = options
  const pool = useTrainingSsePool()

  let timer: ReturnType<typeof setInterval> | null = null
  const active = ref(false)
  // 手动 pause 状态 (临时阻止 onTick, 例如详情打开时)
  const paused = ref(false)
  // 标签页可见性: true=可见, false=隐藏
  const pageVisible = ref(typeof document !== 'undefined' ? !document.hidden : true)

  /**
   * 评估当前是否满足"启动/保持定时器"的所有条件.
   * 返回 true 表示当前应运行, false 表示应停止.
   * - shouldRun? 返回 false → 不应运行
   * - enabledByVisibility + pageVisible=false → 不应运行
   * - paused=true → 暂停中 (但定时器仍在, onTick 内被阻止, 仍视为"运行中")
   */
  const shouldBeActive = (): boolean => {
    if (shouldRun && !shouldRun()) return false
    if (enabledByVisibility && !pageVisible.value) return false
    return true
  }

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
    // 启动时再次评估 shouldRun / visibility, 避免外部条件已不满足却仍跑
    if (!shouldBeActive()) {
      // 不启动定时器, 但 active 标记仍为 true 表示"逻辑上期望运行, 只是条件不满足"
      timer = null
      return
    }
    timer = setInterval(tick, intervalMs)
  }

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    active.value = false
  }

  /**
   * 重新评估启停条件, 状态变化时自动启动或停止.
   * - 父组件在 jobs / 可见性 / 业务条件变化时调用
   * - 幂等: 调用多次无副作用
   * - 行为: 满足 shouldBeActive 时启动, 不满足时停止
   */
  const evaluate = () => {
    if (!active.value) return  // 未通过 start() 进入活动态, 不动
    if (shouldBeActive()) {
      // 条件满足 → 确保定时器在跑
      if (!timer) {
        timer = setInterval(tick, intervalMs)
      }
    } else {
      // 条件不满足 → 停止定时器 (但保持 active=true, 等待下次 evaluate 重新启动)
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    }
  }

  /** 临时暂停 (不销毁定时器, 仅阻止本次/后续 onTick) */
  const pause = () => { paused.value = true }
  const resume = () => { paused.value = false }

  // ============== 可见性联动 ==============
  // 仅在 enabledByVisibility=true 时挂载监听
  if (enabledByVisibility && typeof document !== 'undefined') {
    const onVisChange = () => {
      pageVisible.value = !document.hidden
      evaluate()
    }
    document.addEventListener('visibilitychange', onVisChange)
    // 卸载时解绑
    onBeforeUnmount(() => {
      document.removeEventListener('visibilitychange', onVisChange)
    })
  }

  onBeforeUnmount(stop)

  return {
    start, stop, pause, resume, evaluate,
    active,
    /** 当前定时器是否真在运行 (用于调试 / 状态展示) */
    get running() { return timer != null },
  }
}
