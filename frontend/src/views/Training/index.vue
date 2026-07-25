<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  VideoPlay, VideoPause, CircleClose, View, Delete, Refresh, Plus,
  List as ListIcon, DataLine, Search, InfoFilled
} from '@element-plus/icons-vue'
import { trainingApi, datasetApi, autoAnnotateApi } from '@/api'
import { getDefaultBaseModel, getTaskTypeMeta, TASK_TYPE_OPTIONS } from '@/utils/taskType'
import * as echarts from 'echarts'
// v2.5.8 架构优化: 业务组件全部迁入当前页面私有目录
import TrainingParamsForm, { type TrainingParams } from './components/TrainingParamsForm.vue'
import StateBadge from './components/StateBadge.vue'
// v3.0.0 Phase G: 列表级 SSE 抽离到 composable, page 只剩业务编排
import { useTrainingListSSE } from '@/composables/useTrainingListSSE'
// v3.0.0 Phase G: 静默兜底刷新也抽离为 composable
import { useSilentRefresh } from '@/composables/useSilentRefresh'

interface EpochData {
  epoch: number
  train_loss: number
  val_loss: number
  train_acc: number
  val_acc: number
  // v2.2.0 S9.4: 检测训练专属字段 (classification 任务为空)
  box_loss?: number
  cls_loss?: number
  dfl_loss?: number
  map_50?: number
  map_50_95?: number
  precision?: number
  recall?: number
  // v2.5.0 S12.4: 分割训练专属字段 (segmentation 任务专属)
  miou?: number
  pixel_acc?: number
  dice?: number
}

// ============== 基础选项 ==============
// v2.5.47: 基础模型列表改为从后端 /api/auto-annotate/models 动态加载
// - 单一权威源: 后端 list_available_models (auto_annotate.py:217)
// - 训练页 + 标注工作台工具栏共用同一份数据
// - 不再有静态 BASE_MODELS / BASE_MODELS_BY_TASK, 避免前后端不一致
// - 加载失败时回退到 utils/taskType.ts 的 DEFAULT_BASE_MODEL (避免 UI 空白)
// - 数据形状复用 BaseModelSelect 导出的 BaseModelOption (单一 schema, 与后端 /models 对齐)
import { DEFAULT_BASE_MODEL } from '@/utils/taskType'
import type { BaseModelOption } from './components/BaseModelSelect.vue'

/** 全量基础模型 (从后端拉) */
const baseModelsAll = ref<BaseModelOption[]>([])
/** 加载状态, 用于骨架屏 / placeholder */
const baseModelsLoading = ref(false)
/** 按 task_type 过滤后的 base models (供训练下拉用) */
const baseModelsByTask = computed<Record<string, BaseModelOption[]>>(() => {
  const grouped: Record<string, BaseModelOption[]> = {
    classification: [],
    detection: [],
    segmentation: [],
  }
  for (const m of baseModelsAll.value) {
    if (m.task_type && grouped[m.task_type]) grouped[m.task_type].push(m)
  }
  return grouped
})
/** 当前 createFormTaskType 对应的候选列表 (空数组兜底) */
const createFormBaseOptions = computed<BaseModelOption[]>(
  () => baseModelsByTask.value[createFormTaskType.value] || []
)
/** 异步加载基础模型清单 */
async function loadBaseModels() {
  baseModelsLoading.value = true
  try {
    const r: any = await autoAnnotateApi.models()
    const items: any[] = r?.models || r || []
    baseModelsAll.value = items
  } catch (e) {
    console.warn('[Training] 加载基础模型列表失败, 回退到默认模型', e)
    baseModelsAll.value = []
  } finally {
    baseModelsLoading.value = false
  }
}

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
// v2.5.24: 任务类型筛选 ('' = 全部, 与后端 task_type 字段对齐)
// - 固定排序: 图片分类 / 目标检测 / 图片分割 (复用 utils/taskType.ts 的 TASK_TYPE_OPTIONS)
// - 留空时不过滤, 由后端 WHERE 跳过该条件
const taskTypeFilter = ref<string>('')
// 防抖: 关键词输入用 setTimeout 静默刷新, 避免每按一个字母就发一次请求
let keywordDebounceTimer: any = null

