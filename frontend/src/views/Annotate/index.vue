<script setup lang="ts">
/**
 * Annotate.vue - 人工标注工作台 (v2.5.7 重构版)
 * ==================================================
 * - 选择数据集 → 显示下一张待标注图
 * - 显示 AI Top-5 候选 + 确认/修正
 * - 实时统计 + 已标注计数
 * - 可跳转到 DatasetDetail 浏览已标注图片
 * - 「使用项目训练模型」开关 ON 时, 显示项目微调模型下拉 (默认=激活的)
 * - 显示当前激活的模型名 + 训练后引导用户到标注页
 *
 * v2.5.7 重构:
 * - 顶部统计 + 工具栏 → AnnotationToolbar.vue
 * - 画布壳 → AnnotationCanvas.vue (slot 注入 3 个 annotator)
 * - 3 个任务右侧面板 → ClassificationPanel / DetectionPanel / SegmentationPanel
 * - 检测 / 分割 / AI 预标注业务逻辑 → composables
 *   · useDetectionAnnotate (bboxList, 复制建议, 保存, popover)
 *   · useSegmentationAnnotate (initialMaskUrl, 模式, 保存)
 *   · useAutoAnnotate (runAutoAnnotate, runDetectionAutoAnnotate)
 */
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { annotationApi, imageApi, autoAnnotateApi, datasetApi, modelApi } from '@/api'
import { useDetectionAnnotate } from '@/composables/useDetectionAnnotate'
import { useSegmentationAnnotate } from '@/composables/useSegmentationAnnotate'
import { useAutoAnnotate } from '@/composables/useAutoAnnotate'
// v2.5.8 架构优化: 业务组件全部迁入当前页面私有目录, 引用统一使用相对路径
import DetectionAnnotator from './components/DetectionAnnotator.vue'
import SegmentationAnnotator from './components/SegmentationAnnotator.vue'
import ClassificationAnnotator from './components/ClassificationAnnotator.vue'
import ClassificationPanel from './components/ClassificationPanel.vue'
import DetectionPanel from './components/DetectionPanel.vue'
import SegmentationPanel from './components/SegmentationPanel.vue'
import AnnotationToolbar from './components/AnnotationToolbar.vue'
import AnnotationCanvas from './components/AnnotationCanvas.vue'
// v2.5.9 新增: 标注工作台左侧"操作指导"侧栏
import AnnotationGuideSidebar from './components/AnnotationGuideSidebar.vue'
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
const taskTypeFilter = ref<string>('classification')
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
  // v2.5.15: 检测保存成功后立刻重拉 stats
  // - 后端 save_bbox 会把 image.status 提升到 human_confirmed
  // - 前端需主动刷新才能让"待标注"数字减少
  onSaved: refreshStats,
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
  // v2.5.15: 分割保存成功后立刻重拉 stats
  // - 后端 upload_mask 会把 image.status 提升到 human_confirmed
  // - 前端需主动刷新才能让"待标注"数字减少
  onSaved: refreshStats,
})

// ============== 浏览历史栈 (按访问顺序记录看过的 image id) ==============
const historyIds = ref<number[]>([])
const historyCursor = ref(-1)
// 是否已到末尾 (栈顶时后端 list 返回空, 没有更多待标注图)
const noMore = ref(false)
// autoSaveBeforeSwitch 分割分支的兜底超时句柄，卸载时需清理避免回调在卸载后执行
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

// 组件卸载清理: 回收分割 mask blob URL + 清除自动保存兜底定时器，避免内存泄漏
onBeforeUnmount(() => {
  revokeMaskUrl()
  if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null }
})

