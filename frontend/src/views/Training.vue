<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  VideoPlay, VideoPause, CircleClose, View, Delete, Refresh, Plus,
  List as ListIcon, DataLine, Search, InfoFilled
} from '@element-plus/icons-vue'
import { trainingApi, datasetApi, autoAnnotateApi } from '@/api'
import * as echarts from 'echarts'
import TrainingParamsForm, { type TrainingParams } from '@/components/TrainingParamsForm.vue'

interface EpochData {
  epoch: number
  train_loss: number
  val_loss: number
  train_acc: number
  val_acc: number
}

// ============== 基础选项 ==============
// 与后端 /api/auto-annotate/models 保持一致的子集；framework 用于在前端下拉中标注模型来源
const BASE_MODELS: Array<{ name: string; framework: string; params?: string }> = [
  { name: 'resnet18',              framework: 'timm', params: '11.7M' },
  { name: 'resnet50',              framework: 'timm', params: '25.6M' },
  { name: 'efficientnet_b0',       framework: 'timm', params: '5.3M'  },
  { name: 'efficientnet_b3',       framework: 'timm', params: '12.0M' },
  { name: 'mobilenetv3_large_100', framework: 'timm', params: '5.5M'  },
  { name: 'convnext_tiny',         framework: 'timm', params: '28.6M' },
]

