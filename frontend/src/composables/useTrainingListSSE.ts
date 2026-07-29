/**
 * useTrainingListSSE — 训练列表级 SSE 订阅 (Composables)
 * ==================================================
 *
 * 职责:
 * - 对当前页里所有 PENDING/PROGRESS 任务维护 SSE 订阅 (引用计数)
 * - 收到帧时原地更新对应行的 (state, progress, message, current_epoch, total_epochs)
 *   不触发整页重拉
 * - 任务进入终态 (SUCCESS/FAILURE/REVOKED) 时断开流 + 触发静默整页刷新
 *   以同步 model_version 联动等后端字段
 *
 * v3.1.0 性能优化 (Phase T1): 改为共享 useTrainingSsePool
 * - 旧实现每个活跃 job 独立建立 SSE 连接, 单 task 可能与详情弹窗的 streamProgress
 *   并发开 2 条流 (服务端每 1s 一次 Celery+DB 查询, 连接数 = 2N)
 * - 新实现走共享订阅池, 同一 taskId 只开一条底层连接, N 个任务 → N 条连接
 * - 切页/筛选时批量 unsubscribe, 池自动延迟清理
 *
 * 使用:
 * `	s
 * const { syncListStreams, cleanupAll } = useTrainingListSSE({
 *   jobs, onProgressUpdate, onTerminalState, onError,
 * })
 * watch(jobs, () => syncListStreams())
 * onBeforeUnmount(cleanupAll)
 * `
 */
import { onBeforeUnmount, watch, type Ref } from 'vue'
import { useTrainingSsePool } from './useTrainingSsePool'

export interface UseTrainingListSSEOptions {
  jobs: Ref<any[]>
  onProgressUpdate: (jobId: number, data: any) => void
  onTerminalState: (jobId: number) => void
  onError?: (jobId: number) => void
  onComplete?: (jobId: number) => void
}

export function useTrainingListSSE(options: UseTrainingListSSEOptions) {
  const { jobs, onProgressUpdate, onTerminalState, onError, onComplete } = options
  const pool = useTrainingSsePool()
  const unsubs = new Map<number, () => void>()  // key = jobId

  const isTerminal = (s: string) =>
    s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED'

  /**
   * 同步订阅状态:
   * - 对当前活跃 (PENDING/PROGRESS) job 各订阅一条
   * - 不再活跃的 job 取消订阅
   * - 同一 jobId 已在池里时, 复用底层流 + 多 listener fan-out
   */
  const syncListStreams = () => {
    const liveJobs = jobs.value.filter(
      (j: any) => (j.state === 'PROGRESS' || j.state === 'PENDING') && j.celery_task_id,
    )
    const liveIds = new Set(liveJobs.map((j: any) => j.id))

    // 1) 清理已不在活跃集合的订阅
    for (const [jobId, off] of unsubs.entries()) {
      if (!liveIds.has(jobId)) {
        try { off() } catch { /* noop */ }
        unsubs.delete(jobId)
      }
    }

    // 2) 新出现的活跃 job 建立订阅
    for (const j of liveJobs) {
      if (unsubs.has(j.id)) continue
      const taskId: string = j.celery_task_id
      const off = pool.subscribe(taskId, {
        onMessage: (data) => {
          onProgressUpdate(j.id, data)
          if (isTerminal(data?.state)) onTerminalState(j.id)
        },
        onComplete: () => onComplete?.(j.id),
        onError: () => onError?.(j.id),
      })
      unsubs.set(j.id, off)
    }
  }

  const cleanupAll = () => {
    for (const off of unsubs.values()) {
      try { off() } catch { /* noop */ }
    }
    unsubs.clear()
  }

  watch(jobs, () => syncListStreams(), { deep: false })
  onBeforeUnmount(cleanupAll)

  return {
    syncListStreams,
    cleanupAll,
    /** 当前活跃订阅数 (调试用) */
    get activeCount() { return unsubs.size },
  }
}
