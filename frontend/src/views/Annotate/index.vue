<script setup lang="ts">
/**
 * Annotate.vue - 人工标注工作台 (v3.5.0 增强版)
 * ==================================================
 * - 选择数据集 → 显示下一张待标注图
 * - 显示 AI Top-5 候选 + 确认/修正
 * - 实时统计 + 已标注计数
 * - 可跳转到 DatasetDetail 浏览已标注图片
 * - 「使用项目训练模型」开关 ON 时, 显示项目微调模型下拉 (默认=激活的)
 * - 显示当前激活的模型名 + 训练后引导用户到标注页
 *
 * v3.5.0 优化:
 * - 顶部统计 + 工具栏 → AnnotationToolbar.vue (其中状态卡组拆为 AnnotationStatusCards)
 * - 画布壳 → AnnotationCanvas.vue (slot 注入 3 个 annotator)
 * - 3 个任务右侧面板 → ClassificationPanel / DetectionPanel / SegmentationPanel
 * - 检测 / 分割 / AI 预标注业务逻辑 → composables
 *   · useDetectionAnnotate (bboxList, 复制建议, 保存, popover)
 *   · useSegmentationAnnotate (initialMaskUrl, 模式, 保存)
 *   · useAutoAnnotate (runAutoAnnotate, runDetectionAutoAnnotate)
 *   · useAnnotationStatusFilter (状态筛选, localStorage 持久化)
 *   · useAnnotationKeyboard (页面级快捷键: → / ← / Enter / u / h)
 * - 批量操作 → AnnotationBatchBar.vue + annotationApi.clear / batchMarkUnqualified
 * - AI 已标图片顶部提示 → 展示 AI 预标注类型 (ClassificationPanel alert)
 *   · 类别确认由候选行「确认此标签」按钮
 *   · 类别修正由「或选择其他类别」下拉
 * - 修正历史 → CorrectionHistoryDialog (从 /annotations/correction-history/{id} 拉数据)
 */
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { annotationApi, imageApi, autoAnnotateApi, datasetApi, modelApi } from '@/api'
import { useDetectionAnnotate } from '@/composables/useDetectionAnnotate'
import { useSegmentationAnnotate } from '@/composables/useSegmentationAnnotate'
import { useAutoAnnotate } from '@/composables/useAutoAnnotate'
// v3.5.0: 状态筛选 + 页面级快捷键 + 批量操作 + 图片导航 + AI 修正事件
import { useAnnotationStatusFilter, STATUS_FILTER_LABEL, type StatusFilterValue } from '@/composables/useAnnotationStatusFilter'
import { useAnnotationKeyboard } from '@/composables/useAnnotationKeyboard'
import { useAnnotationBatch } from '@/composables/useAnnotationBatch'
import { useAnnotateNav } from '@/composables/useAnnotateNav'
import { useAnnotationAICorrection } from '@/composables/useAnnotationAICorrection'
// v2.5.8 架构优化: 业务组件全部迁入当前页面私有目录, 引用统一使用相对路径
import DetectionAnnotator from './components/DetectionAnnotator.vue'
import SegmentationAnnotator from './components/SegmentationAnnotator.vue'
import ClassificationAnnotator from './components/ClassificationAnnotator.vue'
import ClassificationPanel from './components/ClassificationPanel.vue'
import DetectionPanel from './components/DetectionPanel.vue'
import SegmentationPanel from './components/SegmentationPanel.vue'
import AnnotationToolbar from './components/AnnotationToolbar.vue'
import AnnotationCanvas from './components/AnnotationCanvas.vue'
// v3.5.0: 批量操作条 (页面私有子组件)
import AnnotationBatchBar from './components/AnnotationBatchBar.vue'
// v2.5.9 新增: 标注工作台左侧"操作指导"侧栏
import AnnotationGuideSidebar from './components/AnnotationGuideSidebar.vue'
// v3.5.0: 修正历史弹窗 (复用 business 层组件)
import CorrectionHistoryDialog from '@/components/annotation-business/CorrectionHistoryDialog.vue'
// v2.5.44 新增: 顶部"返回数据集详情"按钮 (跳转回 /datasets/:id)
import { ArrowLeft, Grid } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

// ============== 核心 page state ==============
const loading = ref(false)
const image = ref<any>(null)
const candidates = ref<{ label: string; confidence: number }[]>([])
const startTs = ref(0)
const datasets = ref<any[]>([])
// v2.5.20: 任务类型筛选, 默认 'classification' (图片分类)
// - 排序固定: 'classification' / 'detection' / 'segmentation' (3 项, 不含"全部")
// - 切换筛选时, 若当前 datasetId 不在新筛选范围内, 自动切到第一个匹配项
// - v2.5.x: 持久化于 localStorage, 与仪表盘 Dashboard 的持久化策略对齐
//   (点击侧边栏「标注工作台」进入页面时, 上次选择的任务类型会被还原, 默认「图片分类」)
const TASK_TYPE_VALUES = ['classification', 'detection', 'segmentation'] as const
const ANNOTATE_TASK_TYPE_STORAGE_KEY = 'annotate_task_type_filter'
const loadTaskTypeFromStorage = (): string => {
  try {
    const stored = localStorage.getItem(ANNOTATE_TASK_TYPE_STORAGE_KEY)
    if (stored && (TASK_TYPE_VALUES as readonly string[]).includes(stored)) {
      return stored
    }
  } catch (e) {
    /* localStorage 不可用 (隐私模式等) 时静默回退 */
  }
  return 'classification'
}
const taskTypeFilter = ref<string>(loadTaskTypeFromStorage())
// v3.5.0: 图片状态筛选 (待标注 / AI 已标 / 已人工标注)
// - 持久化于 localStorage, 用户下次进入工作台时自动恢复
// - 切换状态时, 重新拉取对应状态的图 (后端 imageApi.list 支持 status= 过滤)
// - 默认 'pending', 与"进入工作台默认标注未标图"的语义一致
const {
  statusFilter, apiStatusParam,
  isAiCorrectionMode,
  setStatusFilter,
} = useAnnotationStatusFilter()
const datasetId = ref<number | null>(null)
const categories = ref<any[]>([])
const stats = ref<any>(null)
const sessionStats = ref({ confirmed: 0, corrected: 0, total_time_ms: 0 })
const annotatorSaving = ref(false)

