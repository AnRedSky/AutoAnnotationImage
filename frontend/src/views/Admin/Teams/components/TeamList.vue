<script setup lang="ts">
/**
 * 团队列表 (v3.3.1 L3)
 * ==================
 *
 * Props:
 *  - teams:        TeamItem[]
 *  - loading:      boolean
 *  - currentUserId: number  当前登录用户 id (用于判断我是否是该团队的 owner)
 *  - total:        number   总数 (服务端分页)
 *  - page:         number   当前页
 *  - pageSize:     number   每页条数
 *  - search:       string   搜索关键词
 *  - sort:         string   排序方式
 *  - isAdmin:      boolean  是否管理员 (显示归档 + 恢复按钮)
 *
 * Emits:
 *  - create           点击「创建团队」按钮
 *  - enter            进入团队详情 (参数: team)
 *  - delete-team      删除团队 (参数: team)
 *  - restore-team     恢复已归档团队 (参数: team, 仅 admin)
 *  - update:page      分页变化
 *  - update:pageSize  每页条数变化
 *  - update:search    搜索变化
 *  - update:sort      排序变化
 */
import { Plus, Search, RefreshRight } from '@element-plus/icons-vue'
import type { TeamItem } from '@/api'

defineProps<{
  teams: TeamItem[]
  loading: boolean
  currentUserId: number
  total: number
  page: number
  pageSize: number
  search: string
  sort: string
  isAdmin: boolean
}>()

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'enter', team: TeamItem): void
  (e: 'delete-team', team: TeamItem): void
  (e: 'restore-team', team: TeamItem): void
  (e: 'update:page', page: number): void
  (e: 'update:pageSize', size: number): void
  (e: 'update:search', search: string): void
  (e: 'update:sort', sort: string): void
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

const isArchived = (row: TeamItem) => !!row.archived_at
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
        <div class="toolbar-right">
          <el-input
            :model-value="search"
            placeholder="搜索团队名/描述/短标识"
            clearable
            :prefix-icon="Search"
            class="search-input"
            @update:model-value="(v: string) => emit('update:search', v)"
          />
          <el-select
            :model-value="sort"
            class="sort-select"
            @update:model-value="(v: string) => emit('update:sort', v)"
          >
            <el-option label="最新创建" value="id_desc" />
            <el-option label="最早创建" value="id_asc" />
            <el-option label="名称 A→Z" value="name_asc" />
            <el-option label="名称 Z→A" value="name_desc" />
            <el-option label="成员最多" value="member_count_desc" />
            <el-option label="创建近→远" value="created_desc" />
            <el-option label="创建远→近" value="created_asc" />
          </el-select>
        </div>
      </div>

      <el-table :data="teams" v-loading="loading" stripe class="data-table" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="160">
          <template #default="{ row }">
            <span>{{ row.name }}</span>
            <el-tag
              v-if="isArchived(row)"
              type="info"
              size="small"
              effect="plain"
              class="archived-tag"
            >
              已归档
            </el-tag>
          </template>
        </el-table-column>
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
        <el-table-column label="成员数" width="80">
          <template #default="{ row }">
            <span>{{ row.member_count ?? '-' }} / {{ row.max_members }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">
            {{ fmtDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!isArchived(row)"
              size="small"
              type="primary"
              plain
              @click="emit('enter', row)"
            >
              进入管理
            </el-button>
            <el-button
              v-if="isArchived(row) && isAdmin"
              size="small"
              type="success"
              plain
              :icon="RefreshRight"
              @click="emit('restore-team', row)"
            >
              恢复
            </el-button>
            <el-button
              v-if="!isArchived(row) && row.owner_id === currentUserId"
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

      <div class="pagination-bar">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="(p: number) => emit('update:page', p)"
          @size-change="(s: number) => emit('update:pageSize', s)"
        />
      </div>

      <el-empty
        v-if="!loading && teams.length === 0"
        :description="search ? `没有找到匹配 &quot;${search}&quot; 的团队` : '还没有团队,点击上方按钮创建第一个'"
      />
    </el-card>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

.card-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.search-input {
  width: 260px;
}
.sort-select {
  width: 140px;
}
.archived-tag {
  margin-left: 6px;
  vertical-align: middle;
}
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
