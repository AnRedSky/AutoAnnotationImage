<script setup lang="ts">
/**
 * StateBadge - 训练任务状态徽标 (v2.5.24 重构)
 * 设计目标: 与 Models 页 is_active 状态徽标视觉对齐
 * - SUCCESS: el-tag + type="success" + effect="dark" + CircleCheck
 *   (对齐 Models 页"已激活"的最强样式)
 * - PROGRESS: el-tag + type="primary" + effect="plain" + Loading (动画)
 * - 其他: el-tag + type=* + effect="plain", 不加 icon, 保持简洁
 * 取代 v2.5.24 之前的自定义 span+inline style, 统一走 el-tag
 */
import { computed } from 'vue'
import { Loading, CircleCheck } from '@element-plus/icons-vue'

const props = defineProps<{
  state: string  // PENDING | PROGRESS | SUCCESS | FAILURE | REVOKED | PAUSED
  size?: 'sm' | 'md'
}>()

type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'
type TagEffect = 'plain' | 'dark'

const META: Record<string, {
  label: string
  type: TagType
  effect: TagEffect
  icon?: any
}> = {
  PENDING:  { label: '等待中', type: 'info',    effect: 'plain' },
  PROGRESS: { label: '训练中', type: 'primary', effect: 'plain', icon: Loading },
  SUCCESS:  { label: '已完成', type: 'success', effect: 'dark',  icon: CircleCheck },
  FAILURE:  { label: '失败',   type: 'danger',  effect: 'plain' },
  REVOKED:  { label: '已取消', type: 'info',    effect: 'plain' },
  PAUSED:   { label: '已暂停', type: 'warning', effect: 'plain' },
}

const meta = computed(() => META[props.state] || {
  label: props.state, type: 'info' as TagType, effect: 'plain' as TagEffect,
})
// el-tag size: 'small' | 'default' | 'large'
const tagSize = computed(() => props.size === 'md' ? 'default' : 'small')
// PROGRESS 走 Element Plus 自带 is-loading 旋转动画
const isLoading = computed(() => props.state === 'PROGRESS')
</script>

<template>
  <el-tag :type="meta.type" :effect="meta.effect" :size="tagSize">
    <el-icon
      v-if="meta.icon"
      :class="{ 'is-loading': isLoading }"
      style="margin-right: 3px; vertical-align: -2px;"
    >
      <component :is="meta.icon" />
    </el-icon>
    {{ meta.label }}
  </el-tag>
</template>