// ============== 模型选择 state ==============
const modelName = ref('efficientnet_b0')           // 基础模型 (仅 useFinetune=false)
const threshold = ref(0.6)
const iouThreshold = ref(0.45)                    // 检测 NMS 阈值
const detectionModelName = ref('yolov8n')         // 检测预训练模型
// v2.5.46: 分割预训练模型 (torchvision COCO 21 类, useFinetune=OFF 时使用)
const segmentationModelName = ref('deeplabv3_resnet50')
const models = ref<any[]>([])                     // base models (timm ImageNet)
const finetuneModels = ref<any[]>([])             // 项目训练的 fine-tune models
const selectedModelId = ref<number | null>(null)  // 当前选中的 fine-tune model id
const activeModel = ref<any>(null)                // 当前激活的 model (引导用)
// 严格模式: 默认使用项目训练的 fine-tune 模型, 严禁默认走基础模型
// (基础模型 ImageNet 输出的 class_532 等不在项目类目, 会被前端归一为「未知」)
const useFinetune = ref(true)

// ============== 检测 / 分割子组件 ref ==============
const detAnnotRef = ref<any>(null)
const segAnnotRef = ref<any>(null)

// ============== 检测任务 composable ==============
const {
  bboxList,
  copySuggestions,
  copySuggestionSourceCount,
  detDirty,
  detOpenPopoverIdx,
  loadDetectionAnnotations,
  loadCopySuggestion,
  applyCopySuggestions,
  ignoreCopySuggestions,
  saveDetectionBBoxes,
  cancelDetectionDraft,
  onDetTagClick,
  onDetCategoryChange,
  onPopoverVisibleChange,
  onDetTargetCategoryChange,
  onDetDirtyChange,  // v2.5.40: 取代 inline lambda, 修复 ref 不更新导致「清空全部」后无法保存
  removeBboxByIndex,
} = useDetectionAnnotate({
  image,
  detAnnotRef,
  annotatorSaving,
  // v3.0.0: 检测保存成功后 → 刷新 stats + 栈顶时自动 loadNext
  // - 后端 save_bbox/replace_bboxes 会把 image.status 提升到 human_confirmed/corrected
  // - 前端需主动刷新 stats, 才能让"待标注"数字减少
  // - 仅在 historyCursor 在栈顶时自动跳下一张 (与分类 submit 行为一致),
  //   用户在历史中间时不跳, 避免覆盖用户的"上一张"浏览意图
  onSaved: async () => {
    await refreshStats()
    if (historyCursor.value >= historyIds.value.length - 1) {
      loadNext()
    }
  },
})

// ============== 分割任务 composable ==============
const {
  initialMaskUrl,
  segDirty,
  segMode,
  loadSegmentationMask,
  saveSegmentationMask,
  revokeMaskUrl,
  onSegModeChange,
  onSegCategoryChange,
  onSegBrushSizeChange,
  onSegSave,
  onSegClear,
  onSegDirtyChange,
} = useSegmentationAnnotate({
  image,
  segAnnotRef,
  annotatorSaving,
  // v3.0.0: 分割保存成功后 → 刷新 stats + 栈顶时自动 loadNext
  // - 后端 upload_mask 会把 image.status 提升到 human_confirmed
  // - 前端需主动刷新 stats, 才能让"待标注"数字减少
  // - 仅在 historyCursor 在栈顶时自动跳下一张 (与分类 submit 行为一致)
  onSaved: async () => {
    await refreshStats()
    if (historyCursor.value >= historyIds.value.length - 1) {
      loadNext()
    }
  },
})

// ============== 浏览历史栈 + 切图逻辑 (v3.5.0 抽离到 useAnnotateNav composable) ==============
// - historyIds / historyCursor / noMore / canGoPrev / loadNext / loadPrev / loadSpecificImage
//   全部由 composable 自管, 父组件通过返回值使用
// - 注意: composable 调用延后到 fillImage / currentTaskTypeRaw 声明之后 (TS 块作用域前引用问题)
//   下面先占位声明, 真正初始化在 fileImage 之后 (见下方 "延迟初始化 nav composable" 块)

onMounted(async () => {
  try {
    const ds: any = await datasetApi.list()
    datasets.value = ds?.items || ds || []
    if (route.params?.datasetId) {
      datasetId.value = Number(route.params.datasetId)
    } else if (datasets.value.length > 0) {
      // 优先沿用持久化的 taskTypeFilter 范围内的数据集; 否则取该任务类型第一项; 都没有则用列表第一项
      const f = taskTypeFilter.value
      const matched = datasets.value.filter((d: any) => (d.task_type || 'classification') === f)
      if (matched.length > 0) {
        datasetId.value = matched[0].id
      } else {
        datasetId.value = datasets.value[0].id
      }
    }
    // v2.5.19: 初始化 taskTypeFilter, 跟当前 datasetId 的 task_type 保持一致
    // - 避免出现"选了 detection 数据集, 但筛选框停在 all"的割裂感
    // - 用户后续可手动切换筛选 (切换后会写回 localStorage)
    syncTaskTypeFilterFromDataset()
    // 加载 base models (timm) + 项目 fine-tune models
    const ms: any = await autoAnnotateApi.models()
    models.value = ms?.models || []
    await refreshFinetuneModels()
  } catch (e: any) {
    ElMessage.error('初始化失败: ' + (e?.response?.data?.detail || e?.message))
  }
})

