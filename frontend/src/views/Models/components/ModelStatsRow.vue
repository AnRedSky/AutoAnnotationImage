<script setup lang="ts">
/**
 * ModelStatsRow - 模型页顶部统计条
 *
 * v3.0.0 Phase I 拆分: 从 Models/index.vue 抽离, 4 张统计卡:
 * - 模型版本数 (蓝)
 * - 当前激活 (绿)
 * - 最高准确率 + 最佳模型名 (橙)
 * - 基础模型数 (紫)
 */
import { Grid, Aim, TrendCharts, CircleCheck } from '@element-plus/icons-vue'

defineProps<{
  total: number
  active: number
  bestAcc: number
  bestName: string
  baseCount: number
}>()
</script>

<template>
  <el-row :gutter="14" class="stats-row">
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--blue">
        <div class="stat-icon"><el-icon><Grid /></el-icon></div>
        <el-statistic title="模型版本数" :value="total" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--green">
        <div class="stat-icon"><el-icon><Aim /></el-icon></div>
        <el-statistic title="当前激活" :value="active" suffix="个" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--orange">
        <div class="stat-icon"><el-icon><TrendCharts /></el-icon></div>
        <el-statistic
          title="最高准确率"
          :value="bestAcc * 100"
          :precision="2"
          suffix="%"
          :value-style="{ color: '#ff8a4c' }"
        />
        <div class="stat-meta">{{ bestName }}</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--purple">
        <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
        <el-statistic title="基础模型数" :value="baseCount" suffix="种" />
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.stats-row { margin-bottom: 16px; flex-shrink: 0; }
.stat-card {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg) !important;
  background: #fff !important;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
}
.stat-card--blue::before { background: var(--gradient-brand); }
.stat-card--green::before { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--purple::before { background: linear-gradient(135deg, #722ed1 0%, #531dab 100%); }

.stat-card :deep(.el-card__body) {
  padding: 20px 22px;
  position: relative;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 26px;
  font-weight: 600;
  color: var(--text-primary);
}

.stat-icon {
  position: absolute;
  right: 18px;
  top: 18px;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon :deep(.el-icon) { font-size: 20px; }
.stat-card--blue .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--purple .stat-icon { background: rgba(114, 46, 209, 0.1); color: #722ed1; }

.stat-meta {
  color: var(--text-placeholder);
  margin-top: 6px;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
