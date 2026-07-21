<script setup lang="ts">
/**
 * StateBadge - 训练任务状态徽标
 * 设计目标: 高对比度 (深色文字 + 浅色背景), 一眼看清状态, PROGRESS 有脉冲动画
 * 替代 Element Plus 默认 el-tag, 后者 info/primary 都是蓝色调, 在表格中难以快速区分
 */
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps<{
  state: string  // PENDING | PROGRESS | SUCCESS | FAILURE | REVOKED | PAUSED
  size?: 'sm' | 'md'
}>()

const META: Record<string, { label: string; color: string; bg: string; dot: string; icon?: any }> = {
  PENDING:  { label: '等待中', color: '#7c3aed', bg: '#f3e8ff', dot: '#a78bfa' },
  PROGRESS: { label: '训练中', color: '#1d4ed8', bg: '#dbeafe', dot: '#3b82f6', icon: Loading },
  SUCCESS:  { label: '已完成', color: '#15803d', bg: '#dcfce7', dot: '#22c55e' },
  FAILURE:  { label: '失败',   color: '#b91c1c', bg: '#fee2e2', dot: '#ef4444' },
  REVOKED:  { label: '已取消', color: '#9a3412', bg: '#ffedd5', dot: '#f97316' },
  PAUSED:   { label: '已暂停', color: '#a16207', bg: '#fef3c7', dot: '#eab308' },
}

const meta = computed(() => META[props.state] || {
  label: props.state, color: '#475569', bg: '#f1f5f9', dot: '#94a3b8',
})
const isProgress = computed(() => props.state === 'PROGRESS')
const size = computed(() => props.size || 'sm')
</script>

<template>
  <span class="state-badge" :class="[`state-badge--${size}`]" :style="{
    color: meta.color,
    background: meta.bg,
  }">
    <span v-if="isProgress" class="state-badge__spinner" :style="{ borderTopColor: meta.color }" />
    <span v-else class="state-badge__dot" :style="{ background: meta.dot, boxShadow: `0 0 0 2px ${meta.bg}` }" />
    <span class="state-badge__label">{{ meta.label }}</span>
  </span>
</template>

<style scoped>
.state-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 7px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  letter-spacing: 0.2px;
  /* 轻微边框让标签在白底表格里更立体 */
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.04);
  transition: transform 0.18s var(--ease-out);
}
.state-badge:hover {
  transform: translateY(-1px);
}
.state-badge--sm { font-size: 12px; padding: 3px 8px 3px 7px; }
.state-badge--md { font-size: 13px; padding: 5px 11px 5px 9px; }

.state-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* 训练中: 旋转小圈替代静态 dot, 表示"在跑" */
.state-badge__spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(0, 0, 0, 0.08);
  border-top-color: currentColor;
  border-radius: 50%;
  flex-shrink: 0;
  animation: state-badge-spin 0.9s linear infinite;
}
@keyframes state-badge-spin {
  to { transform: rotate(360deg); }
}

.state-badge__label {
  /* tabular-nums 让数字字符等宽 */
  font-variant-numeric: tabular-nums;
}
</style>
