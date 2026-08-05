/**
 * useTrainingActions - 训练任务行操作 composable
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 行操作: 启动 (含 resume/restart 模式) / 暂停 / 取消 / 删除
 * - 合并的"启动/暂停"按钮文案与类型 (runBtnLabel/Type/Action/Disabled)
 * - 调用 insertPlaceholderJob + loadJobs 触发列表刷新
 *
 * 注意: 不持有 selectedJobIds / jobs, 由 page 持有后通过参数注入,
 *       保持 composable 单一职责 (无外部隐式依赖)
 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { trainingApi } from '@/api'
import type { Ref } from 'vue'
import type { TrainingJob, PlaceholderParams } from './useTrainingJobs'

export interface UseTrainingActionsOptions {
  /** 占位行插入函数 */
  insertPlaceholder: (taskId: string, params: PlaceholderParams) => TrainingJob
  /** 列表重新加载 */
  reload: () => Promise<void>
  /** 行操作 loading 状态 (jobId -> 'start'/'pause'/'cancel'/'delete') - Ref 包裹以保持响应性 */
  actionPending: Ref<Record<number, string>>
}

export function useTrainingActions(options: UseTrainingActionsOptions) {
  const { insertPlaceholder, reload, actionPending } = options

  // ============== 合并按钮文案/类型/操作 ==============
  // v3.5.0: 增加 CANCELED 终态 — 用户主动取消, 不可"继续"但可"再训练"
  const runBtnLabel = (s: string) => {
    if (s === 'PROGRESS') return '暂停'
    if (s === 'PAUSED') return '继续'
    if (s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'CANCELED') return '再训练'
    return '启动'
  }
  const runBtnType = (s: string) => (s === 'PROGRESS' ? 'warning' : 'primary')
  const runBtnAction = (s: string) => (s === 'PROGRESS' ? 'pause' : 'start')
  const runBtnDisabled = (s: string) => {
    if (s === 'PROGRESS') return s !== 'PROGRESS'  // canPause(s) === s in [PENDING, PROGRESS]
    if (s === 'PAUSED') return false
    if (s === 'PENDING') return false
    return !(s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED' || s === 'CANCELED')
  }

  // ============== 行操作 ==============
  /**
   * 合并按钮点击入口: 根据状态分发
   * - PROGRESS → 暂停 (无弹窗, 走 onRowPause 流程)
   * - PAUSED → 继续 (复用原 job, mode=resume)
   * - 其他状态 (PENDING/终态) → 弹窗修改参数后启动 (由 page 触发)
   *
   * 返回 true 表示已处理 (PROGRESS/PAUSED), false 表示需要打开弹窗
   */
  const onRunBtnClick = async (row: any, openEditDialog: (row: any) => void): Promise<boolean> => {
    if (row.state === 'PROGRESS') {
      await onRowPause(row)
      return true
    }
    if (row.state === 'PAUSED') {
      await onRowStart(row, 'resume')
      return true
    }
    openEditDialog(row)
    return false
  }

  /**
   * 启动 / 再训练 / 继续
   * - PAUSED: 必须走 resume, 才会复用原 job (不创建新 job, 不改 model_name)
   * - 其他终态: 走 restart (默认, 创建 _r{timestamp} 新 model_name 的再训练)
   * - restart 模式: 后端预创建新 TrainingJob 行, 返回 new_job_id + task_id
   *   立即插入占位行让用户看到"再训练已生效"
   */
  const onRowStart = async (row: any, mode: 'restart' | 'resume' = 'restart') => {
    if (mode === 'restart' && !canStart(row.state)) return
    if (mode === 'resume' && row.state !== 'PAUSED') return
    try {
      actionPending.value[row.id] = 'start'
      const r: any = await trainingApi.startJob(row.id, mode)
      if (r.success) {
        ElMessage.success(r.message || (mode === 'resume' ? '已继续训练' : '已启动新一轮训练'))
        // mode=restart: 后端预创建新 TrainingJob 行, 立即插入占位行
        // mode=resume: 复用旧行, 不需要占位, 直接 reload 即可
        if (mode === 'restart' && r.task_id) {
          insertPlaceholder(r.task_id, {
            dataset_id: row.dataset_id,
            base_model: row.base_model,
            model_name: row.model_name,
            epochs: row.epochs,
            batch_size: row.batch_size,
            learning_rate: row.learning_rate,
          })
        }
        await reload()
      } else {
        ElMessage.warning(r.message || '启动失败')
      }
    } catch (e: any) {
      ElMessage.error('启动失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      delete actionPending.value[row.id]
    }
  }

  const onRowPause = async (row: any) => {
    if (row.state !== 'PENDING' && row.state !== 'PROGRESS') return
    try {
      await ElMessageBox.confirm(
        `确认暂停训练任务 #${row.id}? Worker 将在下一个 epoch 边界停止`,
        '暂停训练',
        { type: 'warning' }
      )
    } catch { return }
    try {
      actionPending.value[row.id] = 'pause'
      const r: any = await trainingApi.pauseJob(row.id)
      if (r.success) {
        ElMessage.success(r.message || '已发送暂停信号')
        await reload()
      } else {
        ElMessage.warning(r.message || '无法暂停')
      }
    } catch (e: any) {
      ElMessage.error('暂停失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      delete actionPending.value[row.id]
    }
  }

  const onRowCancel = async (row: any) => {
    if (row.state !== 'PENDING' && row.state !== 'PROGRESS') return
    try {
      await ElMessageBox.confirm(
        `确认取消训练任务 #${row.id}? 该操作不可恢复`,
        '取消训练',
        { type: 'warning' }
      )
    } catch { return }
    try {
      actionPending.value[row.id] = 'cancel'
      const r: any = await trainingApi.cancel(row.id)
      if (r.success) {
        ElMessage.success('训练任务已取消')
        await reload()
      } else {
        ElMessage.warning(r.message || '无法取消')
      }
    } catch (e: any) {
      ElMessage.error('取消失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      delete actionPending.value[row.id]
    }
  }

  /**
   * 删除任务 (可删除: SUCCESS/FAILURE/REVOKED/PAUSED/CANCELED)
   * 终态才可删; 详情弹窗里展示的任务被删时, 由 page 主动关弹窗
   */
  const onRowDelete = async (row: any, onAfterDelete?: (row: any) => void) => {
    const terminalStates = ['SUCCESS', 'FAILURE', 'REVOKED', 'PAUSED', 'CANCELED']
    if (!terminalStates.includes(row.state)) {
      return
    }
    try {
      await ElMessageBox.confirm(
        `确认删除训练任务 #${row.id} (${row.state})? 此操作不可恢复, 不会影响已生成的模型版本`,
        '删除训练任务',
        { type: 'warning' }
      )
    } catch { return }
    try {
      actionPending.value[row.id] = 'delete'
      const r: any = await trainingApi.removeJob(row.id)
      if (r.success) {
        ElMessage.success('任务已删除')
        onAfterDelete?.(row)
        await reload()
      } else {
        ElMessage.warning(r.message || '无法删除')
      }
    } catch (e: any) {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      delete actionPending.value[row.id]
    }
  }

  return {
    runBtnLabel, runBtnType, runBtnAction, runBtnDisabled,
    onRunBtnClick, onRowStart, onRowPause, onRowCancel, onRowDelete,
  }
}

// ---- 状态判定工具 (与 useTrainingJobs 重复, 单独导出供 useTrainingActions 内部使用) ----
// v3.5.0: 增加 CANCELED 终态 — 可"再训练"
function canStart(s: string) {
  return s === 'PENDING' || s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED' || s === 'CANCELED'
}
