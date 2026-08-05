<script setup lang="ts">
/**
 * 团队动态 Tab (v3.3.1 L4)
 * =========================
 * 展示团队相关 audit_log, 调用 GET /api/teams/{id}/activities.
 *
 * 架构: 纯展示组件, 接收 teamId 作为 prop, 内部发起请求 (顶层页面允许).
 * 展示规则:
 *   - 时间倒序
 *   - 事件类型语义化标签
 *   - 详情 JSON 折叠展示
 *   - 操作人 + 时间 + 资源
 */
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi } from '@/api'

interface ActivityItem {
  id: number
  user_id: number
  username: string
  event_type: string
  event_label: string
  resource_type: string | null
  resource_id: number | null
  detail: any
  created_at: string | null
}

const props = defineProps<{
  teamId: number
  limit?: number
}>()

const activities = ref<ActivityItem[]>([])
const total = ref(0)
const loading = ref(false)
const filterType = ref<string>('')
const expandedRows = ref<number[]>([])

const eventTypeOptions = [
  { value: '', label: '全部动态' },
  { value: 'team_created', label: '创建团队' },
  { value: 'team_updated', label: '更新团队' },
  { value: 'team_deleted', label: '归档团队' },
  { value: 'team_restored', label: '恢复团队' },
  { value: 'team_ownership_transferred', label: '转让所有权' },
  { value: 'team_left', label: '退出团队' },
  { value: 'team_member_invited', label: '邀请成员' },
  { value: 'team_member_role_changed', label: '变更成员角色' },
  { value: 'team_member_removed', label: '移除成员' },
  { value: 'dataset_shared_to_team', label: '共享数据集' },
  { value: 'dataset_unshared_from_team', label: '取消共享' },
]

const eventTypeTag = (type: string): 'success' | 'warning' | 'info' | 'danger' | 'primary' => {
  if (type.includes('deleted') || type.includes('removed')) return 'danger'
  if (type.includes('created') || type.includes('restored')) return 'success'
  if (type.includes('change') || type.includes('transferred') || type.includes('left')) return 'warning'
  if (type.includes('shared') || type.includes('updated')) return 'primary'
  return 'info'
}

const loadActivities = async () => {
  loading.value = true
  try {
    const res: any = await teamApi.listActivities(props.teamId, {
      limit: props.limit ?? 50,
      event_type: filterType.value || undefined,
    })
    activities.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载动态失败: ' + (e?.response?.data?.detail || e?.message))
    activities.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

const onFilterChange = () => {
  loadActivities()
}

const onToggleExpand = (id: number) => {
  const idx = expandedRows.value.indexOf(id)
  if (idx >= 0) expandedRows.value.splice(idx, 1)
  else expandedRows.value.push(id)
}

const isExpanded = (id: number) => expandedRows.value.includes(id)

const formatDetail = (detail: any): string => {
  if (!detail || typeof detail !== 'object') return String(detail || '-')
  return Object.entries(detail)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' | ')
}

watch(() => props.teamId, () => {
  loadActivities()
})

onMounted(loadActivities)
</script>

<template>
  <div class="team-activities">
    <div class="activities-toolbar">
      <span class="toolbar-label">动态类型:</span>
      <el-select
        v-model="filterType"
        placeholder="全部"
        clearable
        size="default"
        style="width: 200px"
        @change="onFilterChange"
      >
        <el-option
          v-for="opt in eventTypeOptions"
          :key="opt.value || 'all'"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <span class="total-info">共 {{ total }} 条动态</span>
      <el-button @click="loadActivities" :loading="loading" size="small">刷新</el-button>
    </div>

    <el-skeleton v-if="loading && activities.length === 0" :rows="6" animated />

    <div v-else-if="activities.length === 0" class="empty">
      <el-empty description="暂无团队动态" />
    </div>

    <el-timeline v-else class="activity-timeline">
      <el-timeline-item
        v-for="item in activities"
        :key="item.id"
        :timestamp="fmtDate(item.created_at)"
        placement="top"
      >
        <el-card shadow="never" class="activity-card">
          <div class="activity-head">
            <div class="activity-meta">
              <el-tag :type="eventTypeTag(item.event_type)" size="small">
                {{ item.event_label }}
              </el-tag>
              <span class="username">{{ item.username }}</span>
              <span v-if="item.resource_type" class="resource">
                {{ item.resource_type }}#{{ item.resource_id }}
              </span>
            </div>
            <el-button
              v-if="item.detail"
              link
              type="primary"
              size="small"
              @click="onToggleExpand(item.id)"
            >
              {{ isExpanded(item.id) ? '收起' : '详情' }}
            </el-button>
          </div>
          <div v-if="isExpanded(item.id) && item.detail" class="activity-detail">
            <code>{{ formatDetail(item.detail) }}</code>
          </div>
        </el-card>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<style scoped>
.team-activities { padding: 4px 0; }
.activities-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.toolbar-label {
  font-size: 13px;
  color: var(--text-secondary, #606266);
}
.total-info {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-left: auto;
}
.activity-timeline {
  padding-left: 4px;
}
.activity-card {
  margin-bottom: 0;
}
.activity-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.activity-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.username {
  font-weight: 500;
  color: var(--text-primary, #303133);
  font-size: 13px;
}
.resource {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
}
.activity-detail {
  margin-top: 8px;
  padding: 8px 12px;
  background: #fafbfc;
  border-radius: 4px;
  font-size: 12px;
  color: var(--text-secondary, #606266);
  word-break: break-all;
  line-height: 1.6;
}
.empty {
  padding: 32px 0;
  text-align: center;
}
</style>
