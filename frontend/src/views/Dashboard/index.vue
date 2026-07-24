<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { statsApi, datasetApi } from '@/api'
import { useUserStore } from '@/stores/user'
import {
  Folder, Picture, CircleCheck, Grid, Promotion, CollectionTag, DataAnalysis, TrendCharts, Sunny, Cloudy
} from '@element-plus/icons-vue'

const userStore = useUserStore()

const overview = ref<any>({})
const datasets = ref<any[]>([])
const datasetId = ref<number | null>(null)

/** 任务类型筛选 (与标注工作台 AnnotationToolbar 对齐)
 *  - 固定排序: 图片分类 / 目标检测 / 图片分割 (3 项, 不含"全部")
 *  - 默认值: classification (持久化于 localStorage)
 *  - 联动: 切换后 filteredDatasets 重算, 若当前 datasetId 不在范围内则切到第一项 */
const TASK_TYPE_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
]
const TASK_TYPE_STORAGE_KEY = 'dashboard_task_type_filter'

/** 从 localStorage 读取任务类型筛选, 失败回退默认 classification */
const loadTaskTypeFromStorage = (): string => {
  try {
    const stored = localStorage.getItem(TASK_TYPE_STORAGE_KEY)
    if (stored && TASK_TYPE_FILTER_OPTIONS.some((o) => o.value === stored)) {
      return stored
    }
  } catch (e) {
    /* localStorage 不可用 (隐私模式等) 时静默回退 */
  }
  return 'classification'
}

const taskTypeFilter = ref<string>(loadTaskTypeFromStorage())

/** 按 taskTypeFilter 过滤后的数据集列表 (与标注工作台 filteredDatasets 一致) */
const filteredDatasets = computed(() => {
  const f = taskTypeFilter.value
  if (!f) return datasets.value
  return datasets.value.filter((d) => (d.task_type || 'classification') === f)
})

/** 时间感知的问候语 + 图标, 让顶部 hero 更有"日常感" */
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6)  return { text: '夜深了, 注意休息', icon: Cloudy, gradient: 'bg-gradient-night' }
  if (h < 11) return { text: '早上好', icon: Sunny, gradient: 'bg-gradient-warm' }
  if (h < 14) return { text: '中午好', icon: Sunny, gradient: 'bg-gradient-warm' }
  if (h < 18) return { text: '下午好', icon: Sunny, gradient: 'bg-gradient-cool' }
  return { text: '晚上好', icon: Cloudy, gradient: 'bg-gradient-night' }
})
const username = computed(() => userStore.user?.username || '同学')
const today = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'
})
const datasetStats = ref<any>(null)
const confidence = ref<any>(null)
const timeline = ref<any>(null)
const efficiency = ref<any>(null)

const statusChartRef = ref<HTMLDivElement>()
const confidenceChartRef = ref<HTMLDivElement>()
const timelineChartRef = ref<HTMLDivElement>()
const efficiencyChartRef = ref<HTMLDivElement>()
let statusChart: echarts.ECharts | null = null
let confidenceChart: echarts.ECharts | null = null
let timelineChart: echarts.ECharts | null = null
let efficiencyChart: echarts.ECharts | null = null

const loadOverview = async () => {
  try {
    const ov: any = await statsApi.overview()
    overview.value = ov || {}
    const ds: any = await datasetApi.list()
    const list = ds?.items || ds || []
    datasets.value = list
    // 优先沿用当前 taskTypeFilter 范围内的数据集; 否则取该任务类型第一项; 都没有则 null
    if (list.length > 0) {
      const f = taskTypeFilter.value
      const matched = list.filter((d: any) => (d.task_type || 'classification') === f)
      if (matched.length > 0) {
        if (!datasetId.value || !matched.some((d: any) => d.id === datasetId.value)) {
          datasetId.value = matched[0].id
        }
      } else {
        // 当前任务类型下无数据集, 置空
        datasetId.value = null
      }
    }
    await loadEfficiency()
  } catch (e) {
    console.error('概览加载失败', e)
  }
}

const loadEfficiency = async () => {
  try {
    const eff: any = await statsApi.annotatorEfficiency({ task_type: taskTypeFilter.value })
    efficiency.value = eff
  } catch (e) {
    console.error('标注员效率加载失败', e)
  }
}

