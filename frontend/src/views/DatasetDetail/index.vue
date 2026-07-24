<script setup lang="ts">
/**
 * DatasetDetail - 数据集详情页
 * 顶部: 数据集基本信息 + 统计
 * 中部: 状态过滤 + 图像网格 (缩略图 + 文件名 + 大小 + 状态 + AI top-1 + 最终类别)
 * 弹窗: 点击图像 → AnnotationViewer (标注查看器)
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, Refresh, Delete, Download, Lightning, Search, Picture, Document, UploadFilled, RefreshLeft, View, Grid, List,
  CircleCheck, CircleClose, CollectionTag, Check, Minus, InfoFilled, DataAnalysis, EditPen,
  WarningFilled, Promotion
} from '@element-plus/icons-vue'
import {
  datasetApi, imageApi, annotationApi, autoAnnotateApi, exportApi, statsApi, modelApi
} from '@/api'
// v2.5.8 架构优化: 业务组件全部迁入当前页面私有目录
import AnnotationViewer from './components/AnnotationViewer.vue'
import UploadQueue from '../Datasets/components/UploadQueue.vue'  // 与 Datasets 列表页共享同一组件 (同模块复用)
import PreviewList from './components/PreviewList.vue'
import { getTaskTypeMeta } from '@/utils/taskType'

const route = useRoute()
const router = useRouter()
const datasetId = computed(() => Number(route.params.id))

const dataset = ref<any>(null)
const images = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(24)
const pageSizes = ref([12, 24, 48, 96])
const statusFilter = ref<string>('all')   // 'all' = 全部 (element-plus el-select 不把空字符串 v-model 视为"已选中", 用 'all' 让默认 label "全部" 渲染)
const keyword = ref('')
const loading = ref(false)
const stats = ref<any>(null)
// v2.5.49: 数据集类目列表 (供「类别数」卡 + hover 各类进度详情)
// - 来源: datasetApi.categories(v) 返回 {items: [{id, name, color, sample_count, human_labeled_count, ai_labeled_count, ...}]}
// - 与 Annotate 页面同源, 保证两个页面的「类别数」hover 详情口径一致
const categories = ref<any[]>([])
const selectedIds = ref<number[]>([])
// 修复: v-model 必须是 Boolean, 不能复用 number 类型的 imageId
// 拆成 viewerOpen (boolean) + viewerImageId (number | null) 两个状态
const viewerOpen = ref(false)
const viewerImageId = ref<number | null>(null)
const autoLabeling = ref(false)
// ========== AI 预标注模型选择: 仅 Fine-tune 模型 ==========
// 详情页批量预标注统一走项目自训练的 fine-tune 模型, 命中率高且输出对齐项目类目
// 若项目无 fine-tune 模型, 后端 /api/images/auto-label?use_finetune=true 会自动
// 回退到 timm ImageNet 预训练 (冷启动), 无需前端额外处理
const finetuneModels = ref<any[]>([])     // 本数据集已训练出的 fine-tune 模型
const selectedFinetuneId = ref<number | null>(null)  // 当前选中的 fine-tune ModelVersion.id
const activeModel = ref<any>(null)        // 当前数据集激活的 fine-tune 模型 (与 Annotate 命名一致)
const threshold = ref(0.6)
const uploadOpen = ref(false)
// 视图模式: grid (默认) / list
const viewMode = ref<'grid' | 'list'>('grid')

const statusOptions = [
  // 注意: value 不能用 '' (空串), element-plus el-select 把 v-model='' 视为 unselected,
  // 不匹配任何 option, 默认显示 placeholder 而非 label. 用 'all' 占位 + load 时翻译成 undefined.
  { value: 'all', label: '全部', color: '#909399' },
  { value: 'pending', label: '待标注', color: '#909399' },
  { value: 'ai_labeled', label: 'AI 已标', color: '#409eff' },
  { value: 'human_confirmed', label: '已确认', color: '#67c23a' },
  { value: 'human_corrected', label: '已修正', color: '#e6a23c' }
]

async function load() {
  if (!datasetId.value) return
  loading.value = true
  try {
    // v2.5.49: 并行拉取 categories (供「类别数」卡 hover 详情)
    // - 与原有 4 路请求合并为 5 路 Promise.all, 仍是 1 次 RTT
    const [d, list, s, actResp, catsResp]: any[] = await Promise.all([
      datasetApi.get(datasetId.value),
      imageApi.list(datasetId.value, {
        // 'all' 翻译成 undefined (不传 status 参数, 后端返所有)
        status: statusFilter.value === 'all' ? undefined : statusFilter.value,
        page: page.value,
        page_size: pageSize.value
      }),
      statsApi.dataset(datasetId.value).catch(() => null),
      // 与 Annotate 一致: 拿当前数据集的激活模型, 用于 tooltip 提示
      modelApi.getActive(datasetId.value).catch(() => ({ model: null })),
      // v2.5.49: 类目列表 (含实时 sample_count / human_labeled_count / ai_labeled_count)
      datasetApi.categories(datasetId.value).catch(() => null),
    ])
    dataset.value = d
    images.value = list?.items || []
    total.value = list?.total || 0
    stats.value = s
    activeModel.value = actResp?.model || actResp?.items?.[0] || null
    const c: any = catsResp
    categories.value = c?.items || c || []

    // 模型下拉列表: 与标注工作台 (Annotate.vue) 完全对齐
    // - 仅显示本数据集**已激活**的 fine-tune 模型
    // - 无激活时下拉禁用 (与 Annotate 行为一致)
    await loadFinetuneModels()
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

/**
 * 加载本数据集已激活的 fine-tune 模型下拉列表
 * 与 Annotate.vue 的 refreshFinetuneModels 行为完全一致:
 * - 仅显示已激活模型
 * - 默认选择: 已选仍存在 > 第一个
 * - 无激活时清空下拉, 由模板 disabled 控制禁用态
 */
async function loadFinetuneModels() {
  const did = datasetId.value
  if (!did) {
    finetuneModels.value = []
    selectedFinetuneId.value = null
    return
  }
  try {
    // 与 Annotate 一致: 先 list+active=true, 兜底 getActive
    let items: any[] = []
    try {
      const r: any = await modelApi.list({ dataset_id: did, active: true })
      items = r?.items || r || []
    } catch {
      items = []
    }
    if (items.length === 0) {
      const r: any = await modelApi.getActive(did)
      items = r?.items || (r?.model ? [r.model] : [])
    }
    finetuneModels.value = items
    // 默认选择: 已选仍存在 > 第一个; 否则清空
    if (items.length > 0) {
      const prev = selectedFinetuneId.value
      const stillExists = items.find((m: any) => m.id === prev)
      selectedFinetuneId.value = stillExists ? prev : items[0].id
    } else {
      selectedFinetuneId.value = null
    }
  } catch {
    finetuneModels.value = []
    selectedFinetuneId.value = null
  }
}

const filteredImages = computed(() => {
  if (!keyword.value.trim()) return images.value
  const kw = keyword.value.toLowerCase()
  return images.value.filter((img) =>
    (img.filename || '').toLowerCase().includes(kw) ||
    (img.final_label_name || '').toLowerCase().includes(kw) ||
    (img.ai_prediction?.top1 || '').toLowerCase().includes(kw)
  )
})

function resetPage() {
  page.value = 1
  selectedIds.value = []
  load()
}

function changePage(p: number) {
  page.value = p
  selectedIds.value = []
  load()
}

function onSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  selectedIds.value = []
  load()
}

function openViewer(imgId: number) {
  viewerImageId.value = imgId
  viewerOpen.value = true
}

/**
 * 点击图片卡片: 切换选中状态 (用于批量操作)
 * 之前行为: 直接弹出图片详情; 改为: 默认勾选, 详情走右上角图标按钮
 */
function toggleSelect(imgId: number) {
  if (selectedIds.value.includes(imgId)) {
    selectedIds.value = selectedIds.value.filter((id) => id !== imgId)
  } else {
    selectedIds.value = [...selectedIds.value, imgId]
  }
}

/**
 * 全选 / 取消全选: 基于当前过滤后可见的图像列表 (filteredImages)
 * - 全部已选 → 取消全选
 * - 部分或未选 → 全选当前页可见的图
 */
const allOnPageSelected = computed(
  () => filteredImages.value.length > 0 &&
        filteredImages.value.every((img) => selectedIds.value.includes(img.id))
)
function toggleSelectAll() {
  if (allOnPageSelected.value) {
    // 取消全选: 只清掉当前可见的, 保留其他页已选的
    const visibleIds = new Set(filteredImages.value.map((img) => img.id))
    selectedIds.value = selectedIds.value.filter((id) => !visibleIds.has(id))
  } else {
    // 全选: 把当前可见的全部加入
    const set = new Set(selectedIds.value)
    filteredImages.value.forEach((img) => set.add(img.id))
    selectedIds.value = Array.from(set)
  }
}

