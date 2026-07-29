/**
 * useTrainingSsePool - 训练 SSE 订阅池 (singleton)
 * ==================================================
 *
 * 之前 useTrainingListSSE 在每个活跃 job 上独立建立 SSE 连接, useTrainingDetailStream 在
 * 详情打开时又为同一 taskId 单独建立第二个 SSE 连接, 同一任务出现两个并发连接, 浪费资源
 * (服务端每 1s 一次 Celery+DB 查询 × N 任务)。本模块把同一 taskId 的底层 SSE 连接共享,
 * 多处订阅 (列表 / 详情 / 后台静默刷新等) 只开一条 stream, 多路 fan-out。
 *
 * 设计:
 * - 单例 (module 顶层 Map): 跨多个组件复用, Vue 多实例打开/关闭是同一个池
 * - 引用计数 (subscriber 数): 全部取消订阅时延迟 1.5s 再真正关闭, 防止 detail 弹窗快进快出
 *   导致反复 reconnect 的抖动
 * - 广播: 每条 frame 触发所有 listener, 各自决定如何处理 (列表行更新 / 详情重渲染 / 计数)
 * - 终态广播: 连接进入 SUCCESS/FAILURE/REVOKED 后, 池先广播给所有监听器, 再延迟 1.5s
 *   清理连接 (避免新订阅者错失最后一帧)
 *
 * 使用:
 * `	s
 * const pool = useTrainingSsePool()
 * const off = pool.subscribe(taskId, {
 *   onMessage: (data) => updateRow(rowId, data),
 *   onComplete: () => reload(),
 * })
 * // 卸载时:
 * off()
 * `
 *
 * 注意: 监听器 onError 收敛在池内部, 不会因为单个监听器的取消订阅而断开底层连接,
 *      只有全部取消且无新订阅时才延迟关闭。
 */
import { ref } from 'vue'
import { trainingApi } from '@/api'

interface PoolListener {
  onMessage: (data: any) => void
  onComplete?: () => void
  onError?: (err: Error) => void
}

interface PoolEntry {
  taskId: string
  listeners: Set<PoolListener>
  cancel: (() => void) | null
  lastFrameAt: number
  lastState: string | null
  graceTimer: ReturnType<typeof setTimeout> | null
  terminalBroadcastAt: number | null
}

// 单例容器
const pool = new Map<string, PoolEntry>()

// 模块级 ref, 暴露活跃订阅数 (诊断 + useSilentRefresh 联动)
const activeTaskIds = ref<string[]>([])

// 终态保持窗口 (毫秒) - 让最后一帧有机会被所有订阅者接收
const TERMINAL_GRACE_MS = 1500

function refreshActiveList() {
  activeTaskIds.value = Array.from(pool.keys())
}

function closeEntry(taskId: string) {
  const entry = pool.get(taskId)
  if (!entry) return
  if (entry.graceTimer) {
    clearTimeout(entry.graceTimer)
    entry.graceTimer = null
  }
  if (entry.cancel) {
    try { entry.cancel() } catch { /* noop */ }
    entry.cancel = null
  }
  pool.delete(taskId)
  refreshActiveList()
}

function ensureOpen(taskId: string) {
  if (pool.has(taskId)) return pool.get(taskId)!
  const listeners = new Set<PoolListener>()
  const entry: PoolEntry = {
    taskId,
    listeners,
    cancel: null,
    lastFrameAt: 0,
    lastState: null,
    graceTimer: null,
    terminalBroadcastAt: null,
  }
  pool.set(taskId, entry)
  refreshActiveList()

  // 真正下单条 SSE 连接
  entry.cancel = trainingApi.streamProgress(taskId, {
    onMessage: (data) => {
      entry.lastFrameAt = Date.now()
      entry.lastState = data?.state ?? entry.lastState
      for (const l of entry.listeners) {
        try { l.onMessage(data) } catch (e) { /* 单个监听器异常不影响其它 */ }
      }
      // 终态: 标记广播时间, 保留窗口期
      if (data?.state && ['SUCCESS', 'FAILURE', 'REVOKED'].includes(data.state)) {
        if (entry.terminalBroadcastAt == null) entry.terminalBroadcastAt = Date.now()
      }
    },
    onComplete: () => {
      // 服务端主动 close / 终态: 广播 onComplete, 延迟清理 (避免 last-minute 订阅者错过)
      for (const l of entry.listeners) {
        try { l.onComplete?.() } catch { /* ignore */ }
      }
      if (entry.terminalBroadcastAt == null) entry.terminalBroadcastAt = Date.now()
      scheduleTerminalClose(taskId)
    },
    onError: (err) => {
      for (const l of entry.listeners) {
        try { l.onError?.(err) } catch { /* ignore */ }
      }
    },
  })
  return entry
}

function scheduleTerminalClose(taskId: string) {
  const entry = pool.get(taskId)
  if (!entry) return
  if (entry.graceTimer) clearTimeout(entry.graceTimer)
  entry.graceTimer = setTimeout(() => {
    if (entry.listeners.size === 0) {
      closeEntry(taskId)
    }
  }, TERMINAL_GRACE_MS)
}

export function useTrainingSsePool() {
  /**
   * 订阅指定 taskId 的 SSE 推送. 同一 taskId 已有连接时直接加入监听集合.
   * 返回 off() 函数, 调用后取消订阅; 全部取消后再延迟关闭底层连接.
   */
  function subscribe(taskId: string, listener: PoolListener): () => void {
    if (!taskId) return () => {}
    const entry = ensureOpen(taskId)
    entry.listeners.add(listener)
    // 清掉计划中的延迟关闭 (上一个订阅者刚退订, 新订阅者立刻复活)
    if (entry.graceTimer) {
      clearTimeout(entry.graceTimer)
      entry.graceTimer = null
    }

    let active = true
    return () => {
      if (!active) return
      active = false
      const e = pool.get(taskId)
      if (!e) return
      e.listeners.delete(listener)
      if (e.listeners.size === 0) {
        // 全员退订 → 延迟关闭, 给快进快出场景一次复用机会
        const grace = e.lastState && ['SUCCESS', 'FAILURE', 'REVOKED'].includes(e.lastState)
          ? TERMINAL_GRACE_MS
          : 500
        if (e.graceTimer) clearTimeout(e.graceTimer)
        e.graceTimer = setTimeout(() => {
          const cur = pool.get(taskId)
          if (cur && cur.listeners.size === 0) closeEntry(taskId)
        }, grace)
      }
    }
  }

  /** 当前活跃 taskId 列表 (用于 useSilentRefresh 联动) */
  function getActiveTaskIds(): string[] {
    return activeTaskIds.value
  }

  /** 当前池内总订阅数 (调试用) */
  function getActiveConnectionCount(): number {
    let n = 0
    for (const e of pool.values()) if (e.listeners.size > 0) n++
    return n
  }

  /** 强制关闭所有连接 (页面级强制清理) */
  function disposeAll() {
    for (const taskId of Array.from(pool.keys())) closeEntry(taskId)
  }

  return {
    subscribe,
    getActiveTaskIds,
    getActiveConnectionCount,
    disposeAll,
  }
}