// ============== 表格多选 + 批量操作 ==============
// selectedJobIds: 当前页选中的 job id 列表 (跨页不持久, 由 Element Plus 默认行为决定)
const selectedJobIds = ref<number[]>([])
const onJobSelectionChange = (rows: any[]) => {
  selectedJobIds.value = rows.map((r) => r.id)
}
/** 智能全选 (跨当前页) — 切换为全选/取消 */
const allOnPageSelected = computed(
  () => jobs.value.length > 0 && selectedJobIds.value.length === jobs.value.length
)
function toggleSelectAllJobs() {
  if (allOnPageSelected.value) {
    selectedJobIds.value = []
  } else {
    selectedJobIds.value = jobs.value.map((j) => j.id)
  }
}
/** 批量删除: 仅对终态 (SUCCESS/FAILURE/REVOKED/PAUSED) 可删, 跳过非终态并提示 */
async function batchDeleteJobs() {
  const ids = [...selectedJobIds.value]
  if (!ids.length) return
  // 拆分可删与不可删
  const deletable: any[] = []
  const skipped: any[] = []
  for (const id of ids) {
    const job = jobs.value.find((j: any) => j.id === id)
    if (job && canDelete(job.state)) {
      deletable.push(job)
    } else {
      skipped.push(job)
    }
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

const DATASET_OPTIONS = ref<any[]>([])
const loadDatasets = async () => {
  try {
    const r: any = await datasetApi.list()
    DATASET_OPTIONS.value = r?.items || r || []
  } catch (e: any) {
    ElMessage.warning('加载数据集失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 任务列表 + 分页 ==============
const jobs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const stateFilter = ref<string>('')
const loading = ref(false)

// 数据集筛选: 按 dataset_id (与后端 TrainingJob.dataset_id 对应)
const datasetIdFilter = ref<number | null>(null)
// 模型关键词筛选: 同时模糊匹配 model_name 和 base_model (后端 ILIKE)
const modelKeywordFilter = ref<string>('')
// 防抖: 关键词输入用 setTimeout 静默刷新, 避免每按一个字母就发一次请求
let keywordDebounceTimer: any = null

const STATE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '等待中', value: 'PENDING' },
  { label: '训练中', value: 'PROGRESS' },
  { label: '已完成', value: 'SUCCESS' },
  { label: '已暂停', value: 'PAUSED' },
  { label: '已取消', value: 'REVOKED' },
  { label: '失败', value: 'FAILURE' },
]

const loadJobs = async () => {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize.value }
    if (stateFilter.value) params.state = stateFilter.value
    if (datasetIdFilter.value != null) params.dataset_id = datasetIdFilter.value
    if (modelKeywordFilter.value && modelKeywordFilter.value.trim()) {
      params.q = modelKeywordFilter.value.trim()
    }
    const r: any = await trainingApi.jobs(params)
    jobs.value = r?.items || []
    total.value = r?.total ?? 0
  } catch (e: any) {
    ElMessage.error('加载训练任务失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const onPageChange = (p: number) => { page.value = p; loadJobs() }
const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1; loadJobs() }
const onStateFilterChange = () => { page.value = 1; loadJobs() }
const onDatasetFilterChange = () => { page.value = 1; loadJobs() }
/**
 * 关键词筛选: 300ms 防抖, 避免每个字符都发请求
 * 清空关键词也立即触发一次 (用户主动 clear 时希望看到完整列表)
 */
const onModelKeywordChange = (val: string) => {
  if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
  if (!val || !val.trim()) {
    // 清空场景: 立即刷新
    page.value = 1
    loadJobs()
    return
  }
  keywordDebounceTimer = setTimeout(() => {
    page.value = 1
    loadJobs()
  }, 300)
}
/** 重置所有筛选条件 */
const resetFilters = () => {
  if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
  stateFilter.value = ''
  datasetIdFilter.value = null
  modelKeywordFilter.value = ''
  page.value = 1
  loadJobs()
}
/** 筛选 chip 上的 ✕ 关闭回调: 只清掉对应那一项 */
const onStateFilterClear = () => {
  stateFilter.value = ''
  page.value = 1
  loadJobs()
}
const onDatasetFilterClear = () => {
  datasetIdFilter.value = null
  page.value = 1
  loadJobs()
}
const onModelKeywordClear = () => {
  if (keywordDebounceTimer) clearTimeout(keywordDebounceTimer)
  modelKeywordFilter.value = ''
  page.value = 1
  loadJobs()
}

/**
 * 表格序号: 当前页 = (page - 1) * pageSize + 行索引 (从 1 开始)
 * 三个表格 (Training / Models / Datasets) 复用同一公式
 */
const indexMethod = (idx: number) => (page.value - 1) * pageSize.value + idx + 1

// ============== 训练资源 (设备) 辅助函数 ==============
// device_type: "cuda" | "mps" | "cpu" | undefined (老数据可能没)
const deviceTagType = (t: string | null | undefined) => {
  if (t === 'cuda') return 'success'   // GPU 绿
  if (t === 'mps') return 'warning'    // MPS 黄
  if (t === 'cpu') return 'info'       // CPU 灰
  return 'info'
}
const deviceShortLabel = (t: string | null | undefined, name: string | null | undefined) => {
  const type = (t || 'cpu').toUpperCase()
  if (!name) return type
  // 名字截断: NVIDIA GeForce RTX 4090 -> RTX 4090 (去掉前缀)
  const short = name.replace(/^NVIDIA\s+/i, '').replace(/^GeForce\s+/i, '')
  return `${type} · ${short}`
}
const formatDeviceTooltip = (info: any) => {
  if (!info || typeof info !== 'object') return ''
  const lines: string[] = []
  lines.push(`设备: ${info.device_name || '?'}`)
  if (info.device_type) lines.push(`类型: ${info.device_type.toUpperCase()}`)
  if (info.cuda_version) lines.push(`CUDA: ${info.cuda_version}`)
  if (info.cudnn_version) lines.push(`cuDNN: ${info.cudnn_version}`)
  if (info.gpu_memory_total_mb) lines.push(`GPU 显存: ${(info.gpu_memory_total_mb / 1024).toFixed(1)} GB`)
  if (info.gpu_peak_mb) lines.push(`训练峰值显存: ${(info.gpu_peak_mb / 1024).toFixed(1)} GB`)
  if (info.cpu_count) lines.push(`CPU 核数: ${info.cpu_count}`)
  if (info.ram_gb) lines.push(`RAM: ${info.ram_gb} GB`)
  if (info.torch_version) lines.push(`PyTorch: ${info.torch_version}`)
  if (info.python_version) lines.push(`Python: ${info.python_version}`)
  if (info.os_platform) lines.push(`系统: ${info.os_platform}`)
  if (info.fallback_reason) lines.push(`⚠️ 退回: ${info.fallback_reason}`)
  return lines.join('\n')
}

// ============== 新建任务对话框 ==============
const createDialogVisible = ref(false)
const createSubmitting = ref(false)

// 工具: 生成默认 model_name (格式 {base_model}_v{ver}_{ts})
// 例: resnet50_v1_1701234567 (10 位时间戳, 短而唯一)
const genDefaultModelName = (baseModel: string): string => {
  const ver = '1'
  const ts = Math.floor(Date.now() / 1000) % 10000000000  // 取 10 位数字
  return `${baseModel}_v${ver}_${ts}`
}

const createForm = ref({
  dataset_id: null as number | null,
  base_model: 'resnet50',
  model_name: genDefaultModelName('resnet50'),
  epochs: 20,
  batch_size: 32,
  learning_rate: 0.0001,
})

const openCreateDialog = () => {
  // 每次打开重置默认值 (含自动生成的 model_name)
  createForm.value = {
    dataset_id: null,
    base_model: 'resnet50',
    model_name: genDefaultModelName('resnet50'),
    epochs: 20,
    batch_size: 32,
    learning_rate: 0.0001,
  }
  createDialogVisible.value = true
}

const onCreateSubmit = async () => {
  if (!createForm.value.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  if (!createForm.value.model_name) {
    ElMessage.warning('请填写模型版本名')
    return
  }
  createSubmitting.value = true
  try {
    const r: any = await trainingApi.start({
      ...createForm.value,
      dataset_id: createForm.value.dataset_id!,
    })
    const newTaskId: string = r.task_id
    ElMessage.success(`训练任务已提交 (task_id=${(newTaskId || '').slice(0, 8)}…)`)
    createDialogVisible.value = false
    // 跳到第 1 页, 重新加载列表
    page.value = 1
    await loadJobs()

    // ---- 关键: 用 SSE 等 worker 真正开始执行 (DB 记录就绪), 再静默刷新一次 ----
    // 原因: 后端 start_training 端点只入队不写库, TrainingJob 记录是 worker 启动时
    // 才会写. 紧跟 loadJobs() 之后立即再刷一次, 列表里就能稳定看到这条新任务.
    if (newTaskId) {
      waitForJobInList(newTaskId).catch(() => { /* 超时也无妨, 用户可手动刷新 */ })
    }
  } catch (e: any) {
    ElMessage.error('启动失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    createSubmitting.value = false
  }
}

/**
 * 用 SSE 监测一个新提交的 task_id, 当首帧 (或 worker 端任何状态变化)
 * 到达时, 表明 worker 已接手 (DB 已有 TrainingJob 记录), 此时调用 loadJobs()
 * 把新任务拉入列表.
 *
 * 实现: 复用 streamProgress 接口, 在 onMessage 触发一次 silent refresh
 * 即可; 然后主动关闭流.
 *
 * 兜底: 30s 内未收到任何帧, 主动 close + 静默调用一次 loadJobs()
 * (避免 worker 永远不启动的场景下永远不刷新).
 */
const waitForJobInList = (taskId: string): Promise<void> => {
  return new Promise((resolve) => {
    let done = false
    const cleanup = (cancel?: () => void) => {
      if (done) return
      done = true
      if (cancel) cancel()
    }
    const cancel = trainingApi.streamProgress(taskId, {
      onMessage: () => {
        if (done) return
        cleanup()
        loadJobs()
        resolve()
      },
      onError: () => {
        if (done) return
        cleanup()
        // SSE 出错时也兜底刷一次
        loadJobs()
        resolve()
      },
      onComplete: () => {
        if (done) return
        cleanup()
        loadJobs()
        resolve()
      },
    })
    // 兜底超时: 30s 还没拿到首帧, 主动断流 + 静默刷一次
    setTimeout(() => {
      if (done) return
      cleanup(cancel)
      loadJobs()
      resolve()
    }, 30000)
  })
}

/**
 * TrainingParamsForm 单字段变更 → 同步到本地 form
 * 抽到顶层是为了在两个弹窗 (create / editAndStart) 共用, 避免重复
 * 接收 TrainingParams (而非 Ref), 模板中 ref 会自动解包
 */
const onParamsChange = (form: TrainingParams, patch: Partial<TrainingParams>) => {
  Object.assign(form, patch)
}

// ============== 行操作: 启动 / 暂停 / 取消 / 删除 ==============
const actionPending = ref<Record<number, string>>({})  // jobId -> 操作名, 用于按钮 loading

const canStart = (s: string) =>
  s === 'PENDING' || s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED'
// PENDING 状态: worker 没接走时可手动复用启动; 终态: 再训练一次

const canPause = (s: string) => s === 'PENDING' || s === 'PROGRESS'
const canCancel = (s: string) => s === 'PENDING' || s === 'PROGRESS'
const canDelete = (s: string) =>
  s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED' || s === 'PAUSED'

/**
 * 合并后的"启动/暂停"按钮文案与类型
 * - PENDING: 「启动」 (PENDING 状态 worker 还没接走, 主动推一下)
 * - PROGRESS: 「暂停」
 * - PAUSED: 「继续」 (复用 startJob 接口再提交)
 * - SUCCESS/FAILURE/REVOKED: 「再训练」
 */
const runBtnLabel = (s: string) => {
  if (s === 'PROGRESS') return '暂停'
  if (s === 'PAUSED') return '继续'
  if (s === 'SUCCESS' || s === 'FAILURE' || s === 'REVOKED') return '再训练'
  return '启动'
}
const runBtnType = (s: string) => (s === 'PROGRESS' ? 'warning' : 'primary')
const runBtnAction = (s: string) => (s === 'PROGRESS' ? 'pause' : 'start')
const runBtnDisabled = (s: string) => {
  if (s === 'PROGRESS') return !canPause(s)
  if (s === 'PAUSED') return false
  if (s === 'PENDING') return false
  return !canStart(s)
}

const onRunBtnClick = async (row: any) => {
  // PROGRESS → 暂停 (无弹窗, 走原 onRowPause 流程)
  if (row.state === 'PROGRESS') return onRowPause(row)
  // PAUSED → 继续 (复用原 job, 不创建新任务, mode=resume)
  if (row.state === 'PAUSED') return onRowStart(row, 'resume')
  // 其他状态 (PENDING/终态) → 弹窗修改参数后启动
  return openEditAndStartDialog(row)
}

/**
 * 弹窗: 修改参数 + 启动 (合并"再训练"和"编辑参数")
 * - 预填当前 row 的所有参数
 * - 用户可以微调 (epochs/batch_size/learning_rate/model_name/dataset/base_model)
 * - 保存时直接 POST /start?mode=restart + body (新参数)
 *   原任务记录保持不变, 仅作为新任务的训练参数基底.
 *   这是「再训练」语义: 新建任务, 不修改原任务.
 */
const editStartDialogVisible = ref(false)
const editStartSubmitting = ref(false)
const editStartForm = ref({
  id: 0 as number,
  state: 'PENDING' as string,  // 仅用于弹窗标题文案 (区分 PENDING/终态)
  dataset_id: null as number | null,
  base_model: 'resnet50',
  model_name: '',
  epochs: 20,
  batch_size: 32,
  learning_rate: 0.0001,
})

const openEditAndStartDialog = (row: any) => {
  editStartForm.value = {
    id: row.id,
    state: row.state,
    dataset_id: row.dataset_id,
    base_model: row.base_model,
    model_name: row.model_name,
    epochs: row.epochs,
    batch_size: row.batch_size,
    learning_rate: row.learning_rate,
  }
  editStartDialogVisible.value = true
}

const onEditAndStartSubmit = async () => {
  if (!editStartForm.value.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  if (!editStartForm.value.model_name) {
    ElMessage.warning('请填写模型版本名')
    return
  }
  editStartSubmitting.value = true
  const jobId = editStartForm.value.id
  try {
    // 再训练 (mode=restart): 把新参数作为 body 传给后端
    // - 原 job 记录不会被修改
    // - 后端会合并 payload + 旧 job 字段, 自动加 model_name 时间戳后缀
    // - 返回新 PENDING 任务 (worker 接手后写 DB)
    actionPending.value[jobId] = 'start'
    const r: any = await trainingApi.startJob(jobId, 'restart', {
      dataset_id: editStartForm.value.dataset_id!,
      base_model: editStartForm.value.base_model,
      model_name: editStartForm.value.model_name,
      epochs: editStartForm.value.epochs,
      batch_size: editStartForm.value.batch_size,
      learning_rate: editStartForm.value.learning_rate,
    })
    if (r.success) {
      ElMessage.success(r.message || '已创建新一轮训练任务, 原任务保持不变')
    } else {
      ElMessage.warning(r.message || '启动失败')
    }
    editStartDialogVisible.value = false
    await loadJobs()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    delete actionPending.value[jobId]
    editStartSubmitting.value = false
  }
}

const onRowStart = async (row: any, mode: 'restart' | 'resume' = 'restart') => {
  // PAUSED 必须走 resume, 才会复用原 job (不创建新 job, 不改 model_name)
  // 其他终态走 restart (默认, 创建 _r{timestamp} 新 model_name 的再训练)
  if (mode === 'restart' && !canStart(row.state)) return
  if (mode === 'resume' && row.state !== 'PAUSED') return
  try {
    actionPending.value[row.id] = 'start'
    const r: any = await trainingApi.startJob(row.id, mode)
    if (r.success) {
      ElMessage.success(r.message || (mode === 'resume' ? '已继续训练' : '已启动新一轮训练'))
      await loadJobs()
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
  if (!canPause(row.state)) return
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
      await loadJobs()
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
  if (!canCancel(row.state)) return
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
      await loadJobs()
    } else {
      ElMessage.warning(r.message || '无法取消')
    }
  } catch (e: any) {
    ElMessage.error('取消失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    delete actionPending.value[row.id]
  }
}

const onRowDelete = async (row: any) => {
  if (!canDelete(row.state)) return
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
      // 若删除的正是详情弹窗里展示的任务, 关闭弹窗并释放 SSE/echarts 资源
      if (detailVisible.value && detailJob.value?.id === row.id) {
        cleanupDetail()
        detailJob.value = null
        detailVisible.value = false
      }
      // 当前页可能空了, 向前翻
      if (jobs.value.length === 1 && page.value > 1) page.value -= 1
      await loadJobs()
    } else {
      ElMessage.warning(r.message || '无法删除')
    }
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    delete actionPending.value[row.id]
  }
}

// ============== 详情对话框 (含 SSE 实时进度) ==============
const detailVisible = ref(false)
const detailJob = ref<any>(null)
const detailError = ref<any>(null)
const detailProgress = ref(0)
const detailState = ref('PENDING')
const detailCurrentEpoch = ref<number | null>(null)
const detailTotalEpochs = ref<number | null>(null)
const detailMessage = ref('')
const detailLog = ref<string[]>([])
const detailHistory = ref<EpochData[]>([])
const detailChartEl = ref<HTMLDivElement>()
// 数据集统计 (来自 train.py 的 progress_callback extra)
const detailDataTotal = ref<number | null>(null)
const detailDataTrain = ref<number | null>(null)
const detailDataVal = ref<number | null>(null)
const detailNumClasses = ref<number | null>(null)
const detailClassNames = ref<string[]>([])
// 增量训练状态 (从再训练启动那一刻 SSE 推过来)
const detailPretrainedLoaded = ref<boolean | null>(null)
const detailPretrainedPath = ref<string | null>(null)
const detailPretrainedError = ref<string | null>(null)
let detailChart: echarts.ECharts | null = null
let detailCancelStream: (() => void) | null = null
let detailHistoryTimer: any = null

const openDetail = async (row: any) => {
  // 先关掉旧详情资源
  cleanupDetail()

  detailJob.value = { ...row }
  detailError.value = null
  detailProgress.value = Number(row.progress || 0)
  detailState.value = row.state || 'PENDING'
  detailCurrentEpoch.value = null
  detailTotalEpochs.value = row.epochs
  detailMessage.value = row.message || ''
  detailLog.value = []
  detailHistory.value = []
  // 重置数据集统计
  detailDataTotal.value = null
  detailDataTrain.value = null
  detailDataVal.value = null
  detailNumClasses.value = null
  detailClassNames.value = []
  detailPretrainedLoaded.value = null
  detailPretrainedPath.value = null
  detailPretrainedError.value = null
  detailVisible.value = true

  // 取一次最新详情 (含 error)
  try {
    const d: any = await trainingApi.job(row.id)
    detailJob.value = d
    detailProgress.value = Number(d.progress || 0)
    detailState.value = d.state
    detailTotalEpochs.value = d.epochs
    detailMessage.value = d.message || ''
    if (d.state === 'FAILURE' || d.error) {
      try {
        const e: any = await trainingApi.error(row.id)
        detailError.value = e
      } catch {}
    }
  } catch (e: any) {
    ElMessage.warning('获取任务详情失败: ' + (e?.response?.data?.detail || e?.message))
  }

  // 拉取历史日志 (D3 SSE 持久化, 详情页再次打开可还原完整训练日志)
  try {
    const lg: any = await trainingApi.getLog(row.id)
    const historical: string[] = Array.isArray(lg?.log) ? lg.log : []
    if (historical.length > 0) {
      detailLog.value = [
        `[${new Date().toLocaleTimeString()}] 已加载历史日志 (${historical.length} 行)`,
        ...historical,
      ]
    }
  } catch {
    // 拉取失败不影响主流程
  }

  await nextTick()
  initDetailChart()

  // 仅当任务活跃时连 SSE
  if (row.celery_task_id && (row.state === 'PENDING' || row.state === 'PROGRESS')) {
    startDetailStream(row.celery_task_id)
    detailHistoryTimer = setInterval(() => refreshDetailHistory(row.celery_task_id), 5000)
  } else if (row.celery_task_id) {
    // 终态也拉一次历史曲线
    refreshDetailHistory(row.celery_task_id)
  }
}

const cleanupDetail = () => {
  if (detailCancelStream) { detailCancelStream(); detailCancelStream = null }
  if (detailHistoryTimer) { clearInterval(detailHistoryTimer); detailHistoryTimer = null }
  detailChart?.dispose()
  detailChart = null
}

/**
 * 持久化一行训练日志到后端 (D3 SSE 持久化)
 * - 失败时静默 (log 不是关键路径, 拉流推送不影响训练)
 * - 节流: 500ms 内最多调一次, 避免 SSE 高频推送时打爆后端
 */
let lastLogSaveAt = 0
const saveDetailLog = (taskId: string, line: string) => {
  const jobId = detailJob.value?.id
  if (!jobId) return
  const now = Date.now()
  if (now - lastLogSaveAt < 500) return
  lastLogSaveAt = now
  trainingApi.appendLog(jobId, line).catch(() => {
    // 静默失败: 持久化是 best-effort, 训练过程不受影响
  })
}

const startDetailStream = (taskId: string) => {
  detailLog.value.push(
    `[${new Date().toLocaleTimeString()}] 已连接 SSE 进度推送, task_id=${taskId.slice(0, 8)}…`
  )
  detailCancelStream = trainingApi.streamProgress(taskId, {
    onMessage: (data) => {
      detailState.value = data.state || 'PROGRESS'
      detailProgress.value = Number(data.progress || 0)
      detailCurrentEpoch.value = data.current_epoch ?? null
      detailTotalEpochs.value = data.total_epochs ?? detailTotalEpochs.value
      detailMessage.value = data.message || detailMessage.value
      // 数据集统计 (从 train.py 的 progress_callback extra 推过来, 一次到位)
      if (typeof data.data_total === 'number') detailDataTotal.value = data.data_total
      if (typeof data.data_train === 'number') detailDataTrain.value = data.data_train
      if (typeof data.data_val === 'number') detailDataVal.value = data.data_val
      if (typeof data.num_classes === 'number') detailNumClasses.value = data.num_classes
      if (Array.isArray(data.class_names)) detailClassNames.value = data.class_names
      // 增量训练状态 (再训练启动那一刻推过来)
      if (typeof data.pretrained_loaded === 'boolean') {
        detailPretrainedLoaded.value = data.pretrained_loaded
      }
      if (typeof data.pretrained_path === 'string') {
        detailPretrainedPath.value = data.pretrained_path
      }
      if (typeof data.pretrained_error === 'string') {
        detailPretrainedError.value = data.pretrained_error
      }
      const logLine =
        `[${new Date().toLocaleTimeString()}] state=${data.state} ` +
        `progress=${(Number(data.progress || 0)).toFixed(1)}% ` +
        `epoch=${data.current_epoch ?? '-'}/${data.total_epochs ?? '-'} ` +
        `msg=${data.message || ''}`
      detailLog.value.push(logLine)
      if (detailLog.value.length > 100) detailLog.value = detailLog.value.slice(-100)
      // 同步持久化日志到后端 (D1) — 详情页再次打开可还原
      saveDetailLog(taskId, logLine)
    },
    onComplete: () => {
      detailCancelStream = null
      if (detailHistoryTimer) { clearInterval(detailHistoryTimer); detailHistoryTimer = null }
      // 终态: 刷一次 history + 主列表
      refreshDetailHistory(taskId)
      loadJobs()
    },
    onError: (e) => {
      detailCancelStream = null
      detailLog.value.push(`[${new Date().toLocaleTimeString()}] SSE 断开: ${e.message}`)
    },
  })
}

const refreshDetailHistory = async (taskId: string) => {
  try {
    const h: any = await trainingApi.history(taskId)
    detailHistory.value = h?.history || []
  } catch {}
}

const initDetailChart = () => {
  if (!detailChartEl.value) return
  detailChart = echarts.init(detailChartEl.value)
  detailChart.setOption({
    title: {
      text: '训练曲线 (Loss / Accuracy)',
      left: 'center',
      textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' },
    },
    tooltip: { trigger: 'axis' },
    legend: {
      data: ['train_loss', 'val_loss', 'train_acc', 'val_acc'],
      top: 30,
      textStyle: { color: '#6b7280' },
    },
    grid: { top: 80, left: 50, right: 50, bottom: 40, containLabel: true },
    xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
    yAxis: [
      {
        type: 'value', name: 'Loss', position: 'left',
        axisLine: { lineStyle: { color: '#4f7cff' } },
        splitLine: { lineStyle: { color: '#eef0f4' } },
      },
      {
        type: 'value', name: 'Accuracy', position: 'right', min: 0, max: 1,
        axisLine: { lineStyle: { color: '#00c48c' } },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'train_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true,
        lineStyle: { color: '#4f7cff', width: 2 },
        itemStyle: { color: '#4f7cff' },
        symbolSize: 6,
      },
      {
        name: 'val_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true,
        lineStyle: { color: '#ff8a4c', width: 2 },
        itemStyle: { color: '#ff8a4c' },
        symbolSize: 6,
      },
      {
        name: 'train_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true,
        lineStyle: { color: '#00c48c', width: 2 },
        itemStyle: { color: '#00c48c' },
        symbolSize: 6,
      },
      {
        name: 'val_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true,
        lineStyle: { color: '#722ed1', width: 2 },
        itemStyle: { color: '#722ed1' },
        symbolSize: 6,
      },
    ],
  })
  window.addEventListener('resize', () => detailChart?.resize())
}

watch(detailHistory, (h) => {
  if (!detailChart || h.length === 0) return
  detailChart.setOption({
    xAxis: { data: h.map((x) => x.epoch) },
    series: [
      { data: h.map((x) => +x.train_loss.toFixed(4)) },
      { data: h.map((x) => +x.val_loss.toFixed(4)) },
      { data: h.map((x) => +x.train_acc.toFixed(4)) },
      { data: h.map((x) => +x.val_acc.toFixed(4)) },
    ],
  })
}, { deep: true })

// ============== 工具 ==============
const formatTime = (iso: string | null | undefined): string => {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch { return iso }
}

const stateType = (s: string) => {
  if (s === 'SUCCESS') return 'success'
  if (s === 'FAILURE' || s === 'REVOKED') return 'danger'
  if (s === 'PROGRESS') return 'primary'
  if (s === 'PAUSED') return 'warning'
  return 'info'
}

const stateLabel = (s: string) => {
  const m: Record<string, string> = {
    PENDING: '等待中', PROGRESS: '训练中', SUCCESS: '已完成',
    FAILURE: '失败', REVOKED: '已取消', PAUSED: '已暂停',
  }
  return m[s] || s
}

// ============== 顶部统计 (基于 jobs 聚合) ==============
// 修复: 之前 `const total = total.value || list.length` 在同一行先引用后声明, 触发 TDZ,
// 导致 `total` 永远是 list.length, 顶部"任务总数"只显示当前页条数, 不显示后端 total.
// 这里改名为 totalCount 避免与顶层 ref `total` 遮蔽.
const stats = computed(() => {
  const list = jobs.value
  const totalCount = total.value || list.length
  const cnt: Record<string, number> = { PENDING: 0, PROGRESS: 0, SUCCESS: 0, FAILURE: 0, PAUSED: 0, REVOKED: 0 }
  for (const j of list) cnt[j.state] = (cnt[j.state] || 0) + 1
  return {
    total: totalCount,
    running: cnt.PROGRESS,
    success: cnt.SUCCESS,
    failed: cnt.FAILURE + cnt.REVOKED,
  }
})

const progressStatus = (state: string) => {
  if (state === 'SUCCESS') return 'success'
  if (state === 'FAILURE' || state === 'REVOKED') return 'exception'
  if (state === 'PAUSED') return 'warning'
  return undefined
}

const datasetNameOf = (id: number) => {
  const ds = DATASET_OPTIONS.value.find((x: any) => x.id === id)
  return ds ? ds.name : `#${id}`
}

// ============== 生命周期 ==============
onMounted(async () => {
  await loadDatasets()
  await loadJobs()
  startSilentRefresh()  // 静默兜底刷新
})

onBeforeUnmount(() => {
  cleanupDetail()
  stopSilentRefresh()
  // 清理所有列表级 SSE 订阅
  for (const h of listStreams.values()) {
    try { h.cancel() } catch {}
  }
  listStreams.clear()
})

// ============== 列表级 SSE: 跟踪活跃任务的进度, 替代整页轮询 ==============
// 思路:
// - 列表本身是按页拉的全量数据, 静默期不重拉整页
// - 对当前页里所有 PROGRESS/PENDING 状态的 task 维护一条 SSE 订阅
// - 收到帧时仅原地更新那一行的 (state, progress), 不触发 loadJobs()
// - 任务进入终态 (SUCCESS/FAILURE/REVOKED) 时, 静默拉一次 loadJobs() 以刷新
//   对应的 model_version 联动信息 (准确率/模型版本等后端字段)
type ListStreamHandle = { cancel: () => void; taskId: string; jobId: number }
const listStreams = new Map<number, ListStreamHandle>()  // key = jobId

/**
 * 对当前页里所有 PENDING/PROGRESS 行建立 SSE 订阅.
 * - 同一 jobId 已存在订阅则跳过 (避免重复)
 * - 收到帧时通过 updateJobProgressInPlace 原地更新 rows
 * - 终态时主动断开流, 并触发一次静默 loadJobs() 拉取最新 DB 字段
 */
const syncListStreams = () => {
  const liveJobs = jobs.value.filter(
    (j: any) => (j.state === 'PROGRESS' || j.state === 'PENDING') && j.celery_task_id
  )
  const liveIds = new Set(liveJobs.map((j: any) => j.id))

  // 1) 清理已经不在活跃集合里的订阅 (被删除/翻页/状态变了)
  for (const [jobId, h] of listStreams.entries()) {
    if (!liveIds.has(jobId)) {
      try { h.cancel() } catch {}
      listStreams.delete(jobId)
    }
  }

  // 2) 为新出现的活跃 job 建立订阅
  for (const j of liveJobs) {
    if (listStreams.has(j.id)) continue
    const cancel = trainingApi.streamProgress(j.celery_task_id, {
      onMessage: (data) => {
        updateJobProgressInPlace(j.id, data)
        // 终态: 断开 + 拉一次整页 (拿 model_version 关联等终态字段)
        if (data.state === 'SUCCESS' || data.state === 'FAILURE' || data.state === 'REVOKED') {
          const h = listStreams.get(j.id)
          if (h) {
            try { h.cancel() } catch {}
            listStreams.delete(j.id)
          }
          // 静默拉整页, 状态/进度/模型版本等信息以 DB 为准
          loadJobs()
        }
      },
      onError: () => {
        // 出错时保留订阅, 让后台重连 (EventSource 自动重连); 但防止死循环, 5s 后强切回轮询兜底
        // 这里简单处理: 出错就断开, 走静默轮询
        const h = listStreams.get(j.id)
        if (h) {
          try { h.cancel() } catch {}
          listStreams.delete(j.id)
        }
      },
      onComplete: () => {
        // 服务端主动 end 事件: 同上, 拉一次整页
        const h = listStreams.get(j.id)
        if (h) {
          try { h.cancel() } catch {}
          listStreams.delete(j.id)
        }
        loadJobs()
      },
    })
    listStreams.set(j.id, { cancel, taskId: j.celery_task_id, jobId: j.id })
  }
}

/**
 * 原地更新一行 (jobs.value 中对应 jobId) 的 (state, progress, message)
 * 避免触发 loadJobs() 重拉整页. 仅做轻量合并, 其它字段保持原样.
 */
const updateJobProgressInPlace = (jobId: number, data: any) => {
  const idx = jobs.value.findIndex((j: any) => j.id === jobId)
  if (idx < 0) return
  const cur = jobs.value[idx]
  const next = { ...cur }
  if (data.state) next.state = data.state
  if (typeof data.progress === 'number') next.progress = data.progress
  if (data.message) next.message = data.message
  if (typeof data.current_epoch === 'number') next.current_epoch = data.current_epoch
  if (typeof data.total_epochs === 'number') next.total_epochs = data.total_epochs
  // 替换为响应式新对象, 触发 el-table 重渲染
  jobs.value.splice(idx, 1, next)
}

// 监听列表变化 (loadJobs / 翻页 / 搜索), 自动同步活跃流的订阅集
watch(jobs, () => { syncListStreams() }, { deep: false })

// ============== 静默兜底刷新 ==============
// 之前的实现是 15s 整页拉一次. 改为:
// - 当列表里有活跃任务时, 不做静默刷新 (由 SSE 驱动行内更新)
// - 当列表里没有活跃任务 (全终态) 时, 60s 拉一次兜底 (应对其他用户操作/系统状态变化)
let silentRefreshTimer: any = null
const startSilentRefresh = () => {
  if (silentRefreshTimer) clearInterval(silentRefreshTimer)
  silentRefreshTimer = setInterval(() => {
    if (detailVisible.value) return  // 详情 dialog 打开时, 由详情 SSE 驱动
    const hasActive = jobs.value.some(
      (j: any) => j.state === 'PROGRESS' || j.state === 'PENDING'
    )
    if (!hasActive) {
      loadJobs()
    }
    // 活跃任务存在时: SSE 已经在逐行更新, 无需整页拉
  }, 60000)
}
const stopSilentRefresh = () => {
  if (silentRefreshTimer) { clearInterval(silentRefreshTimer); silentRefreshTimer = null }
}
</script>

<template>
  <div class="training-page">
    <!-- ============== 顶部统计条 ============== -->
    <el-row :gutter="14" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><ListIcon /></el-icon></div>
          <el-statistic title="任务总数" :value="stats.total" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--orange">
          <div class="stat-icon"><el-icon><VideoPlay /></el-icon></div>
          <el-statistic title="训练中" :value="stats.running" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--green">
          <div class="stat-icon"><el-icon><DataLine /></el-icon></div>
          <el-statistic title="已完成" :value="stats.success" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--red">
          <div class="stat-icon"><el-icon><CircleClose /></el-icon></div>
          <el-statistic title="失败/取消" :value="stats.failed" />
        </el-card>
      </el-col>
    </el-row>

    <!-- ============== 顶部操作栏 ============== -->
    <div class="page-header">
      <h2 class="page-title">
        <span>训练任务</span>
        <span class="subtitle">Training</span>
      </h2>
      <div class="header-actions">
        <!-- 状态筛选 -->
        <el-select
          v-model="stateFilter"
          placeholder="状态"
          style="width: 120px;"
          clearable
          @change="onStateFilterChange"
        >
          <el-option
            v-for="o in STATE_OPTIONS.filter((o) => o.value)" :key="o.value"
            :label="o.label" :value="o.value"
          />
        </el-select>
        <!-- 数据集筛选: 独立下拉, 从 DATASET_OPTIONS 取数 -->
        <el-select
          v-model="datasetIdFilter"
          placeholder="数据集"
          style="width: 200px;"
          clearable
          filterable
          @change="onDatasetFilterChange"
        >
          <el-option
            v-for="d in DATASET_OPTIONS" :key="d.id"
            :label="d.name" :value="d.id"
          />
        </el-select>
        <!-- 模型关键词筛选: 模糊匹配 model_name / base_model -->
        <el-input
          v-model="modelKeywordFilter"
          placeholder="模型名 / 基础模型"
          style="width: 200px;"
          clearable
          :prefix-icon="Search"
          @input="onModelKeywordChange"
        />
        <!-- 重置按钮: 仅在有任一筛选时显示 -->
        <el-button
          v-if="stateFilter || datasetIdFilter != null || modelKeywordFilter"
          text
          :icon="Refresh"
          @click="resetFilters"
        >
          重置
        </el-button>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建训练任务</el-button>
        <el-button :icon="Refresh" @click="loadJobs">刷新</el-button>
      </div>
    </div>

    <!-- 筛选状态条: 当有任一筛选生效时, 显示当前命中条件 (便于用户确认筛了啥) -->
    <div v-if="stateFilter || datasetIdFilter != null || modelKeywordFilter" class="filter-chips">
      <span class="chips-label">当前筛选:</span>
      <el-tag v-if="stateFilter" type="info" effect="plain" closable @close="onStateFilterClear">
        状态: {{ STATE_OPTIONS.find((o) => o.value === stateFilter)?.label || stateFilter }}
      </el-tag>
      <el-tag v-if="datasetIdFilter != null" type="info" effect="plain" closable @close="onDatasetFilterClear">
        数据集: {{ datasetNameOf(datasetIdFilter) }}
      </el-tag>
      <el-tag v-if="modelKeywordFilter" type="info" effect="plain" closable @close="onModelKeywordClear">
        关键词: {{ modelKeywordFilter }}
      </el-tag>
      <span class="chips-count">
        共 <strong>{{ total }}</strong> 条命中
      </span>
    </div>

    <!-- ============== 表格工具条: 多选统计 + 批量删除 (仅选中时显示) ============== -->
    <div v-if="selectedJobIds.length > 0" class="batch-toolbar">
      <el-tag type="warning" effect="dark" size="default">
        已选 {{ selectedJobIds.length }} 个任务
      </el-tag>
      <el-button size="small" @click="toggleSelectAllJobs">
        {{ allOnPageSelected ? '取消全选' : '全选当前页' }}
      </el-button>
      <el-button
        type="danger" size="small" :icon="Delete"
        :loading="Object.keys(actionPending).length > 0"
        @click="batchDeleteJobs"
      >批量删除</el-button>
      <el-button size="small" @click="selectedJobIds = []">清空选择</el-button>
    </div>

    <!-- ============== 任务列表 (占满剩余高度, 内部滚动) ============== -->
    <div class="table-wrapper">
      <el-table
        :data="jobs"
        v-loading="loading"
        size="small" border stripe
        height="100%"
        style="width: 100%;"
        empty-text="暂无训练任务"
        @selection-change="onJobSelectionChange"
      >        
        <el-table-column type="index" :index="indexMethod" label="#" width="42" align="center" />
        <el-table-column type="selection" width="40" :reserve-selection="false" />
        <el-table-column label="数据集" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">{{ datasetNameOf(row.dataset_id) }}</template>
        </el-table-column>
        <el-table-column prop="base_model" label="基础模型" min-width="92" show-overflow-tooltip />
        <el-table-column prop="model_name" label="模型版本" min-width="118" show-overflow-tooltip />
        <!-- 训练资源: 实际使用的设备 (GPU/CPU), 后端采集 + 写库 -->
        <el-table-column label="设备" min-width="118" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip
              v-if="row.device_info"
              placement="top"
              :content="formatDeviceTooltip(row.device_info)">
              <el-tag :type="deviceTagType(row.device_type)" size="small">
                {{ deviceShortLabel(row.device_type, row.device_name) }}
              </el-tag>
            </el-tooltip>
            <span v-else style="color: #c0c4cc;">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="epochs" label="轮次" width="80" align="center" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="stateType(row.state)" size="small">{{ stateLabel(row.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="100">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.progress || 0)"
              :status="progressStatus(row.state)"
              :stroke-width="6"
            />
          </template>
        </el-table-column>
        <el-table-column label="开始时间" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right" align="center">
          <template #default="{ row }">
            <div class="row-actions">
              <!-- 合并: 启动/暂停/继续/再训练 同一按钮, 根据状态切换 (统一图标 + tooltip) -->
              <el-tooltip :content="runBtnLabel(row.state)" placement="top" :show-after="200">
                <el-button
                  size="small"
                  :icon="row.state === 'PROGRESS' ? VideoPause : VideoPlay"
                  circle plain
                  :type="runBtnType(row.state)"
                  :disabled="runBtnDisabled(row.state)"
                  :loading="actionPending[row.id] === runBtnAction(row.state)"
                  @click="onRunBtnClick(row)"
                />
              </el-tooltip>
              <el-tooltip content="取消训练" placement="top" :show-after="200">
                <el-button
                  size="small"
                  :icon="CircleClose"
                  circle plain
                  type="danger"
                  :disabled="!canCancel(row.state)"
                  :loading="actionPending[row.id] === 'cancel'"
                  @click="onRowCancel(row)"
                />
              </el-tooltip>
              <el-tooltip content="查看详情" placement="top" :show-after="200">
                <el-button
                  size="small"
                  :icon="View"
                  circle plain
                  type="primary"
                  @click="openDetail(row)"
                />
              </el-tooltip>
              <el-tooltip content="删除任务" placement="top" :show-after="200">
                <el-button
                  size="small"
                  :icon="Delete"
                  circle plain
                  type="danger"
                  :disabled="!canDelete(row.state)"
                  :loading="actionPending[row.id] === 'delete'"
                  @click="onRowDelete(row)"
                />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ============== 分页栏 (固定在页面底部) ============== -->
    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>

    <!-- ============== 新建任务对话框 ============== -->
    <el-dialog
      v-model="createDialogVisible"
      title="新建训练任务"
      width="520px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <TrainingParamsForm
        :form="createForm"
        :datasets="DATASET_OPTIONS"
        :base-models="BASE_MODELS"
        @form-change="(p) => onParamsChange(createForm, p)"
      />
      <el-alert
        title="提示: 训练任务启动后会进入 Celery 队列, 需要 worker 在跑才能真正开始执行"
        type="info" :closable="false" show-icon
        style="margin-top: 16px;"
      />
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button
          type="primary" :loading="createSubmitting"
          @click="onCreateSubmit"
        >提交</el-button>
      </template>
    </el-dialog>

    <!-- ============== 再训练对话框 (修改参数 + 启动) ============== -->
    <el-dialog
      v-model="editStartDialogVisible"
      :title="`再训练  #${editStartForm.id}`"
      width="520px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <el-alert
        type="info" :closable="false" style="margin-bottom: 16px;"
        title="可在此调整训练参数 (数据集/基础模型/版本名/轮次/批大小/学习率)."
      />
      <el-alert
        type="warning" :closable="false" style="margin-bottom: 16px;"
        title="⚠ 原任务 #${editStartForm.id} 的参数不会被修改. 此处填写的参数仅作为「新一轮训练任务」的参数; 新任务的 model_name 会自动加 _r{时间戳} 后缀."
      />
      <TrainingParamsForm
        :form="editStartForm"
        :datasets="DATASET_OPTIONS"
        :base-models="BASE_MODELS"
        @form-change="(p) => onParamsChange(editStartForm, p)"
      />
      <template #footer>
        <el-button @click="editStartDialogVisible = false">取消</el-button>
        <el-button
          type="primary" :loading="editStartSubmitting"
          @click="onEditAndStartSubmit"
        >启动新任务</el-button>
      </template>
    </el-dialog>

    <!-- ============== 详情对话框 (内嵌 SSE 实时进度) ============== -->
    <el-dialog
      v-model="detailVisible"
      :title="`训练任务详情  #${detailJob?.id ?? ''}  (${detailJob ? stateLabel(detailJob.state) : ''})`"
      width="880px"
      destroy-on-close
      @close="cleanupDetail"
    >
      <div v-if="detailJob">
        <!-- 顶部信息条 -->
        <div class="detail-hero">
          <div class="hero-left">
            <div class="hero-mark">
              <el-icon><VideoPlay /></el-icon>
            </div>
            <div>
              <div class="hero-name">{{ detailJob.model_name }}</div>
              <div class="hero-base">{{ detailJob.base_model }} · {{ datasetNameOf(detailJob.dataset_id) }}</div>
            </div>
          </div>
          <el-tag :type="stateType(detailJob.state)" size="small">
            {{ stateLabel(detailJob.state) }}
          </el-tag>
        </div>

        <!-- 基本信息 -->
        <el-descriptions class="detail-descs" :column="3" border size="small">
          <el-descriptions-item label="任务 ID">{{ detailJob.id }}</el-descriptions-item>
          <el-descriptions-item label="数据集">{{ datasetNameOf(detailJob.dataset_id) }}</el-descriptions-item>
          <el-descriptions-item label="基础模型">{{ detailJob.base_model }}</el-descriptions-item>
          <el-descriptions-item label="模型版本">{{ detailJob.model_name }}</el-descriptions-item>
          <el-descriptions-item label="训练设备">
            <el-tag v-if="detailJob.device_type" :type="deviceTagType(detailJob.device_type)" size="small">
              {{ deviceShortLabel(detailJob.device_type, detailJob.device_name) }}
            </el-tag>
            <span v-else style="color: #c0c4cc;">未记录</span>
            <el-tooltip v-if="detailJob.device_info" placement="top" :content="formatDeviceTooltip(detailJob.device_info)">
              <el-icon style="margin-left: 4px; cursor: help;"><InfoFilled /></el-icon>
            </el-tooltip>
          </el-descriptions-item>
          <el-descriptions-item label="轮次">{{ detailJob.epochs }}</el-descriptions-item>
          <el-descriptions-item label="批大小">{{ detailJob.batch_size }}</el-descriptions-item>
          <el-descriptions-item label="学习率">{{ detailJob.learning_rate }}</el-descriptions-item>
          <el-descriptions-item label="耗时(s)">
            {{ detailJob.duration_seconds ? detailJob.duration_seconds.toFixed(1) : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ formatTime(detailJob.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatTime(detailJob.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="Celery task_id" :span="3">
            <code style="word-break: break-all; font-family: var(--font-mono); font-size: 12px;">
              {{ detailJob.celery_task_id || '-' }}
            </code>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 错误信息 (FAILURE 状态) -->
        <div v-if="detailError" style="margin-top: 16px;">
          <el-divider content-position="left">错误详情</el-divider>
          <el-alert
            :title="`DB.error: ${detailError.db_error || '(无)'}`"
            type="error" :closable="false" show-icon
            style="margin-bottom: 8px;"
          />
          <el-alert
            v-if="detailError.redis_error"
            :title="`Redis 兜底: ${JSON.stringify(detailError.redis_error)}`"
            type="warning" :closable="false" show-icon
          />
        </div>

        <!-- 数据集统计 (SSE 启动时推过来, 实时显示) -->
        <el-divider content-position="left">数据集统计</el-divider>
        <el-row :gutter="12" style="margin-bottom: 8px;">
          <el-col :span="6">
            <div class="mini-stat mini-stat--blue">
              <div class="mini-label">总样本数</div>
              <div class="mini-value">{{ detailDataTotal ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--green">
              <div class="mini-label">训练集</div>
              <div class="mini-value">{{ detailDataTrain ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--orange">
              <div class="mini-label">验证集</div>
              <div class="mini-value">{{ detailDataVal ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--red">
              <div class="mini-label">类别数</div>
              <div class="mini-value">{{ detailNumClasses ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">类</span></div>
            </div>
          </el-col>
        </el-row>
        <div
          v-if="detailClassNames.length > 0"
          style="margin-bottom: 8px; color: #606266; font-size: 13px;"
        >
          类别: <el-tag
            v-for="cn in detailClassNames" :key="cn"
            size="small" type="info" effect="plain" style="margin-right: 4px;"
          >{{ cn }}</el-tag>
        </div>
        <div
          v-else
          style="margin-bottom: 8px; color: #C0C4CC; font-size: 13px;"
        >
          等待 worker 启动并加载数据集后显示...
        </div>

        <!-- 增量训练状态 (再训练时, 启动那一刻 SSE 推过来) -->
        <el-alert
          v-if="detailPretrainedLoaded === true"
          type="success" :closable="false" show-icon
          :title="`✓ 增量训练: 已加载模型权重 ${detailPretrainedPath || ''}`"
          style="margin-bottom: 8px;"
        />
        <el-alert
          v-else-if="detailPretrainedLoaded === false"
          type="warning" :closable="false" show-icon
          :title="`⚠ 增量训练: 未加载历史权重 (${detailPretrainedError || '原因未知'}), 改为随机初始化 / ImageNet 预训练`"
          style="margin-bottom: 8px;"
        />

        <!-- 实时进度区 -->
        <el-divider content-position="left">训练进度</el-divider>
        <el-row :gutter="12" style="margin-bottom: 8px;">
          <el-col :span="6">
            <div class="mini-stat mini-stat--blue">
              <div class="mini-label">进度</div>
              <div class="mini-value">{{ detailProgress.toFixed(1) }}<span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">%</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--green">
              <div class="mini-label">当前 epoch</div>
              <div class="mini-value">{{ detailCurrentEpoch || 0 }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--orange">
              <div class="mini-label">总轮次</div>
              <div class="mini-value">{{ detailTotalEpochs || 0 }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--red">
              <div class="mini-label">已记录 epoch</div>
              <div class="mini-value">{{ detailHistory.length }}</div>
            </div>
          </el-col>
        </el-row>
        <el-progress
          :percentage="detailProgress"
          :status="progressStatus(detailState)"
          :stroke-width="14"
        />
        <div style="margin-top: 6px; color: #909399; font-size: 13px;">
          {{ detailMessage || '等待 worker 启动...' }}
        </div>
        <div ref="detailChartEl" class="training-chart-box"></div>
        <pre class="log-box">{{ detailLog.join('\n') }}</pre>
      </div>

      <template #footer>
        <el-button
          v-if="detailJob && (detailJob.state === 'PROGRESS' || detailJob.state === 'PENDING' || detailJob.state === 'PAUSED' || canStart(detailJob.state))"
          :type="runBtnType(detailJob.state)"
          :loading="actionPending[detailJob.id] === runBtnAction(detailJob.state)"
          @click="onRunBtnClick(detailJob); loadJobs()"
        >{{ runBtnLabel(detailJob.state) }}</el-button>
        <el-button
          v-if="detailJob && canCancel(detailJob.state)"
          type="danger"
          :loading="actionPending[detailJob.id] === 'cancel'"
          @click="onRowCancel(detailJob)"
        >取消</el-button>
        <el-button
          v-if="detailJob && canDelete(detailJob.state)"
          type="danger" link
          :loading="actionPending[detailJob.id] === 'delete'"
          @click="onRowDelete(detailJob)"
        >删除</el-button>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.training-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100%;
  /* 让页面在 el-main 内部撑满, 表格区占满剩余高度, 分页栏钉在底部 */
  min-height: 0;
}

/* ============== 顶部统计条 ============== */
.stats-row { margin-bottom: 16px; flex-shrink: 0; }

/* ============== 批量操作工具条 (选中行时出现) ============== */
.batch-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  margin-bottom: 8px;
  background: linear-gradient(90deg, rgba(255, 169, 64, 0.08) 0%, rgba(255, 169, 64, 0.02) 100%);
  border: 1px solid rgba(255, 169, 64, 0.25);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

/* ============== 表格行操作按钮组 ============== */
.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 16px;
  white-space: nowrap;
  justify-content: center;
}
.row-actions .el-button {
  margin: 0;
  padding: 4px;
  min-height: auto;
}
/* 行内 cell 紧凑: el-table 默认 cell-padding 12px 0, 适当压缩 */
:deep(.el-table .el-table__cell) {
  padding: 4px 0 !important;
}
:deep(.el-table--small .el-table__cell) {
  padding: 3px 0 !important;
}
:deep(.el-table .el-table__cell .cell) {
  padding: 0 6px;
  word-break: break-word;
}
/* 列头居中视觉对齐 */
:deep(.el-table th.el-table__cell) > .cell {
  font-weight: 600;
  color: var(--text-primary);
}
:deep(.el-table--small th.el-table__cell) > .cell {
  padding: 0 6px;
  font-size: 12.5px;
}
.duration-text {
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
  font-size: 12.5px;
}
.stat-card {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg) !important;
  background: #fff !important;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
}
.stat-card--blue::before   { background: var(--gradient-brand); }
.stat-card--green::before  { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--red::before    { background: linear-gradient(135deg, #ff4d4f 0%, #cf1322 100%); }

.stat-card :deep(.el-card__body) {
  padding: 20px 22px;
  position: relative;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 26px;
  font-weight: 600;
  color: var(--text-primary);
}
.stat-icon {
  position: absolute;
  right: 18px;
  top: 18px;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon :deep(.el-icon) { font-size: 20px; }
.stat-card--blue   .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green  .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--red    .stat-icon { background: rgba(255, 77, 79, 0.1); color: #ff4d4f; }

/* ============== 顶部操作栏 ============== */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}
.page-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}
.page-title .subtitle {
  color: var(--text-placeholder);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.header-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }

/* ============== 筛选 chip 状态条 ============== */
.filter-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.04) 0%, rgba(110, 81, 233, 0.04) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}
.chips-label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  margin-right: 4px;
}
.filter-chips :deep(.el-tag) {
  margin: 0;
  font-size: 12px;
  border-radius: var(--radius-sm);
}
.filter-chips :deep(.el-tag .el-tag__close) {
  background-color: transparent !important;
  color: var(--text-secondary);
}
.filter-chips :deep(.el-tag .el-tag__close:hover) {
  color: var(--brand-primary) !important;
  background-color: transparent !important;
}
.chips-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-secondary);
}
.chips-count strong {
  color: var(--brand-primary);
  font-weight: 600;
  font-size: 14px;
  margin: 0 2px;
}

/* 表格区: 占据所有剩余高度, 内部滚动, 不挤压分页栏 */
.table-wrapper {
  flex: 1 1 0;
  min-height: 0;
  overflow: auto;  /* 内容多时表格内部滚动 */
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: #fff;
}
.table-wrapper .el-table {
  /* el-table 自身是 display: table, 不接受 flex:1; 用 height: 100% 占满父容器 */
  height: 100% !important;
  width: 100% !important;
}
/* Element Plus el-table 在 flex 容器中, 默认会自己处理 body 滚动, 不要
   给 __inner-wrapper 强加 overflow:auto, 否则 fixed-right 列会盖住内容列. */

/* 分页栏: 固定在页面底部, 不会被表格滚动条挡住 */
.pager {
  flex-shrink: 0;
  margin-top: 12px;
  padding: 8px 0;
  display: flex;
  justify-content: flex-end;
  background: #fff;
  border-top: 1px solid var(--border-soft);
  /* sticky 兜底: 即使外层 el-main 自身滚动 (内容比视口高), 分页栏也粘在视口底部 */
  position: sticky;
  bottom: 0;
  z-index: 5;
}

/* 训练图表与日志 */
.training-chart-box { width: 100%; height: 300px; margin-top: 12px; }
.log-box {
  background: #0a0a0a;
  color: #0f0;
  padding: 12px;
  max-height: 200px;
  overflow: auto;
  margin-top: 12px;
  font-size: 12px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid #222;
}

/* 详情对话框: 顶部信息区与指标块 */
.detail-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.06) 0%, rgba(110, 81, 233, 0.06) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  margin-bottom: 12px;
}
.hero-left { display: flex; align-items: center; gap: 12px; }
.hero-mark {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  box-shadow: 0 4px 12px rgba(79, 124, 255, 0.3);
}
.hero-name { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.hero-base { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

/* 详情内的小型指标块 (用于 4 联指标和数据集统计) */
.mini-stat {
  background: var(--bg-soft);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  border: 1px solid var(--border-soft);
  text-align: center;
  height: 100%;
}
.mini-stat .mini-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.mini-stat .mini-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.mini-stat--blue   .mini-value { color: #4f7cff; }
.mini-stat--green  .mini-value { color: #00c48c; }
.mini-stat--orange .mini-value { color: #ff8a4c; }
.mini-stat--red    .mini-value { color: #ff4d4f; }

/* 描述列表: 顶部空隙收紧 */
.detail-descs :deep(.el-descriptions__label) {
  color: var(--text-secondary) !important;
  font-weight: 500;
  background: var(--bg-soft) !important;
}

/* 状态 tag 颜色: 与统计卡色调保持一致 */
:deep(.el-tag.el-tag--success) { background: var(--gradient-success) !important; border-color: transparent !important; }
:deep(.el-tag.el-tag--danger)  { background: linear-gradient(135deg, #ff4d4f 0%, #cf1322 100%) !important; border-color: transparent !important; }
:deep(.el-tag.el-tag--warning) { background: var(--gradient-warn) !important; border-color: transparent !important; color: #fff !important; }
:deep(.el-tag.el-tag--primary) { background: var(--gradient-brand) !important; border-color: transparent !important; }
</style>