/**
 * "待标注" 图片数: AI 预标注的输入目标
 * - status=pending 的图才会被 batch_predict 处理
 * - 实时显示在 AI 配置提示中, 帮用户感知阈值/模型选择的影响
 */
const pendingCount = computed(
  () => images.value.filter((img) => img.status === 'pending').length
)

/**
 * 当前生效模型的显示文案 (用于 AI 配置提示行)
 * - 选中 fine-tune: 显示「<name> (基础模型 resnet50, 准确率 92.4%)」
 * - 未选 (无 fine-tune): 显示「暂未训练, AI 预标注将自动走 timm 冷启动」
 */
const displayModel = computed(() => {
  const m = selectedFinetuneModel.value
  if (m) {
    const acc = ((m.accuracy || 0) * 100).toFixed(1)
    return `${m.name} (基础 ${m.base_model}, 准确率 ${acc}%)`
  }
  return '暂未训练 fine-tune 模型, 将自动回退 timm 预训练 (冷启动)'
})

/**
 * 当前选中的 fine-tune 模型对象 (computed, 用于多处展示)
 * - 模板中用来生成 el-option 的 label (含基础模型)
 * - 模板中可绑定 :label="{...}" 让 el-select 关闭态也展示「基础模型」标签
 */
const selectedFinetuneModel = computed(
  () => finetuneModels.value.find((m: any) => m.id === selectedFinetuneId.value) || null
)

/**
 * 是否存在本数据集的 fine-tune 模型
 * - false 时: 测评按钮 disabled + 友好提示, 引导去训练
 * - true 时:  正常测评
 */
const hasFinetuneModel = computed(() => finetuneModels.value.length > 0)

/**
 * v2.5.45: 当前数据集的任务类型判定
 * - 用于条件渲染「测评」按钮等任务类型相关操作
 * - dataset.value 可能在 load 完成前为 null, 默认值走 'classification' 保持向后兼容
 * - 返回固定联合类型, 避免下游 PreviewList 等组件出现 TS2322
 */
const currentDatasetTaskType = computed<'classification' | 'detection' | 'segmentation'>(
  () => {
    const tt = (dataset.value?.task_type as string) || 'classification'
    if (tt === 'detection' || tt === 'segmentation') return tt
    return 'classification'
  }
)

/**
 * v2.5.46: 测评按钮的 tooltip 文案 — 按任务类型动态切换
 * - 分类: top-1 标签 + 置信度 + top-3 候选
 * - 检测: bbox 计数 + 最高置信度 + bbox 类别摘要
 * - 分割: 最大 softmax + 像素级 mask
 * 无 fine-tune 模型时统一走"无模型引导"提示
 */
const previewTooltipText = computed(() => {
  if (!hasFinetuneModel.value) {
    return '该数据集暂无训练模型, 请先到「训练任务」页选定该数据集启动训练'
  }
  const tt = currentDatasetTaskType.value
  if (tt === 'detection') {
    return 'dry-run 试跑当前页图片, 不写库, 弹窗显示 bbox 计数 + 最高置信度 + 类目摘要, 帮你在执行批量预标注前评估阈值是否合适'
  }
  if (tt === 'segmentation') {
    return 'dry-run 试跑当前页图片, 不写库, 弹窗显示每张图的最大 softmax 与是否会被落标, 帮你在执行批量预标注前评估阈值是否合适'
  }
  return 'dry-run 试跑当前页图片, 不写库, 弹窗显示 3 类: 会标/待标/无交集, 帮你在执行批量预标注前评估阈值是否合适'
})

function onSelectionChange(rows: any[]) {
  selectedIds.value = rows.map((r) => r.id)
}

// ============== 阈值测评 (非破坏性) ==============
// 不修改任何图片状态, 仅展示模型对当前页图片的 top-1 预测, 让用户在执行批量预标注前
// 评估"在当前阈值下, 哪些图会被自动标注, 哪些会留在待标注, 哪些与项目类目无交集"
const previewing = ref(false)
const previewDialogVisible = ref(false)
const previewResult = ref<any | null>(null)

