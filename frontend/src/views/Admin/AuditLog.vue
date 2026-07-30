<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api/http'

interface AuditItem {
  id: number
  tenant_id: number | null
  user_id: number
  event_type: string
  resource_type: string | null
  resource_id: number | null
  detail: any
  ip_address: string | null
  created_at: string | null
}

const logs = ref<AuditItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const eventTypeTag = (type: string) => {
  if (type.includes('delete')) return 'danger'
  if (type.includes('create') || type.includes('start')) return 'success'
  if (type.includes('change') || type.includes('share')) return 'warning'
  return 'info'
}

const loadLogs = async () => {
  loading.value = true
  try {
    // 后端暂无 /api/audit/logs 端点; 显示空表 + 提示
    try {
      const r: any = await http.get('/admin/audit-logs', {
        params: { page: page.value, page_size: pageSize.value }
      })
      logs.value = r.items || []
      total.value = r.total || 0
    } catch {
      logs.value = []
      total.value = 0
      ElMessage.info('审计日志查询端点尚未实现')
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

const onPageChange = (p: number) => {
  page.value = p
  loadLogs()
}

onMounted(loadLogs)
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>审计日志</h2>
      <p>查看系统权限敏感操作的审计记录</p>
    </div>

    <el-card shadow="never" class="main-card">
      <el-table :data="logs" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="user_id" label="用户ID" width="80" />
        <el-table-column label="事件类型" width="180">
          <template #default="{ row }">
            <el-tag :type="eventTypeTag(row.event_type)" size="small">{{ row.event_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="resource_type" label="资源类型" width="120">
          <template #default="{ row }">{{ row.resource_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="resource_id" label="资源ID" width="80">
          <template #default="{ row }">{{ row.resource_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="详情" min-width="200" show-overflow-tooltip>
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

.admin-page { max-width: 1200px; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 22px; font-weight: 600; color: var(--text-primary); }
.page-header p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.main-card { border-radius: 12px; }
.detail-code { font-size: 12px; color: var(--text-secondary); }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
