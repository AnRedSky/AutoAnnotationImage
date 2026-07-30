<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userApi } from '@/api'

interface UserItem {
  id: number
  username: string
  email: string | null
  role: string
  is_active: boolean
  created_at: string | null
}

const users = ref<UserItem[]>([])
const loading = ref(false)
const roleDialog = ref(false)
const editingUser = ref<UserItem | null>(null)
const newRole = ref('')

const roles = [
  { label: '超级管理员', value: 'super_admin' },
  { label: '管理员', value: 'admin' },
  { label: '标注员', value: 'annotator' },
  { label: '观察者', value: 'viewer' }
]

const roleTagType = (role: string) => {
  if (role === 'super_admin') return 'danger'
  if (role === 'admin') return 'warning'
  if (role === 'annotator') return 'success'
  return 'info'
}

const roleLabel = (role: string) => roles.find(r => r.value === role)?.label || role

const loadUsers = async () => {
  loading.value = true
  try {
    const res: any = await userApi.list()
    users.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载用户列表失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const onToggleActive = async (user: UserItem) => {
  const action = user.is_active ? '停用' : '激活'
  try {
    await ElMessageBox.confirm(`确认${action}用户 "${user.username}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    if (user.is_active) {
      await userApi.deactivate(user.id)
    } else {
      await userApi.activate(user.id)
    }
    ElMessage.success(`${action}成功`)
    await loadUsers()
  } catch (e: any) {
    ElMessage.error(`${action}失败: ` + (e?.response?.data?.detail || e?.message))
  }
}

const onEditRole = (user: UserItem) => {
  editingUser.value = user
  newRole.value = user.role
  roleDialog.value = true
}

const onSaveRole = async () => {
  if (!editingUser.value) return
  try {
    await userApi.changeRole(editingUser.value.id, newRole.value)
    ElMessage.success('角色修改成功')
    roleDialog.value = false
    await loadUsers()
  } catch (e: any) {
    ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(loadUsers)
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>用户管理</h2>
      <p>管理系统用户账号、角色和状态</p>
    </div>

    <el-card shadow="never" class="main-card">
      <el-table :data="users" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.email || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="140">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small" effect="plain">
              {{ row.is_active ? '活跃' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="onEditRole(row)">改角色</el-button>
            <el-button
              size="small"
              :type="row.is_active ? 'danger' : 'success'"
              plain
              @click="onToggleActive(row)"
            >{{ row.is_active ? '停用' : '激活' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 角色编辑对话框 -->
    <el-dialog v-model="roleDialog" title="修改角色" width="400px">
      <div class="role-dialog-body" v-if="editingUser">
        <p>用户: <strong>{{ editingUser.username }}</strong></p>
        <p>当前角色: <el-tag :type="roleTagType(editingUser.role)" size="small">{{ roleLabel(editingUser.role) }}</el-tag></p>
        <el-divider />
        <el-radio-group v-model="newRole" class="role-radio-group">
          <el-radio v-for="r in roles" :key="r.value" :value="r.value" class="role-radio">
            {{ r.label }} <span class="role-desc">{{ r.value }}</span>
          </el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="roleDialog = false">取消</el-button>
        <el-button type="primary" @click="onSaveRole">确认</el-button>
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
.role-dialog-body p { margin: 8px 0; }
.role-radio-group { display: flex; flex-direction: column; gap: 12px; }
.role-radio { display: flex; align-items: center; }
.role-desc { color: var(--text-placeholder); font-size: 12px; margin-left: 8px; }
</style>