async function onPreviewConfidence() {
  if (!datasetId.value) return
  if (images.value.length === 0) {
    ElMessage.warning('当前页没有图片, 请调整过滤条件或翻页')
    return
  }
  // 硬性约束: 测评**必须**用本数据集训练出的 fine-tune 模型
  // 防止用户用 timm ImageNet 基础模型测评出与项目业务无关的结果
  if (!hasFinetuneModel.value || !selectedFinetuneId.value) {
    ElMessageBox.confirm(
      '该数据集暂无训练模型, 无法进行置信度测评。\n请先到「训练任务」页选定该数据集启动训练, 完成后即可用本数据集专属模型测评。',
      '缺少数据集模型',
      {
        type: 'warning',
        confirmButtonText: '前往训练任务',
        cancelButtonText: '稍后再说',
      }
    )
      .then(() => router.push('/training'))
      .catch(() => { /* 用户取消 */ })
    return
  }
  previewing.value = true
  try {
    const ids = images.value.map((img: any) => img.id)
    const res: any = await imageApi.previewConfidence(datasetId.value, ids, {
      model_id: selectedFinetuneId.value,
      confidence_threshold: threshold.value,
      use_finetune: true,
    })
    previewResult.value = res
    previewDialogVisible.value = true
  } catch (e: any) {
    ElMessage.error('测评失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    previewing.value = false
  }
}

/** 按 reason 分组, 用于弹窗内 Tab 切换 */
const previewGroups = computed(() => {
  const r = previewResult.value
  if (!r) return { would: [] as any[], human: [] as any[], none: [] as any[] }
  return {
    would: r.items.filter((x: any) => x.would_label),
    human: r.items.filter((x: any) => !x.would_label && x.reason !== 'no_match'),
    none:  r.items.filter((x: any) => x.reason === 'no_match'),
  }
})
const previewActiveTab = ref('would')

function previewReasonLabel(reason: string): string {
  return {
    would_label: '≥ 阈值, 会被自动标注',
    below_threshold: '低于阈值, 保留待人工',
    not_in_categories: '不在项目类目',
    no_match: '模型输出与项目类目无交集',
  }[reason] || reason
}

function previewReasonType(reason: string): 'success' | 'warning' | 'info' | 'danger' {
  const t: Record<string, 'success' | 'warning' | 'info' | 'danger'> = {
    would_label: 'success',
    below_threshold: 'warning',
    not_in_categories: 'info',
    no_match: 'danger',
  }
  return t[reason] || 'info'
}

async function onAutoAnnotate() {
  if (!datasetId.value) return
  // 友好提示: 项目无 fine-tune 模型时, 后端会自动走 timm 冷启动
  if (finetuneModels.value.length === 0) {
    ElMessage.info('该项目暂无 fine-tune 模型, AI 预标注将自动回退到 timm ImageNet 预训练 (冷启动兜底)')
  }
  autoLabeling.value = true
  try {
    // 统一走 /api/images/auto-label 接口:
    //   - use_finetune=true 时: 后端优先用 model_id 指定的 fine-tune; 缺省 = 激活
    //   - 无任何 fine-tune 时: 后端自动 fallback 到 timm pretraining (冷启动)
    //   - 不再调用旧的 /auto-annotate/run (已统一到 auto-label)
    const res: any = await autoAnnotateApi.autoLabel(datasetId.value, {
      model_id: selectedFinetuneId.value ?? undefined,
      confidence_threshold: threshold.value,
      use_finetune: true,
    })
    const usedName = res.model_name || (selectedFinetuneModel.value?.name) || 'timm 预训练 (冷启动)'
    const noMatch = res.no_match || 0
    const noMatchTip = noMatch > 0
      ? `, 无匹配 ${noMatch} 张 (输出与项目类目无交集, 已保持待标注)`
      : ''
    ElMessage.success(
      `[${usedName}] 共 ${res.total} 张, 命中 ${res.auto_labeled} 张, ` +
      `需人工 ${res.need_human} 张${noMatchTip}, 平均置信度 ${(res.avg_confidence * 100).toFixed(1)}%`
    )
    await load()
  } catch (e: any) {
    ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    autoLabeling.value = false
  }
}

async function batchDelete() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择图片')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedIds.value.length} 张图片? 此操作不可恢复`,
      '危险操作',
      { type: 'warning' }
    )
  } catch { return }
  try {
    const r: any = await imageApi.batchRemove(selectedIds.value)
    ElMessage.success(`已删除 ${r.deleted} 张${r.missing > 0 ? `, 缺失 ${r.missing} 张` : ''}`)
    selectedIds.value = []
    await load()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

async function deleteOne(img: any) {
  try {
    await ElMessageBox.confirm(`确认删除「${img.filename}」?`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await imageApi.remove(img.id)
    ElMessage.success('已删除')
    selectedIds.value = selectedIds.value.filter((id) => id !== img.id)
    await load()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/** 单张去除标注 (人工 + AI 预标注 + 检测 bbox + 分割 mask) */
async function clearOneAnnotation(img: any) {
  // v2.5.17: 用统一 hasAnnotation 判断, 兼容 detection/segmentation 老数据
  if (!hasAnnotation(img)) {
    ElMessage.warning('该图片尚无标注, 无需去除')
    return
  }
  const hadAi = img.status === 'ai_labeled' && img.ai_prediction
  // 提示文案根据 task_type 区分
  const tt: string = img.task_type || 'classification'
  const tipMap: Record<string, string> = {
    classification: `确认去除「${img.filename}」的人工标注? 该图片将回到待标注状态, 历史会保留在审计日志`,
    detection: `确认去除「${img.filename}」的所有检测框? 该图片将回到待标注状态, 训练/导出将不再含这些框`,
    segmentation: `确认去除「${img.filename}」的分割 mask? 该图片将回到待标注状态, 训练/导出将不再含该 mask`,
  }
  const tip = hadAi
    ? `确认去除「${img.filename}」的 AI 预标注? 该图片将回到待标注状态, 下次自动标注会重新预测`
    : (tipMap[tt] || tipMap.classification)
  try {
    await ElMessageBox.confirm(tip, '清除标注', { type: 'warning' })
  } catch { return }
  try {
    const r: any = await annotationApi.clear([img.id])
    const ok = r?.cleared || 0
    const skip = r?.skipped || 0
    if (ok > 0) {
      const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
      const bboxN = r?.bbox_cleared_count || 0
      const maskN = r?.mask_cleared_count || 0
      // 根据 task_type 拼装更精确的提示
      const detailParts: string[] = []
      if (aiN > 0) detailParts.push(`含 AI 预标注 ${aiN} 张`)
      if (bboxN > 0) detailParts.push(`清理 ${bboxN} 个检测框`)
      if (maskN > 0) detailParts.push(`清理 ${maskN} 个分割 mask`)
      const detailSuffix = detailParts.length > 0 ? `, ${detailParts.join(', ')}` : ''
      ElMessage.success(
        `已清除标注 (${ok} 张${detailSuffix}${skip > 0 ? `, 跳过 ${skip} 张` : ''})`
      )
    } else {
      ElMessage.info(`无需处理 (跳过 ${skip} 张)`)
    }
    selectedIds.value = selectedIds.value.filter((id) => id !== img.id)
    await load()
  } catch (e: any) {
    ElMessage.error('清除标注失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/** 批量去除标注 (人工 + AI + 检测 + 分割 一起清) */
async function batchClearAnnotation() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择图片')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认对选中的 ${selectedIds.value.length} 张图片执行"清除标注"?` +
      `\n分类图会清人工/AI 类别, 检测图会清所有检测框, 分割图会清 mask 物理文件; 无标注的图片会跳过; 历史会保留在审计日志`,
      '批量清除标注',
      { type: 'warning' }
    )
  } catch { return }
  try {
    const r: any = await annotationApi.clear(selectedIds.value)
    const ok = r?.cleared || 0
    const skip = (r?.skipped || 0) + (r?.missing || 0)
    if (ok > 0) {
      const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
      const bboxN = r?.bbox_cleared_count || 0
      const maskN = r?.mask_cleared_count || 0
      const detailParts: string[] = []
      if (aiN > 0) detailParts.push(`AI 预标注 ${aiN} 张`)
      if (bboxN > 0) detailParts.push(`检测框 ${bboxN} 个`)
      if (maskN > 0) detailParts.push(`分割 mask ${maskN} 个`)
      const detailSuffix = detailParts.length > 0 ? `, 清理 ${detailParts.join(', ')}` : ''
      ElMessage.success(
        `已清除标注 ${ok} 张${detailSuffix}${skip > 0 ? `, 跳过 ${skip} 张` : ''}`
      )
    } else {
      ElMessage.info('所选图片均无标注, 跳过')
    }
    selectedIds.value = []
    await load()
  } catch (e: any) {
    ElMessage.error('批量清除标注失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

async function handleExport(format: 'coco' | 'yolo' | 'csv') {
  const url = exportApi[format](datasetId.value)
  const token = localStorage.getItem('token')
  try {
    const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const blob = await r.blob()
    const ext = format === 'yolo' ? 'zip' : format === 'coco' ? 'json' : 'csv'
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${dataset.value?.name || 'dataset'}_${datasetId.value}.${ext}`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e?.message)
  }
}

function goBack() { router.push('/datasets') }
/**
 * v2.5.44: 「去标注」按钮跳转到标注工作台
 * - 之前: 一律跳到 /annotate/:datasetId, 工作台自动 loadNext 拉第一张待标注
 *   问题: 用户在数据集详情页精心勾选了 N 张图片 (通常是从大量已标图中挑出漏标的),
 *   跳转后却看到一张无关的「下一张」, 还得自己一张张翻到选中区域, 体验割裂
 * - 现在: 若 selectedIds 非空, 从中找出第一张 status === 'pending' 的图片,
 *   把其 id 作为 imageId query 拼到 URL 上
 *   - Annotate 页 onMounted / watch(datasetId) 会读这个 query, 把该图设为当前图
 *   - 用户进入工作台后看到的第 1 张, 必然是自己勾选的那批里的「待标注」第 1 张
 *   - 继续点「下一张」时, 工作台按原有逻辑从后端拉新图 (排除 historyIds), 流转顺畅
 * - 若 selectedIds 为空, 走原行为 (无 imageId, 工作台 loadNext)
 * - 若 selectedIds 中无 pending 图 (例如全选的都是已标图), 也走原行为,
 *   工作台会拉下一张待标, 用户至少不会卡在"加载不出图"的死状态
 */
function goAnnotate() {
  const did = datasetId.value
  if (!did) return
  let targetImageId: number | null = null
  if (selectedIds.value.length > 0) {
    // 按当前 images 数组顺序找第一张 pending (顺序与表格/网格一致, 符合"第一张"直觉)
    // 用 Set 加速 selectedIds 查找
    const sel = new Set(selectedIds.value)
    const firstPending = images.value.find(
      (img: any) => sel.has(img.id) && img.status === 'pending'
    )
    if (firstPending) targetImageId = firstPending.id
  }
  if (targetImageId != null) {
    router.push({ path: `/annotate/${did}`, query: { imageId: String(targetImageId) } })
  } else {
    router.push(`/annotate/${did}`)
  }
}

function formatBytes(b: number): string {
  if (!b) return '-'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(2)} MB`
}

function confColor(c: number): string {
  if (c >= 0.8) return '#67c23a'
  if (c >= 0.5) return '#e6a23c'
  return '#909399'
}

function statusType(s: string): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
  const t: Record<string, 'success' | 'warning' | 'info' | 'primary' | 'danger'> = {
    pending: 'info',
    ai_labeled: 'primary',
    human_confirmed: 'success',
    human_corrected: 'warning',
    trained: 'success'
  }
  return t[s] || 'info'
}

function statusLabel(s: string): string {
  return {
    pending: '待标注',
    ai_labeled: 'AI',
    human_confirmed: '已确认',
    human_corrected: '已修正',
    trained: '已训练'
  }[s] || s
}

/**
 * 判断图片是否"有任何标注" (用于「去标」按钮可见性)
 * v2.5.17 修复: 之前只看 image.status + final_label_id, 漏掉
 *   - detection 图: image.status 可能停留在 pending/ai_labeled, 但已有 BBoxAnnotation 行
 *   - segmentation 图: 同上, SegmentationMask 已存在但 image.status 没更新
 * 现在 OR 上后端新返回的 bbox_count (检测) / has_mask (分割), 即使老数据
 *   status 未更新, 只要 DB 有真实标注就显示「去标」按钮
 */
function hasAnnotation(img: any): boolean {
  if (!img) return false
  // 分类: 有人工或 AI 标注
  if (img.final_label_id) return true
  if (['human_confirmed', 'human_corrected', 'trained', 'ai_labeled'].includes(img.status)) {
    return true
  }
  // 检测: 后端返回 bbox_count
  if ((img.bbox_count || 0) > 0) return true
  // 分割: 后端返回 has_mask
  if (img.has_mask) return true
  return false
}

onMounted(load)
watch(statusFilter, resetPage)
// 切换数据集时重置分页与选中状态，避免停留在旧数据集的高页码导致空列表
watch(() => route.params.id, resetPage)
</script>

<template>
  <div v-loading="loading" class="page-container">
    <!-- 顶部封面 -->
    <div v-if="dataset" class="ds-hero">
      <div class="ds-hero__bg" />
      <div class="ds-hero__body">
        <div class="ds-hero__left">
          <el-button :icon="ArrowLeft" round size="small" @click="goBack" class="ds-hero__back">
            返回
          </el-button>
          <div class="ds-hero__title">
            <h1>{{ dataset.name }}</h1>
            <div class="ds-hero__meta">
              <el-tooltip
                v-if="dataset.task_type"
                :content="getTaskTypeMeta(dataset.task_type).desc"
                placement="bottom"
              >
                <el-tag size="small" :type="getTaskTypeMeta(dataset.task_type).type" effect="plain">
                  <el-icon style="margin-right: 3px; vertical-align: -2px;">
                    <component :is="getTaskTypeMeta(dataset.task_type).icon" />
                  </el-icon>
                  {{ getTaskTypeMeta(dataset.task_type).label }}
                </el-tag>
              </el-tooltip>
              <span v-if="dataset.description" class="ds-hero__desc">
                {{ dataset.description }}
              </span>
            </div>
          </div>
        </div>
        <div class="ds-hero__actions">
          <el-button :icon="Refresh" @click="load">刷新</el-button>
          <el-button :icon="UploadFilled" type="success" @click="uploadOpen = true">
            上传图片
          </el-button>
          <el-dropdown @command="(c: any) => handleExport(c)">
            <el-button :icon="Download">导出</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="coco">COCO 格式</el-dropdown-item>
                <el-dropdown-item command="yolo">YOLO 格式</el-dropdown-item>
                <el-dropdown-item command="csv">CSV 明细</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <!-- 启动 AI 预标注 / 去标注 / 模型选择 仍整合在 filter-card 的 action-bar (单行)
               仅导出按钮按要求回到 page header 原位 -->
        </div>
      </div>
    </div>

    <!-- 统计卡 (单行 x 4 列, 紧凑展示: 图片总数 / 已人工标注 / AI 已标 / 类别数)
         内部 padding/icon/字号 全部下调, 避免 4 列下中文换行; 卡片等高由 .ds-stats :deep(.el-col) 拉伸

         v2.5.49: 移除 2 个低价值指标 ——
         · 平均耗时:
           1) 检测/分割场景下后端写 time_spent_ms=0 (image.py:286, auto_annotate.py:162,421),
              3 种任务类型里只有 classification 写真实耗时, 跨任务口径不一致
           2) 即使分类场景, confirm + correct 都算进分母, "修正" 行为污染人均速度
           3) 标注员效率排行已在后端 /api/stats/annotator-efficiency 提供, 详情页无需重复展示
         · AI 节省时间:
           1) 公式 = total_processed * 8s（硬编码 baseline, stats.py:31） - 实际秒数
              8s 拍脑袋, 不可验证; 与 Annotate 工作台已移除的「估算 AI 节省工作量」同源
           2) 跨三任务同样无解释力, 移除后由类目级 hover 详情 (各类 human/ai 已标) 替代

         v2.5.49: 「类别数」卡升级 ——
         - 仍显示 dataset.category_count
         - hover 弹 el-tooltip, 内含类目进度表 (类目 + 总样本 + 已人工 + AI 已标)
         - 与 Annotate 工作台 hover 视觉一致, 两页面同源
         - 0 类时禁用 tooltip, 显示「-」+ 提示「数据集未配置类目」 -->
    <el-row v-if="stats" :gutter="12" class="ds-stats">
      <el-col :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><Picture /></el-icon></div>
          <el-statistic title="图片总数" :value="dataset?.image_count || 0" />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
        <el-card shadow="hover" class="stat-card stat-card--green">
          <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
          <el-statistic title="已人工标注"
            :value="(stats.annotation?.human_confirmed_count || 0) + (stats.annotation?.human_corrected_count || 0)" />
          <div class="stat-meta">
            已确认 {{ stats.annotation?.human_confirmed_count || 0 }} ·
            修正 {{ stats.annotation?.human_corrected_count || 0 }}
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
        <el-card shadow="hover" class="stat-card stat-card--orange">
          <div class="stat-icon"><el-icon><Lightning /></el-icon></div>
          <el-statistic title="AI 已标"
            :value="stats.annotation?.ai_labeled_count || 0" />
          <div class="stat-meta">
            占比 {{ dataset?.image_count
              ? Math.round((stats.annotation?.ai_labeled_count || 0) / dataset.image_count * 100)
              : 0 }}%
          </div>
        </el-card>
      </el-col>
      <!-- v2.5.49: 类别数卡 — 显示总类数, hover 看每类详情
           - el-tooltip 触发 hover 弹出详细面板
           - 面板内: 类目名 + 3 列计数 (总样本 / 已人工 / AI 已标)
           - 数据来源: datasetApi.categories 已在 load() 拉到 categories.value
           - 空态: 0 个类目时显示「-」 + tooltip 提示「数据集未配置类目」 -->
      <el-col :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
        <el-tooltip
          placement="top"
          :disabled="categories.length === 0"
          :show-after="200"
        >
          <template #content>
            <div v-if="categories.length === 0" style="padding: 4px 8px;">
              当前数据集未配置类目
            </div>
            <div v-else class="category-tooltip">
              <div class="category-tooltip__header">各类目已标进度</div>
              <table class="category-tooltip__table">
                <thead>
                  <tr>
                    <th class="ct-name">类目</th>
                    <th class="ct-num">总样本</th>
                    <th class="ct-num">已人工</th>
                    <th class="ct-num">AI 已标</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in categories" :key="c.id">
                    <td class="ct-name">
                      <span class="ct-dot" :style="{ background: c.color || '#00a3e0' }"></span>
                      {{ c.name }}
                    </td>
                    <td class="ct-num">{{ c.sample_count ?? 0 }}</td>
                    <td class="ct-num ct-human">{{ c.human_labeled_count ?? 0 }}</td>
                    <td class="ct-num ct-ai">{{ c.ai_labeled_count ?? 0 }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
          <el-card shadow="hover" class="stat-card stat-card--cyan stat-card--clickable">
            <div class="stat-icon"><el-icon><CollectionTag /></el-icon></div>
            <el-statistic :title="categories.length === 0 ? '类别数' : `类别数 (共 ${categories.length} 类)`"
              :value="categories.length || (dataset?.category_count || 0)" />
            <div v-if="categories.length > 0" class="stat-meta stat-meta--hint">
              <el-icon style="vertical-align: -2px; margin-right: 2px;"><InfoFilled /></el-icon>
              悬停查看各类进度
            </div>
          </el-card>
        </el-tooltip>
      </el-col>
    </el-row>

    <!-- 过滤 + AI 选项 -->
    <el-card shadow="never" class="filter-card">
      <!-- 单行紧凑布局: flex + flex-wrap 保证窄屏自动换行
           分 3 组 (用 | 视觉分隔):
             过滤组: 状态 / 搜索
             AI 组:  模型下拉 / 启动AI预标注 / 测评 (相邻, 测评是启动的 dry-run)
             操作组: 去标注 / 导出 (去标注 = 跳标注页, 导出 = 输出数据集)
             辅助组: 批量 (去标/删除) / 视图切换
           注: 启动AI预标注 / 去标注 / 导出 / 模型选择 整合在同一行 (按产品要求) -->
      <div class="filter-row filter-row--single">
        <!-- ===== 过滤组 ===== -->
        <div class="filter-group filter-group--filter">
          <el-select v-model="statusFilter" size="default" placeholder="状态" class="filter-cell filter-cell--select app-select app-select--medium">
            <el-option
              v-for="opt in statusOptions" :key="opt.value"
              :label="opt.label" :value="opt.value"
            />
          </el-select>
          <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索文件名/类别"
            clearable size="default" class="filter-cell filter-cell--search" />
        </div>

        <!-- ===== AI 组 (核心) ===== -->
        <div class="filter-group filter-group--ai">
          <el-tooltip
            placement="top" :show-after="200"
            :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
          >
            <el-select
              v-model="selectedFinetuneId"
              :placeholder="finetuneModels.length === 0 ? '选择 fine-tune 模型 (仅本数据集已激活)' : '选择 fine-tune 模型'"
              size="default"
              :fit-input-width="false"
              popper-class="app-select-dropdown model-select-dropdown"
              class="app-select"
              :disabled="finetuneModels.length === 0"
              filterable
              :empty-text="finetuneModels.length === 0 ? '该数据集暂无训练模型' : '无可用模型'"
            >
              <el-option
                v-for="m in finetuneModels" :key="m.id" :value="m.id"
                :label="`${m.name} · ${m.base_model}`"
              >
                <div style="display: flex; align-items: center; gap: 6px;">
                  <span>{{ m.name }}</span>
                  <span style="color: #909399; font-size: 12px;">· {{ m.base_model }}</span>
                  <span style="margin-left: auto; color: #67c23a; font-size: 12px;">{{ (m.accuracy * 100).toFixed(1) }}%</span>
                </div>
              </el-option>
            </el-select>
          </el-tooltip>
          <!-- 启动 AI 预标注 按钮已移除 (按要求)
               标注工作台 Annotate.vue 仍提供此功能入口 -->
          <!-- 置信度阈值 (从原 filter-row--threshold 第 2 行挪到 AI 组, 测评前面, 紧凑模式) -->
          <el-tooltip
            placement="top" :show-after="200"
            :content="`置信度阈值: 决定一张图被自动标注的最低可信度。>= ${(threshold * 100).toFixed(0)}% 将直接标注, 其余保留为「待标注」由人工复核`"
          >
            <div class="threshold-row threshold-row--inline">
              <span class="threshold-label">置信度阈值</span>
              <el-slider v-model="threshold" :min="0.1" :max="1.0" :step="0.05" :show-tooltip="true"
                :format-tooltip="(v: number) => `阈值 ${(v * 100).toFixed(0)}%`"
                class="threshold-slider threshold-slider--inline" />
              <el-tag type="primary" effect="dark" class="threshold-value">
                {{ (threshold * 100).toFixed(0) }}%
              </el-tag>
            </div>
          </el-tooltip>
          <!-- v2.5.46: 测评按钮三任务均可用, 后端 preview_confidence 按 dataset.task_type 三路分派
               - 分类: top-1 标签 + 置信度 + top-3 候选
               - 检测: bbox 计数 + 最高置信度 + bbox 类别摘要
               - 分割: 最大 softmax + 像素级 mask
               测评是 dry-run (不写库, 不写审计), 让用户在执行批量预标注前评估阈值是否合适 -->
          <el-tooltip
            placement="top" :show-after="200"
            :content="previewTooltipText"
          >
            <el-button
              plain :icon="DataAnalysis" :loading="previewing"
              :disabled="images.length === 0 || !hasFinetuneModel"
              @click="onPreviewConfidence"
              class="filter-cell filter-cell--btn"
            >测评</el-button>
          </el-tooltip>
        </div>

        <!-- ===== 操作组 (去标注; 导出已回到 page header 原位) =====
             v2.5.44: 选中有图片时, 跳转后默认显示选中区域的第一张待标注图
             (通过 URL ?imageId= 透传给工作台, 见 goAnnotate) -->
        <div class="filter-group filter-group--ops">
          <el-tooltip
            placement="top" :show-after="200"
            :content="selectedIds.length > 0
              ? '跳转到标注工作台, 默认显示选中图片中的第一张待标注图'
              : '跳转到标注工作台, 继续人工确认/修正'"
          >
            <el-button :icon="EditPen" @click="goAnnotate" class="filter-cell filter-cell--btn">去标注</el-button>
          </el-tooltip>
        </div>

        <!-- ===== 辅助组 (批量 + 视图) ===== -->
        <div class="filter-group filter-group--aux">
          <el-button
            plain
            :type="allOnPageSelected ? 'primary' : 'default'"
            :icon="allOnPageSelected ? 'Minus' : 'Check'"
            @click="toggleSelectAll"
            class="filter-cell filter-cell--btn"
          >{{ allOnPageSelected ? '取消' : '全选' }}</el-button>
          <el-tag v-if="selectedIds.length > 0" type="warning" effect="dark" size="default" class="batch-count">
            {{ selectedIds.length }}
          </el-tag>
          <el-button :disabled="selectedIds.length === 0" type="warning"
            :icon="RefreshLeft" @click="batchClearAnnotation" class="filter-cell filter-cell--btn">清除标注</el-button>
          <el-button :disabled="selectedIds.length === 0" type="danger"
            :icon="Delete" @click="batchDelete" class="filter-cell filter-cell--btn">删除</el-button>
          <div class="view-mode-switch" :title="viewMode === 'grid' ? '网格视图' : '列表视图'">
            <button
              class="mode-btn" :class="{ active: viewMode === 'grid' }"
              :title="'网格视图'" @click="viewMode = 'grid'"
            >
              <el-icon><Grid /></el-icon>
            </button>
            <button
              class="mode-btn" :class="{ active: viewMode === 'list' }"
              :title="'列表视图'" @click="viewMode = 'list'"
            >
              <el-icon><List /></el-icon>
            </button>
          </div>
        </div>
      </div>
      <!-- 第 2 行 (filter-row--threshold) 已移除: 置信度 slider 已挪到第 1 行 AI 组测评前面 -->
      <!-- AI 配置实时提示: 告知用户当前模型/阈值将如何作用于待标图片 -->
      <div class="ai-config-hint" :class="{ 'ai-config-hint--warn': !hasFinetuneModel }">
        <el-icon class="ai-config-hint__icon">
          <component :is="hasFinetuneModel ? InfoFilled : WarningFilled" />
        </el-icon>
        <span v-if="hasFinetuneModel">
          当前将用
          <b class="ai-config-hint__model">{{ displayModel }}</b>
          对 <b>{{ pendingCount }}</b> 张「待标注」图片进行预标注,
          置信度 ≥ <b>{{ (threshold * 100).toFixed(0) }}%</b> 的图片会自动落标,
          其余保留为「待标注」由人工复核。
        </span>
        <span v-else>
          当前数据集<b>暂无训练模型</b>, <b>测评</b>功能不可用。
          请先到「训练任务」页选定本数据集启动训练, 训练完成后再来预标注和测评。
        </span>
        <div class="ai-config-hint__actions">
          <el-button v-if="!hasFinetuneModel" type="warning" size="small" round
            :icon="Promotion" @click="router.push('/training')">
            前往训练任务
          </el-button>
          <el-tag v-else-if="pendingCount === 0" type="success" size="small" effect="plain">
            暂无待标注
          </el-tag>
          <el-tag v-else size="small" type="info" effect="plain">
            待标 {{ pendingCount }}
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- 图像网格 / 列表 -->
    <el-empty v-if="!loading && filteredImages.length === 0" description="该状态下没有图片" />

    <!-- 网格视图 -->
    <el-row v-else-if="viewMode === 'grid'" :gutter="14">
      <el-col v-for="img in filteredImages" :key="img.id" :xs="12" :sm="8" :md="6" :lg="4" :xl="4">
        <el-card
          shadow="hover"
          class="image-card"
          :class="{ selected: selectedIds.includes(img.id) }"
          @click="toggleSelect(img.id)"
        >
          <div class="image-thumb">
            <img
              :src="imageApi.thumbnailUrl(img.id, 320)"
              :alt="img.filename"
              loading="lazy"
              @error="(e: any) => { e.target.src = imageApi.fileUrl(img.id) }"
            />
            <el-checkbox
              class="image-checkbox"
              :model-value="selectedIds.includes(img.id)"
              @change="(v: any) => {
                if (v) selectedIds.push(img.id);
                else selectedIds = selectedIds.filter(x => x !== img.id);
              }"
              @click.stop
            />
            <el-tag :type="statusType(img.status)" size="small" class="status-tag">
              {{ statusLabel(img.status) }}
            </el-tag>
            <el-button
              class="detail-btn"
              type="primary"
              :icon="View"
              size="small"
              circle
              @click.stop="openViewer(img.id)"
            />
            <el-tooltip
              v-if="hasAnnotation(img)"
              content="清除标注" placement="top"
            >
              <el-button
                class="clear-btn"
                type="warning"
                :icon="RefreshLeft"
                size="small"
                circle
                @click.stop="clearOneAnnotation(img)"
              />
            </el-tooltip>
            <el-button class="del-btn" type="danger" :icon="Delete" size="small" circle
              @click.stop="deleteOne(img)" />
          </div>
          <div class="image-info">
            <el-tooltip :content="img.filename" placement="top">
              <div class="image-name">{{ img.filename }}</div>
            </el-tooltip>
            <div class="image-meta">
              <span>{{ formatBytes(img.file_size) }}</span>
              <span v-if="img.width && img.height">{{ img.width }}×{{ img.height }}</span>
            </div>
            <div v-if="img.final_label_name" class="image-label">
              <el-tag size="small" effect="dark">{{ img.final_label_name }}</el-tag>
            </div>
            <div v-else-if="img.ai_prediction?.top1" class="image-ai">
              <span style="font-size: 11px; color: #909399;">AI:</span>
              <el-tag size="small">{{ img.ai_prediction.top1 }}</el-tag>
              <el-tag size="small" :color="confColor(img.ai_prediction.top1_conf || 0)"
                effect="dark" style="margin-left: 4px;">
                {{ ((img.ai_prediction.top1_conf || 0) * 100).toFixed(0) }}%
              </el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 列表视图 -->
    <el-table
      v-else
      :data="filteredImages"
      border
      stripe
      class="image-list-table"
      @row-click="(row: any) => toggleSelect(row.id)"
    >
      <el-table-column type="selection" width="48" :selectable="() => true" />
      <el-table-column label="缩略图" width="100">
        <template #default="{ row }">
          <div class="list-thumb">
            <img :src="imageApi.thumbnailUrl(row.id, 200)" :alt="row.filename" loading="lazy" />
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最终类别" width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tag v-if="row.final_label_name" size="small" effect="dark">{{ row.final_label_name }}</el-tag>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
      <el-table-column label="AI 预测" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.ai_prediction?.top1">
            <el-tag size="small">{{ row.ai_prediction.top1 }}</el-tag>
            <el-tag size="small" :color="confColor(row.ai_prediction.top1_conf || 0)"
              effect="dark" style="margin-left: 4px;">
              {{ ((row.ai_prediction.top1_conf || 0) * 100).toFixed(0) }}%
            </el-tag>
          </span>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
      <el-table-column label="尺寸" width="100">
        <template #default="{ row }">
          <span v-if="row.width && row.height" class="dim">{{ row.width }}×{{ row.height }}</span>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="90">
        <template #default="{ row }">{{ formatBytes(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="上传时间" width="170">
        <template #default="{ row }">
          <span v-if="row.created_at" class="dim">{{ row.created_at.slice(0, 16).replace('T', ' ') }}</span>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :icon="View" @click.stop="openViewer(row.id)">详情</el-button>
          <el-button
            v-if="row.final_label_id || ['human_confirmed','human_corrected','trained','ai_labeled'].includes(row.status)"
            size="small" type="warning" :icon="RefreshLeft"
            @click.stop="clearOneAnnotation(row)"
          >清除标注</el-button>
          <el-button size="small" type="danger" :icon="Delete" @click.stop="deleteOne(row)" />
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页: 始终可见 -->
    <div class="pager">
      <el-pagination
        background
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="pageSizes"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="changePage"
        @size-change="onSizeChange"
      />
    </div>

    <!-- 标注查看器弹窗 -->
    <el-dialog
      v-model="viewerOpen"
      :title="`图片详情`"
      width="1080px"
      top="5vh"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <AnnotationViewer
        v-if="viewerImageId"
        :image-id="viewerImageId"
        @saved="() => load()"
      />
    </el-dialog>

    <!-- 上传图片弹窗 -->
    <el-dialog
      v-model="uploadOpen"
      :title="`上传图片到「${dataset?.name || ''}」`"
      width="780px"
      :close-on-click-modal="false"
      destroy-on-close
      @close="load"
    >
      <UploadQueue
        v-if="uploadOpen"
        :dataset-id="datasetId"
        @uploaded="(r: any) => {
          ElMessage.success(`上传 ${r?.uploaded ?? 0}/${r?.total ?? 0} 张成功`)
        }"
      />
    </el-dialog>

    <!-- 置信度测评结果弹窗 (非破坏性预览) -->
    <el-dialog
      v-model="previewDialogVisible"
      title="置信度测评结果"
      width="980px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <div v-if="previewResult" class="preview-summary">
        <el-alert
          v-if="previewResult.warning"
          :title="previewResult.warning"
          type="warning" :closable="false" show-icon
          style="margin-bottom: 12px;"
        />
        <div class="preview-summary__cards">
          <div class="preview-card preview-card--success">
            <div class="preview-card__num">{{ previewResult.would_label }}</div>
            <div class="preview-card__label">将被自动标注</div>
            <div class="preview-card__hint">≥ 阈值 {{ (previewResult.threshold * 100).toFixed(0) }}% 且在项目类目内</div>
          </div>
          <div class="preview-card preview-card--warning">
            <div class="preview-card__num">{{ previewResult.need_human }}</div>
            <div class="preview-card__label">需人工复核</div>
            <div class="preview-card__hint">低于阈值 / top-1 不在项目类目</div>
          </div>
          <div class="preview-card preview-card--danger">
            <div class="preview-card__num">{{ previewResult.no_match }}</div>
            <div class="preview-card__label">无匹配</div>
            <div class="preview-card__hint">模型输出与项目类目无交集</div>
          </div>
        </div>
        <div class="preview-summary__model">
          测评模型:
          <b v-if="previewResult.used_finetune && previewResult.finetune_name">
            {{ previewResult.finetune_name }}
            <span style="color: #909399; font-weight: normal; font-size: 12px;">
              (基础模型 {{ previewResult.base_model || previewResult.model_name }})
            </span>
          </b>
          <b v-else>{{ previewResult.model_name || '(空)' }}</b>
          <el-tag
            v-if="previewResult.used_finetune" type="success" size="small" effect="plain"
            style="margin-left: 8px;"
          >fine-tune</el-tag>
          <el-tag
            v-else type="info" size="small" effect="plain"
            style="margin-left: 8px;"
          >timm 预训练</el-tag>
        </div>
      </div>

      <el-tabs v-model="previewActiveTab" class="preview-tabs">
        <el-tab-pane :name="'would'">
          <template #label>
            <span><el-icon><Check /></el-icon> 会被标注 ({{ previewGroups.would.length }})</span>
          </template>
          <PreviewList :items="previewGroups.would" :task-type="currentDatasetTaskType" />
        </el-tab-pane>
        <el-tab-pane :name="'human'">
          <template #label>
            <span><el-icon><InfoFilled /></el-icon> 需人工复核 ({{ previewGroups.human.length }})</span>
          </template>
          <PreviewList :items="previewGroups.human" :task-type="currentDatasetTaskType" />
        </el-tab-pane>
        <el-tab-pane :name="'none'">
          <template #label>
            <span><el-icon><CircleClose /></el-icon> 无匹配 ({{ previewGroups.none.length }})</span>
          </template>
          <PreviewList :items="previewGroups.none" :task-type="currentDatasetTaskType" />
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <el-button @click="previewDialogVisible = false">关闭</el-button>
        <el-button
          type="primary" :icon="Lightning"
          :loading="autoLabeling"
          @click="async () => {
            previewDialogVisible = false
            await onAutoAnnotate()
          }"
        >应用并启动预标注</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ===========================================================
   顶部封面 (Dashboard 风格的 hero 渐变)
   =========================================================== */