/**
 * v2.5.20: 任务类型筛选切换
 * - 记录新筛选值
 * - 检查当前 datasetId 是否在新的筛选范围内, 若不在则切到第一个匹配的 dataset
 *   (切到 null 视作"当前筛选下没有可用数据集", UI 会显示空态)
 * - 手动切换 dataset 时, 也会反向同步筛选 (syncTaskTypeFilterFromDataset)
 */
const onTaskTypeFilterChange = (v: string) => {
  taskTypeFilter.value = v
  const list = filteredDatasets.value
  const currentInList = list.find((d: any) => d.id === datasetId.value)
  if (!currentInList) {
    // 当前 dataset 不在新筛选范围, 切到第一个或清空
    datasetId.value = list.length > 0 ? list[0].id : null
  }
  // 若 datasetId 实际改了, watch(datasetId) 会自动重新加载, 不需要手动 loadNext
}

/**
 * v2.5.19: 反向同步 — 根据当前 datasetId 自动设置 taskTypeFilter
 * - 在初始化、用户手动改 dataset、URL 跳转时调用
 * - 保持"筛选框 ↔ 当前数据集"语义一致
 */
const syncTaskTypeFilterFromDataset = () => {
  if (!datasetId.value) return
  const ds = datasets.value.find((d: any) => d.id === datasetId.value)
  if (ds?.task_type) {
    taskTypeFilter.value = ds.task_type
  }
}

/**
 * 拉取"指定 dataset 已激活的 fine-tune 模型"
 * - 仅显示该 dataset 的激活模型, 与"模型版本管理"的"按数据集显示已激活"语义一致
 */
const refreshFinetuneModels = async () => {
  const did = datasetId.value
  if (!did) {
    finetuneModels.value = []
    return
  }
  try {
    const ft: any = await modelApi.list({ dataset_id: did, active: true })
    let items: any[] = ft?.items || ft || []
    if (items.length === 0) {
      const r: any = await modelApi.getActive(did)
      items = r?.items || (r?.model ? [r.model] : [])
    }
    finetuneModels.value = items
    if (selectedModelId.value && items.find((m) => m.id === selectedModelId.value)) {
      // 保留当前选中
    } else if (items.length > 0) {
      selectedModelId.value = items[0].id
    } else {
      selectedModelId.value = null
    }
  } catch {
    finetuneModels.value = []
    selectedModelId.value = null
  }
}

/** 任务类型筛选变化时持久化到 localStorage (与 Dashboard 行为一致)
 *  - 侧边栏「标注工作台」进入页面时, 会从 localStorage 还原上次选择
 *  - 用户主动切换筛选后, 新值即时落盘, 供下次进入页面恢复 */
watch(taskTypeFilter, (newVal) => {
  try {
    localStorage.setItem(ANNOTATE_TASK_TYPE_STORAGE_KEY, newVal)
  } catch (e) { /* localStorage 不可用时静默 */ }
})

/**
 * v3.5.0 P1-1 优化: 状态切换后默认跳到该状态第一张
 * - 旧: watch(statusFilter) → loadNext(), loadNext 用历史栈 exclude, 常跳到第 2/3 张
 * - 新: watch(statusFilter) → refreshViewList() (拉全量 id 供批量) + loadFirstOfStatus() (跳第一张)
 * - 切换 dataset 时 statusFilter 不重置, 用户的筛选偏好跨 dataset 保留
 * - selectedIds 在状态切换时清空, 与新状态的图不重叠
 */
watch(statusFilter, async () => {
  selectedIds.value = []
  if (datasetId.value) {
    await refreshViewList()
    await loadFirstOfStatus()
  }
})

watch(datasetId, async (v) => {
  if (!v) return
  sessionStats.value = { confirmed: 0, corrected: 0, total_time_ms: 0 }
  // v3.5.0: 切换 dataset 时, 调用 useAnnotateNav 提供的 resetHistory 清空浏览历史
  // (新 dataset 的 id 集合不同, 必须清)
  resetHistory()
  candidates.value = []
  // 三类互不依赖的请求并行拉取，避免串行 RTT 累加（原 200-800ms → 单次 RTT）
  const [cats, s, actResp] = await Promise.all([
    datasetApi.categories(v).catch(() => null),
    annotationApi.stats(v).catch(() => null),
    modelApi.getActive(v).catch(() => null),
  ])
  const c: any = cats
  categories.value = c?.items || c || []
  stats.value = s
  const r: any = actResp
  activeModel.value = r?.items?.[0] || r?.model || null
  await refreshFinetuneModels()
  await refreshViewList()
  // v2.5.44: 优先消费 URL 上的 ?imageId= query (来自数据集详情页"去标注"按钮)
  const targetImageId = Number(route.query.imageId)
  if (targetImageId && !Number.isNaN(targetImageId)) {
    const ok = await loadSpecificImage(targetImageId)
    if (ok) {
      router.replace({ query: {} })
      return
    }
  }
  loadNext()
})

// v3.5.0: loadSpecificImage 已抽离到 useAnnotateNav composable, 父组件直接用返回值

// ============== 切图 race 控制 (v3.0.0) ==============
// onMarkUnqualified 后 600ms 自动 loadNext, 但用户在窗口期内可能点"下一张"
// 用 setTimeout 句柄 + 检查 image.value.id 是否还是被标记的图, 避免重复 loadNext
let pendingMarkUnqualifiedTimer: ReturnType<typeof setTimeout> | null = null

// ============== v3.0.0: 不合格标记事件处理 ==============

