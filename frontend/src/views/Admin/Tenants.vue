<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { tenantApi } from '@/api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const isSuperAdmin = computed(() => userStore.user?.role === 'super_admin')

interface TenantItem {
  id: number
  name: string
  slug: string
  status: string
  max_users: number
  max_datasets: number
  created_at: string | null
}

const tenants = ref<TenantItem[]>([])
const loading = ref(false)
const createDialog = ref(false)
const createForm = ref({ name: '', slug: '', max_users: 50, max_datasets: 100 })

const loadTenants = async () => {
  loading.value = true
  try {
    const res: any = await tenantApi.list()
    tenants.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载租户列表失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const onCreate = async () => {
  if (!createForm.value.name || !createForm.value.slug) {
    ElMessage.warning('名称和短标识不能为空')
    return
  }
  try {
    await tenantApi.create({ ...createForm.value })
    ElMessage.success('租户创建成功')
    createDialog.value = false
    createForm.value = { name: '', slug: '', max_users: 50, max_datasets: 100 }
    await loadTenants()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onToggleStatus = async (t: TenantItem) => {
  const newStatus = t.status === 'active' ? 'suspended' : 'active'
  const action = newStatus === 'active' ? '激活' : '停用'
  try {
    await ElMessageBox.confirm(`确认${action}租户 "${t.name}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await tenantApi.updateStatus(t.id, newStatus)
    ElMessage.success(`${action}成功`)
    await loadTenants()
  } catch (e: any) {
    ElMessage.error(`${action}失败: ` + (e?.response?.data?.detail || e?.message))
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(loadTenants)
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>租户管理</h2>
      <p>管理多租户组织、配额和状态</p>
    </div>

    <el-card shadow="never" class="main-card">
      <div class="card-toolbar">
        <el-button type="primary" :disabled="!isSuperAdmin" @click="createDialog = true">
          新建租户
        </el-button>
        <span v-if="!isSuperAdmin" class="hint">仅超级管理员可创建租户</span>
      </div>

      <el-table :data="tenants" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="slug" label="短标识" min-width="120">
          <template #default="{ row }">
            <code>{{ row.slug }}</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
              {{ row.status === 'active' ? '活跃' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="max_users" label="用户上限" width="100" />
        <el-table-column prop="max_datasets" label="数据集上限" width="110" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              :type="row.status === 'active' ? 'danger' : 'success'"
              plain
              :disabled="!isSuperAdmin"
              @click="onToggleStatus(row)"
            >{{ row.status === 'active' ? '停用' : '激活' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createDialog" title="新建租户" width="460px">
      <el-form label-position="top" :model="createForm">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="如: 团队A" />
        </el-form-item>
        <el-form-item label="短标识 (slug)">
          <el-input v-model="createForm.slug" placeholder="如: team-a" />
        </el-form-item>
        <el-form-item label="用户上限">
          <el-input-number v-model="createForm.max_users" :min="1" :max="1000" />
        </el-form-item>
        <el-form-item label="数据集上限">
          <el-input-number v-model="createForm.max_datasets" :min="1" :max="10000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.admin-page { max-width: 1200px; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 22px; }
.page-header p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.main-card { border-radius: 12px; }
.card-toolbar { margin-bottom: 16px; display: flex; align-items: center; gap: 12px; }
.hint { font-size: 12px; color: var(--text-placeholder); }
</style>
