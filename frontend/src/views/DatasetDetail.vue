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
  CircleCheck, CircleClose, Clock, MagicStick, CollectionTag, Check, Minus, InfoFilled, DataAnalysis, EditPen
} from '@element-plus/icons-vue'
import {
  datasetApi, imageApi, annotationApi, autoAnnotateApi, exportApi, statsApi, modelApi
} from '@/api'
import AnnotationViewer from '@/components/AnnotationViewer.vue'
import UploadQueue from '@/components/UploadQueue.vue'
import PreviewList from '@/components/PreviewList.vue'
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
    const [d, list, s, mvsResp, actResp]: any[] = await Promise.all([
      datasetApi.get(datasetId.value),
      imageApi.list(datasetId.value, {
        // 'all' 翻译成 undefined (不传 status 参数, 后端返所有)
        status: statusFilter.value === 'all' ? undefined : statusFilter.value,
        page: page.value,
        page_size: pageSize.value
      }),
      statsApi.dataset(datasetId.value).catch(() => null),
      // 与「标注工作台 Annotate.vue」一致:
      // 顶部模型下拉只显示**当前数据集已激活的** fine-tune 模型
      // 后端 /models/ 支持 dataset_id + active 过滤, 一次拉到位
      modelApi.list({ dataset_id: datasetId.value, active: true }).catch(() => ({ items: [] })),
      // 保留 actResp 给启动预标注时用 (后端兜底)
      modelApi.getActive(datasetId.value).catch(() => ({ model: null })),
    ])
    dataset.value = d
    images.value = list?.items || []
    total.value = list?.total || 0
    stats.value = s

    // 当前数据集已激活的 fine-tune 模型
    // (后端已按 dataset_id + active 过滤; 客户端再冗余校验, 防止 API 返回异常)
    const allFT = mvsResp?.items || mvsResp || []
    finetuneModels.value = allFT.filter((m: any) => m.dataset_id === datasetId.value && m.is_active)

    // 默认选择: list 本身就是按 active=true 过滤的结果, 取第一个即可
    if (finetuneModels.value.length > 0) {
      selectedFinetuneId.value = finetuneModels.value[0].id
    } else {
      selectedFinetuneId.value = null
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
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

/** 单张去除标注 (人工 + AI 预标注) */
async function clearOneAnnotation(img: any) {
  const hadHuman = ['human_confirmed', 'human_corrected', 'trained'].includes(img.status)
  const hadAi = img.status === 'ai_labeled' && img.ai_prediction
  if (!hadHuman && !hadAi && !img.final_label_id) {
    ElMessage.warning('该图片尚无标注, 无需去除')
    return
  }
  // 提示文案根据是否含 AI 标注区分
  const tip = hadHuman
    ? `确认去除「${img.filename}」的人工标注? 该图片将回到待标注状态, 历史会保留在审计日志`
    : hadAi
      ? `确认去除「${img.filename}」的 AI 预标注? 该图片将回到待标注状态, 下次自动标注会重新预测`
      : `确认去除「${img.filename}」的标注? 该图片将回到待标注状态, 历史会保留在审计日志`
  try {
    await ElMessageBox.confirm(tip, '去除标注', { type: 'warning' })
  } catch { return }
  try {
    const r: any = await annotationApi.clear([img.id])
    const ok = r?.cleared || 0
    const skip = r?.skipped || 0
    if (ok > 0) {
      const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
      ElMessage.success(
        `已去除标注 (${ok} 张${aiN > 0 ? `, 含 AI 预标注 ${aiN} 张` : ''}${skip > 0 ? `, 跳过 ${skip} 张` : ''})`
      )
    } else {
      ElMessage.info(`无需处理 (跳过 ${skip} 张)`)
    }
    selectedIds.value = selectedIds.value.filter((id) => id !== img.id)
    await load()
  } catch (e: any) {
    ElMessage.error('去除标注失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/** 批量去除标注 (勾选的多张) */
async function batchClearAnnotation() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择图片')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认对选中的 ${selectedIds.value.length} 张图片执行"去除标注"?` +
      `\n对人工标注和 AI 预标注都会生效, 无标注的图片会跳过; 历史会保留在审计日志`,
      '批量去除标注',
      { type: 'warning' }
    )
  } catch { return }
  try {
    const r: any = await annotationApi.clear(selectedIds.value)
    const ok = r?.cleared || 0
    const skip = (r?.skipped || 0) + (r?.missing || 0)
    const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
    if (ok > 0) {
      ElMessage.success(
        `已去除标注 ${ok} 张${aiN > 0 ? `, 含 AI 预标注 ${aiN} 张` : ''}${skip > 0 ? `, 跳过 ${skip} 张` : ''}`
      )
    } else {
      ElMessage.info('所选图片均无标注, 跳过')
    }
    selectedIds.value = []
    await load()
  } catch (e: any) {
    ElMessage.error('批量去除标注失败: ' + (e?.response?.data?.detail || e?.message))
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
function goAnnotate() { router.push(`/annotate/${datasetId.value}`) }

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

onMounted(load)
watch(statusFilter, resetPage)
watch(() => route.params.id, () => load())
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

    <!-- 统计卡 (与 Dashboard 风格统一: 2 行 x 3 列, 避免卡片过窄导致中文换行) -->
    <el-row v-if="stats" :gutter="14" class="ds-stats">
      <el-col :xs="12" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><Picture /></el-icon></div>
          <el-statistic title="图片总数" :value="dataset?.image_count || 0" />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="8">
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
      <el-col :xs="12" :sm="8" :md="8">
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
      <el-col :xs="12" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card stat-card--purple">
          <div class="stat-icon"><el-icon><Clock /></el-icon></div>
          <el-statistic title="平均耗时"
            :value="stats.annotation?.avg_seconds_per_image || 0"
            suffix="秒/张" />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card stat-card--warm">
          <div class="stat-icon"><el-icon><MagicStick /></el-icon></div>
          <el-statistic title="AI 节省时间"
            :value="stats.annotation?.estimated_saved_seconds || 0"
            suffix="秒" />
          <div class="stat-meta">
            按 3 秒/张估算
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card stat-card--cyan">
          <div class="stat-icon"><el-icon><CollectionTag /></el-icon></div>
          <el-statistic title="类别数" :value="dataset?.category_count || 0" />
        </el-card>
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
            content="选择用于 AI 预标注的 fine-tune 模型. 若数据集暂无激活的 fine-tune 模型, 切换到模型管理页面激活"
          >
            <el-select
              v-model="selectedFinetuneId"
              placeholder="选择 fine-tune 模型 (仅本数据集已激活)"
              size="default"
              :fit-input-width="false"
              popper-class="app-select-dropdown model-select-dropdown"
              class="app-select"
              filterable
              :empty-text="finetuneModels.length === 0 ? '该数据集暂无激活的 fine-tune 模型' : '无可用模型'"
            >
              <el-option
                v-for="m in finetuneModels" :key="m.id" :value="m.id"
                :label="`${m.name} · ${m.base_model}`"
              >
                <div class="ft-option">
                  <span class="ft-option__name">{{ m.name }}</span>
                  <span class="ft-option__meta">
                    <el-tag size="small" type="info" effect="plain">{{ m.base_model }}</el-tag>
                    <el-tag size="small" type="warning" effect="plain" style="margin-left: 4px;">
                      {{ (m.accuracy * 100).toFixed(1) }}%
                    </el-tag>
                  </span>
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
          <el-tooltip
            placement="top" :show-after="200"
            content="dry-run 试跑当前页图片, 不写库, 弹窗显示 3 类: 会标/待标/无交集, 帮你在执行批量预标注前评估阈值是否合适"
          >
            <el-button
              plain :icon="DataAnalysis" :loading="previewing"
              :disabled="images.length === 0"
              @click="onPreviewConfidence"
              class="filter-cell filter-cell--btn"
            >测评</el-button>
          </el-tooltip>
        </div>

        <!-- ===== 操作组 (去标注; 导出已回到 page header 原位) ===== -->
        <div class="filter-group filter-group--ops">
          <el-tooltip placement="top" :show-after="200" content="跳转到标注工作台, 继续人工确认/修正">
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
            :icon="RefreshLeft" @click="batchClearAnnotation" class="filter-cell filter-cell--btn">去标</el-button>
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
      <div class="ai-config-hint">
        <el-icon class="ai-config-hint__icon"><InfoFilled /></el-icon>
        <span>
          当前将用
          <b class="ai-config-hint__model">{{ displayModel }}</b>
          对 <b>{{ pendingCount }}</b> 张「待标注」图片进行预标注,
          置信度 ≥ <b>{{ (threshold * 100).toFixed(0) }}%</b> 的图片会自动落标,
          其余保留为「待标注」由人工复核。
        </span>
        <el-tag v-if="pendingCount === 0" type="success" size="small" effect="plain">
          暂无待标注
        </el-tag>
        <el-tag v-else size="small" type="info" effect="plain">
          待标 {{ pendingCount }}
        </el-tag>
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
          @click.native="toggleSelect(img.id)"
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
            <el-button
              v-if="img.final_label_id || ['human_confirmed','human_corrected','trained','ai_labeled'].includes(img.status)"
              class="clear-btn"
              type="warning"
              :icon="RefreshLeft"
              size="small"
              circle
              @click.stop="clearOneAnnotation(img)"
            />
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
          >去标</el-button>
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
          <PreviewList :items="previewGroups.would" />
        </el-tab-pane>
        <el-tab-pane :name="'human'">
          <template #label>
            <span><el-icon><InfoFilled /></el-icon> 需人工复核 ({{ previewGroups.human.length }})</span>
          </template>
          <PreviewList :items="previewGroups.human" />
        </el-tab-pane>
        <el-tab-pane :name="'none'">
          <template #label>
            <span><el-icon><CircleClose /></el-icon> 无匹配 ({{ previewGroups.none.length }})</span>
          </template>
          <PreviewList :items="previewGroups.none" />
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
.stat-card--warm::before   { background: var(--gradient-warm); }
.stat-card--purple::before { background: linear-gradient(135deg, #722ed1 0%, #531dab 100%); }
.stat-card--cyan::before   { background: linear-gradient(135deg, #00a3e0 0%, #00c48c 100%); }

.stat-card :deep(.el-card__body) {
  padding: 22px 24px;
  position: relative;
  /* 让卡片在同 row 内等高: 撑满父级 (theme.css 已设 .stat-card height: 100%) */
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 8px;
  /* 给右上角图标留位, 避免标题被覆盖 */
  padding-right: 56px;
  min-height: 18px;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 28px;
  font-weight: 600;
  color: var(--text-primary);
  padding-right: 56px;
  line-height: 1.15;
}
.stat-icon {
  position: absolute;
  right: 18px;
  top: 18px;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon :deep(.el-icon) { font-size: 22px; }
.stat-card--blue   .stat-icon { background: rgba(79, 124, 255, 0.1);  color: #4f7cff; }
.stat-card--green  .stat-icon { background: rgba(0, 196, 140, 0.1);  color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1);  color: #ff8a4c; }
.stat-card--warm   .stat-icon { background: rgba(255, 138, 76, 0.1);  color: #ff8a4c; }
.stat-card--purple .stat-icon { background: rgba(114, 46, 209, 0.1);  color: #722ed1; }
.stat-card--cyan   .stat-icon { background: rgba(0, 163, 224, 0.1);   color: #00a3e0; }
.stat-meta {
  color: var(--text-placeholder);
  margin-top: 6px;
  font-size: 12px;
  padding-right: 56px;
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
/* Fine-tune 下拉中的选项排版: 名称 + 基础模型标签 + 准确率标签 */
.ft-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}
.ft-option__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
}
.ft-option__meta {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
}
.ft-option__icon {
  font-size: 10px;
  margin-right: 2px;
  vertical-align: middle;
}
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
}
.ai-config-hint__icon {
  color: var(--color-primary, #409eff);
  font-size: 14px;
  flex: 0 0 auto;
}
.ai-config-hint__model { color: var(--color-primary, #409eff); }
.ai-config-hint b { color: var(--text-primary); font-weight: 600; }
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
