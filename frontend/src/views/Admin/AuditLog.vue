<script setup lang="ts">
/**
 * 审计日志查看页 (v3.3.1 L4)
 * ==========================
 * 路径: /admin/audit
 * 权限: 仅 admin
 *
 * v3.3.1 L4 增强:
 *   - 调用 GET /api/audit-logs (后端新增)
 *   - 多维度过滤: 事件类型 / 团队ID / 用户ID / 资源类型 / 时间范围
 *   - 显示 username 字段 (后端批量拉取)
 *   - 分页 + 总数 + 过滤器回显
 */
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { auditApi, type AuditLogItem } from '@/api'

const logs = ref<AuditLogItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 过滤器
const filterEventType = ref('')
const filterTeamId = ref<number | null>(null)
const filterUserId = ref<number | null>(null)
const filterResourceType = ref('')
const filterTimeRange = ref<[string, string] | null>(null)

// 事件类型白名单 (前端展示用, 与后端 _EXTENDED_AUDIT_EVENT_TYPES 对应)
const EVENT_TYPE_OPTIONS = [
  { value: '', label: '全部事件' },
  // 用户管理
  { value: 'user_login', label: '用户登录' },
  { value: 'user_logout', label: '用户登出' },
  { value: 'user_created', label: '用户创建' },
  { value: 'user_deactivated', label: '用户停用' },
  { value: 'role_changed', label: '角色变更' },
  // 团队 (v3.3.1 L1-L4)
  { value: 'team_created', label: '创建团队' },
  { value: 'team_updated', label: '更新团队' },
  { value: 'team_deleted', label: '归档团队' },
  { value: 'team_restored', label: '恢复团队' },
  { value: 'team_ownership_transferred', label: '转让所有权' },
  { value: 'team_left', label: '退出团队' },
  { value: 'team_member_invited', label: '邀请成员' },
  { value: 'team_member_role_changed', label: '变更成员角色' },
  { value: 'team_member_removed', label: '移除成员' },
  // 数据集
  { value: 'dataset_created', label: '创建数据集' },
  { value: 'dataset_deleted', label: '删除数据集' },
  { value: 'dataset_shared', label: '共享数据集' },
  { value: 'dataset_unshared', label: '取消共享' },
  { value: 'dataset_shared_to_team', label: '共享至团队' },
  { value: 'dataset_unshared_from_team', label: '从团队取消共享' },
  // 标注/训练
  { value: 'annotation_saved', label: '保存标注' },
  { value: 'training_started', label: '开始训练' },
  { value: 'training_cancelled', label: '取消训练' },
  { value: 'model_activated', label: '激活模型' },
  { value: 'model_deleted', label: '删除模型' },
]

const eventTypeTag = (type: string): 'success' | 'warning' | 'info' | 'danger' | 'primary' => {
  if (type.includes('deleted') || type.includes('removed') || type.includes('deactivated')) return 'danger'
  if (type.includes('created') || type.includes('start') || type.includes('login') || type.includes('restored')) return 'success'
  if (type.includes('change') || type.includes('share') || type.includes('transferred') || type.includes('left')) return 'warning'
  if (type.includes('updated')) return 'primary'
  return 'info'
}

const loadLogs = async () => {
  loading.value = true
  try {
    const params: any = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (filterEventType.value) params.event_type = filterEventType.value
    if (filterTeamId.value !== null) params.team_id = filterTeamId.value
    if (filterUserId.value !== null) params.user_id = filterUserId.value
    if (filterResourceType.value) params.resource_type = filterResourceType.value
    if (filterTimeRange.value) {
      params.start = filterTimeRange.value[0]
      params.end = filterTimeRange.value[1]
    }
    const r: any = await auditApi.list(params)
    logs.value = r.items
    total.value = r.total
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    logs.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

const onPageChange = (p: number) => {
  page.value = p
  loadLogs()
}

const onFilterChange = () => {
  page.value = 1
  loadLogs()
}

const onResetFilter = () => {
  filterEventType.value = ''
  filterTeamId.value = null
  filterUserId.value = null
  filterResourceType.value = ''
  filterTimeRange.value = null
  page.value = 1
  loadLogs()
}

const hasActiveFilter = computed(() =>
  !!filterEventType.value || filterTeamId.value !== null ||
  filterUserId.value !== null || !!filterResourceType.value ||
  filterTimeRange.value !== null,
)

onMounted(loadLogs)
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>审计日志</h2>
      <p>查看系统权限敏感操作的审计记录 (v3.3.1 L4: 支持多维度过滤 + 时间范围)</p>
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form inline :model="{}" class="filter-form" @submit.prevent>
        <el-form-item label="事件类型">
          <el-select
            v-model="filterEventType"
            placeholder="全部"
            clearable
            style="width: 200px"
            @change="onFilterChange"
          >
            <el-option
              v-for="opt in EVENT_TYPE_OPTIONS"
              :key="opt.value || 'all'"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="团队ID">
          <el-input-number
            v-model="filterTeamId"
            :min="0"
            placeholder="团队ID"
            controls-position="right"
            style="width: 130px"
            @change="onFilterChange"
          />
        </el-form-item>
        <el-form-item label="用户ID">
          <el-input-number
            v-model="filterUserId"
            :min="0"
            placeholder="用户ID"
            controls-position="right"
            style="width: 130px"
            @change="onFilterChange"
          />
        </el-form-item>
        <el-form-item label="资源类型">
          <el-input
            v-model="filterResourceType"
            placeholder="如: team/dataset"
            clearable
            style="width: 160px"
            @change="onFilterChange"
          />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="filterTimeRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 360px"
            @change="onFilterChange"
          />
        </el-form-item>
        <el-form-item>
          <el-button @click="onResetFilter" :disabled="!hasActiveFilter">重置</el-button>
          <el-button type="primary" @click="loadLogs">刷新</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="main-card">
      <el-table :data="logs" v-loading="loading" stripe class="data-table" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="操作人" width="120">
          <template #default="{ row }">
            {{ row.username || `user#${row.user_id}` }}
          </template>
        </el-table-column>
        <el-table-column label="事件类型" width="200">
          <template #default="{ row }">
            <el-tag :type="eventTypeTag(row.event_type)" size="small">
              {{ row.event_type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="resource_type" label="资源类型" width="110">
          <template #default="{ row }">{{ row.resource_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="resource_id" label="资源ID" width="80">
          <template #default="{ row }">{{ row.resource_id || '-' }}</template>
        </el-table-column>
        <el-table-column prop="team_id" label="团队ID" width="80">
          <template #default="{ row }">{{ row.team_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="详情" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <code class="detail-code">{{ JSON.stringify(row.detail) }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="ip_address" label="IP" width="130">
          <template #default="{ row }">{{ row.ip_address || '-' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <div v-if="total > 0" class="pager">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPageChange"
        />
      </div>

      <el-empty v-if="!loading && logs.length === 0" description="暂无审计日志" />
    </el-card>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

.detail-code { font-size: 12px; color: var(--text-secondary); }
.filter-card { margin-bottom: 16px; }
.filter-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
</style>