const loadDatasetStats = async () => {
  if (!datasetId.value) {
    // 当前任务类型下无数据集, 清空图表数据避免脏数据
    datasetStats.value = null
    confidence.value = null
    timeline.value = null
    return
  }
  try {
    const ds: any = await statsApi.dataset(datasetId.value)
    datasetStats.value = ds
    const cf: any = await statsApi.confidence(datasetId.value)
    confidence.value = cf
    const tl: any = await statsApi.timeline(datasetId.value, 14)
    timeline.value = tl
  } catch (e) {
    console.error('数据集统计失败', e)
  }
}

/** 任务类型切换处理:
 *  - 切换后, 若当前 datasetId 不在新筛选范围, 切到范围内第一项 (或 null)
 *  - datasetId 与 taskTypeFilter 联动由下方 watch 触发数据重载 */
const onTaskTypeFilterChange = (v: string) => {
  taskTypeFilter.value = v
  const list = filteredDatasets.value
  const currentInList = list.some((d) => d.id === datasetId.value)
  if (!currentInList) {
    datasetId.value = list.length > 0 ? list[0].id : null
  }
}

onMounted(async () => {
  await loadOverview()
  await nextTick()
  initCharts()
  await loadDatasetStats()
  updateCharts()
})

watch(datasetId, () => { loadDatasetStats() })
/** 任务类型切换:
 *  - localStorage 持久化 (供刷新/导航后恢复)
 *  - 重新拉取标注员效率 (按新 task_type 过滤)
 *  - datasetId 的重选由 onTaskTypeFilterChange 同步处理 */
watch(taskTypeFilter, async (newVal) => {
  try {
    localStorage.setItem(TASK_TYPE_STORAGE_KEY, newVal)
  } catch (e) { /* 静默 */ }
  await loadEfficiency()
})
watch(datasetStats, () => updateStatusChart(), { deep: true })
watch(confidence, () => updateConfidenceChart(), { deep: true })
watch(timeline, () => updateTimelineChart(), { deep: true })
watch(efficiency, () => updateEfficiencyChart(), { deep: true })

onBeforeUnmount(() => {
  statusChart?.dispose()
  confidenceChart?.dispose()
  timelineChart?.dispose()
  efficiencyChart?.dispose()
})

const initCharts = () => {
  if (statusChartRef.value) {
    statusChart = echarts.init(statusChartRef.value)
    statusChart.setOption({
      title: { text: '图片状态分布', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'item' },
      legend: { bottom: 0 },
      color: ['#4f7cff', '#67c23a', '#e6a23c', '#f56c6c', '#909399'],
      series: [{ type: 'pie', radius: ['40%', '70%'], data: [] }]
    })
  }
  if (confidenceChartRef.value) {
    confidenceChart = echarts.init(confidenceChartRef.value)
    confidenceChart.setOption({
      title: { text: 'AI 置信度分布', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'axis' },
      grid: { top: 50, right: 20, bottom: 30, left: 50 },
      xAxis: { type: 'category', data: [] },
      yAxis: { type: 'value', name: '图片数' },
      series: [{ type: 'bar', data: [], itemStyle: { color: '#4f7cff', borderRadius: [4, 4, 0, 0] } }]
    })
  }
  if (timelineChartRef.value) {
    timelineChart = echarts.init(timelineChartRef.value)
    timelineChart.setOption({
      title: { text: '近 14 日标注量', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'axis' },
      grid: { top: 50, right: 20, bottom: 30, left: 50 },
      xAxis: { type: 'category', data: [], boundaryGap: false },
      yAxis: { type: 'value', name: '图片数' },
      series: [{
        type: 'line', smooth: true,
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(79, 124, 255, 0.3)' },
            { offset: 1, color: 'rgba(79, 124, 255, 0)' }
          ])
        },
        lineStyle: { color: '#4f7cff', width: 2 },
        itemStyle: { color: '#4f7cff' },
        data: []
      }]
    })
  }
  if (efficiencyChartRef.value) {
    efficiencyChart = echarts.init(efficiencyChartRef.value)
    efficiencyChart.setOption({
      title: { text: '标注员效率 (Top 10)', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['标注数', '平均秒数'], top: 30 },
      grid: { top: 70, right: 50, bottom: 30, left: 50 },
      xAxis: { type: 'category', data: [] },
      yAxis: [
        { type: 'value', name: '标注数' },
        { type: 'value', name: '秒/张', position: 'right' }
      ],
      series: [
        { name: '标注数', type: 'bar', itemStyle: { color: '#4f7cff', borderRadius: [4, 4, 0, 0] }, data: [] },
        { name: '平均秒数', type: 'line', yAxisIndex: 1, itemStyle: { color: '#ff8a4c' }, lineStyle: { color: '#ff8a4c' }, data: [] }
      ]
    })
  }
  window.addEventListener('resize', onResize)
}

