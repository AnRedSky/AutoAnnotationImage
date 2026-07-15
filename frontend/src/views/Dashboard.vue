<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { statsApi, datasetApi } from '@/api'
import {
  Folder, Picture, CircleCheck, Grid, Promotion, CollectionTag
} from '@element-plus/icons-vue'

const overview = ref<any>({})
const datasets = ref<any[]>([])
const datasetId = ref<number | null>(null)
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
    if (list.length > 0 && !datasetId.value) datasetId.value = list[0].id
    const eff: any = await statsApi.annotatorEfficiency()
    efficiency.value = eff
  } catch (e) {
    console.error('概览加载失败', e)
  }
}

const loadDatasetStats = async () => {
  if (!datasetId.value) return
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

onMounted(async () => {
  await loadOverview()
  await nextTick()
  initCharts()
  await loadDatasetStats()
  updateCharts()
})

watch(datasetId, () => { loadDatasetStats() })
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
  <div>
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

    <!-- 数据集选择 -->
    <el-card class="selector-card" style="margin-top: 16px;">
      <template #header>
        <div class="card-header">
          <span>选择数据集</span>
          <el-tag v-if="datasetId" size="small" effect="plain" type="info">
            当前: {{ datasets.find((d: any) => d.id === datasetId)?.name || datasetId }}
          </el-tag>
        </div>
      </template>
      <el-select v-model="datasetId" placeholder="请选择" style="width: 280px;" filterable>
        <el-option
          v-for="d in datasets"
          :key="d.id"
          :label="d.name"
          :value="d.id"
        />
      </el-select>
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
</style>
