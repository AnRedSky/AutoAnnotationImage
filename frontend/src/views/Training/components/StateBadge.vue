<script setup lang="ts">
/**
 * StateBadge - 训练任务状态徽标 (v2.5.25 简化)
 * 设计目标: 最低视觉干扰, 白底细边 + 文字, 状态靠文案承担
 * - 仅 SUCCESS / FAILURE / PROGRESS 用类型色 (绿/红/蓝) 提示
 * - PENDING / REVOKED / PAUSED 全部用 info 灰, 视觉降噪
 * - 不加任何 icon (包括 SUCCESS 的 CircleCheck, PROGRESS 的 Loading spinner)
 *   训练中的"动态感"由 el-progress 列的 0-100% 数字承担
 * 取代 v2.5.24 的 icon+dark 方案, 解决表格色块过重问题
 */
import { computed } from 'vue'

const props = defineProps<{
  state: string  // PENDING | PROGRESS | SUCCESS | FAILURE | REVOKED | PAUSED
  size?: 'sm' | 'md'
}>()

type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'

// 配色规则: 仅强调"训练完成 / 失败 / 进行中" 3 个有信息量的状态
// 其余用 info 灰, 让表格整体更"安静"
const META: Record<string, { label: string; type: TagType }> = {
  PENDING:  { label: '等待中', type: 'info' },
  PROGRESS: { label: '训练中', type: 'primary' },
  SUCCESS:  { label: '已完成', type: 'success' },
  FAILURE:  { label: '失败',   type: 'danger' },
  REVOKED:  { label: '已取消', type: 'info' },
  PAUSED:   { label: '已暂停', type: 'warning' },
}

const meta = computed(() => META[props.state] || {
  label: props.state, type: 'info' as TagType,
})
// el-tag size: 'small' | 'default' | 'large'
const tagSize = computed(() => props.size === 'md' ? 'default' : 'small')
</script>

<template>
  <el-tag :type="meta.type" effect="plain" :size="tagSize">
    {{ meta.label }}
  </el-tag>
</template>