const onResize = () => {
  statusChart?.resize()
  confidenceChart?.resize()
  timelineChart?.resize()
  efficiencyChart?.resize()
}

const updateCharts = () => {
  updateStatusChart()
  updateConfidenceChart()
  updateTimelineChart()
  updateEfficiencyChart()
}

const updateStatusChart = () => {
  if (!statusChart || !datasetStats.value) return
  const data = Object.entries(datasetStats.value.status_counts || {}).map(([k, v]) => ({
    name: k, value: v
  }))
  statusChart.setOption({ series: [{ data }] })
}

const updateConfidenceChart = () => {
  if (!confidenceChart || !confidence.value) return
  const buckets = confidence.value.buckets || []
  confidenceChart.setOption({
    xAxis: { data: buckets.map((b: any) => b.range) },
    series: [{ data: buckets.map((b: any) => b.count) }]
  })
}

const updateTimelineChart = () => {
  if (!timelineChart || !timeline.value) return
  const t = timeline.value.timeline || []
  timelineChart.setOption({
    xAxis: { data: t.map((d: any) => d.date.slice(5)) },
    series: [{ data: t.map((d: any) => d.count) }]
  })
}

const updateEfficiencyChart = () => {
  if (!efficiencyChart || !efficiency.value) return
  const items = efficiency.value.items || []
  efficiencyChart.setOption({
    xAxis: { data: items.map((it: any) => it.username) },
    series: [
      { data: items.map((it: any) => it.annotation_count) },
      { data: items.map((it: any) => it.avg_seconds), yAxisIndex: 1 }
    ]
  })
}
</script>

