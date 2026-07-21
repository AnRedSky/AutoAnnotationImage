<!--
  DetectionStatsPanel.vue (v2.2.0 S9.2)
  =======================================
  目标检测数据集统计面板
  - 类别分布柱状图 (各类别 bbox 数)
  - bbox 宽高散点图 (按类别上色, 看尺寸分布)
  - 每图 bbox 数 top 20
  - 关键指标: bbox 总数 / 已标图数 / 覆盖率

  设计: 零业务耦合 - 通过 props 接收 stats 数据; emit 事件给父组件.
  数据由父组件 (Datasets.vue 弹窗) 从 GET /api/detection/stats/{dataset_id} 拉取后传入.
-->
<template>
  <div class="det-stats-panel">
    <el-empty v-if="!stats" description="暂无统计数据" />

    <template v-else>
      <!-- 关键指标卡 -->
      <el-row :gutter="12" class="kpi-row">
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-value">{{ stats.total_bboxes }}</div>
            <div class="kpi-label">bbox 总数</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-value">{{ stats.annotated_images }}</div>
            <div class="kpi-label">已标注图数</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-value">{{ stats.total_images }}</div>
            <div class="kpi-label">总图数</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-card">
            <div class="kpi-value">{{ coverage }}%</div>
            <div class="kpi-label">覆盖率</div>
          </div>
        </el-col>
      </el-row>

      <!-- 类别分布 + 宽高散点 -->
      <el-row :gutter="12" style="margin-top: 12px;">
        <el-col :span="12">
          <el-card shadow="hover" header="各类别 bbox 数">
            <div ref="catChartRef" class="chart-box"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="hover" header="bbox 宽高分布 (按类别上色)">
            <div ref="scatterChartRef" class="chart-box"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 每图 bbox 数 top 20 -->
      <el-card shadow="hover" header="每图 bbox 数 (Top 20)" style="margin-top: 12px;">
        <div ref="perImgChartRef" class="chart-box-tall"></div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'

interface CategoryStat {
  category_id: number | null
  name: string
  count: number
}
interface ScatterPoint {
  w: number
  h: number
  category_id: number | null
}
interface PerImageStat {
  image_id: number
  filename: string
  count: number
}
interface Stats {
  dataset_id: number
  total_bboxes: number
  annotated_images: number
  total_images: number
  category_distribution: CategoryStat[]
  per_image_count: PerImageStat[]
  bbox_scatter: ScatterPoint[]
}
const props = defineProps<{
  stats: Stats | null
  categories?: { id: number; name: string; color?: string }[]
}>()

// 调色板 (与 DetectionAnnotator 一致, 视觉连贯)
const PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function colorOf(catId: number | null | undefined, idx: number): string {
  if (catId != null) {
    return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
  }
  return PALETTE[idx % PALETTE.length]
}
function nameOf(catId: number | null | undefined, fallback: string): string {
  if (catId == null) return fallback
  const c = props.categories?.find((x) => x.id === catId)
  return c?.name || fallback
}

const coverage = computed(() => {
  if (!props.stats || !props.stats.total_images) return '0.0'
  return ((props.stats.annotated_images / props.stats.total_images) * 100).toFixed(1)
})

// ============== ECharts 实例 ==============
let catChart: echarts.ECharts | null = null
let scatterChart: echarts.ECharts | null = null
let perImgChart: echarts.ECharts | null = null
const catChartRef = ref<HTMLDivElement | null>(null)
const scatterChartRef = ref<HTMLDivElement | null>(null)
const perImgChartRef = ref<HTMLDivElement | null>(null)

function initCharts() {
  if (catChartRef.value && !catChart) catChart = echarts.init(catChartRef.value)
  if (scatterChartRef.value && !scatterChart) scatterChart = echarts.init(scatterChartRef.value)
  if (perImgChartRef.value && !perImgChart) perImgChart = echarts.init(perImgChartRef.value)
}

function renderCatChart() {
  if (!catChart || !props.stats) return
  const data = props.stats.category_distribution.map((c, i) => ({
    name: nameOf(c.category_id, c.name),
    value: c.count,
    itemStyle: { color: colorOf(c.category_id, i) },
  }))
  catChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: data.map((d) => d.name), axisLabel: { rotate: 30, fontSize: 11 } },
    yAxis: { type: 'value', name: 'bbox 数' },
    series: [{
      type: 'bar',
      data,
      label: { show: true, position: 'top', fontSize: 11 },
      barWidth: '50%',
    }],
  }, true)
}

function renderScatterChart() {
  if (!scatterChart || !props.stats) return
  // 按 category_id 分组, 每组一个 series (ECharts scatter 上色)
  const groups = new Map<number | string, ScatterPoint[]>()
  props.stats.bbox_scatter.forEach((p) => {
    const k = p.category_id ?? 'none'
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(p)
  })
  const series = Array.from(groups.entries()).map(([k, pts], idx) => {
    const cid = k === 'none' ? null : (k as number)
    const name = nameOf(cid, '(未分类)')
    return {
      name,
      type: 'scatter',
      symbolSize: 6,
      data: pts.map((p) => [p.w, p.h]),
      itemStyle: { color: colorOf(cid, idx), opacity: 0.6 },
    }
  })
  scatterChart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.seriesName}<br/>w=${p.data[0].toFixed(3)} h=${p.data[1].toFixed(3)}`,
    },
    legend: { type: 'scroll', top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'value', name: '宽度 (归一化)', min: 0, max: 1 },
    yAxis: { type: 'value', name: '高度 (归一化)', min: 0, max: 1 },
    series,
  }, true)
}

function renderPerImgChart() {
  if (!perImgChart || !props.stats) return
  const top = props.stats.per_image_count.slice(0, 20).reverse()  // 横向柱状图: 少在上, 多在下
  perImgChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', name: 'bbox 数' },
    yAxis: { type: 'category', data: top.map((p) => p.filename || `#${p.image_id}`), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar',
      data: top.map((p) => p.count),
      itemStyle: { color: '#409eff' },
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }, true)
}

function renderAll() {
  initCharts()
  renderCatChart()
  renderScatterChart()
  renderPerImgChart()
}

onMounted(() => nextTick(() => renderAll()))

// 监听 props.stats 变化重绘
watch(
  () => props.stats,
  () => nextTick(() => renderAll()),
  { deep: true }
)

function handleResize() {
  catChart?.resize()
  scatterChart?.resize()
  perImgChart?.resize()
}
onMounted(() => window.addEventListener('resize', handleResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  catChart?.dispose(); catChart = null
  scatterChart?.dispose(); scatterChart = null
  perImgChart?.dispose(); perImgChart = null
})
</script>

<style scoped>
.det-stats-panel {
  padding: 4px;
}
.kpi-row {
  margin-bottom: 4px;
}
.kpi-card {
  background: linear-gradient(135deg, #f0f5ff 0%, #ffffff 100%);
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px 12px;
  text-align: center;
  transition: all 0.2s;
}
.kpi-card:hover {
  box-shadow: 0 2px 12px 0 rgba(64, 158, 255, 0.15);
  transform: translateY(-1px);
}
.kpi-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}
.kpi-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.chart-box {
  width: 100%;
  height: 280px;
}
.chart-box-tall {
  width: 100%;
  height: 360px;
}
</style>
