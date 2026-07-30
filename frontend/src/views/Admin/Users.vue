<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { Search, Refresh, Plus, Delete, Key } from '@element-plus/icons-vue'
import '@/styles/admin.css'

const userStore = useUserStore()

interface UserItem {
  id: number; username: string; email: string | null; role: string
  is_active: boolean; created_at: string | null
}

const allUsers = ref<UserItem[]>([])
const loading = ref(false)
const searchText = ref('')
const currentPage = ref(1)
const pageSize = ref(15)

const roleDialog = ref(false)
const editingUser = ref<UserItem | null>(null)
const newRole = ref('')

const createDialog = ref(false)
const createForm = ref({ username: '', password: '', email: '', role: 'annotator' })

const resetDialog = ref(false)
const resetUser = ref<UserItem | null>(null)
const resetPassword = ref('')

const roles = [
  { label: '超级管理员', value: 'super_admin', desc: '全局最高权限' },
  { label: '管理员', value: 'admin', desc: '管理用户，查看全部数据' },
  { label: '标注员', value: 'annotator', desc: '标注数据集，创建和加入团队' },
  { label: '观察者', value: 'viewer', desc: '只读权限' }
]

const roleTagType = (r: string) => r === 'super_admin' ? 'danger' : r === 'admin' ? 'warning' : r === 'annotator' ? 'success' : 'info'
const roleLabel = (r: string) => roles.find(x => x.value === r)?.label || r

const filteredUsers = computed(() => {
  if (!searchText.value) return allUsers.value
  const q = searchText.value.toLowerCase()
  return allUsers.value.filter(u => u.username.toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q) || u.role.toLowerCase().includes(q))
})
const pagedUsers = computed(() => filteredUsers.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value))

// 统计
const stats = computed(() => ({
  total: allUsers.value.length,
  active: allUsers.value.filter(u => u.is_active).length,
  admins: allUsers.value.filter(u => u.role.includes('admin')).length,
  annotators: allUsers.value.filter(u => u.role === 'annotator').length,
}))

const isSelf = (id: number) => userStore.user?.id === id
const canManage = (t: UserItem) => !isSelf(t.id) && !(t.role === 'super_admin' && userStore.user?.role !== 'super_admin')

const loadUsers = async () => {
  loading.value = true
  try { const res: any = await userApi.list(); allUsers.value = res.items || [] }
  catch (e: any) { ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message)) }
  finally { loading.value = false }
}

const onToggleActive = async (u: UserItem) => {
  const a = u.is_active ? '停用' : '激活'
  try { await ElMessageBox.confirm(`确认${a}用户 "${u.username}"？`, '提示', { type: 'warning' }) } catch { return }
  try { u.is_active ? await userApi.deactivate(u.id) : await userApi.activate(u.id); ElMessage.success(`${a}成功`); await loadUsers() }
  catch (e: any) { ElMessage.error(`${a}失败: ` + (e?.response?.data?.detail || e?.message)) }
}

const onEditRole = (u: UserItem) => { editingUser.value = u; newRole.value = u.role; roleDialog.value = true }
const onSaveRole = async () => {
  if (!editingUser.value) return
  try { await userApi.changeRole(editingUser.value.id, newRole.value); ElMessage.success('角色修改成功'); roleDialog.value = false; await loadUsers() }
  catch (e: any) { ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message)) }
}

const onCreate = async () => {
  if (!createForm.value.username || !createForm.value.password) { ElMessage.warning('用户名和密码必填'); return }
  try {
    await userApi.create({ username: createForm.value.username, password: createForm.value.password, email: createForm.value.email || undefined, role: createForm.value.role })
    ElMessage.success('用户创建成功'); createDialog.value = false
    createForm.value = { username: '', password: '', email: '', role: 'annotator' }; await loadUsers()
  } catch (e: any) { ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message)) }
}

const onDelete = async (u: UserItem) => {
  try { await ElMessageBox.confirm(`确认删除用户 "${u.username}"？此操作不可恢复。`, '危险操作', { type: 'error', confirmButtonText: '确认删除' }) } catch { return }
  try { await userApi.remove(u.id); ElMessage.success('删除成功'); await loadUsers() }
  catch (e: any) { ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message)) }
}

