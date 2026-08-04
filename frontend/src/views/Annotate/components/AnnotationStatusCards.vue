<!--
  AnnotationStatusCards.vue (v3.5.0 新增, 从 AnnotationToolbar 抽出)
  ====================================================
  标注工作台 - 状态筛选卡组 (页面私有子组件)

  职责:
  1. 展示 5 张统计卡: 待标注 / AI 已标 / 已人工标注 / 类别 / 不合格
  2. 前 3 张 (待标注/AI 已标/已人工标注) 同时作为「状态筛选」入口
     - 点击卡片 → emit('status-filter-change', 'pending' | 'ai_labeled' | 'human_labeled')
     - 当前激活的卡片有「主色边框 + ✓ 角标」视觉强调
  3. 后 2 张 (类别 / 不合格) 仅展示, 不参与筛选
  4. 类别卡 hover 展示各类目进度 (与原 AnnotationToolbar 行为一致)
  5. 不合格卡副标题显示占比

  Props (页面私有, 接收父组件状态):
    stats, pendingCount, aiLabeledCount, humanConfirmedCount, humanCorrectedCount
    categories, datasets, datasetId
    activeFilter: StatusFilterValue

  Emits:
    status-filter-change: 状态筛选变化
-->
<template>
  <div v-if="stats" class="annotate-stats">
    <!-- 1) 待标注 (蓝) - 状态筛选入口 -->
    <el-card
      shadow="hover" class="stat-card stat-card--filterable"
      :class="{ 'stat-card--active': activeFilter === 'pending' }"
      @click="emit('status-filter-change', 'pending')"
    >
      <el-statistic title="待标注" :value="pendingCount" suffix="张"
        :value-style="{ color: '#409eff' }" />
      <div class="stat-meta">
        <span class="stat-meta__hint">点击查看 →</span>
      </div>
    </el-card>

    <!-- 2) AI 已标 (紫) - 状态筛选入口 -->
    <el-card
      shadow="hover" class="stat-card stat-card--filterable"
      :class="{ 'stat-card--active': activeFilter === 'ai_labeled' }"
      @click="emit('status-filter-change', 'ai_labeled')"
    >
      <el-statistic title="AI 已标" :value="aiLabeledCount" suffix="张"
        :value-style="{ color: '#722ed1' }" />
      <div class="stat-meta">
        <span class="stat-meta__hint stat-meta__hint--ai">可修正 / 确认 →</span>
      </div>
    </el-card>

    <!-- 3) 已人工标注 (绿) - 状态筛选入口 (human_confirmed + human_corrected) -->
    <el-card
      shadow="hover" class="stat-card stat-card--filterable"
      :class="{ 'stat-card--active': activeFilter === 'human_labeled' }"
      @click="emit('status-filter-change', 'human_labeled')"
    >
      <el-statistic title="已人工标注"
        :value="humanConfirmedCount + humanCorrectedCount" suffix="张"
        :value-style="{ color: '#67c23a' }" />
      <div class="stat-meta">
        <span style="color: #67c23a;">已确认 {{ humanConfirmedCount }}</span>
        <span class="stat-meta-sep">·</span>
        <span style="color: #e6a23c;">已修正 {{ humanCorrectedCount }}</span>
      </div>
    </el-card>

    <!-- 4) 类别卡 (展示用, 不参与筛选) -->
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
                  <span class="ct-dot" :style="{ background: c.color || '#409eff' }"></span>
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
      <el-card shadow="hover" class="stat-card stat-card--clickable">
        <el-statistic :title="categories.length === 0 ? '类别' : `类别 (共 ${categories.length} 类)`"
          :value="categories.length" suffix="类"
          :value-style="{ color: categories.length > 0 ? '#722ed1' : '#c0c4cc' }" />
      </el-card>
    </el-tooltip>

    <!-- 5) 不合格 (红) - 展示用, 不参与筛选 -->
    <el-card shadow="hover" class="stat-card stat-card--unqualified">
      <el-statistic title="不合格" :value="unqualifiedCount" suffix="张"
        :value-style="{ color: '#f56c6c' }" />
      <div class="stat-meta">
        占比 {{ unqualifiedRatioPct }}%
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { StatusFilterValue } from '@/composables/useAnnotationStatusFilter'

