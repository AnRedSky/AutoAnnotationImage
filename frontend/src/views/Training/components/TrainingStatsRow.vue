<script setup lang="ts">
/**
 * TrainingStatsRow - 训练页顶部统计条
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离, 4 张统计卡:
 * - 任务总数 (蓝)
 * - 训练中 (橙)
 * - 已完成 (绿)
 * - 失败/取消 (红)
 */
import { List as ListIcon, VideoPlay, DataLine, CircleClose } from '@element-plus/icons-vue'

defineProps<{
  total: number
  running: number
  success: number
  failed: number
}>()
</script>

<template>
  <el-row :gutter="14" class="stats-row">
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--blue">
        <div class="stat-icon"><el-icon><ListIcon /></el-icon></div>
        <el-statistic title="任务总数" :value="total" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--orange">
        <div class="stat-icon"><el-icon><VideoPlay /></el-icon></div>
        <el-statistic title="训练中" :value="running" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--green">
        <div class="stat-icon"><el-icon><DataLine /></el-icon></div>
        <el-statistic title="已完成" :value="success" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover" class="stat-card stat-card--red">
        <div class="stat-icon"><el-icon><CircleClose /></el-icon></div>
        <el-statistic title="失败/取消" :value="failed" />
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
.stat-card--blue::before   { background: var(--gradient-brand); }
.stat-card--green::before  { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--red::before    { background: linear-gradient(135deg, #ff4d4f 0%, #cf1322 100%); }

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
.stat-card--blue   .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green  .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--red    .stat-icon { background: rgba(255, 77, 79, 0.1); color: #ff4d4f; }
</style>