<template>
  <div class="dashboard">
    <!-- 顶部 hero 欢迎区: 渐变背景 + 时间感知问候 + 关键数据 -->
    <div class="hero-banner" :class="greeting.gradient">
      <div class="hero-banner__bg" />
      <div class="hero-banner__content">
        <div class="hero-banner__left">
          <div class="hero-banner__greet">
            <el-icon class="hero-banner__icon"><component :is="greeting.icon" /></el-icon>
            <span>{{ greeting.text }}, </span>
            <span class="hero-banner__name">{{ username }}</span>
          </div>
          <div class="hero-banner__sub">
            <span class="hero-banner__date">{{ today }}</span>
            <span class="hero-banner__sep">·</span>
            <span>图像智能标注工作台</span>
          </div>
          <div class="hero-banner__stats">
            <div class="hero-banner__stat">
              <span class="hero-banner__stat-num text-number">{{ overview.datasets || 0 }}</span>
              <span class="hero-banner__stat-label">数据集</span>
            </div>
            <div class="hero-banner__divider" />
            <div class="hero-banner__stat">
              <span class="hero-banner__stat-num text-number">{{ overview.images || 0 }}</span>
              <span class="hero-banner__stat-label">图片</span>
            </div>
            <div class="hero-banner__divider" />
            <div class="hero-banner__stat">
              <span class="hero-banner__stat-num text-number">{{ overview.model_versions || 0 }}</span>
              <span class="hero-banner__stat-label">模型</span>
            </div>
          </div>
        </div>
        <div class="hero-banner__right">
          <div class="hero-banner__chart-icon">
            <el-icon><TrendCharts /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- 顶部 4 个统计卡: 渐变顶部色条 + 右侧图标 -->
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><Folder /></el-icon></div>
          <el-statistic title="数据集" :value="overview.datasets || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--green">
          <div class="stat-icon"><el-icon><Picture /></el-icon></div>
          <el-statistic title="图片总数" :value="overview.images || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--orange">
          <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
          <el-statistic title="已标注" :value="overview.labeled_images || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--red">
          <div class="stat-icon"><el-icon><Grid /></el-icon></div>
          <el-statistic title="模型版本" :value="overview.model_versions || 0" />
        </el-card>
      </el-col>
    </el-row>

    <!-- AI 节省时间核心数据 -->
    <el-row v-if="datasetStats" :gutter="16" style="margin-top: 16px;">
      <el-col :span="8">
        <el-card shadow="hover" class="stat-card stat-card--green">
          <div class="stat-icon"><el-icon><Promotion /></el-icon></div>
          <el-statistic
            title="AI 节省时间估算"
            :value="datasetStats.annotation?.estimated_saved_seconds || 0"
            suffix="秒"
            :value-style="{ color: '#00c48c' }"
          />
          <div class="stat-meta">
            节省比例 {{ ((datasetStats.annotation?.estimated_saved_ratio || 0) * 100).toFixed(1) }}%
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="stat-card stat-card--purple">
          <div class="stat-icon"><el-icon><CollectionTag /></el-icon></div>
          <el-statistic
            title="平均标注耗时"
            :value="datasetStats.annotation?.avg_seconds_per_image || 0"
            suffix="秒/张"
          />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
          <el-statistic
            title="AI 命中"
            :value="datasetStats.annotation?.ai_labeled_count || 0"
            suffix="张"
            :value-style="{ color: '#4f7cff' }"
          />
          <div class="stat-meta">
            待人工 {{ datasetStats.annotation?.human_corrected_count || 0 }} 张
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 数据集 + 任务类型筛选 (与标注工作台 AnnotationToolbar 对齐)
         - 任务类型默认 "图片分类" (持久化于 localStorage)
         - 数据集列表按当前任务类型过滤; 切换后联动 datasetId 重选 -->
    <el-card class="selector-card" style="margin-top: 16px;">
      <template #header>
        <div class="card-header">
          <span>数据视图</span>
          <el-tag v-if="datasetId" size="small" effect="plain" type="info">
            当前数据集: {{ datasets.find((d: any) => d.id === datasetId)?.name || datasetId }}
          </el-tag>
        </div>
      </template>
      <el-form inline class="filter-form">
        <el-form-item label="任务类型">
          <el-select
            :model-value="taskTypeFilter"
            @update:model-value="onTaskTypeFilterChange"
            class="app-select"
            style="width: 160px;"
          >
            <el-option
              v-for="opt in TASK_TYPE_FILTER_OPTIONS"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集">
          <el-select
            v-model="datasetId"
            placeholder="请选择"
            class="app-select"
            filterable
            :disabled="filteredDatasets.length === 0"
            style="min-width: 220px;"
          >
            <el-option
              v-for="d in filteredDatasets"
              :key="d.id"
              :label="d.name"
              :value="d.id"
            />
            <template #empty>
              <div style="padding: 8px 12px; color: #909399; font-size: 12px;">
                当前任务类型下没有数据集, 请切换任务类型或新建数据集
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item>
          <span class="filter-hint">
            共 {{ filteredDatasets.length }} 个{{ taskTypeFilter === 'classification' ? '分类' : taskTypeFilter === 'detection' ? '检测' : '分割' }}数据集
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 4 个图表 -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="12">
        <el-card shadow="hover" class="chart-card">
          <div ref="statusChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover" class="chart-card">
          <div ref="confidenceChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="12">
        <el-card shadow="hover" class="chart-card">
          <div ref="timelineChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover" class="chart-card">
          <div ref="efficiencyChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
