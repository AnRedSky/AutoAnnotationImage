/**
 * useTrainingJobs - 训练任务列表 + 筛选 + 分页 + 批量 composable
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 列表加载 (loadJobs) 含占位保留 + 幽灵占位超时清理
 * - 数据集 + 关键词 (300ms 防抖) + 状态 + 任务类型筛选
 * - 分页 (client-server 双层: server 拉页 / client 切片展示)
 * - 多选状态 + 批量删除 (按 canDelete 拆分)
 * - 占位行插入 (新建/再训练提交后乐观插入, 提升视觉反馈)
 */
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { trainingApi } from '@/api'

const GHOST_TIMEOUT_MS = 5 * 60 * 1000  // 5 分钟未确认的占位行视为幽灵, 主动清理

export interface TrainingJob {
  id: number
  celery_task_id?: string
  state: string
  progress: number
  message?: string
  model_name?: string
  base_model?: string
  dataset_id?: number
  task_type?: string
  epochs?: number
  is_placeholder?: boolean
  _placeholder_at?: number
  [k: string]: any
}

export interface PlaceholderParams {
  dataset_id: number
  base_model: string
  model_name: string
  epochs: number
  batch_size: number
  learning_rate: number
}

export function useTrainingJobs() {
  // ============== 状态 ==============
  const jobs = ref<TrainingJob[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(10)
  const loading = ref(false)
  // 筛选
  const stateFilter = ref<string>('')
  const datasetIdFilter = ref<number | null>(null)
  const modelKeywordFilter = ref<string>('')
  const taskTypeFilter = ref<string>('')
  // 多选
  const selectedJobIds = ref<number[]>([])
  const actionPending = ref<Record<number, string>>({})

  // 关键词防抖
  let keywordDebounceTimer: any = null

  // ============== 工具: canXxx 状态判定 ==============
  const canStart = (s: string) =>
    s === 'PENDING' || s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED'
  const canPause = (s: string) => s === 'PENDING' || s === 'PROGRESS'
  const canCancel = (s: string) => s === 'PENDING' || s === 'PROGRESS'
  const canDelete = (s: string) =>
    s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED'

  // ============== 加载 ==============
  const loadJobs = async () => {
    loading.value = true
    try {
      const params: any = { page: page.value, page_size: pageSize.value }
      if (stateFilter.value) params.state = stateFilter.value
      if (datasetIdFilter.value != null) params.dataset_id = datasetIdFilter.value
      if (modelKeywordFilter.value && modelKeywordFilter.value.trim()) {
        params.q = modelKeywordFilter.value.trim()
      }
      if (taskTypeFilter.value) params.task_type = taskTypeFilter.value

      const r: any = await trainingApi.jobs(params)
      const items: any[] = r?.items || []
      total.value = r?.total ?? 0

      // ---- 占位保留: 解决"刷新后占位被抹掉"问题 ----
      // 保留旧的占位行 (celery_task_id 在新列表里找不到的), 等 worker 写库后自动剔除
      const placeholders = jobs.value.filter((j) => j.is_placeholder)
      if (placeholders.length > 0) {
        const stillMissing: TrainingJob[] = []
        for (const p of placeholders) {
          const hit = items.some((it) => it.celery_task_id === p.celery_task_id)
          if (!hit) stillMissing.push(p)
        }
        if (stillMissing.length > 0) {
          // ---- 幽灵占位检测: 5 分钟未确认的占位行主动清理 ----
          const now = Date.now()
          const fresh: TrainingJob[] = []
          const ghosts: TrainingJob[] = []
          for (const p of stillMissing) {
            const age = now - (p._placeholder_at || now)
            if (age > GHOST_TIMEOUT_MS) ghosts.push(p)
            else fresh.push(p)
          }
          if (ghosts.length > 0) {
            console.warn('[loadJobs] 丢弃幽灵占位 (后端查不到对应 celery_task_id):',
              ghosts.map((g) => g.celery_task_id))
            ElMessage.warning(`${ghosts.length} 个任务长时间未确认, 已自动清理 (后端可能未写入)`)
          }
          jobs.value = [...fresh, ...items]
          total.value = (r?.total ?? 0) + fresh.length
          return
        }
      }
      jobs.value = items
    } catch (e: any) {
      ElMessage.error('加载训练任务失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      loading.value = false
    }
  }

  // ============== 分页回调 ==============
  const onPageChange = (p: number) => { page.value = p; loadJobs() }
  const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1; loadJobs() }

  // ============== 筛选变更 ==============
  const onStateFilterChange = () => { page.value = 1; loadJobs() }
  const onDatasetFilterChange = () => { page.value = 1; loadJobs() }
  const onTaskTypeFilterChange = () => { page.value = 1; loadJobs() }
  const onModelKeywordChange = (val: string) => {
    if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
    if (!val || !val.trim()) {
      page.value = 1
      loadJobs()
      return
    }
    keywordDebounceTimer = setTimeout(() => {
      page.value = 1
      loadJobs()
    }, 300)
  }

  // ============== 筛选重置 + 单项清除 ==============
  const resetFilters = () => {
    if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
    stateFilter.value = ''
    datasetIdFilter.value = null
    modelKeywordFilter.value = ''
    taskTypeFilter.value = ''
    page.value = 1
    loadJobs()
  }
  const onStateFilterClear = () => { stateFilter.value = ''; page.value = 1; loadJobs() }
  const onDatasetFilterClear = () => { datasetIdFilter.value = null; page.value = 1; loadJobs() }
  const onTaskTypeFilterClear = () => { taskTypeFilter.value = ''; page.value = 1; loadJobs() }
  const onModelKeywordClear = () => {
    if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
    modelKeywordFilter.value = ''
    page.value = 1
    loadJobs()
  }

  // ============== 多选 ==============
  const onSelectionChange = (rows: any[]) => {
    selectedJobIds.value = rows.map((r) => r.id)
  }
  const allOnPageSelected = computed(
    () => jobs.value.length > 0 && selectedJobIds.value.length === jobs.value.length
  )
  const toggleSelectAllJobs = () => {
    if (allOnPageSelected.value) selectedJobIds.value = []
    else selectedJobIds.value = jobs.value.map((j) => j.id)
  }

  // ============== 批量删除 ==============
  async function batchDeleteJobs() {
    const ids = [...selectedJobIds.value]
    if (!ids.length) return
    const deletable: any[] = []
    const skipped: any[] = []
    for (const id of ids) {
      const job = jobs.value.find((j) => j.id === id)
      if (job && canDelete(job.state)) deletable.push(job)
      else skipped.push(job)
    }
    if (!deletable.length) {
      ElMessage.warning('所选任务均处于非终态, 无法删除')
      return
    }
    try {
      await ElMessageBox.confirm(
        skipped.length
          ? `确认删除 ${deletable.length} 个任务? 另有 ${skipped.length} 个非终态任务将被跳过。`
          : `确认删除 ${deletable.length} 个任务? 此操作不可恢复`,
        '批量删除',
        { type: 'warning' }
      )
    } catch { return }  // 用户取消

    let okCount = 0
    let failCount = 0
    for (const job of deletable) {
      try {
        actionPending.value[job.id] = 'delete'
        await trainingApi.removeJob(job.id)
        okCount++
      } catch (e: any) {
        failCount++
        console.warn(`删除任务 #${job.id} 失败:`, e)
      } finally {
        delete actionPending.value[job.id]
      }
    }
    selectedJobIds.value = []
    if (okCount) ElMessage.success(`已删除 ${okCount} 个任务${failCount ? `, ${failCount} 个失败` : ''}`)
    else ElMessage.error('删除失败')
    await loadJobs()
  }

  // ============== 占位行插入 (新建/再训练提交后乐观插入) ==============
  const insertPlaceholderJob = (newTaskId: string, params: PlaceholderParams): TrainingJob => {
    const placeholder: TrainingJob = {
      id: -Date.now(),  // 负数 id, 不与真实 id 冲突
      celery_task_id: newTaskId,
      user_id: 0,
      dataset_id: params.dataset_id,
      base_model: params.base_model,
      model_name: params.model_name,
      epochs: params.epochs,
      batch_size: params.batch_size,
      learning_rate: params.learning_rate,
      state: 'PENDING',
      progress: 0,
      message: '等待 worker 启动...',
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      duration_seconds: null,
      is_placeholder: true,
      _placeholder_at: Date.now(),
    }
    jobs.value = [placeholder, ...jobs.value]
    total.value = (total.value || 0) + 1
    page.value = 1
    return placeholder
  }

  // ============== 原地更新一行 (SSE 行内更新用) ==============
  // v3.1.1 Round 2A perf: 改 splice 整行替换为 Object.assign 细粒度更新
  // - splice(idx,1,next) 触发该行整体重新渲染 + 数组 length 变化 (Vue 会 invalidate 整行)
  // - Object.assign 在已响应式代理上写属性, 触发细粒度依赖, 只更新引用了变化字段的 cell
  // - SSE 高频 (秒级 progress) 路径下, 避免每次重渲整行 12 列
  const updateJobProgressInPlace = (jobId: number, data: any) => {
    const idx = jobs.value.findIndex((j) => j.id === jobId)
    if (idx < 0) return
    const cur = jobs.value[idx]
    // 预校验: 没有任何可应用字段则直接 return, 避免空 Object.assign 触发依赖
    let touched = false
    if (data.state) { cur.state = data.state; touched = true }
    if (typeof data.progress === 'number') { cur.progress = data.progress; touched = true }
    if (data.message) { cur.message = data.message; touched = true }
    if (typeof data.current_epoch === 'number') { cur.current_epoch = data.current_epoch; touched = true }
    if (typeof data.total_epochs === 'number') { cur.total_epochs = data.total_epochs; touched = true }
    if (data.started_at) { cur.started_at = data.started_at; touched = true }
    if (data.finished_at) { cur.finished_at = data.finished_at; touched = true }
    if (!touched) return
    // 显式浅 trigger: 让 <el-table> 检测到行对象引用更新 (cur 已 reactive)
    jobs.value[idx] = cur
  }

  return {
    // state
    jobs, total, page, pageSize, loading,
    stateFilter, datasetIdFilter, modelKeywordFilter, taskTypeFilter,
    selectedJobIds, actionPending,
    // computed
    allOnPageSelected,
    // utils
    canStart, canPause, canCancel, canDelete,
    // load + 筛选
    loadJobs, onPageChange, onSizeChange,
    onStateFilterChange, onDatasetFilterChange, onTaskTypeFilterChange, onModelKeywordChange,
    resetFilters,
    onStateFilterClear, onDatasetFilterClear, onTaskTypeFilterClear, onModelKeywordClear,
    // 多选 + 批量
    onSelectionChange, toggleSelectAllJobs, batchDeleteJobs,
    // 占位行
    insertPlaceholderJob,
    // SSE 行内更新
    updateJobProgressInPlace,
  }
}
