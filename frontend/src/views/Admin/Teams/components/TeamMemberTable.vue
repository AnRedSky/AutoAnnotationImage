<script setup lang="ts">
/**
 * 团队成员表格 (v3.3.1 + v3.3.3)
 * ======================
 *
 * v3.3.3 增强 (用户新需求 §3):
 *   - 团队创建者 (所有者) 在「角色」列额外标注「（所有者）」字样
 *   - 后端 list_members 返回 is_owner 字段, 前端按该字段显示双标签
 *
 * Props:
 *  - members: TeamMemberItem[]
 *  - loading: boolean
 *  - canManage: boolean   (manager 角色以上可操作)
 *
 * Emits:
 *  - invite              点击邀请
 *  - edit-role           改角色 (参数: member)
 *  - remove              移除 (参数: member)
 *  - full                团队已满 (用于禁用邀请按钮)
 */
import { ElMessageBox } from 'element-plus'
import { User, Setting, Delete } from '@element-plus/icons-vue'
import { teamApi, type TeamMemberItem } from '@/api'

const props = defineProps<{
  teamId: number
  members: TeamMemberItem[]
  loading: boolean
  canManage: boolean
  maxMembers: number
}>()

const emit = defineEmits<{
  (e: 'invite'): void
  (e: 'edit-role', m: TeamMemberItem): void
  (e: 'remove', m: TeamMemberItem): void
  (e: 'changed'): void
}>()

const roleLabel = (role: string | null) => {
  const map: Record<string, string> = {
    manager: '可管理',
    editor: '可编辑',
    viewer: '仅阅读',
  }
  return map[role || ''] || role || '-'
}
const roleTagType = (r: string) =>
  r === 'manager' ? 'warning' : r === 'editor' ? 'success' : 'info'

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString('zh-CN') : '-')

const onRemove = async (m: TeamMemberItem) => {
  try {
    await ElMessageBox.confirm(`确认从团队移除 "${m.username}"?`, '提示', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await teamApi.removeMember(props.teamId, m.user_id)
    emit('changed')
  } catch (e: any) {
    throw e // 让父组件处理
  }
}
</script>

<template>
  <el-card shadow="never" class="main-card">
    <div class="card-toolbar">
      <span class="member-count">
        成员 ({{ members.length }}/{{ maxMembers }})
      </span>
      <el-button
        v-if="canManage"
        type="primary"
        :icon="User"
        :disabled="members.length >= maxMembers"
        @click="emit('invite')"
      >
        邀请成员
      </el-button>
    </div>

    <el-table :data="members" v-loading="loading" stripe class="data-table">
      <el-table-column prop="user_id" label="ID" width="70" />
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.email || '-' }}
        </template>
      </el-table-column>
      <!-- v3.3.3: 角色列 — 团队创建者额外标注「（所有者）」 -->
      <el-table-column label="角色" width="160">
        <template #default="{ row }">
          <div class="role-cell">
            <el-tag :type="roleTagType(row.role)" size="small">
              {{ roleLabel(row.role) }}
            </el-tag>
            <el-tag
              v-if="row.is_owner"
              type="danger"
              size="small"
              effect="dark"
              class="owner-badge"
            >
              （所有者）
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="加入时间" width="170">
        <template #default="{ row }">
          {{ fmtDate(row.joined_at) }}
        </template>
      </el-table-column>
      <el-table-column v-if="canManage" label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.role !== 'manager'"
            size="small"
            :icon="Setting"
            @click="emit('edit-role', row)"
          >
            改角色
          </el-button>
          <el-button
            v-if="row.role !== 'manager'"
            size="small"
            type="danger"
            plain
            :icon="Delete"
            @click="onRemove(row)"
          >
            移除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
@import '@/styles/admin.css';
.member-count {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}
/* v3.3.3: 角色单元格 — 主标签 + 所有者徽章 */
.role-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.owner-badge {
  font-size: 11px;
  padding: 0 6px;
  height: 20px;
  line-height: 18px;
  letter-spacing: 0.5px;
}
</style>
