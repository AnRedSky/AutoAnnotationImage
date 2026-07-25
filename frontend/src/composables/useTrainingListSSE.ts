/**
 * useTrainingListSSE — 训练列表级 SSE 订阅 (Composables)
 * ===================================================
 *
 * 职责:
 * - 对当前页里所有 PENDING/PROGRESS 任务维护 SSE 订阅
 * - 收到帧时原地更新对应行的 (state, progress, message, current_epoch, total_epochs)
 *   不触发整页重拉
 * - 任务进入终态 (SUCCESS/FAILURE/REVOKED) 时断开流 + 触发静默整页刷新
 *   以同步 model_version 联动等后端字段
 *
 * 抽离动机:
 * - Training/index.vue 2480 行, 列表级 SSE 逻辑 ~95 行, 是相对独立的关注点
 * - 抽离后 page 只剩 use + 生命周期接入, 业务复杂度显著下降
 *
 * 使用:
 * ```ts
 * const { syncListStreams, cleanupAll } = useTrainingListSSE({
 *   jobs,
 *   onProgressUpdate: (jobId, data) => { ... },  // 原地更新一行
 *   onTerminalState: (jobId) => { loadJobs() },  // 终态时拉整页
 *   onError: (jobId) => { loadJobs() },          // SSE 错误兜底
 * })
 *
 * watch(jobs, () => syncListStreams())
 * onBeforeUnmount(cleanupAll)
 * ```
 */
import { onBeforeUnmount, watch, type Ref } from 'vue'
import { trainingApi } from '@/api'

interface ListStreamHandle {
  cancel: () => void
  taskId: string
  jobId: number
}

export interface UseTrainingListSSEOptions {
  /** 任务列表 (Ref) */
  jobs: Ref<any[]>
  /** 收到帧时回调 (原地更新一行) */
  onProgressUpdate: (jobId: number, data: any) => void
  /** 进入终态时回调 (用于拉整页) */
  onTerminalState: (jobId: number) => void
  /** SSE 出错时回调 (兜底, 通常 = 拉整页) */
  onError?: (jobId: number) => void
  /** 收到 complete 事件时回调 (服务端主动 end) */
  onComplete?: (jobId: number) => void
}

export function useTrainingListSSE(options: UseTrainingListSSEOptions) {
  const {
    jobs,
    onProgressUpdate,
    onTerminalState,
    onError,
    onComplete,
  } = options

  const streams = new Map<number, ListStreamHandle>()  // key = jobId

  /**
   * 对当前页里所有 PENDING/PROGRESS 行建立 SSE 订阅.
   * - 同一 jobId 已存在订阅则跳过 (避免重复)
   * - 收到帧时通过 onProgressUpdate 原地更新 rows
   * - 终态时主动断开流, 并触发 onTerminalState 拉整页
   */
  const syncListStreams = () => {
    const liveJobs = jobs.value.filter(
      (j: any) => (j.state === 'PROGRESS' || j.state === 'PENDING') && j.celery_task_id
    )
    const liveIds = new Set(liveJobs.map((j: any) => j.id))

    // 1) 清理已经不在活跃集合里的订阅 (被删除/翻页/状态变了)
    for (const [jobId, h] of streams.entries()) {
      if (!liveIds.has(jobId)) {
        try { h.cancel() } catch { /* ignore */ }
        streams.delete(jobId)
      }
    }

    // 2) 为新出现的活跃 job 建立订阅
    for (const j of liveJobs) {
      if (streams.has(j.id)) continue
      const cancel = trainingApi.streamProgress(j.celery_task_id, {
        onMessage: (data) => {
          onProgressUpdate(j.id, data)
          if (data.state === 'SUCCESS' || data.state === 'FAILURE' || data.state === 'REVOKED') {
            const h = streams.get(j.id)
            if (h) {
              try { h.cancel() } catch { /* ignore */ }
              streams.delete(j.id)
            }
            onTerminalState(j.id)
          }
        },
        onError: () => {
          const h = streams.get(j.id)
          if (h) {
            try { h.cancel() } catch { /* ignore */ }
            streams.delete(j.id)
          }
          // v2.5.27 修复: SSE 出错时立即拉一次 DB, 防止训练已失败但 SSE 断开后
          // 列表一直显示训练中. 这是非常关键的兜底.
          onError?.(j.id)
        },
        onComplete: () => {
          const h = streams.get(j.id)
          if (h) {
            try { h.cancel() } catch { /* ignore */ }
            streams.delete(j.id)
          }
          onComplete?.(j.id)
        },
      })
      streams.set(j.id, { cancel, taskId: j.celery_task_id, jobId: j.id })
    }
  }

  const cleanupAll = () => {
    for (const h of streams.values()) {
      try { h.cancel() } catch { /* ignore */ }
    }
    streams.clear()
  }

  // 自动监听 jobs 变化, 同步订阅
  watch(jobs, () => syncListStreams(), { deep: false })

  // 组件卸载时自动清理
  onBeforeUnmount(cleanupAll)

  return {
    syncListStreams,
    cleanupAll,
    /** 当前活跃订阅数 (调试用) */
    get activeCount() { return streams.size },
  }
}
