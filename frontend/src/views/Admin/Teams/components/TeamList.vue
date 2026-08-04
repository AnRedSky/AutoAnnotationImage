<script setup lang="ts">
/**
 * 团队列表 (v3.3.1)
 * ==================
 *
 * Props:
 *  - teams:        TeamItem[]
 *  - loading:      boolean
 *  - currentUserId: number  当前登录用户 id (用于判断我是否是该团队的 owner)
 *
 * Emits:
 *  - create           点击「创建团队」按钮
 *  - enter            进入团队详情 (参数: team)
 *  - delete-team      删除团队 (参数: team)
 */
import { Plus } from '@element-plus/icons-vue'
import type { TeamItem } from '@/api'

defineProps<{
  teams: TeamItem[]
  loading: boolean
  currentUserId: number
}>()

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'enter', team: TeamItem): void
  (e: 'delete-team', team: TeamItem): void
}>()

const roleLabel = (role: string | null) => {
  const map: Record<string, string> = {
    manager: '可管理',
    editor: '可编辑',
    viewer: '仅阅读',
  }
  return map[role || ''] || role || '未加入'
}

const roleTagType = (role: string | null) =>
  role === 'manager' ? 'warning' : role === 'editor' ? 'success' : 'info'

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString('zh-CN') : '-')
</script>

<template>
  <div class="team-list">
    <div class="page-header">
      <h2>团队管理</h2>
      <p>创建团队、邀请成员、共享数据集进行协同标注</p>
    </div>

    <el-card shadow="never" class="main-card">
      <div class="card-toolbar">
        <el-button type="primary" :icon="Plus" @click="emit('create')">创建团队</el-button>
      </div>

      <el-table :data="teams" v-loading="loading" stripe class="data-table" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="slug" label="短标识" min-width="120">
          <template #default="{ row }">
            <code>{{ row.slug }}</code>
          </template>
        </el-table-column>
        <el-table-column label="我的角色" width="100">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.my_role)" size="small">
              {{ roleLabel(row.my_role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="max_members" label="成员上限" width="100" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">
            {{ fmtDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              plain
              @click="emit('enter', row)"
            >
              进入管理
            </el-button>
            <el-button
              v-if="row.owner_id === currentUserId"
              size="small"
              type="danger"
              plain
              @click="emit('delete-team', row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && teams.length === 0"
        description="还没有团队,点击上方按钮创建第一个"
      />
    </el-card>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';
</style>