const onResetPassword = (u: UserItem) => { resetUser.value = u; resetPassword.value = ''; resetDialog.value = true }
const onSaveResetPassword = async () => {
  if (!resetUser.value || !resetPassword.value) { ElMessage.warning('请输入新密码'); return }
  try { await userApi.resetPassword(resetUser.value.id, resetPassword.value); ElMessage.success('密码重置成功'); resetDialog.value = false }
  catch (e: any) { ElMessage.error('重置失败: ' + (e?.response?.data?.detail || e?.message)) }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'
onMounted(loadUsers)
</script>

<template>
  <div class="admin-page">
    <!-- 统计卡 -->
    <div class="stat-row">
      <div class="stat-card stat-card--brand">
        <div class="stat-card__num">{{ stats.total }}</div>
        <div class="stat-card__label">总用户数</div>
      </div>
      <div class="stat-card stat-card--success">
        <div class="stat-card__num">{{ stats.active }}</div>
        <div class="stat-card__label">活跃用户</div>
      </div>
      <div class="stat-card stat-card--warm">
        <div class="stat-card__num">{{ stats.admins }}</div>
        <div class="stat-card__label">管理员</div>
      </div>
      <div class="stat-card stat-card--purple">
        <div class="stat-card__num">{{ stats.annotators }}</div>
        <div class="stat-card__label">标注员</div>
      </div>
    </div>

    <!-- 页头 -->
    <div class="page-header">
      <div class="page-header-left">
        <h2>用户管理 <span class="subtitle">Users</span></h2>
        <p>管理系统用户账号（仅管理员可见）</p>
      </div>
    </div>

    <!-- 主卡片 -->
    <el-card shadow="never" class="main-card">
      <div class="card-toolbar">
        <el-input v-model="searchText" placeholder="搜索用户名/邮箱/角色" :prefix-icon="Search" clearable style="width: 280px" @input="currentPage = 1" />
        <el-button :icon="Refresh" circle @click="loadUsers" />
        <el-button type="primary" :icon="Plus" @click="createDialog = true">添加用户</el-button>
        <span class="total-count">共 {{ filteredUsers.length }} 个用户</span>
      </div>

      <el-table :data="pagedUsers" v-loading="loading" stripe class="data-table">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="120">
          <template #default="{ row }">{{ row.username }}<el-tag v-if="isSelf(row.id)" size="small" type="primary" effect="plain" class="self-tag">我</el-tag></template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.email || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="120">
          <template #default="{ row }"><el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'danger'" size="small" effect="plain">{{ row.is_active ? '活跃' : '停用' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="380" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" :disabled="!canManage(row)" @click="onEditRole(row)">改角色</el-button>
              <el-button size="small" :icon="Key" :disabled="!canManage(row)" @click="onResetPassword(row)">重置密码</el-button>
              <el-button size="small" :type="row.is_active ? 'danger' : 'success'" plain :disabled="!canManage(row)" @click="onToggleActive(row)">{{ row.is_active ? '停用' : '激活' }}</el-button>
              <el-button size="small" type="danger" :icon="Delete" :disabled="!canManage(row)" @click="onDelete(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination background layout="total, prev, pager, next, sizes" :total="filteredUsers.length" :page-size="pageSize" :current-page="currentPage" :page-sizes="[10, 15, 20, 50]" @current-change="(p: number) => currentPage = p" @size-change="(s: number) => { pageSize = s; currentPage = 1 }" />
      </div>
    </el-card>

    <!-- 角色编辑 -->
    <el-dialog v-model="roleDialog" title="修改用户角色" width="480px">
      <div v-if="editingUser">
        <div class="dialog-row"><span class="dialog-label">用户</span><strong>{{ editingUser.username }}</strong></div>
        <div class="dialog-row"><span class="dialog-label">当前角色</span><el-tag :type="roleTagType(editingUser.role)" size="small">{{ roleLabel(editingUser.role) }}</el-tag></div>
        <el-divider />
        <el-radio-group v-model="newRole" class="role-radio-group">
          <el-radio v-for="r in roles" :key="r.value" :value="r.value" class="role-radio">
            <div class="role-info"><span class="role-name">{{ r.label }}</span><span class="role-desc">{{ r.desc }}</span></div>
          </el-radio>
        </el-radio-group>
      </div>
      <template #footer><el-button @click="roleDialog = false">取消</el-button><el-button type="primary" @click="onSaveRole">确认修改</el-button></template>
    </el-dialog>

    <!-- 创建用户 -->
    <el-dialog v-model="createDialog" title="添加用户" width="460px">
      <el-form label-position="top" :model="createForm">
        <el-form-item label="用户名"><el-input v-model="createForm.username" placeholder="2-50 字符" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="createForm.password" type="password" show-password placeholder="至少 6 位" /></el-form-item>
        <el-form-item label="邮箱 (可选)"><el-input v-model="createForm.email" placeholder="user@example.com" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="r in roles" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="createDialog = false">取消</el-button><el-button type="primary" @click="onCreate">创建</el-button></template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetDialog" title="重置用户密码" width="440px">
      <div v-if="resetUser">
        <div class="dialog-row"><span class="dialog-label">用户</span><strong>{{ resetUser.username }}</strong></div>
        <el-divider />
        <el-input v-model="resetPassword" type="password" show-password placeholder="输入新密码 (至少 6 位)" />
      </div>
      <template #footer><el-button @click="resetDialog = false">取消</el-button><el-button type="primary" @click="onSaveResetPassword">确认重置</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* admin.css 已通过 @import 加载 */
</style>