onMounted(async () => {
  try {
    const ds: any = await datasetApi.list()
    datasets.value = ds?.items || ds || []
    if (route.params?.datasetId) {
      datasetId.value = Number(route.params.datasetId)
    } else if (datasets.value.length > 0) {
      datasetId.value = datasets.value[0].id
    }
    // v2.5.19: 初始化 taskTypeFilter, 跟当前 datasetId 的 task_type 保持一致
    // - 避免出现"选了 detection 数据集, 但筛选框停在 all"的割裂感
    // - 用户后续可手动切换筛选
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

watch(datasetId, async (v) => {
  if (!v) return
  sessionStats.value = { confirmed: 0, corrected: 0, total_time_ms: 0 }
  // 切换 dataset 时, 清空浏览历史 (新 dataset 的 id 集合不同)
  historyIds.value = []
  historyCursor.value = -1
  noMore.value = false
  image.value = null
  candidates.value = []
  revokeMaskUrl()
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
  // v2.5.44: 优先消费 URL 上的 ?imageId= query (来自数据集详情页"去标注"按钮)
  // - 存在则调 loadSpecificImage, 跳过 loadNext, 工作台首张图即用户勾选的「待标注」图
  // - 加载成功后清掉 query, 避免后续 dataset 切换时再次误用
  // - 加载失败 (例如该 imageId 不属于当前 dataset / 已被删除) 回退到 loadNext
  const targetImageId = Number(route.query.imageId)
  if (targetImageId && !Number.isNaN(targetImageId)) {
    const ok = await loadSpecificImage(targetImageId)
    if (ok) {
      // 清除 query, 避免后续 watch(datasetId) 再次触发同一 imageId 加载
      router.replace({ query: {} })
      return
    }
  }
  loadNext()
})

/**
 * v2.5.44: 加载指定的 imageId 作为当前图
 * - 用于支持「数据集详情页勾选 N 张图, 点击去标注, 工作台默认显示选中区域第一张待标注图」
 * - 实现: 调 imageApi.detail 拉详情, 推入 historyIds 头, cursor=0
 *   之后用户点「下一张」会从后端拉新图 (排除 historyIds), 流转顺畅
 * - 返回值: true=成功, false=失败 (后端报错 / 数据不合法)
 *   失败时 caller 决定回退策略 (目前是回退到 loadNext)
 */
const loadSpecificImage = async (imageId: number): Promise<boolean> => {
  if (!datasetId.value) return false
  loading.value = true
  try {
    const detail: any = await imageApi.detail(imageId)
    // 基础校验: 详情必须属于当前 dataset, 否则视为失败 (避免跨 dataset 误显示)
    if (detail?.dataset_id && Number(detail.dataset_id) !== Number(datasetId.value)) {
      ElMessage.warning('指定的图片不属于当前数据集, 已回退到默认加载')
      return false
    }
    // 推入 history 栈头, cursor=0
    historyIds.value = [imageId]
    historyCursor.value = 0
    fillImage(detail)
    return true
  } catch (e: any) {
    ElMessage.error(
      '加载指定图片失败, 已回退到默认加载: ' +
      (e?.response?.data?.detail || e?.message)
    )
    return false
  } finally {
    loading.value = false
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
const allUnknown = computed(() => {
  if (!candidates.value.length) return false
  return candidates.value.every((c) => !findCategory(c.label))
})
// 上一张按钮是否可用
const canGoPrev = computed(() => historyCursor.value > 0)
// v2.5.44 新增: 当前数据集名称 (顶部返回按钮旁展示, 增强上下文)
const currentDatasetName = computed(() => {
  const ds = datasets.value.find((d: any) => d.id === datasetId.value)
  return ds?.name || ''
})
// v2.5.44 新增: 返回按钮 — 跳转到当前数据集的详情页 (/datasets/:id)
// - 仅在 datasetId 存在时启用, 避免空态时跳到不存在的路由
// - 与右侧 viewDataset 行为一致, 但放在顶部更醒目
function goBackToDataset() {
  if (datasetId.value) router.push(`/datasets/${datasetId.value}`)
}

async function refreshStats() {
  if (!datasetId.value) return
  try {
    const s: any = await annotationApi.stats(datasetId.value)
    stats.value = s
  } catch {}
}

/**
 * 按 item 填充 image / candidates / startTs, 按 task_type 拉取已有标注
 * 不动 historyCursor, 由调用方控制
 */
const fillImage = (item: any) => {
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

/**
 * 切图前自动保存检测/分割标注 (避免用户画了 bbox/mask 没点保存就跳走)
 * - dirty + 是检测任务 -> 调子组件 save
 * - dirty + 是分割任务 -> 调子组件 save (Promise 间接通过 watch segDirty)
 */
const autoSaveBeforeSwitch = async (): Promise<boolean> => {
  if (currentTaskTypeRaw.value === 'classification') return true
  if (currentTaskTypeRaw.value === 'detection') {
    if (!detAnnotRef.value) return true
    if (!detDirty.value) return true
    try {
      annotatorSaving.value = true
      const cur = detAnnotRef.value.modelValue || []
      await saveDetectionBBoxes(cur)
      return true
    } catch (e: any) {
      ElMessage.error('自动保存失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      annotatorSaving.value = false
    }
  }
  if (currentTaskTypeRaw.value === 'segmentation') {
    if (!segAnnotRef.value) return true
    if (!segDirty.value) return true
    return new Promise<boolean>((resolve) => {
      const stop = watch(
        segDirty,
        (v) => {
          if (!v) { stop(); resolve(true) }
        },
        { flush: 'sync' }
      )
      autoSaveTimer = setTimeout(() => { stop(); resolve(true) }, 5000)
      segAnnotRef.value?.save?.()
    })
  }
  return true
}

/**
 * 「下一张」逻辑:
 * 1. 在历史栈中间 → cursor++ 直接拿历史图, 不发请求 (浏览器行为)
 * 2. 已在栈顶 → 调后端 list (排除整个 history) 拉新图, 推入栈尾
 */
const loadNext = async () => {
  if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
  const ok = await autoSaveBeforeSwitch()
  if (!ok) return
  // 情况 1: 历史栈中间, 直接前进
  if (historyCursor.value < historyIds.value.length - 1) {
    historyCursor.value++
    const id = historyIds.value[historyCursor.value]
    loading.value = true
    try {
      const detail: any = await imageApi.detail(id)
      fillImage(detail)
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      loading.value = false
    }
    return
  }
  // 情况 2: 栈顶, 拉新图
  loading.value = true
  try {
    const excludeIdsParam = historyIds.value.length > 0
      ? historyIds.value.join(',')
      : undefined
    const resp: any = await imageApi.list(datasetId.value, {
      status: 'pending',
      page: 1,
      page_size: 1,
      exclude_ids: excludeIdsParam,
    })
    const items = resp?.items || []
    const item = items[0]
    if (!item) {
      noMore.value = true
      if (historyIds.value.length > 0) {
        ElMessage.warning({
          message: '已经是最后一张了, 没有更多待标注图片。可点击「启动 AI 预标注」让 AI 继续标注。',
          duration: 3500,
          showClose: true,
        })
      } else {
        ElMessage.info('当前数据集没有待标注的图片')
      }
      return
    }
    noMore.value = false
    historyIds.value.push(item.id)
    historyCursor.value = historyIds.value.length - 1
    fillImage(item)
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

/**
 * 「上一张」逻辑: cursor--, 从 history 拿图, 调 detail 拉最新数据
 */
const loadPrev = async () => {
  if (historyCursor.value <= 0) {
    ElMessage.info('已经是第一张了')
    return
  }
  const ok = await autoSaveBeforeSwitch()
  if (!ok) return
  historyCursor.value--
  noMore.value = false
  const prevId = historyIds.value[historyCursor.value]
  loading.value = true
  try {
    const detail: any = await imageApi.detail(prevId)
    fillImage(detail)
  } catch (e: any) {
    historyIds.value.splice(historyCursor.value, 1)
    historyCursor.value++
    ElMessage.error('加载上一张失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

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
 */
const submit = async (labelId: number, labelName: string, isConfirm: boolean) => {
  if (!image.value) return
  const cost = Date.now() - startTs.value
  try {
    await annotationApi.save({
      image_id: image.value.id,
      label_id: labelId,
      time_spent_ms: cost,
      is_confirm: isConfirm
    })
    ElMessage.success(
      `${isConfirm ? '确认' : '修正'}「${labelName}」成功, 耗时 ${cost}ms`
    )
    sessionStats.value.total_time_ms += cost
    if (isConfirm) sessionStats.value.confirmed++
    else sessionStats.value.corrected++
    noMore.value = false
    await refreshStats()
    loadNext()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const viewDataset = () => {
  if (datasetId.value) router.push(`/datasets/${datasetId.value}`)
}

const findCategory = (label: string) => categories.value.find((c) => c.name === label)
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
      :session-stats="sessionStats"
      :auto-labeling="autoLabeling"
      :auto-label-progress="autoLabelProgress"
      :auto-label-progress-message="autoLabelProgressMessage"
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
          @submit="submit"
          @prev="loadPrev"
          @next="loadNext"
          @view-dataset="viewDataset"
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
          @mode-change="onSegModeChange"
          @category-change="onSegCategoryChange"
          @brush-size-change="onSegBrushSizeChange"
          @prev="loadPrev"
          @next="loadNext"
          @view-dataset="viewDataset"
          @save="onSegSave"
          @cancel="onSegClear"
        />
      </el-col>
    </el-row>
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
