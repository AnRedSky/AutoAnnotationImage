<script setup lang="ts">
/**
 * 团队详情头部 (v3.3.1)
 * ======================
 *
 * Props:
 *  - team: TeamItem
 *  - isOwner: boolean   当前用户是 owner
 *  - isManager: boolean  当前用户有 manager 权限 (含 owner)
 *
 * Emits:
 *  - back              返回列表
 *  - edit              编辑团队
 *  - transfer          转让所有权
 *  - leave             主动退队
 */
import { ArrowLeft, Edit, Switch, Back } from '@element-plus/icons-vue'
import type { TeamItem } from '@/api'

defineProps<{
  team: TeamItem
  isOwner: boolean
  isManager: boolean
}>()

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'edit'): void
  (e: 'transfer'): void
  (e: 'leave'): void
}>()

const roleLabel = (role: string | null) => {
  const map: Record<string, string> = {
    manager: '可管理',
    editor: '可编辑',
    viewer: '仅阅读',
  }
  return map[role || ''] || role || '未加入'
}
</script>

<template>
  <div class="team-detail-header">
    <el-button :icon="ArrowLeft" text @click="emit('back')">返回团队列表</el-button>
    <h2>
      {{ team.name }}
      <span class="slug-badge">{{ team.slug }}</span>
    </h2>
    <p>
      {{ team.description || '无描述' }} · 我的角色: {{ roleLabel(team.my_role) }}
    </p>

    <div class="header-actions">
      <el-button
        v-if="isManager"
        :icon="Edit"
        type="primary"
        plain
        @click="emit('edit')"
      >
        编辑团队
      </el-button>
      <el-button
        v-if="isOwner"
        :icon="Switch"
        type="warning"
        plain
        @click="emit('transfer')"
      >
        转让所有权
      </el-button>
      <el-button
        v-if="!isOwner"
        :icon="Back"
        type="danger"
        plain
        @click="emit('leave')"
      >
        退出团队
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.team-detail-header {
  margin-bottom: 16px;
}
.team-detail-header h2 {
  margin: 12px 0 4px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.slug-badge {
  font-size: 12px;
  font-weight: normal;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: monospace;
}
.team-detail-header p {
  margin: 0 0 12px 0;
  color: var(--text-secondary);
  font-size: 13px;
}
.header-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
