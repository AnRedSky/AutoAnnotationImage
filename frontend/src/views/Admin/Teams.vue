<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { teamApi, userApi } from '@/api'
import { useUserStore } from '@/stores/user'
import {
  Plus, Delete, User, ArrowLeft, Setting
} from '@element-plus/icons-vue'

const userStore = useUserStore()

interface TeamItem {
  id: number
  name: string
  slug: string
  description: string | null
  owner_id: number
  max_members: number
  my_role: string
  created_at: string | null
}

interface TeamMemberItem {
  user_id: number
  username: string
  email: string | null
  role: string
  joined_at: string | null
}

interface UserItem {
  id: number
  username: string
  role: string
}

const teams = ref<TeamItem[]>([])
const allUsers = ref<UserItem[]>([])
const teamMembers = ref<TeamMemberItem[]>([])
const loading = ref(false)
const memberLoading = ref(false)

// 视图: 'list' = 团队列表, 'detail' = 团队详情(成员管理)
const view = ref<'list' | 'detail'>('list')
const selectedTeam = ref<TeamItem | null>(null)

// 创建团队
const createDialog = ref(false)
const createForm = ref({ name: '', slug: '', description: '', max_members: 20 })

// 邀请成员
const inviteDialog = ref(false)
const inviteUserId = ref<number | undefined>()
const inviteRole = ref('annotator')

// 改成员角色
const roleDialog = ref(false)
const editingMember = ref<TeamMemberItem | null>(null)
const editMemberRole = ref('editor')

const teamRoles = [
  { label: '可管理', value: 'manager', desc: '管理成员 + 配置数据集权限 + 编辑标注' },
  { label: '可编辑', value: 'editor', desc: '可对共享数据集进行标注' },
  { label: '仅阅读', value: 'viewer', desc: '只读' }
]

const roleLabel = (role: string) => teamRoles.find(r => r.value === role)?.label || role
const roleTagType = (role: string) => role === 'manager' ? 'warning' : role === 'editor' ? 'success' : 'info'

const canManage = computed(() => {
  if (!selectedTeam.value) return false
  return selectedTeam.value.my_role === 'manager' || userStore.user?.role?.includes('admin') === true
})

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

// ============== 团队列表 ==============

