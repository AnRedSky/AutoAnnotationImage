<script setup lang="ts">
/**
 * 团队统计卡片 (v3.3.1 L2)
 * ========================
 *
 * 集成 ECharts 可视化, 展示团队维度的标注/AI 节省时间数据。
 * 仅团队成员可访问 (后端已校验, 403 会被自动捕获)。
 *
 * Props:
 *  - teamId:  当前团队 ID
 *  - days:    趋势天数 (1-90, 默认 7)
 *
 * Emits:
 *  - refresh-after-action:  数据变更后请求父级刷新
 *   (预留, 当前无写操作)
 */
import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { statsApi, type TeamStats } from '@/api'

const props = withDefaults(defineProps<{
  teamId: number
  days?: number
}>(), {
  days: 7,
})

const emit = defineEmits<{
  (e: 'refresh-after-action'): void
}>()

// ============== 状态 ==============
const loading = ref(false)
const stats = ref<TeamStats | null>(null)

const statusChartRef = ref<HTMLDivElement>()
const contributorChartRef = ref<HTMLDivElement>()
const timelineChartRef = ref<HTMLDivElement>()
let statusChart: echarts.ECharts | null = null
let contributorChart: echarts.ECharts | null = null
let timelineChart: echarts.ECharts | null = null

// ============== 状态映射 (中文标签 + 配色) ==============
const STATUS_LABELS: Record<string, string> = {
  pending: '待标注',
  ai_labeled: 'AI 已标',
  human_confirmed: '人工确认',
  human_corrected: '人工修正',
  trained: '已训练',
}
const STATUS_COLORS: Record<string, string> = {
  pending: '#909399',
  ai_labeled: '#4f7cff',
  human_confirmed: '#67c23a',
  human_corrected: '#e6a23c',
  trained: '#9b59b6',
}

// ============== 数据加载 ==============
const load = async () => {
  if (!props.teamId) return
  loading.value = true
  try {
    const res = await statsApi.team(props.teamId, props.days)
    stats.value = res
    await nextTick()
    renderAllCharts()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || '加载失败'
    ElMessage.error('团队统计加载失败: ' + msg)
    stats.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.teamId, () => { load() })
watch(() => props.days, () => { load() })

onMounted(() => {
  load()
  initCharts()
})

onBeforeUnmount(() => {
  statusChart?.dispose()
  contributorChart?.dispose()
  timelineChart?.dispose()
})

// ============== 图表初始化 ==============
const initCharts = () => {
  if (statusChartRef.value) {
    statusChart = echarts.init(statusChartRef.value)
    statusChart.setOption({
      title: { text: '图片状态分布', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie', radius: ['40%', '70%'], avoidLabelOverlap: true,
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11 },
        data: [],
      }],
    })
  }
  if (contributorChartRef.value) {
    contributorChart = echarts.init(contributorChartRef.value)
    contributorChart.setOption({
      title: { text: '贡献者 Top 10', left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 50, right: 20, bottom: 30, left: 60 },
      xAxis: { type: 'value', name: '标注数' },
      yAxis: { type: 'category', data: [], inverse: true },
      series: [{
        type: 'bar', data: [],
        itemStyle: { color: '#4f7cff', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', fontSize: 11 },
      }],
    })
  }
  if (timelineChartRef.value) {
    timelineChart = echarts.init(timelineChartRef.value)
    timelineChart.setOption({
      title: { text: `近 ${props.days} 日标注量`, left: 'center', textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: 'axis' },
      grid: { top: 50, right: 20, bottom: 30, left: 50 },
      xAxis: { type: 'category', data: [], axisLabel: { rotate: 30, fontSize: 10 } },
      yAxis: { type: 'value', name: '标注数', minInterval: 1 },
      series: [{
        type: 'line', data: [], smooth: true, symbol: 'circle', symbolSize: 6,
        lineStyle: { color: '#4f7cff', width: 2 },
        itemStyle: { color: '#4f7cff' },
        areaStyle: { color: 'rgba(79, 124, 255, 0.15)' },
      }],
    })
  }
}

// ============== 图表渲染 ==============
const renderAllCharts = () => {
  renderStatusChart()
  renderContributorChart()
  renderTimelineChart()
}