.ds-hero {
  position: relative;
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-bottom: 18px;
  background: var(--bg-card);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-soft);
}
.ds-hero__bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 18% 30%, rgba(79, 124, 255, 0.12) 0%, transparent 45%),
    radial-gradient(circle at 85% 75%, rgba(110, 81, 233, 0.10) 0%, transparent 50%),
    linear-gradient(135deg, #f7f9ff 0%, #ffffff 60%, #fff7f0 100%);
  pointer-events: none;
}
.ds-hero__body {
  position: relative;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 20px 24px;
  flex-wrap: wrap;
}
.ds-hero__left {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
  min-width: 0;
}
.ds-hero__back {
  background: rgba(255, 255, 255, 0.7) !important;
  backdrop-filter: blur(6px);
  border: 1px solid var(--border-soft) !important;
}
.ds-hero__title { min-width: 0; }
.ds-hero__title h1 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -0.3px;
}
.ds-hero__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ds-hero__desc {
  color: var(--text-secondary);
  font-size: 13px;
  max-width: 600px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ds-hero__actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* ===========================================================
   统计卡 (与 Dashboard 完全一致的色条 + 彩色图标样式)
   高度统一由 theme.css 的 .stat-card min-height 兜底, 这里只覆盖局部样式
   =========================================================== */
.ds-stats { margin-bottom: 18px; }
/* 让 el-col 内部卡片在同行内等高: 强制 col 纵向 stretch */
.ds-stats :deep(.el-col) { display: flex; }
.ds-stats :deep(.el-col) > .el-card { width: 100%; }

.stat-card {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg) !important;
  background: #fff !important;
  padding: 4px;
  transition: transform 0.25s var(--ease-out), box-shadow 0.25s var(--ease-out);
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md) !important;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  border-radius: 3px 3px 0 0;
}
.stat-card--blue::before   { background: var(--gradient-brand); }
.stat-card--green::before  { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--cyan::before   { background: linear-gradient(135deg, #00a3e0 0%, #00c48c 100%); }
/* v2.5.49 移除: .stat-card--warm / .stat-card--purple 渐变定义 (对应卡片已删除) */

.stat-card :deep(.el-card__body) {
  padding: 14px 16px;
  position: relative;
  /* 覆盖 theme.css 的 min-height: 116px, 1 行 4 列卡片更紧凑 (卡片更宽, 字号可保持) */
  min-height: 92px;
  /* 让卡片在同 row 内等高: 撑满父级 (theme.css 已设 .stat-card height: 100%) */
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 12.5px;
  font-weight: 500;
  margin-bottom: 6px;
  /* 给右上角图标留位, 避免标题被覆盖 (图标 28px + 间距 8px) */
  padding-right: 40px;
  min-height: 16px;
  /* 标题过长时, 截断省略, 避免 6 列下中文换行把布局撑乱 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  padding-right: 40px;
  line-height: 1.15;
  /* 大数字过长时也避免撑破卡片 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stat-icon {
  position: absolute;
  right: 14px;
  top: 14px;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon :deep(.el-icon) { font-size: 16px; }
.stat-card--blue   .stat-icon { background: rgba(79, 124, 255, 0.1);  color: #4f7cff; }
.stat-card--green  .stat-icon { background: rgba(0, 196, 140, 0.1);  color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1);  color: #ff8a4c; }
.stat-card--cyan   .stat-icon { background: rgba(0, 163, 224, 0.1);   color: #00a3e0; }
/* v2.5.49 移除: .stat-card--warm / .stat-card--purple 图标色 (对应卡片已删除) */
.stat-meta {
  color: var(--text-placeholder);
  margin-top: 4px;
  font-size: 11.5px;
  padding-right: 40px;
  /* 4 列卡片下, meta 文案过长可优雅换行, 避免撑破卡片 */
  line-height: 1.4;
  word-break: break-all;
}
/* v2.5.49: 「类别数」卡的「悬停查看」提示 (放在 stat-meta 下面, 青色引导色) */
.stat-meta--hint {
  color: #00a3e0;
  font-size: 11px;
  margin-top: 2px;
  opacity: 0.85;
}
/* v2.5.49: 「类别数」卡 hover 状态 (区别于普通卡, 提示用户可悬停) */
.stat-card--clickable {
  cursor: help;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stat-card--clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 163, 224, 0.15);
}

/* ===========================================================
   过滤卡
   =========================================================== */
.filter-card {
  border-radius: var(--radius-md) !important;
  border: 1px solid var(--border-soft) !important;
  background: #fff !important;
  margin-bottom: 14px;
  padding: 4px 0;
}
.filter-card :deep(.el-card__body) { padding: 16px 20px 12px; }
.filter-label {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}
.filter-row { margin-top: 0; padding-top: 0; border-top: none; }

/* ===== 单行整合布局: 4 组 flex + flex-wrap (响应式) =====
   .filter-row--single: 单行, 4 组用 | 视觉分隔
     [过滤组] | [AI 组] | [操作组] | [辅助组]
   窄屏 (< 1100px) 自动换行, 每组 100% 宽度 */
.filter-row--single {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
}
/* 组与组之间的 | 视觉分隔: 每组右侧加 1px 浅灰 (除最后一组) */
.filter-group {
  display: flex;
  align-items: center;
  gap: var(--gap);
  min-width: 0;
  flex-wrap: nowrap;            /* 强制单行, 防止组内 wrap */
}
.filter-group:not(:last-child) {
  padding-right: var(--divider);
  border-right: 1px solid var(--border-soft, #ebeef5);
}
/* 各 group 宽度策略 (1130-1280 屏主区 ~858-1008px):
   - filter: 状态(140) + 搜索(150) + gap(8) = 298
   - ai:     模型(180) + 阈值组(170) + 测评(72) + 2×8 = 446
   - ops:    去标注(88) = 88
   - aux:    全选+去标+删除+视图 ≈ 280
   - 3×12 (|) = 36
   - 总 ≈ 1148 (1130 屏会换行, 1280 屏单行) */
.filter-group--filter { flex: 0 1 auto; min-width: 280px; }
.filter-group--ai     { flex: 1 1 auto; min-width: 440px; }
.filter-group--ops    { flex: 0 0 auto; }
.filter-group--aux    { flex: 0 1 auto; margin-left: auto; min-width: 260px; }  /* 辅助组靠右 */

/* ───────────────────────────────────────────────────────────
   filter-card 宽度统一管理 (CSS 变量)
   改一个数字, 所有用到的 width / flex-basis / min-width 同步更新
   ─────────────────────────────────────────────────────────── */
.filter-card {
  /* 单值宽度: 改这里就能调整对应元素 */
  --w-status:  140px;   /* 状态下拉 (label "已修正" 3 汉字 + 箭头) */
  --w-search:  200px;   /* 搜索框 (placeholder "搜索文件名/类别" + icon) */
  --w-model:    260px;   /* 模型下拉触发框 (300 = 固定值, 不留白也不截断) */
  --w-thresh:  180px;   /* 阈值组 (label "阈值" + slider + 60% tag) */
  --w-btn:      72px;   /* 次按钮 (测评/去标注 padding 0 10) */
  --w-btn-pri: 100px;   /* 主按钮 (启动 AI 预标注 padding 0 14) */
  --gap:        8px;    /* filter-row 内部 gap */
  --divider:   12px;    /* group 之间的 | 内边距 */
}

/* 通用单元 */
.filter-cell { display: flex; align-items: center; min-width: 0; }

/* ── 状态下拉 (固定, 不让 flex 改) ── */
.filter-cell--select {
  width: var(--w-status);
  flex-shrink: 0;
  /* flex: 0 0 var(--w-status); */
  display: block;   /* 不能用 display: flex 包裹 el-select, 会压扁 .el-select__placeholder */
}
.filter-cell--select .el-select { width: 100%; }

/* ── 搜索框 (固定 200) ── */
.filter-cell--search {
  width: var(--w-search);
  flex: 0 0 var(--w-search);
}

/* ── 模型下拉 (固定 300) ── */
.filter-cell--model {
  width: var(--w-model);
  /* flex: 0 0 var(--w-model); */
  flex-shrink: 0;
}
/* 关键: el-select 默认 width = content, 不是 100%.
   外层 500px 但内部 select 只占 content 宽 (≈ 200px), 右侧空白.
   必须给 .el-select 设 100% 撑满外层. */
.filter-cell--model .el-select,
.filter-cell--model .el-select__wrapper { width: 100%; }

/* 下拉面板 (popper): 跟内容走, 不强制匹配触发框
   fit-input-width="false" 已让面板按 option 内容自适应
   这里再设 max-width: max-content 防止过长 */
.model-select-dropdown {
  width: max-content;
  max-width: 600px;        /* 防超长 option 撑爆 */
  min-width: 200px;        /* 不短于触发框 */
}

/* ── 按钮 (auto 宽, 跟随 content) ── */
.filter-cell--btn,
.filter-cell--btn-primary {
  flex: 0 0 auto;
}
.filter-cell--btn-primary {
  font-weight: 600;
  --el-button-size: 32px;
  height: 32px;
  padding: 0 14px;
}
.filter-cell--btn { padding: 0 10px; }

/* ── 阈值组 (label + slider + tag) ── */
.threshold-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.threshold-label {
  font-size: 13px;
  color: var(--text-secondary, #606266);
  font-weight: 500;
  flex: 0 0 auto;
  white-space: nowrap;
}
.threshold-row--inline {
  width: var(--w-thresh);
  flex: 0 0 var(--w-thresh);
}
.threshold-row :deep(.el-slider) { flex: 1 1 auto; min-width: 0; }
.threshold-slider--inline { margin: 0 4px; }
.threshold-slider--inline :deep(.el-slider__runway) { margin: 0 6px; }
.threshold-value {
  flex: 0 0 auto;
  font-weight: 600;
  min-width: 44px;
  text-align: center;
}

/* 辅助组内: 全选/批量/视图切换紧凑排列 */
.filter-group--aux { gap: 6px; }
.batch-count { font-weight: 600; }
.view-mode-switch {
  display: inline-flex;
  border: 1px solid var(--border-soft, #ebeef5);
  border-radius: 6px;
  overflow: hidden;
  margin-left: 4px;
}
.mode-btn {
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--bg-card, #fff);
  color: var(--text-secondary, #606266);
  border: none;
  cursor: pointer;
  transition: all 0.18s;
}
.mode-btn + .mode-btn { border-left: 1px solid var(--border-soft, #ebeef5); }
.mode-btn:hover { color: var(--el-color-primary); background: rgba(64,158,255,0.08); }
.mode-btn.active { color: #fff; background: var(--el-color-primary); }

/* 置信度阈值 (紧凑模式, 在第 1 行 AI 组测评前面)
   原 filter-row--threshold 第 2 行整行布局已移除, slider 改为 inline 模式 */
.threshold-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.threshold-label {
  font-size: 13px;
  color: var(--text-secondary, #606266);
  font-weight: 500;
  flex: 0 0 auto;
  white-space: nowrap;
}
/* 内联模式: 带 label (简化为"阈值" 2 字符), slider 较窄 (140-170px) */
.threshold-row--inline {
  flex: 0 1 180px;       /* 基础 180, 允许收缩 */
  min-width: 300px;
}
.threshold-row :deep(.el-slider) {
  flex: 1 1 auto;
  min-width: 0;
}
/* 内联 slider: 高度压缩, 适配 32px 按钮同行 */
.threshold-slider--inline {
  margin: 0 4px;
}
.threshold-slider--inline :deep(.el-slider__runway) {
  margin: 0 6px;
}
.threshold-value {
  flex: 0 0 auto;
  font-weight: 600;
  min-width: 44px;
  text-align: center;
}

/* 响应式: 窄屏 (< 1100px) 组内元素换行, 组不再用 | 分隔 */
@media (max-width: 1100px) {
  .filter-group:not(:last-child) {
    border-right: none;
    padding-right: 0;
  }
  .filter-group--aux { margin-left: 0; }
  .filter-group {
    flex-basis: 100%;
  }
  .filter-group--ai { flex-basis: 100%; }
  .filter-cell--search { width: 100%; flex: 1 1 100%; }
}
@media (max-width: 720px) {
  .filter-cell--model { min-width: 0; }
  .filter-cell--btn-primary { font-size: 12px; padding: 0 10px; }
}

/* 测评结果弹窗: 顶部三张统计卡 */
.preview-summary {
  margin-bottom: 12px;
}
.preview-summary__cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 10px;
}
.preview-card {
  border-radius: 8px;
  padding: 12px 14px;
  border: 1px solid var(--border-soft);
  background: var(--bg-card);
  position: relative;
  overflow: hidden;
}
.preview-card::before {
  content: '';
  position: absolute;
  inset: 0;
  opacity: 0.06;
  pointer-events: none;
}
.preview-card--success { border-color: rgba(103, 194, 58, 0.4); }
.preview-card--success::before { background: #67c23a; }
.preview-card--warning { border-color: rgba(230, 162, 60, 0.4); }
.preview-card--warning::before { background: #e6a23c; }
.preview-card--danger  { border-color: rgba(245, 108, 108, 0.4); }
.preview-card--danger::before  { background: #f56c6c; }
.preview-card__num {
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 6px;
}
.preview-card--success .preview-card__num { color: #67c23a; }
.preview-card--warning .preview-card__num { color: #e6a23c; }
.preview-card--danger  .preview-card__num { color: #f56c6c; }
.preview-card__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 2px;
}
.preview-card__hint {
  font-size: 11px;
  color: var(--text-secondary);
  line-height: 1.4;
}
.preview-summary__model {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 4px 0;
}
.preview-summary__model b { color: var(--text-primary); font-weight: 600; }
.preview-tabs { margin-top: 4px; }
.preview-tabs :deep(.el-tabs__header) { margin-bottom: 8px; }
/* Fine-tune 下拉选项已与 Annotate.vue 视觉对齐:
   名称 + 灰字「· base_model」 + 绿字准确率 (由内联 style 控制),
   此处不再需要 ft-option 系列样式 */
/* 视图切换单元: 贴在最右, 固定宽度 */
.filter-cell--view { justify-content: flex-end; }
.filter-cell--view .view-mode-switch { flex: 0 0 auto; }
/* AI 配置实时提示: 让用户理解模型/阈值/待标数量的联动关系 */
.ai-config-hint {
  margin-top: 12px;
  padding: 10px 14px;
  background: linear-gradient(90deg, rgba(64, 158, 255, 0.04) 0%, rgba(64, 158, 255, 0.01) 100%);
  border-left: 3px solid var(--color-primary, #409eff);
  border-radius: 4px;
  font-size: 12.5px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.5;
  flex-wrap: wrap;
}
/* 警告态: 本数据集无 fine-tune 模型, 用更醒目的暖色系 */
.ai-config-hint--warn {
  background: linear-gradient(90deg, rgba(255, 168, 64, 0.08) 0%, rgba(255, 168, 64, 0.02) 100%);
  border-left-color: var(--brand-warning, #ffa940);
}
.ai-config-hint--warn .ai-config-hint__icon { color: var(--brand-warning, #ffa940); }
.ai-config-hint__icon {
  color: var(--color-primary, #409eff);
  font-size: 14px;
  flex: 0 0 auto;
}
.ai-config-hint__model { color: var(--color-primary, #409eff); }
.ai-config-hint b { color: var(--text-primary); font-weight: 600; }
.ai-config-hint__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.filter-right {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;
}

/* ===========================================================
   图像卡
   =========================================================== */
.image-card {
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s var(--ease-out);
  user-select: none;
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
.image-card.selected {
  border-color: var(--brand-primary) !important;
  box-shadow: 0 0 0 2px rgba(79, 124, 255, 0.3) !important;
}
.image-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-md) !important;
}
.image-thumb {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  background: linear-gradient(135deg, #f5f7 0%, #ebedf2 100%);
  border-radius: 4px;
  overflow: hidden;
}
.image-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.4s var(--ease-out);
}
.image-card:hover .image-thumb img {
  transform: scale(1.05);
}
.image-checkbox {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 0 4px;
  backdrop-filter: blur(4px);
}
.status-tag {
  position: absolute;
  top: 6px;
  right: 6px;
  font-weight: 500;
  backdrop-filter: blur(4px);
}
.del-btn {
  position: absolute;
  bottom: 6px;
  right: 6px;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}
.clear-btn {
  position: absolute;
  bottom: 6px;
  left: 32px;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}
.image-card:hover .del-btn { opacity: 1; }
.image-card:hover .clear-btn { opacity: 1; }

.detail-btn {
  position: absolute;
  top: 32px;
  right: 6px;
}

.image-info {
  padding: 10px 4px 0;
  font-size: 12px;
}
.image-name {
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
}
.image-meta {
  display: flex;
  justify-content: space-between;
  color: var(--text-placeholder);
  margin-top: 2px;
  font-size: 11px;
}
.image-label, .image-ai { margin-top: 6px; }
.image-ai { display: flex; align-items: center; gap: 4px; }

/* ===========================================================
   列表视图
   =========================================================== */
.image-list-table {
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
.list-thumb {
  width: 64px;
  height: 64px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg-soft);
  flex-shrink: 0;
}
.list-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.dim { color: var(--text-placeholder); font-size: 12px; }

/* ===========================================================
   分页
   =========================================================== */
.pager {
  margin-top: 18px;
  display: flex;
  justify-content: center;
  padding: 12px 16px;
  background: #fff;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
}
</style>

<!-- v2.5.49: 「类别数」卡 hover 弹出的类目进度表全局样式
     - popper 由 element-plus append 到 body, scoped 不可达, 必须用全局 style
     - 与 Annotate 工作台同源, 保证两页面的 hover 详情视觉一致 -->
<style>
.category-tooltip {
  font-size: 12px;
  line-height: 1.5;
  min-width: 240px;
}
.category-tooltip__header {
  font-weight: 600;
  color: #303133;
  padding-bottom: 6px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 6px;
}
.category-tooltip__table {
  width: 100%;
  border-collapse: collapse;
}
.category-tooltip__table th,
.category-tooltip__table td {
  padding: 4px 6px;
  text-align: left;
}
.category-tooltip__table th {
  color: #909399;
  font-weight: 500;
  font-size: 11px;
  border-bottom: 1px solid #ebeef5;
}
.category-tooltip__table .ct-name {
  min-width: 90px;
}
.category-tooltip__table .ct-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  width: 56px;
}
.category-tooltip__table .ct-human {
  color: #67c23a;
  font-weight: 600;
}
.category-tooltip__table .ct-ai {
  color: #909399;
}
.category-tooltip__table .ct-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: 1px;
}
</style>