const loadTeams = async () => {
  loading.value = true
  try {
    const res: any = await teamApi.list()
    teams.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载团队列表失败: ' + (e?.response?.data?.detail || e?.message))
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

const onCreate = async () => {
  if (!createForm.value.name || !createForm.value.slug) {
    ElMessage.warning('名称和短标识不能为空')
    return
  }
  try {
    await teamApi.create({ ...createForm.value })
    ElMessage.success('团队创建成功，您自动成为队长')
    createDialog.value = false
    createForm.value = { name: '', slug: '', description: '', max_members: 20 }
    await loadTeams()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onDelete = async (t: TeamItem) => {
  try {
    await ElMessageBox.confirm(`确认删除团队 "${t.name}"？所有共享数据集将变为个人数据集。`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await teamApi.remove(t.id)
    ElMessage.success('删除成功')
    await loadTeams()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onEnterTeam = (t: TeamItem) => {
  selectedTeam.value = t
  view.value = 'detail'
  loadTeamMembers()
}

const onBackToList = () => {
  view.value = 'list'
  selectedTeam.value = null
  teamMembers.value = []
}

// ============== 团队成员管理 ==============

const loadTeamMembers = async () => {
  if (!selectedTeam.value) return
  memberLoading.value = true
  try {
    const res: any = await teamApi.listMembers(selectedTeam.value.id)
    teamMembers.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载成员失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    memberLoading.value = false
  }
}

const onInvite = async () => {
  if (!selectedTeam.value || !inviteUserId.value) {
    ElMessage.warning('请选择用户')
    return
  }
  try {
    await teamApi.inviteMember(selectedTeam.value.id, {
      user_id: inviteUserId.value,
      role: inviteRole.value
    })
    ElMessage.success('邀请成功')
    inviteDialog.value = false
    inviteUserId.value = undefined
    await loadTeamMembers()
  } catch (e: any) {
    ElMessage.error('邀请失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onRemoveMember = async (m: TeamMemberItem) => {
  if (!selectedTeam.value) return
  try {
    await ElMessageBox.confirm(`确认从团队移除 "${m.username}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await teamApi.removeMember(selectedTeam.value.id, m.user_id)
    ElMessage.success('移除成功')
    await loadTeamMembers()
  } catch (e: any) {
    ElMessage.error('移除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onEditMemberRole = (m: TeamMemberItem) => {
  editingMember.value = m
  editMemberRole.value = m.role
  roleDialog.value = true
}

const onSaveMemberRole = async () => {
  if (!selectedTeam.value || !editingMember.value) return
  try {
    await teamApi.updateMemberRole(selectedTeam.value.id, editingMember.value.user_id, editMemberRole.value)
    ElMessage.success('角色修改成功')
    roleDialog.value = false
    await loadTeamMembers()
  } catch (e: any) {
    ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// 过滤掉已在团队中的用户
const availableUsers = computed(() => {
  const memberIds = new Set(teamMembers.value.map(m => m.user_id))
  return allUsers.value.filter(u => !memberIds.has(u.id))
})

onMounted(async () => {
  await Promise.all([loadTeams(), loadAllUsers()])
})
</script>

<template>
  <div class="admin-page">
    <!-- ============ 团队列表视图 ============ -->
    <template v-if="view === 'list'">
      <div class="page-header">
        <h2>团队管理</h2>
        <p>创建团队、邀请成员、共享数据集进行协同标注</p>
      </div>

      <el-card shadow="never" class="main-card">
        <div class="card-toolbar">
          <el-button type="primary" :icon="Plus" @click="createDialog = true">创建团队</el-button>
        </div>

        <el-table :data="teams" v-loading="loading" stripe class="data-table" style="width: 100%">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="slug" label="短标识" min-width="120">
            <template #default="{ row }"><code>{{ row.slug }}</code></template>
          </el-table-column>
          <el-table-column label="我的角色" width="100">
            <template #default="{ row }">
              <el-tag :type="roleTagType(row.my_role)" size="small">{{ roleLabel(row.my_role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="max_members" label="成员上限" width="100" />
          <el-table-column label="创建时间" width="170">
            <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" plain @click="onEnterTeam(row)">进入管理</el-button>
              <el-button
                v-if="row.my_role === 'manager'"
                size="small" type="danger" plain :icon="Delete"
                @click="onDelete(row)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-empty v-if="!loading && teams.length === 0" description="还没有团队，点击上方按钮创建第一个" />
      </el-card>
    </template>

    <!-- ============ 团队详情视图 (成员管理) ============ -->
    <template v-if="view === 'detail' && selectedTeam">
      <div class="page-header">
        <el-button :icon="ArrowLeft" text @click="onBackToList">返回团队列表</el-button>
      <h2>{{ selectedTeam.name }} <span class="slug-badge">{{ selectedTeam.slug }}</span></h2>
        <p>{{ selectedTeam.description || '无描述' }} · 我的角色: {{ roleLabel(selectedTeam.my_role) }}</p>
      </div>

      <el-card shadow="never" class="main-card">
        <div class="card-toolbar">
          <span class="member-count">成员 ({{ teamMembers.length }}/{{ selectedTeam.max_members }})</span>
          <el-button
            v-if="canManage"
            type="primary" :icon="User"
            :disabled="teamMembers.length >= selectedTeam.max_members"
            @click="inviteDialog = true"
          >邀请成员</el-button>
        </div>

        <el-table :data="teamMembers" v-loading="memberLoading" stripe class="data-table">
          <el-table-column prop="user_id" label="ID" width="70" />
          <el-table-column prop="username" label="用户名" min-width="120" />
          <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.email || '-' }}</template>
          </el-table-column>
          <el-table-column label="角色" width="120">
            <template #default="{ row }">
              <el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="加入时间" width="170">
            <template #default="{ row }">{{ fmtDate(row.joined_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right" v-if="canManage">
            <template #default="{ row }">
              <el-button
                v-if="row.role !== 'manager'"
                size="small" :icon="Setting"
                @click="onEditMemberRole(row)"
              >改角色</el-button>
              <el-button
                v-if="row.role !== 'manager'"
                size="small" type="danger" plain
                @click="onRemoveMember(row)"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- ============ 创建团队对话框 ============ -->
    <el-dialog v-model="createDialog" title="创建团队" width="460px">
      <el-form label-position="top" :model="createForm">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="如: 标注组A" />
        </el-form-item>
        <el-form-item label="短标识 (slug)">
          <el-input v-model="createForm.slug" placeholder="如: annot-group-a" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" placeholder="团队描述（可选）" />
        </el-form-item>
        <el-form-item label="成员上限">
          <el-input-number v-model="createForm.max_members" :min="2" :max="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 邀请成员对话框 ============ -->
    <el-dialog v-model="inviteDialog" title="邀请成员加入团队" width="440px">
      <el-form label-position="top">
        <el-form-item label="选择用户">
          <el-select v-model="inviteUserId" filterable placeholder="搜索用户名" style="width: 100%">
            <el-option
              v-for="u in availableUsers"
              :key="u.id"
              :label="`${u.username} (${u.role})`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="inviteRole" class="role-radio-group">
            <el-radio v-for="r in teamRoles" :key="r.value" :value="r.value" class="role-radio">
              <span class="role-name">{{ r.label }}</span>
              <span class="role-desc">{{ r.desc }}</span>
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inviteDialog = false">取消</el-button>
        <el-button type="primary" @click="onInvite">确认邀请</el-button>
      </template>
    </el-dialog>

    <!-- ============ 修改成员角色对话框 ============ -->
    <el-dialog v-model="roleDialog" title="修改成员角色" width="440px">
      <div v-if="editingMember" class="role-dialog-body">
        <p>用户: <strong>{{ editingMember.username }}</strong></p>
        <p>当前角色: <el-tag :type="roleTagType(editingMember.role)" size="small">{{ roleLabel(editingMember.role) }}</el-tag></p>
        <el-divider />
        <el-radio-group v-model="editMemberRole" class="role-radio-group">
          <el-radio v-for="r in teamRoles" :key="r.value" :value="r.value" class="role-radio">
            <span class="role-name">{{ r.label }}</span>
            <span class="role-desc">{{ r.desc }}</span>
          </el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="roleDialog = false">取消</el-button>
        <el-button type="primary" @click="onSaveMemberRole">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

/* Teams.vue 特有样式 */
.member-count { font-size: 14px; font-weight: 500; color: var(--text-secondary); }
</style>