<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { Search, Refresh } from '@element-plus/icons-vue'

const userStore = useUserStore()

interface UserItem {
  id: number
  username: string
  email: string | null
  role: string
  is_active: boolean
  created_at: string | null
}

const allUsers = ref<UserItem[]>([])
const loading = ref(false)
const searchText = ref('')

// 分页
const currentPage = ref(1)
const pageSize = ref(15)

// 角色编辑
const roleDialog = ref(false)
const editingUser = ref<UserItem | null>(null)
const newRole = ref('')

const roles = [
  { label: '超级管理员', value: 'super_admin', desc: '全局最高权限，管理所有用户和团队' },
  { label: '管理员', value: 'admin', desc: '管理用户，查看全部数据' },
  { label: '标注员', value: 'annotator', desc: '标注数据集，创建和加入团队' },
  { label: '观察者', value: 'viewer', desc: '只读权限' }
]

const roleTagType = (role: string) => {
  if (role === 'super_admin') return 'danger'
  if (role === 'admin') return 'warning'
  if (role === 'annotator') return 'success'
  return 'info'
}

const roleLabel = (role: string) => roles.find(r => r.value === role)?.label || role

// 过滤后的用户 (搜索)
const filteredUsers = computed(() => {
  if (!searchText.value) return allUsers.value
  const q = searchText.value.toLowerCase()
  return allUsers.value.filter(u =>
    u.username.toLowerCase().includes(q) ||
    (u.email || '').toLowerCase().includes(q) ||
    u.role.toLowerCase().includes(q)
  )
})

// 当前页数据
const pagedUsers = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})

// 当前用户不能操作自己 (防误操作)
const isSelf = (userId: number) => userStore.user?.id === userId

// super_admin 不能被非 super_admin 操作
const canManage = (target: UserItem) => {
  if (isSelf(target.id)) return false
  if (target.role === 'super_admin' && userStore.user?.role !== 'super_admin') return false
  return true
}

const loadUsers = async () => {
  loading.value = true
  try {
    const res: any = await userApi.list()
    allUsers.value = res.items || []
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
      <p>管理系统用户账号、角色和状态（仅管理员可见）</p>
    </div>

    <el-card shadow="never" class="main-card">
      <!-- 工具栏: 搜索 + 刷新 -->
      <div class="card-toolbar">
        <el-input
          v-model="searchText" placeholder="搜索用户名 / 邮箱 / 角色"
          :prefix-icon="Search" clearable style="width: 300px"
          @input="currentPage = 1"
        />
        <el-button :icon="Refresh" circle @click="loadUsers" />
        <span class="total-count">共 {{ filteredUsers.length }} 个用户</span>
      </div>

      <el-table :data="pagedUsers" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="120">
          <template #default="{ row }">
            {{ row.username }}
            <el-tag v-if="isSelf(row.id)" size="small" type="primary" effect="plain" class="self-tag">我</el-tag>
          </template>
        </el-table-column>
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
            <el-button
              size="small" :disabled="!canManage(row)"
              @click="onEditRole(row)"
            >改角色</el-button>
            <el-button
              size="small"
              :type="row.is_active ? 'danger' : 'success'"
              plain
              :disabled="!canManage(row)"
              @click="onToggleActive(row)"
            >{{ row.is_active ? '停用' : '激活' }}</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pager">
        <el-pagination
          background
          layout="total, prev, pager, next, sizes"
          :total="filteredUsers.length"
          :page-size="pageSize"
          :current-page="currentPage"
          :page-sizes="[10, 15, 20, 50]"
          @current-change="(p: number) => currentPage = p"
          @size-change="(s: number) => { pageSize = s; currentPage = 1 }"
        />
      </div>
    </el-card>

    <!-- 角色编辑对话框 -->
    <el-dialog v-model="roleDialog" title="修改用户角色" width="480px">
      <div class="role-dialog-body" v-if="editingUser">
        <div class="dialog-row">
          <span class="dialog-label">用户</span>
          <strong>{{ editingUser.username }}</strong>
        </div>
        <div class="dialog-row">
          <span class="dialog-label">当前角色</span>
          <el-tag :type="roleTagType(editingUser.role)" size="small">{{ roleLabel(editingUser.role) }}</el-tag>
        </div>
        <el-divider />
        <p class="dialog-hint">选择新角色：</p>
        <el-radio-group v-model="newRole" class="role-radio-group">
          <el-radio v-for="r in roles" :key="r.value" :value="r.value" class="role-radio">
            <div class="role-info">
              <span class="role-name">{{ r.label }}</span>
              <span class="role-desc">{{ r.desc }}</span>
            </div>
          </el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="roleDialog = false">取消</el-button>
        <el-button type="primary" @click="onSaveRole">确认修改</el-button>
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
.total-count { font-size: 13px; color: var(--text-secondary); margin-left: auto; }
.self-tag { margin-left: 6px; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
.role-dialog-body { padding: 0 4px; }
.dialog-row { display: flex; align-items: center; gap: 12px; margin: 8px 0; }
.dialog-label { color: var(--text-secondary); font-size: 13px; width: 70px; }
.dialog-hint { margin: 0 0 12px; font-size: 14px; color: var(--text-primary); }
.role-radio-group { display: flex; flex-direction: column; gap: 12px; }
.role-radio { display: flex; align-items: flex-start; height: auto; }
.role-info { display: flex; flex-direction: column; }
.role-name { font-weight: 500; font-size: 14px; }
.role-desc { color: var(--text-placeholder); font-size: 12px; margin-top: 2px; }
</style>