/* 统计卡: 渐变顶部色条 + 右侧图标 */
.stat-card {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg) !important;
  padding: 4px;
  background: #fff !important;
  height: 100%;
}
/* 同行卡片等高: el-col 强制 stretch, 卡片宽度填满 */
:deep(.el-row) > [class*="el-col"] { display: flex; }
:deep(.el-row) > [class*="el-col"] > .el-card { width: 100%; }
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  border-radius: 3px 3px 0 0;
}
.stat-card--blue::before { background: var(--gradient-brand); }
.stat-card--green::before { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--red::before { background: linear-gradient(135deg, #ff4d4f 0%, #cf1322 100%); }
.stat-card--purple::before { background: linear-gradient(135deg, #722ed1 0%, #531dab 100%); }

.stat-card :deep(.el-card__body) {
  padding: 22px 24px;
  position: relative;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 8px;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 28px;
  font-weight: 600;
  color: var(--text-primary);
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
.stat-card--blue .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--red .stat-icon { background: rgba(255, 77, 79, 0.1); color: #ff4d4f; }
.stat-card--purple .stat-icon { background: rgba(114, 46, 209, 0.1); color: #722ed1; }

.stat-meta {
  color: var(--text-placeholder);
  margin-top: 8px;
  font-size: 12px;
}

.selector-card {
  border-radius: var(--radius-md) !important;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* 筛选表单: 横向 flex, 移动端自动换行 */
.filter-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 16px;
  row-gap: 8px;
}
.filter-form :deep(.el-form-item) {
  margin-bottom: 0;
  margin-right: 0;
}
.filter-hint {
  color: var(--text-placeholder);
  font-size: 12px;
  line-height: 32px;
  white-space: nowrap;
}

/* 平板/小屏: 筛选控件允许换行, 提示文字下移 */
@media (max-width: 768px) {
  .filter-form {
    flex-direction: column;
    align-items: stretch;
  }
  .filter-form :deep(.el-form-item) {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }
  .filter-form :deep(.el-form-item__label) {
    text-align: left;
    padding: 0 0 4px 0;
    line-height: 1.4;
  }
  .filter-form :deep(.el-select) {
    width: 100% !important;
    min-width: 0 !important;
  }
  .filter-hint {
    line-height: 1.4;
    padding: 4px 0;
  }
}

.chart-card {
  border-radius: var(--radius-md) !important;
  background: #fff !important;
  transition: box-shadow 0.25s var(--ease-out), transform 0.25s var(--ease-out);
}
.chart-card:hover {
  box-shadow: var(--shadow-md) !important;
}
.chart-card :deep(.el-card__body) {
  padding: 16px;
}
.chart-box {
  height: 320px;
  width: 100%;
}

/* 顶部 hero 欢迎区 */
.dashboard > .hero-banner { margin-bottom: 16px; }
.hero-banner {
  position: relative;
  border-radius: var(--radius-xl);
  overflow: hidden;
  color: #fff;
  min-height: 140px;
  box-shadow: var(--shadow-md);
}
.hero-banner__bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 20% 30%, rgba(255,255,255,0.15) 0%, transparent 40%),
    radial-gradient(circle at 80% 70%, rgba(255,255,255,0.10) 0%, transparent 50%);
  pointer-events: none;
}
.hero-banner__content {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 32px;
  gap: 24px;
}
.hero-banner__left { flex: 1 1 auto; min-width: 0; }
.hero-banner__greet {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.4;
}
.hero-banner__icon {
  font-size: 24px;
  color: rgba(255, 255, 255, 0.95);
  filter: drop-shadow(0 1px 2px rgba(0,0,0,0.1));
}
.hero-banner__name {
  background: linear-gradient(135deg, #fff 0%, #ffe4b5 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  font-weight: 700;
}
.hero-banner__sub {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  margin-top: 4px;
  margin-bottom: 16px;
}
.hero-banner__date { color: rgba(255, 255, 255, 0.95); }
.hero-banner__sep { opacity: 0.5; }
.hero-banner__stats {
  display: flex;
  align-items: center;
  gap: 16px;
}
.hero-banner__stat {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
.hero-banner__stat-num {
  font-size: 22px;
  line-height: 1;
  color: #fff;
}
.hero-banner__stat-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.85);
}
.hero-banner__divider {
  width: 1px;
  height: 26px;
  background: rgba(255, 255, 255, 0.3);
}
.hero-banner__right {
  flex: 0 0 auto;
}
.hero-banner__chart-icon {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.25);
  color: #fff;
  font-size: 36px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}
@media (max-width: 768px) {
  .hero-banner__content { flex-direction: column; align-items: flex-start; padding: 20px; }
  .hero-banner__chart-icon { display: none; }
  .hero-banner__greet { font-size: 18px; }
}
</style>