/** 表格行 class: 占位行加 .row--placeholder (虚化 + 斜体) */
const rowClassName = ({ row }: { row: any }) => {
  return row?.is_placeholder ? 'row--placeholder' : ''
}

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
    // v2.5.24: 任务类型筛选, 留空 = 全部, 后端 WHERE 跳过该条件
    if (taskTypeFilter.value) params.task_type = taskTypeFilter.value
    const r: any = await trainingApi.jobs(params)
    const items: any[] = r?.items || []
    total.value = r?.total ?? 0

    // ---- 占位保留: 把占位行 (celery_task_id 在新列表里找不到的) 保留在顶部 ----
    // 解决"刷新后占位被抹掉"的问题:
    // 1. 后端已预创建 TrainingJob 行 (state=PENDING), 正常情况下 loadJobs 立即能查到
    // 2. 但若 worker 刚启动且数据库连接慢 / 同步延迟, 仍可能短暂查不到
    // 3. 此时如果用户点了刷新, 旧的 jobs.value = items 会把占位行清掉, 用户感觉
    //    "刚提交的任务消失了". 修复: 在新 items 里找不到对应 celery_task_id 的
    //    占位行, 把它们 prepend 到顶部, 不被覆盖.
    const placeholders = jobs.value.filter((j: any) => j.is_placeholder)
    if (placeholders.length > 0) {
      const stillMissing: any[] = []
      for (const p of placeholders) {
        const hit = items.some(
          (it: any) => it.celery_task_id === p.celery_task_id
        )
        if (!hit) stillMissing.push(p)
      }
      if (stillMissing.length > 0) {
        // ---- 幽灵占位检测 ----
        // 真实记录已经覆盖了新任务, 把仍缺失的占位 prepend 到顶部
        // 之前 total 累加存在 bug: 反复刷新时, 幽灵占位 (后端实际未写入)
        // 会让 total 持续 +1, 导致 total 与真实数量长期不一致.
        // 修复: 把"占位"也算进 total 一次 (不是每次都 +), 用户翻页/筛选时按 total 走
        // 即可; 同时给占位加个超时, 超过 5 分钟且 celery_task_id 仍查不到, 主动丢弃
        // 并提示, 避免占位永远占着第一行.
        const now = Date.now()
        const GHOST_TIMEOUT_MS = 5 * 60 * 1000
        const fresh: any[] = []
        const ghosts: any[] = []
        for (const p of stillMissing) {
          const age = now - (p._placeholder_at || now)
          if (age > GHOST_TIMEOUT_MS) ghosts.push(p)
          else fresh.push(p)
        }
        if (ghosts.length > 0) {
          // 幽灵占位: 后端查不到, worker 可能未接管, 主动清理
          console.warn('[loadJobs] 丢弃幽灵占位 (后端查不到对应 celery_task_id):',
            ghosts.map((g) => g.celery_task_id))
          ElMessage.warning(`${ghosts.length} 个任务长时间未确认, 已自动清理 (后端可能未写入)`)
        }
        jobs.value = [...fresh, ...items]
        // total: 用后端真实 total + 仍有效的占位数量 (不要累加, 累加会越加越大)
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

const onPageChange = (p: number) => { page.value = p; loadJobs() }
const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1; loadJobs() }
const onStateFilterChange = () => { page.value = 1; loadJobs() }
const onDatasetFilterChange = () => { page.value = 1; loadJobs() }
/** v2.5.24: 任务类型筛选变更 — 翻到第 1 页并刷新 */
const onTaskTypeFilterChange = () => { page.value = 1; loadJobs() }
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
  taskTypeFilter.value = ''
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
const onTaskTypeFilterClear = () => {
  taskTypeFilter.value = ''
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

/**
 * 智能格式化耗时 (秒 → 人类可读)
 *  < 60s   → "42.3s"
 *  < 3600s → "5m 23s"
 *  >= 3600s → "1h 12m"
 * 详细页用这个替代纯数字, 大训练 (1h+) 一眼能看懂
 */
const formatDurationSmart = (s: number): string => {
  if (!s || s < 0) return '-'
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  if (m < 60) {
    const rem = Math.floor(s % 60)
    return `${m}m ${rem}s`
  }
  const h = Math.floor(m / 60)
  const remM = m % 60
  return `${h}h ${remM}m`
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

// ============== S7 新增: 新建任务对话框的"任务类型"下拉选项 ==============
// S7 新增: 当前选中数据集的 task_type, 用于按任务类型切换默认 base_model
const createFormTaskType = ref<string>('classification')
// - 固定排序: 图片分类 / 目标检测 / 图片分割
// - 与 Annotate 工作台的 TASK_TYPE_FILTER_OPTIONS 保持一致
const CREATE_TASK_TYPE_OPTIONS = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
] as const

/**
 * v2.5.47: 根据当前 task_type 重置 createForm.base_model 和 model_name
 * - 优先使用 createFormBaseOptions 第一项 (后端最新数据)
 * - 加载失败 / 该任务类型无候选时, 回退到 utils/taskType.ts DEFAULT_BASE_MODEL
 *   (避免 UI 出现"undefined base_model"导致启动按钮 disabled)
 */
const resetCreateFormByTaskType = (taskType: string) => {
  const opts = baseModelsByTask.value[taskType] || []
  const newBase = opts[0]?.name || getDefaultBaseModel(taskType)
  createForm.value.base_model = newBase
  createForm.value.model_name = genDefaultModelName(newBase)
}

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
  createFormTaskType.value = 'classification'
  createDialogVisible.value = true
}

/**
 * S7 改造: 任务类型下拉 — 用户主动选择, 不再由 dataset 反向推断
 * 流程对齐图片分类/目标检测/图片分割的固定排序 (与 Annotate 工作台一致)
 * - 切换 task_type 时:
 *   · 重置 base_model + model_name (走任务类型默认模型)
 *   · 若当前 dataset_id 不在新筛选范围, 清空 (UI 会强制重新选)
 * - 选 dataset 时: 不再反向改 task_type (用户已主动选, 不要覆盖)
 */
const onCreateTaskTypeChange = (t: string) => {
  createFormTaskType.value = t
  resetCreateFormByTaskType(t)
  // 当前 dataset_id 可能不在新筛选范围, 清空强制重选
  const cur = createForm.value.dataset_id
  if (cur) {
    const stillValid = DATASET_OPTIONS.value.find(
      (d: any) => d.id === cur && (d.task_type || 'classification') === t
    )
    if (!stillValid) {
      // createForm 是 ref, 需要解包成 TrainingParams 才能传给 onParamsChange
      onParamsChange(createForm.value, { dataset_id: null })
    }
  }
}

/**
 * v2.5.24: 按当前 task_type 过滤后的数据集列表 (供 TrainingParamsForm 用)
 * - 'all' 或空值: 全部展示 (兜底, 实际 createFormTaskType 必填)
 * - 'classification'/'detection'/'segmentation': 仅展示同 task_type
 * - 与父组件 (Annotate) 的逻辑保持一致
 */
const filteredDatasets = computed(() => {
  const t = createFormTaskType.value || 'classification'
  return DATASET_OPTIONS.value.filter(
    (d: any) => (d.task_type || 'classification') === t
  )
})

/**
 * 列表筛选行的"数据集"下拉: 按 taskTypeFilter 联动
 * - 留空: 全部展示
 * - 设了 task_type: 仅展示同 task_type 的数据集
 * 用途: 用户先选了任务类型 (如 检测), 数据集下拉就只能选检测类数据集,
 *       避免筛出 0 命中组合 (如 task_type=detection + dataset=分类数据集)
 */
const filterableDatasetsForFilter = computed(() => {
  if (!taskTypeFilter.value) return DATASET_OPTIONS.value
  return DATASET_OPTIONS.value.filter(
    (d: any) => (d.task_type || 'classification') === taskTypeFilter.value
  )
})

/**
 * 训练任务入队的统一占位逻辑 (新建/再训练 共用)
 *
 * 后端已预创建 TrainingJob 行 (PENDING), API 返回 task_id + job_id. 这里
 * 立即在 jobs.value 顶部插入一条占位行, 给用户视觉反馈, 避免提交到
 * loadJobs 返回之间的 RTT (200-500ms) 期间列表为空. 后续 loadJobs() 拉到
 * 真实数据时, 占位行的 celery_task_id 与真实行匹配, 被自动剔除.
 *
 * @param newTaskId 后端返回的 Celery task_id
 * @param params    预填字段, 用于构造占位行 (model_name/base_model/...)
 * @returns         占位行对象, 已 prepend 到 jobs.value
 */
const insertPlaceholderJob = (newTaskId: string, params: {
  dataset_id: number
  base_model: string
  model_name: string
  epochs: number
  batch_size: number
  learning_rate: number
}) => {
  const placeholder = {
    id: -Date.now(),  // 负数 id, 不会与真实 id 冲突
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
    created_at: new Date().toISOString(),  // 占位行的"创建日期"用当前时刻
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    is_placeholder: true,
    _placeholder_at: Date.now(),  // 占位时间戳, 用于 loadJobs 中判断幽灵占位超时
  }
  jobs.value = [placeholder, ...jobs.value]
  total.value = (total.value || 0) + 1
  page.value = 1
  return placeholder
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
    const newJobId: number | undefined = r.job_id
    ElMessage.success(`训练任务已提交 (job=#${newJobId ?? '?'})`)
    createDialogVisible.value = false

    // 立即插入占位行 + 调用 loadJobs()
    // - 后端已预创建 TrainingJob 行 (state=PENDING), loadJobs() 立即能查到
    // - 占位仅作为提交到 loadJobs 返回之间 (约 200-500ms) 的瞬时视觉填充
    // - loadJobs 内部有占位保留逻辑, 真实数据会无缝替换占位
    if (newTaskId) {
      insertPlaceholderJob(newTaskId, {
        dataset_id: createForm.value.dataset_id!,
        base_model: createForm.value.base_model,
        model_name: createForm.value.model_name,
        epochs: createForm.value.epochs,
        batch_size: createForm.value.batch_size,
        learning_rate: createForm.value.learning_rate,
      })
    }
    await loadJobs()
  } catch (e: any) {
    ElMessage.error('启动失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    createSubmitting.value = false
  }
}

// 注意: 之前有 waitForJobInList 后台轮询函数, 已删除.
// 原因: 后端 start_training 已预创建 TrainingJob 行 (state=PENDING), 提交后
// 立即调用 loadJobs() 就能查到新任务, 不需要再单独轮询. 占位行仅作为
// 提交到 loadJobs 返回之间 (约 200-500ms) 的瞬时视觉填充, 由 loadJobs
// 的占位保留逻辑兜底 (见 loadJobs 中关于 placeholders 的合并代码).

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
/** v2.5.47: 编辑重提对话框的 task_type — 由原 job 推断, 用于过滤 base_models 候选 */
const editStartTaskType = ref<string>('classification')
/** v2.5.47: 编辑重提 base_models 候选 — 按 task_type 过滤, 找不到时回退到 classification */
const editStartBaseOptions = computed<BaseModelOption[]>(
  () => baseModelsByTask.value[editStartTaskType.value]
      || baseModelsByTask.value.classification
      || []
)

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
  // v2.5.47: 由原 job 推断 task_type, 让 base_model 下拉只显示该类型可选项
  // - row.task_type 来自后端 /training/jobs/{id} 接口 (TrainingJob.task_type 字段)
  // - 推断失败时回退 classification, 避免下拉空白
  editStartTaskType.value = (row.task_type as string) || 'classification'
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
      const newJobId: number | undefined = r.new_job_id
      const newTaskId: string | undefined = r.task_id
      ElMessage.success(r.message || `已创建新一轮训练任务 #${newJobId}, 原任务 #${jobId} 保持不变`)

      // 立即插入占位行, 让用户看到"再训练已生效"
      // - 后端已预创建新 TrainingJob 行 (state=PENDING), new_job_id 即新行 id
      // - model_name 会在 worker 端 INSERT 时再加 _r{timestamp} 后缀, 但本占位
      //   行用用户输入的 model_name 占位即可, loadJobs() 拉到真实行时替换
      if (newTaskId) {
        insertPlaceholderJob(newTaskId, {
          dataset_id: editStartForm.value.dataset_id!,
          base_model: editStartForm.value.base_model,
          model_name: editStartForm.value.model_name,
          epochs: editStartForm.value.epochs,
          batch_size: editStartForm.value.batch_size,
          learning_rate: editStartForm.value.learning_rate,
        })
      }
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

      // mode=restart: 后端预创建新 TrainingJob 行, 返回 new_job_id + task_id
      // 立即插入占位行让用户看到"再训练已生效"
      // mode=resume: 复用旧行, 不需要占位, 直接 reload 即可
      if (mode === 'restart' && r.task_id) {
        insertPlaceholderJob(r.task_id, {
          dataset_id: row.dataset_id,
          base_model: row.base_model,
          model_name: row.model_name,  // 后端会自动追加 _r{timestamp} 后缀
          epochs: row.epochs,
          batch_size: row.batch_size,
          learning_rate: row.learning_rate,
        })
      }
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

// ---- 数据集统计双源 fallback (SSE 实时 ref + DB 持久化) ----
// 之前: 模板直接绑 detailDataTotal.value, SSE 推之前永远是 null/0
// 现在: SSE 推送的 ref 优先 (最新); ref 为 null 时回退到 detailJob.data_* (DB)
const displayDataTotal = computed(() =>
  detailDataTotal.value ?? detailJob.value?.data_total ?? null
)
const displayDataTrain = computed(() =>
  detailDataTrain.value ?? detailJob.value?.data_train ?? null
)
const displayDataVal = computed(() =>
  detailDataVal.value ?? detailJob.value?.data_val ?? null
)
const displayNumClasses = computed(() =>
  detailNumClasses.value ?? detailJob.value?.num_classes ?? null
)
// class_names: SSE 推的是数组, DB 的也是数组, 选非空的那个
const displayClassNames = computed(() => {
  if (detailClassNames.value.length > 0) return detailClassNames.value
  if (Array.isArray(detailJob.value?.class_names) && detailJob.value.class_names.length > 0) {
    return detailJob.value.class_names
  }
  return []
})

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

    // ---- 从 DB 回填数据集统计 (持久化字段) ----
    // 之前: 详情打开时直接 reset 为 null, 仅靠 SSE 推送填充
    //   - 完成的任务 (SUCCESS): 不连 SSE, 永远是 0
    //   - 中途刷新页面: SSE 重连前也是 0
    // 现在: 从 /jobs/{id} 返回的 d.data_* 回填, 兼容已完成的旧任务 + 中途刷新
    if (typeof d.data_total === 'number') detailDataTotal.value = d.data_total
    if (typeof d.data_train === 'number') detailDataTrain.value = d.data_train
    if (typeof d.data_val === 'number') detailDataVal.value = d.data_val
    if (typeof d.num_classes === 'number') detailNumClasses.value = d.num_classes
    if (Array.isArray(d.class_names)) detailClassNames.value = d.class_names
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
      // 同步 detailState 和 detailJob.state (修复「下方完成, 上方还显示训练中」的 bug)
      // - 之前: 只更新 detailState.value, 模板用 detailJob.state, 导致 StateBadge 卡在 PROGRESS
      // - 现在: 同步两者, 终态时还会重新拉 DB 拿 finished_at / duration / device_info
      const newState = data.state || 'PROGRESS'
      detailState.value = newState
      if (detailJob.value) detailJob.value.state = newState
      detailProgress.value = Number(data.progress || 0)
      detailCurrentEpoch.value = data.current_epoch ?? null
      detailTotalEpochs.value = data.total_epochs ?? detailTotalEpochs.value
      detailMessage.value = data.message || detailMessage.value
      // ---- v2.5.28: 实时同步 started_at / finished_at (SSE 推过来) ----
      // 之前: 只在 openDetail 时从 DB 拉一次, worker 接手后再也没更新过,
      //       详情页「开始时间」一直显示 "-" 直到终态 onComplete 才被 /jobs/{id} 刷新.
      // 现在: SSE 每次去重签名变化时都会带 started_at/finished_at, 拿到就直接同步.
      // - 后端 datetime → JSON ISO 字符串 → 这里用 new Date() 还原
      // - 仅在 SSE 给值时才覆盖, 避免把已有的真实值覆盖成 null
      if (data.started_at && detailJob.value) {
        const _sa = String(data.started_at)
        if (_sa && _sa !== detailJob.value.started_at) {
          detailJob.value.started_at = _sa
        }
      }
      if (data.finished_at && detailJob.value) {
        const _fa = String(data.finished_at)
        if (_fa && _fa !== detailJob.value.finished_at) {
          detailJob.value.finished_at = _fa
        }
      }
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

      // ---- 终态检测: 重新拉完整 job 拿 finished_at / duration / device_info ----
      // 之前: 终态时 detailJob 仍停留在 openDetail 时的初始值, 结束时间/耗时/设备都是空
      // 现在: SSE 收到 SUCCESS/FAILURE/REVOKED 时, 主动调一次 /jobs/{id} 拉 DB 最终状态
      const isTerminal = ['SUCCESS', 'FAILURE', 'REVOKED'].includes(newState)
      if (isTerminal && detailJob.value?.id) {
        // 用 fire-and-forget 模式, 不阻塞 SSE 主流程
        trainingApi.job(detailJob.value.id).then((d: any) => {
          detailJob.value = d
        }).catch(() => {
          // 拉取失败不影响其他字段, 保留 SSE 已推的 state
        })
      }
    },
    onComplete: () => {
      detailCancelStream = null
      if (detailHistoryTimer) { clearInterval(detailHistoryTimer); detailHistoryTimer = null }
      // 终态: 刷一次 history + 主列表 + 详情 (确保 finished_at / duration / device_info 最终值)
      // v2.5.28: SSE onComplete 是流关闭瞬间, 此刻 worker 已写库, /jobs/{id} 拿到的就是终态
      refreshDetailHistory(taskId)
      loadJobs()
      if (detailJob.value?.id) {
        trainingApi.job(detailJob.value.id).then((d: any) => {
          detailJob.value = d
        }).catch(() => {
          // 拉取失败不影响其他字段, 保留 SSE 已推的 state
        })
      }
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
  // v2.2.0 S9.4 + v2.5.0 S12.4: 根据 task_type 切换图表系列
  // classification: loss/accuracy 双轴
  // detection:     box_loss/cls_loss 左轴 + mAP50/precision/recall 右轴
  // segmentation:  train_loss/val_loss 左轴 + mIoU/pixel_acc/dice 右轴
  const isDetection = detailJob.value?.task_type === 'detection'
  const isSegmentation = detailJob.value?.task_type === 'segmentation'
  if (isDetection) {
    detailChart.setOption({
      title: {
        text: '检测训练曲线 (Loss / mAP / P / R)',
        left: 'center',
        textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' },
      },
      tooltip: { trigger: 'axis' },
      legend: {
        data: ['box_loss', 'cls_loss', 'mAP50', 'mAP50-95', 'precision', 'recall'],
        top: 30, type: 'scroll', textStyle: { color: '#6b7280' },
      },
      grid: { top: 80, left: 55, right: 55, bottom: 40, containLabel: true },
      xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
      yAxis: [
        { type: 'value', name: 'Loss', position: 'left', axisLine: { lineStyle: { color: '#4f7cff' } }, splitLine: { lineStyle: { color: '#eef0f4' } } },
        { type: 'value', name: 'mAP / P / R', position: 'right', min: 0, max: 1, axisLine: { lineStyle: { color: '#00c48c' } }, splitLine: { show: false } },
      ],
      series: [
        { name: 'box_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#4f7cff', width: 2 }, itemStyle: { color: '#4f7cff' }, symbolSize: 6 },
        { name: 'cls_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#ff8a4c', width: 2 }, itemStyle: { color: '#ff8a4c' }, symbolSize: 6 },
        { name: 'mAP50', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#00c48c', width: 2 }, itemStyle: { color: '#00c48c' }, symbolSize: 6 },
        { name: 'mAP50-95', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#722ed1', width: 2 }, itemStyle: { color: '#722ed1' }, symbolSize: 6 },
        { name: 'precision', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#13c2c2', width: 2 }, itemStyle: { color: '#13c2c2' }, symbolSize: 6 },
        { name: 'recall', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#fa541c', width: 2 }, itemStyle: { color: '#fa541c' }, symbolSize: 6 },
      ],
    })
  } else if (isSegmentation) {
    // v2.5.0 S12.4: 分割任务曲线 (loss + mIoU/pixel_acc/dice)
    detailChart.setOption({
      title: {
        text: '分割训练曲线 (Loss / mIoU / Pixel Acc / Dice)',
        left: 'center',
        textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' },
      },
      tooltip: { trigger: 'axis' },
      legend: {
        data: ['train_loss', 'val_loss', 'mIoU', 'pixel_acc', 'dice'],
        top: 30, type: 'scroll', textStyle: { color: '#6b7280' },
      },
      grid: { top: 80, left: 55, right: 55, bottom: 40, containLabel: true },
      xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
      yAxis: [
        { type: 'value', name: 'Loss', position: 'left', axisLine: { lineStyle: { color: '#4f7cff' } }, splitLine: { lineStyle: { color: '#eef0f4' } } },
        { type: 'value', name: 'mIoU / Acc / Dice', position: 'right', min: 0, max: 1, axisLine: { lineStyle: { color: '#00c48c' } }, splitLine: { show: false } },
      ],
      series: [
        { name: 'train_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#4f7cff', width: 2 }, itemStyle: { color: '#4f7cff' }, symbolSize: 6 },
        { name: 'val_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#ff8a4c', width: 2 }, itemStyle: { color: '#ff8a4c' }, symbolSize: 6 },
        { name: 'mIoU', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#00c48c', width: 2 }, itemStyle: { color: '#00c48c' }, symbolSize: 6 },
        { name: 'pixel_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#722ed1', width: 2 }, itemStyle: { color: '#722ed1' }, symbolSize: 6 },
        { name: 'dice', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#13c2c2', width: 2 }, itemStyle: { color: '#13c2c2' }, symbolSize: 6 },
      ],
    })
  } else {
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
  }
  window.addEventListener('resize', () => detailChart?.resize())
}

watch(detailHistory, (h) => {
  if (!detailChart || h.length === 0) return
  const isDetection = detailJob.value?.task_type === 'detection'
  const isSegmentation = detailJob.value?.task_type === 'segmentation'
  if (isDetection) {
    detailChart.setOption({
      xAxis: { data: h.map((x) => x.epoch) },
      series: [
        { data: h.map((x) => +(x.box_loss ?? 0).toFixed(4)) },
        { data: h.map((x) => +(x.cls_loss ?? 0).toFixed(4)) },
        { data: h.map((x) => +(x.map_50 ?? 0).toFixed(4)) },
        { data: h.map((x) => +(x.map_50_95 ?? 0).toFixed(4)) },
        { data: h.map((x) => +(x.precision ?? 0).toFixed(4)) },
        { data: h.map((x) => +(x.recall ?? 0).toFixed(4)) },
      ],
    })
  } else if (isSegmentation) {
    // v2.5.0 S12.4: 分割曲线数据更新 (后端 train.py 推 train_loss/val_loss/mIoU/pixel_acc/dice)
    detailChart.setOption({
      xAxis: { data: h.map((x) => x.epoch) },
      series: [
        { data: h.map((x) => +((x.train_loss ?? 0)).toFixed(4)) },
        { data: h.map((x) => +((x.val_loss ?? 0)).toFixed(4)) },
        { data: h.map((x) => +((x.miou ?? 0)).toFixed(4)) },
        { data: h.map((x) => +((x.pixel_acc ?? 0)).toFixed(4)) },
        { data: h.map((x) => +((x.dice ?? 0)).toFixed(4)) },
      ],
    })
  } else {
    detailChart.setOption({
      xAxis: { data: h.map((x) => x.epoch) },
      series: [
        { data: h.map((x) => +x.train_loss.toFixed(4)) },
        { data: h.map((x) => +x.val_loss.toFixed(4)) },
        { data: h.map((x) => +x.train_acc.toFixed(4)) },
        { data: h.map((x) => +x.val_acc.toFixed(4)) },
      ],
    })
  }
}, { deep: true })

// ============== 工具 ==============
/**
 * 格式化时间为本地时区 (CST/GMT+8) 显示
 *
 * 后端 datetime 序列化规则:
 * - 历史数据: 后端用 datetime.utcnow() 写库, FastAPI 序列化为 naive ISO 字符串
 *   例如 "2026-07-17T21:47:58" (无 tz 标记). 浏览器 new Date() 会按本地时区解析,
 *   导致与实际 UTC 时间相差 8 小时.
 * - 新数据: 后续如果后端改用 datetime.now(timezone.utc), 字符串会带 "Z" 或 "+00:00",
 *   浏览器会正确按 UTC 解析.
 *
 * 修复策略: 检测字符串是否带 tz 标记:
 * - 带 "Z" 或 "+/-HH:MM" → 信任浏览器解析 (已经按 UTC)
 * - 不带 → 默认当作 UTC 处理, 手动追加 "Z" 后再解析
 *   (这是历史数据的兼容方案, 避免一次性 ETL 全表改字段)
 *
 * @param iso 后端返回的 ISO 字符串, 可能带也可能不带 tz 标记
 * @returns 本地时区格式化字符串 (yyyy/MM/dd HH:mm:ss)
 */
const formatTime = (iso: string | null | undefined): string => {
  if (!iso) return '-'
  // 已经带 tz 标记 (Z 或 ±HH:MM) → 直接解析
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
  const parseable = hasTz ? iso : `${iso}Z`  // naive datetime → 按 UTC 解释
  try {
    const d = new Date(parseable)
    if (isNaN(d.getTime())) return iso  // 解析失败回退原值
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
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
  // v2.5.47: 基础模型列表异步加载, 训练对话框渲染前完成
  // - 与 loadDatasets / loadJobs 并行触发, 互不依赖
  // - 失败不回滚整页, 训练下拉走 DEFAULT_BASE_MODEL 兜底
  await Promise.all([loadDatasets(), loadJobs(), loadBaseModels()])
  startSilentRefresh()  // 静默兜底刷新
})

onBeforeUnmount(() => {
  cleanupDetail()
  // 列表级 SSE 由 useTrainingListSSE composable 自动清理
  // 静默刷新由 useSilentRefresh composable 自动清理
})

// ============== 列表级 SSE: 跟踪活跃任务的进度, 替代整页轮询 ==============
// v3.0.0 Phase G: 抽离到 useTrainingListSSE composable, page 只剩业务编排
// 思路: 对当前页里所有 PROGRESS/PENDING 状态的 task 维护一条 SSE 订阅,
// 收到帧时仅原地更新那一行的 (state, progress), 终态时静默拉整页

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

useTrainingListSSE({
  jobs,
  onProgressUpdate: updateJobProgressInPlace,
  onTerminalState: () => loadJobs(),
  onError: () => loadJobs(),
  onComplete: () => loadJobs(),
})

// ============== 静默兜底刷新 ==============
// v3.0.0 Phase G: 抽离为 useSilentRefresh composable
// 之前: 任何状态下 30s 拉一次 DB 兜底. SSE 仍负责高频行内更新,
// 拉整页只在 SSE 断 / 终态 / 跨用户时触发, 频率很低, 开销可接受.
// 一次额外 GET /jobs 的开销 (返回当前页 10 条记录) 远低于用户
// "盯着看却看不到失败"的体验损失.
const { start: startSilentRefresh, stop: stopSilentRefresh } = useSilentRefresh({
  intervalMs: 30000,
  skipWhen: () => detailVisible.value,  // 详情 dialog 打开时, 由详情 SSE 驱动
  onTick: () => loadJobs(),
})
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

    <!-- ============== 顶部标题 ============== -->
    <div class="page-header">
      <div>
        <h2 class="page-title">
          <el-icon class="page-title__icon"><Promotion /></el-icon>
          <span>训练任务</span>
          <span class="subtitle">Training</span>
        </h2>
        <p class="page-desc text-soft">
          选择数据集和基础模型, 启动微调训练; 训练完成后激活即可用于 AI 预标注
        </p>
      </div>
    </div>

    <!-- ============== 筛选 + 操作 同一行 ============== -->
    <div class="filter-row">
      <!-- v2.5.24: 任务类型筛选, 固定排序: 图片分类 / 目标检测 / 图片分割
           - 留空 = 全部, 与后端 task_type=NULL 跳过对应
           - 复用 utils/taskType.ts 的 TASK_TYPE_OPTIONS
           - 与 Annotate 工作台 / 模型版本管理 保持一致 -->
      <el-select
        v-model="taskTypeFilter"
        placeholder="任务类型"
        class="app-select app-select--narrow"
        clearable
        @change="onTaskTypeFilterChange"
      >
        <el-option
          v-for="opt in TASK_TYPE_OPTIONS" :key="opt.value"
          :label="opt.label" :value="opt.value"
        />
      </el-select>
      <!-- 状态筛选 -->
      <el-select
        v-model="stateFilter"
        placeholder="状态"
        class="app-select app-select--narrow"
        clearable
        @change="onStateFilterChange"
      >
        <el-option
          v-for="o in STATE_OPTIONS.filter((o) => o.value)" :key="o.value"
          :label="o.label" :value="o.value"
        />
      </el-select>
      <!-- 数据集筛选: 独立下拉, 从 DATASET_OPTIONS 取数
           v2.5.24: 与 taskTypeFilter 联动, 仅展示同 task_type 的数据集 -->
      <el-select
        v-model="datasetIdFilter"
        placeholder="数据集"
        class="app-select"
        clearable
        filterable
        @change="onDatasetFilterChange"
      >
        <el-option
          v-for="d in filterableDatasetsForFilter" :key="d.id"
          :label="d.name" :value="d.id"
        />
      </el-select>
      <!-- 模型关键词筛选: 模糊匹配 model_name / base_model -->
      <el-input
        v-model="modelKeywordFilter"
        placeholder="模型名 / 基础模型"
        class="filter-keyword"
        clearable
        :prefix-icon="Search"
        @input="onModelKeywordChange"
      />
      <!-- 重置按钮: 仅在有任一筛选时显示 -->
      <el-button
        v-if="stateFilter || datasetIdFilter != null || modelKeywordFilter || taskTypeFilter"
        text
        :icon="Refresh"
        @click="resetFilters"
      >
        重置
      </el-button>
      <div class="header-actions">
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建训练任务</el-button>
        <el-button :icon="Refresh" @click="loadJobs">刷新</el-button>
      </div>
    </div>

    <!-- 筛选状态条: 当有任一筛选生效时, 显示当前命中条件 (便于用户确认筛了啥) -->
    <div v-if="stateFilter || datasetIdFilter != null || modelKeywordFilter || taskTypeFilter" class="filter-chips">
      <span class="chips-label">当前筛选:</span>
      <el-tag v-if="taskTypeFilter" type="info" effect="plain" closable @close="onTaskTypeFilterClear">
        任务类型: {{ TASK_TYPE_OPTIONS.find((o) => o.value === taskTypeFilter)?.label || taskTypeFilter }}
      </el-tag>
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
        border stripe
        class="jobs-table"
        height="100%"
        style="width: 100%;"
        :row-class-name="rowClassName"
        @selection-change="onJobSelectionChange"
      >
        <template #empty>
          <div class="empty-state">
            <div class="empty-state__icon empty-state__icon--brand">
              <el-icon><Promotion /></el-icon>
            </div>
            <div class="empty-state__title">
              {{ stateFilter ? '没有匹配的任务' : '还没有训练任务' }}
            </div>
            <div class="empty-state__desc">
              {{ stateFilter
                ? '尝试切换状态筛选, 或等待新任务完成'
                : '点击右上角「新建训练任务」, 选定数据集后即可启动微调'
              }}
            </div>
          </div>
        </template>
        <el-table-column type="index" :index="indexMethod" label="#" width="42" align="center" />
        <el-table-column type="selection" width="40" :reserve-selection="false" />
        <el-table-column label="数据集" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">{{ datasetNameOf(row.dataset_id) }}</template>
        </el-table-column>
        <el-table-column prop="base_model" label="基础模型" min-width="92" show-overflow-tooltip />
        <!-- 任务类型列: 与 Models 页 (v2.5.16) 完全对齐 — el-tag + effect="plain" + meta.type
             颜色映射: classification=primary 蓝, detection=success 绿, segmentation=warning 黄
             (v2.5.25 简化为 info 灰之后, 反馈"看不出是哪种类型", 恢复类型色) -->
        <el-table-column label="任务类型" width="120" align="center">
          <template #default="{ row }">
            <el-tag
              :type="getTaskTypeMeta(row.task_type || 'classification').type"
              effect="plain"
              size="small"
            >
              {{ getTaskTypeMeta(row.task_type || 'classification').label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型版本" min-width="118" show-overflow-tooltip />
        <!-- 训练资源: 实际使用的设备 (GPU/CPU), 后端采集 + 写库 -->
        <el-table-column label="设备" min-width="118" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip
              v-if="row.device_info"
              placement="top"
              :content="formatDeviceTooltip(row.device_info) +
                (row.gpu_peak_memory_mb
                  ? `\n\nGPU 峰值显存: ${row.gpu_peak_memory_mb} MB`
                  : '')">
              <el-tag :type="deviceTagType(row.device_type)" size="small">
                {{ deviceShortLabel(row.device_type, row.device_name) }}
              </el-tag>
            </el-tooltip>
            <span v-else style="color: #c0c4cc;">-</span>
            <el-tag
              v-if="row.gpu_peak_memory_mb"
              size="small" type="warning" effect="plain"
              style="margin-left: 4px;"
            >{{ row.gpu_peak_memory_mb }}MB</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="epochs" label="轮次" width="80" align="center" />
        <el-table-column label="状态" width="86" align="center">
          <template #default="{ row }">
            <StateBadge :state="row.state" />
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
        <el-table-column label="创建日期" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">
            <!--
              创建日期 = 任务入库时间 (PENDING 阶段就有), 与"开始时间"区分
              - created_at: 后端在 API 端提交瞬间写入 (PENDING 起就有)
              - started_at: worker 真正开始训练的时间, PENDING/PAUSED 为空
              对应后端 TrainingJob.created_at / TrainingJob.started_at
            -->
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="开始日期" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">
            <!--
              PENDING/PAUSED 状态下不显示开始时间:
              - started_at 语义是"worker 真正开始训练"的时间, 仅 PROGRESS/SUCCESS/
                FAILURE/REVOKED 时才有意义
              - 历史数据中 mode=resume 的 API 端会写入 started_at (后端已修复),
                但 DB 里已有遗留行. 兜底在此处按状态过滤, 避免显示错乱
            -->
            {{ (row.state === 'PENDING' || row.state === 'PAUSED') ? '-' : formatTime(row.started_at) }}
          </template>
        </el-table-column>
        <el-table-column label="结束日期" min-width="92" show-overflow-tooltip>
          <template #default="{ row }">
            <!--
              结束日期 = finished_at, 仅终态 (SUCCESS/FAILURE/REVOKED) 才有值
              非终态显示 "-"
              三个时间字段语义 (与后端 TrainingJob 模型对应):
                - created_at (创建日期): API 入库瞬间, PENDING 起就有
                - started_at (开始日期): worker 真正开始训练, PENDING/PAUSED 为空
                - finished_at (结束日期): 任务进入终态, 仅 SUCCESS/FAILURE/REVOKED 有值
            -->
            {{ ['SUCCESS', 'FAILURE', 'REVOKED'].includes(row.state) ? formatTime(row.finished_at) : '-' }}
          </template>
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
                  :disabled="row.is_placeholder || runBtnDisabled(row.state)"
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
                  :disabled="row.is_placeholder || !canCancel(row.state)"
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
                  :disabled="row.is_placeholder"
                  @click="openDetail(row)"
                />
              </el-tooltip>
              <el-tooltip content="删除任务" placement="top" :show-after="200">
                <el-button
                  size="small"
                  :icon="Delete"
                  circle plain
                  type="danger"
                  :disabled="row.is_placeholder || !canDelete(row.state)"
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
      <!-- v2.5.24: 任务类型下拉 (在数据集筛选框前)
           - 固定排序: 图片分类 / 目标检测 / 图片分割
           - 切换 task_type 后, 数据集下拉只显示同类型
           - 切换会重置 base_model + model_name (走任务类型默认模型)
           - 与 Annotate 工作台的任务类型筛选逻辑保持一致 -->
      <el-form label-width="90px" size="default">
        <el-form-item label="任务类型">
          <el-select
            :model-value="createFormTaskType"
            @update:model-value="(v: string) => onCreateTaskTypeChange(v)"
            class="app-select" style="width: 160px;"
          >
            <el-option
              v-for="opt in CREATE_TASK_TYPE_OPTIONS"
              :key="opt.value" :value="opt.value" :label="opt.label"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <TrainingParamsForm
        :form="createForm"
        :datasets="filteredDatasets"
        :base-models="createFormBaseOptions"
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
      :title="`再训练任务 #${editStartForm.id}`"
      width="520px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <el-alert
        type="info" :closable="false" style="margin-bottom: 12px;"
        title="可在此调整训练参数 (数据集/基础模型/版本名/轮次/批大小/学习率). 新 model_name 会自动加 _r{时间戳} 后缀, 避免覆盖旧 .pth."
      />
      <el-alert
        type="warning" :closable="false" style="margin-bottom: 12px;"
        :title="`原任务 #${editStartForm.id} 不会被修改, 此处参数仅用于创建新一轮训练任务. 提交后会立即在列表顶部出现新任务 (PENDING).`"
      />
      <TrainingParamsForm
        :form="editStartForm"
        :datasets="DATASET_OPTIONS"
        :base-models="editStartBaseOptions"
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
          <StateBadge :state="detailJob.state" size="md" />
        </div>

        <!-- 基本信息 -->
        <el-descriptions class="detail-descs" :column="3" border size="small">
          <el-descriptions-item label="任务 ID">{{ detailJob.id }}</el-descriptions-item>
          <el-descriptions-item label="数据集">{{ datasetNameOf(detailJob.dataset_id) }}</el-descriptions-item>
          <!-- v2.5.27 新增: 任务类型 (classification / detection / segmentation) -->
          <el-descriptions-item label="任务类型">
            <el-tag
              v-if="detailJob.task_type"
              :type="(getTaskTypeMeta(detailJob.task_type)?.type) || 'info'"
              size="small"
              effect="light"
            >
              {{ getTaskTypeMeta(detailJob.task_type)?.label || detailJob.task_type }}
            </el-tag>
            <span v-else style="color: #c0c4cc;">未记录</span>
          </el-descriptions-item>
          <el-descriptions-item label="基础模型">{{ detailJob.base_model }}</el-descriptions-item>
          <el-descriptions-item label="模型版本">{{ detailJob.model_name }}</el-descriptions-item>
          <el-descriptions-item label="训练设备">
            <el-tag v-if="detailJob.device_type" :type="deviceTagType(detailJob.device_type)" size="small">
              {{ deviceShortLabel(detailJob.device_type, detailJob.device_name) }}
            </el-tag>
            <span v-else style="color: #c0c4cc;">未记录</span>
            <el-tooltip
              v-if="detailJob.device_info"
              placement="top"
              :content="formatDeviceTooltip(detailJob.device_info) +
                (detailJob.gpu_peak_memory_mb
                  ? `\n\nGPU 峰值显存: ${detailJob.gpu_peak_memory_mb} MB`
                  : '')">
              <el-icon style="margin-left: 4px; cursor: help;"><InfoFilled /></el-icon>
            </el-tooltip>
            <el-tag
              v-if="detailJob.gpu_peak_memory_mb"
              size="small" type="warning" effect="plain"
              style="margin-left: 6px;"
            >峰值 {{ detailJob.gpu_peak_memory_mb }} MB</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="轮次">{{ detailJob.epochs }}</el-descriptions-item>
          <el-descriptions-item label="批大小">{{ detailJob.batch_size }}</el-descriptions-item>
          <el-descriptions-item label="学习率">{{ detailJob.learning_rate }}</el-descriptions-item>
          <el-descriptions-item label="耗时(s)">
            <span v-if="detailJob.duration_seconds">{{ detailJob.duration_seconds.toFixed(1) }}</span>
            <span v-else style="color: #c0c4cc;">-</span>
            <!-- duration 也智能格式化: > 60s 显 Xm Ys, > 3600s 显 Xh Ym -->
            <span v-if="detailJob.duration_seconds >= 60" style="color: var(--text-secondary); font-size: 12px; margin-left: 4px;">
              ({{ formatDurationSmart(detailJob.duration_seconds) }})
            </span>
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

        <!-- 数据集统计 (SSE 实时 + DB fallback) -->
        <el-divider content-position="left">数据集统计</el-divider>
        <el-row :gutter="12" style="margin-bottom: 8px;">
          <el-col :span="6">
            <div class="mini-stat mini-stat--blue">
              <div class="mini-label">总样本数</div>
              <div class="mini-value">{{ displayDataTotal ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--green">
              <div class="mini-label">训练集</div>
              <div class="mini-value">{{ displayDataTrain ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--orange">
              <div class="mini-label">验证集</div>
              <div class="mini-value">{{ displayDataVal ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">张</span></div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="mini-stat mini-stat--red">
              <div class="mini-label">类别数</div>
              <div class="mini-value">{{ displayNumClasses ?? 0 }} <span style="font-size: 12px; color: var(--text-secondary); font-weight: 400;">类</span></div>
            </div>
          </el-col>
        </el-row>
        <div
          v-if="displayClassNames.length > 0"
          style="margin-bottom: 8px; color: #606266; font-size: 13px;"
        >
          类别:
          <el-tag
            v-for="cn in displayClassNames" :key="cn"
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
/* 行内 cell 紧凑: el-table 默认 cell-padding 12px 0, 压缩到 8px 让单行更易读 */
:deep(.el-table .el-table__cell) {
  padding: 8px 0 !important;
}
:deep(.el-table .el-table__cell .cell) {
  padding: 0 8px;
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

/* ============== 顶部标题 ============== */
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-shrink: 0;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}
.page-title__icon {
  font-size: 22px;
  color: var(--brand-primary);
}
.page-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}
.page-title .subtitle {
  color: var(--text-placeholder);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* ============== 筛选 + 操作 同一行 ============== */
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fff;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
  flex-shrink: 0;
  flex-wrap: wrap;             /* 控件多时换行, 避免单行过挤 */
}
.filter-keyword { width: 220px; flex-shrink: 0; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;            /* 推到行尾, 筛选在左, 操作在右 */
  flex-wrap: wrap;
  justify-content: flex-end;
}

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
  min-height: 420px;        /* 兜底: 即使上方内容变多, 表格也至少能显示 8-10 行 */
  overflow: auto;  /* 内容多时表格内部滚动 */
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: #fff;
}
.table-wrapper .el-table {
  /* el-table 自身是 display: table, 不接受 flex:1; 用 height: 100% 占满父容器 */
  height: 100% !important;
  width: 100% !important;
  font-size: 13px;          /* 单元格字号: 13px, 与全站表格统一, 比原 12px small 更易读 */
}
/* Element Plus el-table 在 flex 容器中, 默认会自己处理 body 滚动, 不要
   给 __inner-wrapper 强加 overflow:auto, 否则 fixed-right 列会盖住内容列. */

/* ============== 占位行 (乐观插入, 等 worker 写库) ============== */
/* el-table 通过 row-class-name 给行加 class, 这里用 :deep 穿透到行单元 */
.jobs-table :deep(.row--placeholder) {
  background: linear-gradient(90deg,
    rgba(79, 124, 255, 0.06) 0%,
    rgba(79, 124, 255, 0.02) 50%,
    rgba(79, 124, 255, 0.06) 100%) !important;
  background-size: 200% 100%;
  /* 等待中的斜体效果 */
  font-style: italic;
  color: var(--text-secondary);
  animation: placeholder-shimmer 2s ease-in-out infinite;
}
.jobs-table :deep(.row--placeholder td) {
  position: relative;
}
.jobs-table :deep(.row--placeholder td:first-child::before) {
  /* 行首加一根左侧色条, 提示该行是占位状态 */
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--gradient-brand);
  border-radius: 0 3px 3px 0;
}
@keyframes placeholder-shimmer {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}

/* 单元格内边距: 默认 12px 0 偏大, 压缩到 8px 让单行更紧凑; 行高随之 ~38px */
.jobs-table :deep(.el-table .el-table__cell) {
  padding: 8px 0 !important;
}
/* 表头略加粗并放大到 13px (theme.css 已是 13px, 这里显式覆盖确保不被 inherit) */
.jobs-table :deep(.el-table th.el-table__cell) {
  font-size: 13px !important;
  font-weight: 600;
  background: var(--bg-soft) !important;
}

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

/* v2.5.26 修复: 删除 v2.5.15 (a8cff15) 的 4 条 :deep() 渐变色规则
   原意是给状态列做"与统计卡色调一致"的渐变效果
   问题: :deep() 选择器太宽, 把任务类型列 (warning/success/primary) 也一并染上渐变
   修复: 全部状态/类型 tag 统一使用 el-tag plain 默认样式 (白底 + 细边 + 类型色字),
   避免跨列污染, 与 v2.5.25 简化版 StateBadge 设计保持一致 */
</style>
