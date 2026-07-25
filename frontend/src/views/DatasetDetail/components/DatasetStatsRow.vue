<script setup lang="ts">
/**
 * DatasetStatsRow - 顶部统计卡 (5 张同一行)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 * v3.0.0 不合格指标增强:
 * - 新增第 5 张「不合格」卡 (红色), 与现有 4 张卡同一行展示
 * - 布局从 el-row/el-col (24 栅格无法 5 等分) 改为 flex 等分
 * - 视觉风格保持一致: 同样 stat-icon / el-statistic / stat-meta 结构
 * - 点击不合格卡 → emit 'click-unqualified', 父层切换 statusFilter='unqualified'
 * - 响应式: 大屏 5 列等分, 窄屏 (<=768px) 自动换行为 2 列
 *
 * v2.5.49 升级:
 * - 移除 2 个低价值指标 (平均耗时 / AI 节省时间), 跨任务口径不一致
 * - 「类别数」卡升级: hover 看每类进度详情 (类目 + 总样本 + 已人工 + AI 已标)
 * - 与 Annotate 工作台同源, 保证两个页面的「类别数」hover 详情口径一致
 */
import {
  Picture, CircleCheck, Lightning, CollectionTag, InfoFilled, Warning,
} from '@element-plus/icons-vue'

const props = defineProps<{
  dataset: any
  stats: any
  categories: any[]
}>()

const emit = defineEmits<{
  (e: 'click-unqualified'): void  // v3.0.0: 点击不合格卡 → 切换筛选
}>()
</script>

<template>
  <div v-if="stats" class="ds-stats">
    <el-card shadow="hover" class="stat-card stat-card--blue">
      <div class="stat-icon"><el-icon><Picture /></el-icon></div>
      <el-statistic title="图片总数" :value="dataset?.image_count || 0" />
    </el-card>

    <el-card shadow="hover" class="stat-card stat-card--green">
      <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
      <el-statistic title="已人工标注"
        :value="(stats.annotation?.human_confirmed_count || 0) + (stats.annotation?.human_corrected_count || 0)" />
      <div class="stat-meta">
        已确认 {{ stats.annotation?.human_confirmed_count || 0 }} ·
        修正 {{ stats.annotation?.human_corrected_count || 0 }}
      </div>
    </el-card>

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

    <!-- v3.0.0: 不合格图片指标 (与现有指标同一行, 点击切换筛选) -->
    <el-card
      shadow="hover"
      class="stat-card stat-card--red stat-card--clickable"
      @click="emit('click-unqualified')"
    >
      <div class="stat-icon"><el-icon><Warning /></el-icon></div>
      <el-statistic title="不合格" :value="stats.unqualified_count || 0" />
      <div class="stat-meta">
        占比 {{ dataset?.image_count
          ? Math.round((stats.unqualified_count || 0) / dataset.image_count * 100)
          : 0 }}%
      </div>
    </el-card>
  </div>
</template>

<style scoped>
/* v3.0.0: flex 等分布局 (替代 el-row/el-col, 支持 5 张卡同一行)
 * - 大屏: 5 列等分 (flex: 1 1 0)
 * - 窄屏 (<=768px): 自动换行为 2 列 (min-width 触发 wrap) */
.ds-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.ds-stats :deep(.el-card) {
  flex: 1 1 0;
  min-width: 150px;
  width: auto;
  margin-bottom: 0;
}

.stat-card {
  position: relative;
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
.stat-card :deep(.el-card__body) { padding: 14px 16px; }
.stat-card :deep(.el-statistic__head) { font-size: 12px; color: var(--text-secondary); }
.stat-card :deep(.el-statistic__number) { font-size: 22px; font-weight: 600; }
.stat-icon {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  background: rgba(79, 124, 255, 0.1);
  color: var(--brand-primary);
}
.stat-card--blue   .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green  .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--cyan   .stat-icon { background: rgba(64, 158, 255, 0.1); color: #409eff; }
.stat-card--red    .stat-icon { background: rgba(245, 108, 108, 0.1); color: #f56c6c; }

.stat-meta {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: 4px;
}
.stat-meta--hint { color: var(--text-secondary); }
.stat-card--clickable { cursor: pointer; }
.stat-card--red {
  border-color: rgba(245, 108, 108, 0.3) !important;
}
.stat-card--red:hover {
  border-color: #f56c6c !important;
}

/* 类别数卡 hover 详情表格 */
.category-tooltip { font-size: 12px; padding: 4px; }
.category-tooltip__header { font-weight: 600; margin-bottom: 6px; }
.category-tooltip__table { border-collapse: collapse; width: 100%; }
.category-tooltip__table th,
.category-tooltip__table td {
  padding: 3px 8px;
  text-align: left;
}
.category-tooltip__table th { color: var(--text-secondary); font-weight: 500; }
.ct-name { white-space: nowrap; }
.ct-num { text-align: right; font-variant-numeric: tabular-nums; }
.ct-human { color: #67c23a; font-weight: 500; }
.ct-ai { color: #409eff; font-weight: 500; }
.ct-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: 0;
}

/* 响应式: 窄屏 2 列 (flex-basis 50% 减去 gap) */
@media (max-width: 768px) {
  .ds-stats :deep(.el-card) {
    flex: 1 1 calc(50% - 12px);
    min-width: 0;
  }
}
</style>