const props = defineProps({
  stats: { type: Object as PropType<any>, default: null },
  pendingCount: { type: Number, required: true },
  aiLabeledCount: { type: Number, required: true },
  humanConfirmedCount: { type: Number, required: true },
  humanCorrectedCount: { type: Number, required: true },
  categories: {
    type: Array as PropType<Array<{
      id: number
      name: string
      color?: string
      sample_count?: number
      human_labeled_count?: number
      ai_labeled_count?: number
    }>>,
    default: () => [],
  },
  datasets: { type: Array as PropType<any[]>, required: true },
  datasetId: { type: Number as PropType<number | null>, default: null },
  /** v3.5.0: 当前激活的筛选状态, 用于高亮对应卡 */
  activeFilter: { type: String as PropType<StatusFilterValue>, required: true },
})

const emit = defineEmits<{
  /** 状态筛选变化 (用户点击卡) */
  (e: 'status-filter-change', v: StatusFilterValue): void
}>()

const unqualifiedCount = computed(() => Number(props.stats?.unqualified_count || 0))
const unqualifiedRatioPct = computed(() => {
  const total = Number(props.datasets.find((d: any) => d.id === props.datasetId)?.image_count || 0)
  if (total <= 0) return 0
  return Math.round((unqualifiedCount.value / total) * 100)
})
</script>

<style scoped>
.annotate-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.annotate-stats :deep(.el-card) {
  flex: 1 1 0;
  min-width: 150px;
  width: auto;
  margin-bottom: 0;
}

.stat-card { text-align: center; }
.stat-meta {
  font-size: 12px;
  margin-top: 4px;
  color: #606266;
  letter-spacing: 0.3px;
}
.stat-meta-sep {
  margin: 0 4px;
  color: #c0c4cc;
}

/* v3.5.0: 可点击卡片的视觉提示 (cursor / hover) */
.stat-card--filterable {
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  position: relative;
}
.stat-card--filterable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.18);
}
.stat-card--filterable:active {
  transform: translateY(0);
}

/* v3.5.0: 激活态 - 主色边框 + 浅色背景 + 右上角 ✓ 徽标 */
.stat-card--active {
  border: 2px solid #409eff !important;
  background: linear-gradient(180deg, #ecf5ff 0%, #ffffff 60%);
}
.stat-card--active::after {
  content: '✓';
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 12px;
  font-weight: 700;
  color: #409eff;
  line-height: 1;
}
.stat-card--active.stat-card--ai {
  border-color: #722ed1 !important;
  background: linear-gradient(180deg, #f9f0ff 0%, #ffffff 60%);
}
.stat-card--active.stat-card--ai::after { color: #722ed1; }
.stat-card--active.stat-card--human {
  border-color: #67c23a !important;
  background: linear-gradient(180deg, #f0f9eb 0%, #ffffff 60%);
}
.stat-card--active.stat-card--human::after { color: #67c23a; }

.stat-meta__hint {
  color: #409eff;
  font-size: 11px;
  margin-top: 2px;
  opacity: 0.75;
}
.stat-meta__hint--ai { color: #722ed1; }

.stat-card--clickable {
  cursor: help;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stat-card--clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(114, 46, 209, 0.15);
}

.stat-card--unqualified {
  border-color: rgba(245, 108, 108, 0.3) !important;
}
.stat-card--unqualified:hover {
  border-color: #f56c6c !important;
}

/* 响应式 */
@media (max-width: 768px) {
  .annotate-stats :deep(.el-card) {
    flex: 1 1 calc(50% - 12px);
    min-width: 0;
  }
}
</style>

<!--
  全局 tooltip 样式 (不 scoped, 因为 el-tooltip 内容渲染在 popper 里)
  - 命名空间 .category-tooltip-* 避免污染
-->
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
.category-tooltip__table .ct-name { min-width: 90px; }
.category-tooltip__table .ct-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  width: 56px;
}
.category-tooltip__table .ct-human {
  color: #67c23a;
  font-weight: 600;
}
.category-tooltip__table .ct-ai { color: #909399; }
.category-tooltip__table .ct-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: 1px;
}
</style>