/**
 * 标记当前图为不合格
 * - 调 API 设置 quality_flag / reject_reason
 * - 就地更新 image 对象 (避免整页重拉)
 * - 标记后自动加载下一张 (不合格图不应停留)
 * - v3.0.0: 加 race 保护, 600ms 延迟期间用户点"下一张"则跳过定时器
 */
async function onMarkUnqualified(reason: string, customText: string) {
  if (!image.value?.id) return
  const markedId = image.value.id  // v3.0.0: 记录被标记的图 id, 用于 race 检测
  try {
    await annotationApi.markUnqualified({
      image_id: markedId,
      reason,
      custom_text: customText || undefined,
    })
    ElMessage.success('已标记为不合格')
    // 就地更新 image 对象
    image.value = {
      ...image.value,
      quality_flag: 'unqualified',
      reject_reason: reason,
    }
    // v3.0.0: 刷新统计 (不合格卡需要实时更新)
    await refreshStats()
    // 标记后自动加载下一张 (race 保护: 仅在 600ms 后仍是同一张图时才 loadNext)
    // 场景: 用户在 600ms 窗口内点"下一张"或"启动 AI 预标注",
    //       image.value.id 已变, 此时不应再触发重复 loadNext
    if (pendingMarkUnqualifiedTimer) clearTimeout(pendingMarkUnqualifiedTimer)
    pendingMarkUnqualifiedTimer = setTimeout(() => {
      pendingMarkUnqualifiedTimer = null
      // 仅在当前仍是同一张图时才 loadNext
      if (image.value?.id === markedId) {
        loadNext()
      }
    }, 600)
  } catch (e: any) {
    ElMessage.error('标记失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/**
 * 撤销当前图的不合格标记
 * - 调 API 清空 4 字段
 * - 就地更新 image 对象
 * - 刷新统计 (不合格数变化)
 */
async function onUnmarkUnqualified() {
  if (!image.value?.id) return
  try {
    await annotationApi.unmarkUnqualified(image.value.id)
    ElMessage.success('已撤销不合格标记')
    image.value = {
      ...image.value,
      quality_flag: null,
      reject_reason: null,
    }
    await refreshStats()
  } catch (e: any) {
    ElMessage.error('撤销失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== computed ==============
const pendingCount = computed(() => {
  return (stats.value?.status_counts || {}).pending || 0
})
// v2.5.19: 按 taskTypeFilter 过滤后的数据集列表 (供子组件用)
// - 与 AnnotationToolbar 内部的 filteredDatasets 逻辑一致
// - 这里也提供一份是为了在 onTaskTypeFilterChange 等地方能直接读取, 避免重复计算
const filteredDatasets = computed(() => {
  const f = taskTypeFilter.value
  if (!f || f === 'all') return datasets.value
  return datasets.value.filter((d: any) => (d.task_type || 'classification') === f)
})
const aiLabeledCount = computed(() => {
  return (stats.value?.status_counts || {}).ai_labeled || 0
})
// v2.5.15: 已确认 / 已修正人工标注数 (status_counts 字段)
// - 用于顶部"已人工标注"统计卡, 与 DatasetDetail 口径保持一致
// - 这两个值在 detection.py/segmentation.py/annotation.py 保存时由后端写入 image.status
const humanConfirmedCount = computed(() => {
  return (stats.value?.status_counts || {}).human_confirmed || 0
})
const humanCorrectedCount = computed(() => {
  return (stats.value?.status_counts || {}).human_corrected || 0
})
// v3.0.0: 不合格标记状态 (从 image 派生, 供 3 个 Panel 使用)
const isUnqualified = computed(() => image.value?.quality_flag === 'unqualified')
const rejectReason = computed(() => image.value?.reject_reason || null)
// 类别下拉排序 (按 id 升序)
const sortedCategories = computed(() => {
  return [...categories.value].sort((a: any, b: any) => Number(a.id) - Number(b.id))
})
// 当前 dataset 的 task_type 元信息
const currentTaskTypeRaw = computed(() => {
  const ds = datasets.value.find((d: any) => d.id === datasetId.value)
  return ds?.task_type || 'classification'
})
// v2.5.9 新增: 当前图像的实际 task_type (用于左侧指导栏), 优先用 image.task_type
// - image 存在时取 image.task_type (用户从 URL 跳转过来时可能与 dataset 任务不同)
// - image 不存在时取 dataset 的 task_type (默认状态)
const currentImageTaskType = computed<'classification' | 'detection' | 'segmentation'>(() => {
  return (image.value?.task_type as any) || currentTaskTypeRaw.value
})
// 全部 AI 候选标签都不在项目 category 里 → 等同于基础模型 (ImageNet) 输出
// v3.5.0: findCategory 在 index.vue 内统一维护 (原 ClassificationPanel 内部函数, 提升到顶层供 allUnknown / 快捷键 / 分类提交复用)
const findCategory = (label: string) => {
  return categories.value.find((c: any) => c.name === label)
}
const allUnknown = computed(() => {
  if (!candidates.value.length) return false
  return candidates.value.every((c) => !findCategory(c.label))
})
// 上一张按钮是否可用
// v3.5.0: canGoPrev / autoSaveBeforeSwitch / loadNext / loadPrev 已抽离到 useAnnotateNav composable
// - 父组件通过解构返回值获取
// - 这里只保留 viewDataset 的本地实现 (router 已在 index.vue 顶层创建, composable 内复用即可)

/**
 * 按 item 填充 image / candidates / startTs, 按 task_type 拉取已有标注
 * 不动 historyCursor, 由调用方控制
 */
const fillImage = (item: any) => {
  // v3.6.1: 支持 fillImage(null), 用于「该状态无图」时清空所有 image 派生状态
  // - 调用方: useAnnotateNav.loadFirstOfStatus (无图分支)
  // - 行为: 清 image / candidates / bboxList, 不触发 detection/segmentation 详情加载
  if (!item) {
    image.value = null
    candidates.value = []
    bboxList.value = []
    revokeMaskUrl()
    return
  }
  image.value = item
  const aiPred = item.ai_prediction
  if (aiPred && Array.isArray(aiPred.top5)) {
    candidates.value = aiPred.top5.map((c: any) => ({ label: c.label, confidence: c.confidence }))
  } else {
    candidates.value = []
  }
  startTs.value = Date.now()
  // 重置画布数据
  bboxList.value = []
  revokeMaskUrl()
  // 按 task_type 拉取已有标注
  if (item?.id && item.task_type === 'detection') {
    loadDetectionAnnotations(item.id)
    loadCopySuggestion(item.id)
  } else if (item?.id && item.task_type === 'segmentation') {
    loadSegmentationMask(item.id)
  }
}

// ============== v3.5.0: 延迟初始化 nav composable (放在 fillImage / currentTaskTypeRaw 声明之后) ==============
// - composable 引用了 fillImage (const 函数) + currentTaskTypeRaw (computed), 必须在它们声明后才能调用
// - 之前位置 (在文件上方) 会触发 TS2448 "Block-scoped variable used before its declaration"
const {
  historyIds, historyCursor, noMore, canGoPrev,
  loadNext, loadPrev, loadSpecificImage, loadFirstOfStatus, viewDataset,
  resetHistory, cleanup: navCleanup,
} = useAnnotateNav({
  image, datasetId,
  detAnnotRef, segAnnotRef,
  detDirty, segDirty, annotatorSaving,
  currentTaskType: currentTaskTypeRaw as any,
  statusFilter, apiStatusParam,
  saveDetectionBBoxes, saveSegmentationMask,
  revokeMaskUrl, fillImage,
})

// 组件卸载清理: 回收分割 mask blob URL + 清除自动保存兜底定时器
onBeforeUnmount(() => {
  revokeMaskUrl()
  navCleanup()
})

// v3.5.0: loadNext / loadPrev / autoSaveBeforeSwitch / viewDataset 已在 useAnnotateNav composable 内实现

// ============== 工具函数 (refreshStats / 当前数据集名 / 返回顶部) ==============
async function refreshStats() {
  if (!datasetId.value) return
  try {
    const s: any = await annotationApi.stats(datasetId.value)
    stats.value = s
  } catch {}
}

// v2.5.44 新增: 当前数据集名称 (顶部返回按钮旁展示, 增强上下文)
const currentDatasetName = computed(() => {
  const ds = datasets.value.find((d: any) => d.id === datasetId.value)
  return ds?.name || ''
})
// v2.5.44 新增: 返回按钮 — 跳转到当前数据集的详情页 (/datasets/:id)
function goBackToDataset() {
  if (datasetId.value) router.push(`/datasets/${datasetId.value}`)
}

// v3.5.0: 检测/分割 AI 修正事件处理 (抽离到 useAnnotationAICorrection composable)
// - 父组件仅透传 ref + save 函数, composable 内完成弹窗 + save + loadNext 编排
const {
  onConfirmDetectionCorrection,
  onReAnnotateDetection,
  onConfirmSegmentationCorrection,
  onReAnnotateSegmentation,
} = useAnnotationAICorrection({
  image, detAnnotRef, segAnnotRef, annotatorSaving,
  bboxList, historyCursor, historyIds,
  saveDetectionBBoxes, revokeMaskUrl, refreshStats, loadNext,
})

/**
 * 「启动 AI 预标注」按钮: 按 task_type 分派
 * - classification -> runAutoAnnotate (同步, 不走 SSE)
 * - detection -> runDetectionAutoAnnotate (Celery + SSE 实时进度)
 * - segmentation -> runSegmentationAutoAnnotate (Celery + SSE 实时进度)
 * - autoLabelProgress / autoLabelProgressMessage 暴露给 AnnotationToolbar 展示进度条
 */
const {
  autoLabeling,
  autoLabelProgress,
  autoLabelProgressMessage,
  onStartAutoLabelClick,
} = useAutoAnnotate({
  datasetId, threshold, iouThreshold, useFinetune,
  selectedModelId, modelName, detectionModelName, segmentationModelName,
  finetuneModels, activeModel, refreshStats, loadNext,
})

/**
 * 分类任务提交标注 (确认 / 修正)
 * v3.0.0: 标注保存后端会清空不合格标记, 前端需同步更新本地 image.value
 * v3.4.0: 支持 comment 透传 (选填), 后端写入 AnnotationLog.payload.comment
 */
const submit = async (labelId: number, labelName: string, isConfirm: boolean, comment?: string) => {
  if (!image.value) return
  const cost = Date.now() - startTs.value
  try {
    const r: any = await annotationApi.saveWithComment({
      image_id: image.value.id,
      label_id: labelId,
      time_spent_ms: cost,
      is_confirm: isConfirm,
      comment: comment || undefined,
    })
    ElMessage.success(
      `${isConfirm ? '确认' : '修正'}「${labelName}」成功, 耗时 ${cost}ms`
    )
    sessionStats.value.total_time_ms += cost
    if (isConfirm) sessionStats.value.confirmed++
    else sessionStats.value.corrected++
    noMore.value = false
    // v3.0.0: 后端在 save 时已自动撤销不合格标记, 同步清空本地 image.value
    if (r?.auto_unmarked_unqualified) {
      image.value = {
        ...image.value,
        quality_flag: null,
        reject_reason: null,
        rejected_by: null,
        rejected_at: null,
      }
    }
    await refreshStats()
    loadNext()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== v3.5.0: 批量操作 + AI 修正事件 + 修正历史 (抽离到 useAnnotationBatch composable) ==============
// 状态自管 (viewImageList / viewTotal / viewTruncated / selectedIds / batchOperating / historyDialogVisible),
// 父组件通过返回的 ref + 方法与子组件交互, 保持单向数据流
// v3.5.0 P0-2: refreshViewList 改由 composable 内部调用 listIds, 不再从父组件传入
const {
  viewImageList, viewTotal, viewTruncated, selectedIds, batchOperating, historyDialogVisible, statusLabel,
  refreshViewList,
  onBatchClear, onBatchMarkUnqualified,
  onSelectAll, onClearSelection,
  openHistoryDialog, onRevertedFromHistory,
} = useAnnotationBatch({
  image, datasetId, statusFilter, apiStatusParam,
  refreshStats, loadNext, submit,
})

// ============== v3.5.0: 页面级键盘快捷键 ==============
// 仅在分类任务下启用「确认/修正」快捷键 (检测/分割的快捷键由子组件 useDetectionKeyboard 处理)
const confirmTop1Available = computed(() => {
  if (!image.value || currentTaskTypeRaw.value !== 'classification') return false
  if (!candidates.value.length) return false
  const top1 = candidates.value[0]?.label
  if (!top1) return false
  return !!findCategory(top1)
})
useAnnotationKeyboard({
  onNext: () => { if (!noMore.value) loadNext() },
  onPrev: () => { if (canGoPrev.value) loadPrev() },
  onConfirm: () => {
    if (!confirmTop1Available.value) return
    const top1 = candidates.value[0].label
    const cat = findCategory(top1)
    if (cat) submit(cat.id, cat.name, true, undefined)
  },
  onCorrect: () => {
    if (!confirmTop1Available.value) return
    const top1 = candidates.value[0].label
    const cat = findCategory(top1)
    if (cat) submit(cat.id, cat.name, false, '[快捷键] 强制采用 top1')
  },
  onMarkUnqualified: () => {
    if (image.value && !isUnqualified.value) onMarkUnqualified('blurry_or_unrecognizable', '')
  },
  onShowHistory: openHistoryDialog,
  canPrev: () => canGoPrev.value,
  canNext: () => !noMore.value,
})
</script>

<template>
  <div>
    <!-- v2.5.44 新增: 顶部"返回数据集详情"按钮 + 当前数据集名
         - 之前: 只能通过右侧 DetectionPanel/ClassificationPanel/SegmentationPanel
                 底部的"去数据集详情浏览全部图片"链接返回, 路径深, 用户反馈不便
         - 现在: 顶部第一行即可一键返回, 同时展示当前数据集名作为上下文锚点
         - 设计原则: 按钮用 plain + 小尺寸, 避免与下方主要操作 (AI 预标注/保存) 视觉争抢
         - datasetId 为空时按钮禁用, 避免跳到不存在的 /datasets/null 路由 -->
    <div class="annotate-topbar">
      <el-button
        size="small" plain :icon="ArrowLeft"
        :disabled="!datasetId"
        @click="goBackToDataset"
      >返回数据集详情</el-button>
      <span v-if="currentDatasetName" class="annotate-topbar-title">
        <el-icon style="vertical-align: -2px;"><Grid /></el-icon>
        {{ currentDatasetName }}
      </span>
      <span v-else class="annotate-topbar-title annotate-topbar-title--empty">
        未选择数据集
      </span>
    </div>

    <!-- 应用训练好的模型引导 -->
    <el-alert
      v-if="activeModel"
      type="success" :closable="false" show-icon
      :title="`当前已激活模型: ${activeModel.name} (基础模型 ${activeModel.base_model}, 准确率 ${(activeModel.accuracy * 100).toFixed(2)}%, 类别数 ${activeModel.num_classes})`"
      style="margin-bottom: 12px;"
    >
      <template #default>
        <div style="margin-top: 4px; font-size: 13px; line-height: 1.6;">
          <strong>如何应用训练好的模型:</strong>
          切换「使用项目训练模型」(是) →
          从下拉框选择本数据集的微调模型 (已激活) →
          点击「启动 AI 预标注」批量推理,
          候选标签会自动对齐到项目预设类目
          ({{ categories.map((c: any) => c.name).join(' / ') || '尚未配置类目' }})。
        </div>
      </template>
    </el-alert>
    <el-alert
      v-else
      type="info" :closable="false" show-icon
      title="当前项目还没有激活的模型"
      style="margin-bottom: 12px;"
    >
      <template #default>
        <div style="margin-top: 4px; font-size: 13px; line-height: 1.6;">
          流程:
          ① 上传数据集 → ② 标注几张图片 → ③ <el-link type="primary" :underline="'never'" @click="router.push('/training')">训练任务</el-link>
          训练首个 fine-tune 模型 → ④ <el-link type="primary" :underline="'never'" @click="router.push('/models')">模型版本</el-link>
          激活 → ⑤ 回到本页, 系统自动选择激活模型做 AI 预标注。
        </div>
      </template>
    </el-alert>

    <!-- 顶部统计 + 工具栏 (拆分到 AnnotationToolbar.vue) -->
    <AnnotationToolbar
      :datasets="datasets"
      :dataset-id="datasetId"
      :task-type-filter="taskTypeFilter"
      :current-task-type-raw="currentTaskTypeRaw"
      :model-name="modelName"
      :threshold="threshold"
      :iou-threshold="iouThreshold"
      :detection-model-name="detectionModelName"
      :segmentation-model-name="segmentationModelName"
      :models="models"
      :finetune-models="finetuneModels"
      :selected-model-id="selectedModelId"
      :active-model="activeModel"
      :use-finetune="useFinetune"
      :stats="stats"
      :pending-count="pendingCount"
      :ai-labeled-count="aiLabeledCount"
      :human-confirmed-count="humanConfirmedCount"
      :human-corrected-count="humanCorrectedCount"
      :categories="categories"
      :auto-labeling="autoLabeling"
      :auto-label-progress="autoLabelProgress"
      :auto-label-progress-message="autoLabelProgressMessage"
      :active-status-filter="statusFilter"
      @dataset-change="(v: number) => datasetId = v"
      @task-type-filter-change="onTaskTypeFilterChange"
      @threshold-change="(v: number) => threshold = v"
      @iou-threshold-change="(v: number) => iouThreshold = v"
      @detection-model-change="(v: string) => detectionModelName = v"
      @segmentation-model-change="(v: string) => segmentationModelName = v"
      @selected-model-change="(v: number | null) => selectedModelId = v"
      @model-name-change="(v: string) => modelName = v"
      @use-finetune-change="(v: boolean) => useFinetune = v"
      @ai-start="onStartAutoLabelClick(currentTaskTypeRaw)"
      @status-filter-change="(v: any) => setStatusFilter(v)"
    />

    <!-- v3.5.0: 批量操作条 (页面私有子组件, 仅在有图时显示)
         v3.5.0 P0-2: 展示「已加载/总数」, 超过 2000 张时显示截断提示 -->
    <AnnotationBatchBar
      :total-in-view="viewImageList.length"
      :view-total="viewTotal"
      :view-truncated="viewTruncated"
      :selected-count="selectedIds.length"
      :status-label="STATUS_FILTER_LABEL[statusFilter]"
      :batch-operating="batchOperating"
      @select-all="onSelectAll"
      @clear-selection="onClearSelection"
      @batch-clear="onBatchClear"
      @batch-mark-unqualified="onBatchMarkUnqualified"
    />

    <!-- 主体: 左侧操作指导 + 中间画布 + 右侧任务面板 (v2.5.9: 由 2 栏扩为 3 栏)
         v2.5.11 关键调整: 脱离 Element Plus 24 栏网格, 改用纯 flex 布局
         原因: 之前的左 266px(强制) + 中 span=14(58.3%) + 右 span=7(29.2%) 总和
         远超 100% 宽度 (266 + 0.583W + 0.292W = 266 + 0.875W > W, 需要 W >= 2384px 才不溢出)
         导致 el-row flex-wrap: wrap 把右栏挤到下一行, 视觉上"右栏不见"
         新方案: 左 266px 固定 / 右 340px 固定 / 中 flex:1 1 0 填满, flex-wrap: nowrap
         · 左侧固定 266px (含 Element Plus gutter 16px 的 8px×2 内边距, 可视卡片正好 250px)
         · 中间 flex: 1 1 0 (随行宽自适应, 画布 useCanvasSize 已支持响应式缩放)
         · 右侧固定 340px (含 16px gutter, 可视面板约 324px, 容纳检测 5 sections 不挤压)

         v2.5.12 关键调整: 高度从 min-height 改为 固定 height (calc(100vh - 360px))
         原因: 原 min-height: 600px + 画布 min-height: 480px + 画布内部 fixed 600×480 wrap
         会让 el-row 高度随画布实际渲染尺寸增长, 进而通过 align-items: stretch 把左/右栏一起撑高
         新方案: 用 calc(100vh - 360px) 锁定行高 (扣减 toolbar+alert+padding+面包屑 约 360px)
         · 高度由视口决定, 与图片实际像素无关, 三列永远等高
         · 列内部已用 height:100% + overflow:auto, 内容过长自动滚动 (左/右栏 body, 画布 wrap) -->
    <el-row :gutter="16" class="annotate-main-row" style="flex-wrap: nowrap;">
      <!-- 左侧: 操作指导栏 (固定可视宽度 250px)
           box-sizing: border-box + gutter 内边距 8px×2, 故 el-col 总宽 266px 即可 -->
      <el-col style="flex: 0 0 266px; max-width: 266px;">
        <AnnotationGuideSidebar :task-type="currentImageTaskType" />
      </el-col>
      <!-- 中间: 画布 (flex: 1 1 0 填满剩余宽度, min-width: 0 允许缩到 0 而非按内容撑开) -->
      <el-col style="flex: 1 1 0; min-width: 0;">
        <AnnotationCanvas :image="image" :loading="loading">
          <template v-if="image?.task_type === 'detection'">
            <DetectionAnnotator
              ref="detAnnotRef"
              :image-url="imageApi.fileUrl(image.id)"
              :image-id="image.id"
              :image-width="image.width || 0"
              :image-height="image.height || 0"
              :categories="categories"
              v-model="bboxList"
              :disabled="annotatorSaving"
              @save="saveDetectionBBoxes"
              @cancel="loadDetectionAnnotations(image.id)"
              @next="loadNext"
              @prev="loadPrev"
              @dirty-change="onDetDirtyChange"
            />
          </template>
          <template v-else-if="image?.task_type === 'segmentation'">
            <SegmentationAnnotator
              ref="segAnnotRef"
              :image-url="imageApi.fileUrl(image.id)"
              :image-id="image.id"
              :image-width="image.width || 0"
              :image-height="image.height || 0"
              :categories="categories"
              :initial-mask-url="initialMaskUrl"
              :disabled="annotatorSaving"
              @save="saveSegmentationMask"
              @cancel="loadSegmentationMask(image.id)"
              @dirty-change="onSegDirtyChange"
            />
          </template>
          <template v-else>
            <ClassificationAnnotator
              :image-url="imageApi.fileUrl(image.id)"
              :image-id="image.id"
              :image-width="image.width || 0"
              :image-height="image.height || 0"
              :filename="image.filename"
            />
          </template>
        </AnnotationCanvas>
      </el-col>
      <!-- 右侧: 任务面板 (固定可视宽度 324px = 340 - 16 gutter) -->
      <el-col style="flex: 0 0 340px; max-width: 340px;">
        <ClassificationPanel
          v-if="!image || image.task_type === 'classification'"
          :image="image"
          :candidates="candidates"
          :all-unknown="allUnknown"
          :categories="categories"
          :sorted-categories="sortedCategories"
          :can-go-prev="canGoPrev"
          :no-more="noMore"
          :is-unqualified="isUnqualified"
          :reject-reason="rejectReason"
          @submit="submit"
          @prev="loadPrev"
          @next="loadNext"
          @view-dataset="viewDataset"
          @mark-unqualified="onMarkUnqualified"
          @unmark-unqualified="onUnmarkUnqualified"
        />
        <!-- v2.5.14: 移除 @undo, @redo, @clear-draft, @cancel 监听
             撤销、重做、清空 改为 DetectionPanel 直接调 detAnnotRef 子组件方法
             取消功能被「撤销本次修改」按钮替代 -->
        <DetectionPanel
          v-else-if="image.task_type === 'detection'"
          :det-annot-ref="detAnnotRef"
          :bbox-list="bboxList"
          :det-dirty="detDirty"
          :annotator-saving="annotatorSaving"
          :sorted-categories="sortedCategories"
          :copy-suggestions="copySuggestions"
          :copy-suggestion-source-count="copySuggestionSourceCount"
          :can-go-prev="canGoPrev"
          :no-more="noMore"
          :history-cursor="historyCursor"
          :history-ids="historyIds"
          :image="image"
          :det-open-popover-idx="detOpenPopoverIdx"
          :is-unqualified="isUnqualified"
          :reject-reason="rejectReason"
          @save="() => saveDetectionBBoxes(bboxList)"
          @apply-copy-suggestions="applyCopySuggestions"
          @ignore-copy-suggestions="ignoreCopySuggestions"
          @prev="loadPrev"
          @next="loadNext"
          @view-dataset="viewDataset"
          @tag-click="onDetTagClick"
          @category-change="onDetCategoryChange"
          @popover-visible-change="onPopoverVisibleChange"
          @remove-bbox="(idx: number) => removeBboxByIndex(idx)"
          @target-category-change="onDetTargetCategoryChange"
          @mark-unqualified="onMarkUnqualified"
          @unmark-unqualified="onUnmarkUnqualified"
          @confirm-correction="onConfirmDetectionCorrection"
          @re-annotate="onReAnnotateDetection"
        />
        <SegmentationPanel
          v-else-if="image.task_type === 'segmentation'"
          :seg-annot-ref="segAnnotRef"
          :seg-dirty="segDirty"
          :annotator-saving="annotatorSaving"
          :categories="categories"
          :seg-mode="segMode"
          :can-go-prev="canGoPrev"
          :no-more="noMore"
          :history-cursor="historyCursor"
          :history-ids="historyIds"
          :image="image"
          :is-unqualified="isUnqualified"
          :reject-reason="rejectReason"
          :initial-mask-url="initialMaskUrl"
          @mode-change="onSegModeChange"
          @category-change="onSegCategoryChange"
          @brush-size-change="onSegBrushSizeChange"
          @prev="loadPrev"
          @next="loadNext"
          @view-dataset="viewDataset"
          @save="onSegSave"
          @cancel="onSegClear"
          @mark-unqualified="onMarkUnqualified"
          @unmark-unqualified="onUnmarkUnqualified"
          @confirm-correction="onConfirmSegmentationCorrection"
          @re-annotate="onReAnnotateSegmentation"
        />
      </el-col>
    </el-row>

    <!-- v3.5.0: 修正历史弹窗 (业务通用组件, 单图完整历史 + 恢复 AI 预测)
         - 按 'h' 键或主操作区"查看历史"按钮触发
         - 关闭不影响工作台状态, 仅展示历史数据 -->
    <CorrectionHistoryDialog
      v-if="image"
      v-model="historyDialogVisible"
      :image-id="image.id"
      @reverted="onRevertedFromHistory"
    />
  </div>
</template>

<style scoped>
/* v2.5.7: Annotate.vue 主体仅保留 1 处样式 (其余样式已迁到子组件) */
.annotate-canvas {
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* v2.5.12: 三列主体行 — 固定高度 (calc(100vh - 360px))
   · 360px = 顶部 alert(80) + toolbar(120) + breadcrumb(40) + page padding(80) + 安全余量(40)
   · 不用 min-height, 避免画布实际渲染尺寸把整行撑高, 进而把左/右栏一起拉伸
   · 三列 el-col 通过 height:100% 继承此高度, 内部用 overflow:auto 各自滚动 */
.annotate-main-row {
  height: calc(100vh - 360px);
  min-height: 500px;     /* 兜底: 极窄窗口下不至于过小, 但仍允许 row 在 500~视口-360 之间 */
  max-height: 900px;     /* 兜底: 超大窗口下不至于过高, 避免右栏空白过多 */
}
/* 让 el-col 子项也继承 100% 高度 (el-row 默认 align-items: stretch 已能撑开,
   但显式写 height:100% 防止某些 flex 容器算高度时漏算) */
.annotate-main-row :deep(.el-col) {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* v2.5.44: 顶部"返回按钮 + 数据集名"行
   - 独立一行, 与下方 el-alert 之间保留 12px 间距 (el-alert 自带 margin-bottom: 12px)
   - 返回按钮用 plain + small, 不与主要操作按钮争抢视觉权重
   - 右侧数据集名用细体 + 浅灰, 作为上下文标识, 不喧宾夺主 */
.annotate-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;       /* 紧贴下方 el-alert, 让 el-alert 的 12px 自然撑开间距 */
  min-height: 32px;          /* 避免数据集名为空时整行塌缩 */
}
.annotate-topbar-title {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  letter-spacing: 0.3px;
}
.annotate-topbar-title--empty {
  color: #c0c4cc;
  font-style: italic;
  font-weight: 400;
}
</style>