const renderStatusChart = () => {
  if (!statusChart || !stats.value) return
  const data = Object.entries(stats.value.status_counts || {}).map(([k, v]) => ({
    name: STATUS_LABELS[k] || k,
    value: v,
    itemStyle: { color: STATUS_COLORS[k] || '#909399' },
  }))
  statusChart.setOption({ series: [{ data }] })
}

const renderContributorChart = () => {
  if (!contributorChart || !stats.value) return
  const list = stats.value.top_contributors || []
  const yData = list.map((c) => c.username).reverse()
  const xData = list.map((c) => c.annotation_count).reverse()
  contributorChart.setOption({ yAxis: { data: yData }, series: [{ data: xData }] })
}

const renderTimelineChart = () => {
  if (!timelineChart || !stats.value) return
  const list = stats.value.timeline?.data || []
  const xData = list.map((d) => d.date.slice(5)) // MM-DD
  const yData = list.map((d) => d.count)
  timelineChart.setOption({
    title: { text: `近 ${stats.value.timeline.days} 日标注量` },
    xAxis: { data: xData },
    series: [{ data: yData }],
  })
}

// ============== 派生数据 (用于 KPI 卡片) ==============
const labeledRate = computed(() => {
  if (!stats.value || stats.value.image_total === 0) return 0
  return Math.round((stats.value.labeled_total / stats.value.image_total) * 1000) / 10
})

const savedHours = computed(() => {
  if (!stats.value) return 0
  return Math.round((stats.value.ai_saved.estimated_saved_seconds / 3600) * 10) / 10
})

defineExpose({ reload: load })
</script>

<template>
  <div class="team-stats-card" v-loading="loading">
    <!-- 团队无数据 -->
    <el-empty
      v-if="!loading && stats && stats.dataset_count === 0"
      description="该团队暂无共享数据集, 无法展示统计数据"
    />

    <!-- 团队数据 -->
    <template v-if="stats && stats.dataset_count > 0">
      <!-- KPI 4 卡 -->
      <el-row :gutter="16" class="kpi-row">
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-label">数据集</div>
            <div class="kpi-value">{{ stats.dataset_count }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-label">图片总数</div>
            <div class="kpi-value">{{ stats.image_total }}</div>
            <div class="kpi-extra">已标 {{ stats.labeled_total }} ({{ labeledRate }}%)</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-label">团队成员</div>
            <div class="kpi-value">{{ stats.member_count }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-label">不合格</div>
            <div class="kpi-value">{{ stats.unqualified_count }}</div>
          </div>
        </el-col>
      </el-row>

      <!-- AI 节省时间 -->
      <el-alert
        type="success"
        :closable="false"
        class="ai-saved-alert"
        show-icon
      >
        <template #title>
          <strong>AI 节省时间估算</strong>:
          {{ savedHours }} 小时
          (节省 {{ Math.round(stats.ai_saved.estimated_saved_ratio * 100) }}%
          · 共 {{ stats.ai_saved.total_annotations }} 次标注
          · 平均 {{ stats.ai_saved.avg_seconds_per_image }}s/张)
        </template>
      </el-alert>

      <!-- 3 个图表 -->
      <el-row :gutter="16" class="chart-row">
        <el-col :span="8">
          <div ref="statusChartRef" class="chart-box"></div>
        </el-col>
        <el-col :span="8">
          <div ref="contributorChartRef" class="chart-box"></div>
        </el-col>
        <el-col :span="8">
          <div ref="timelineChartRef" class="chart-box"></div>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.team-stats-card {
  width: 100%;
}
.kpi-row {
  margin-bottom: 16px;
}
.kpi-card {
  background: var(--bg-secondary, #f7f8fa);
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 8px;
  padding: 16px;
  text-align: center;
}
.kpi-label {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-bottom: 4px;
}
.kpi-value {
  font-size: 28px;
  font-weight: 600;
  color: var(--text-primary, #303133);
  line-height: 1.2;
}
.kpi-extra {
  font-size: 11px;
  color: var(--text-secondary, #909399);
  margin-top: 4px;
}
.ai-saved-alert {
  margin-bottom: 16px;
}
.chart-row {
  margin-top: 8px;
}
.chart-box {
  height: 320px;
  background: #fff;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
}
</style>
