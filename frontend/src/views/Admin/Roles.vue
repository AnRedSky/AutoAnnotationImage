<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { tenantApi, userApi, datasetApi } from '@/api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const tenantId = computed(() => userStore.user?.tenant_id || 1)

interface TenantUser {
  user_id: number
  username: string
  email: string | null
  role: string
  assigned_at: string | null
}

interface DatasetItem {
  id: number
  name: string
  owner_id: number
}

interface UserItem {
  id: number
  username: string
  role: string
}

const tabActive = ref('users')
const tenantUsers = ref<TenantUser[]>([])
const allUsers = ref<UserItem[]>([])
const datasets = ref<DatasetItem[]>([])
const loading = ref(false)

// 角色分配
const assignDialog = ref(false)
const assignUserId = ref<number | undefined>()
const assignRole = ref('annotator')

// dataset 共享
const shareDialog = ref(false)
const shareDatasetId = ref<number | undefined>()
const shareUserId = ref<number | undefined>()
const shareRole = ref('viewer')

const tenantRoles = [
  { label: '租户管理员', value: 'tenant_admin' },
  { label: '标注员', value: 'annotator' },
  { label: '观察者', value: 'viewer' }
]

const shareRoles = [
  { label: '标注员', value: 'annotator' },
  { label: '观察者', value: 'viewer' }
]

const roleLabel = (role: string) => tenantRoles.find(r => r.value === role)?.label || role

const loadTenantUsers = async () => {
  loading.value = true
  try {
    const res: any = await tenantApi.listUsers(tenantId.value)
    tenantUsers.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载租户用户失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const loadAllUsers = async () => {
  try {
    const res: any = await userApi.list()
    allUsers.value = res.items || []
  } catch {}
}

const loadDatasets = async () => {
  try {
    const res: any = await datasetApi.list()
    datasets.value = res.items || []
  } catch {}
}

const onAssignUser = async () => {
  if (!assignUserId.value) {
    ElMessage.warning('请选择用户')
    return
  }
  try {
    await tenantApi.assignUser(tenantId.value, {
      user_id: assignUserId.value,
      role: assignRole.value
    })
    ElMessage.success('用户分配成功')
    assignDialog.value = false
    assignUserId.value = undefined
    await loadTenantUsers()
  } catch (e: any) {
    ElMessage.error('分配失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onRemoveUser = async (u: TenantUser) => {
  try {
    await ElMessageBox.confirm(`确认从租户移除用户 "${u.username}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await tenantApi.removeUser(tenantId.value, u.user_id)
    ElMessage.success('移除成功')
    await loadTenantUsers()
  } catch (e: any) {
    ElMessage.error('移除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onShareDataset = async () => {
  if (!shareDatasetId.value || !shareUserId.value) {
    ElMessage.warning('请选择数据集和用户')
    return
  }
  try {
    await tenantApi.shareDataset(shareDatasetId.value, {
      user_id: shareUserId.value,
      role: shareRole.value
    })
    ElMessage.success('共享成功')
    shareDialog.value = false
    shareDatasetId.value = undefined
    shareUserId.value = undefined
  } catch (e: any) {
    ElMessage.error('共享失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(async () => {
  await Promise.all([loadTenantUsers(), loadAllUsers(), loadDatasets()])
})
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>角色权限</h2>
      <p>管理租户用户角色和数据集共享</p>
    </div>

    <el-tabs v-model="tabActive" class="role-tabs">
      <!-- 租户用户角色 -->
      <el-tab-pane label="租户用户" name="users">
        <el-card shadow="never" class="main-card">
          <div class="card-toolbar">
            <el-button type="primary" @click="assignDialog = true">分配用户</el-button>
          </div>
          <el-table :data="tenantUsers" v-loading="loading" stripe>
            <el-table-column prop="user_id" label="ID" width="70" />
            <el-table-column prop="username" label="用户名" min-width="120" />
            <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.email || '-' }}</template>
            </el-table-column>
            <el-table-column label="角色" width="140">
              <template #default="{ row }">
                <el-tag size="small">{{ roleLabel(row.role) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="分配时间" width="170">
              <template #default="{ row }">{{ fmtDate(row.assigned_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="danger" plain @click="onRemoveUser(row)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 数据集共享 -->
      <el-tab-pane label="数据集共享" name="share">
        <el-card shadow="never" class="main-card">
          <div class="card-toolbar">
            <el-button type="primary" @click="shareDialog = true">共享数据集</el-button>
          </div>
          <el-table :data="datasets" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="owner_id" label="Owner ID" width="100" />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 分配用户对话框 -->
    <el-dialog v-model="assignDialog" title="分配用户到租户" width="440px">
      <el-form label-position="top">
        <el-form-item label="选择用户">
          <el-select v-model="assignUserId" filterable placeholder="搜索用户名" style="width: 100%">
            <el-option
              v-for="u in allUsers"
              :key="u.id"
              :label="`${u.username} (${u.role})`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="assignRole">
            <el-radio v-for="r in tenantRoles" :key="r.value" :value="r.value">{{ r.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignDialog = false">取消</el-button>
        <el-button type="primary" @click="onAssignUser">确认</el-button>
      </template>
    </el-dialog>

    <!-- 共享数据集对话框 -->
    <el-dialog v-model="shareDialog" title="共享数据集" width="440px">
      <el-form label-position="top">
        <el-form-item label="数据集">
          <el-select v-model="shareDatasetId" filterable placeholder="选择数据集" style="width: 100%">
            <el-option v-for="d in datasets" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="共享给">
          <el-select v-model="shareUserId" filterable placeholder="选择用户" style="width: 100%">
            <el-option
              v-for="u in allUsers"
              :key="u.id"
              :label="u.username"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="shareRole">
            <el-radio v-for="r in shareRoles" :key="r.value" :value="r.value">{{ r.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shareDialog = false">取消</el-button>
        <el-button type="primary" @click="onShareDataset">确认</el-button>
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
.card-toolbar { margin-bottom: 16px; }
.role-tabs :deep(.el-tabs__item) { font-size: 14px; font-weight: 500; }
</style>
