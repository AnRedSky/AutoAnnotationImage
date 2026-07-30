<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { teamApi, userApi, datasetApi } from '@/api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

interface TeamItem {
  id: number
  name: string
  slug: string
  my_role: string
}

interface TeamMemberItem {
  user_id: number
  username: string
  email: string | null
  role: string
  joined_at: string | null
}

interface DatasetItem {
  id: number
  name: string
  owner_id: number
  team_id: number | null
}

interface UserItem {
  id: number
  username: string
  role: string
}

const tabActive = ref('teams')
const teams = ref<TeamItem[]>([])
const teamMembers = ref<TeamMemberItem[]>([])
const allUsers = ref<UserItem[]>([])
const datasets = ref<DatasetItem[]>([])
const loading = ref(false)
const selectedTeamId = ref<number | undefined>()

// 团队成员邀请
const inviteDialog = ref(false)
const inviteUserId = ref<number | undefined>()
const inviteRole = ref('annotator')

// 数据集共享
const shareDialog = ref(false)
const shareDatasetId = ref<number | undefined>()
const shareTeamId = ref<number | undefined>()

const teamRoles = [
  { label: '队长', value: 'leader' },
  { label: '标注员', value: 'annotator' },
  { label: '观察者', value: 'viewer' }
]

const roleLabel = (role: string) => teamRoles.find(r => r.value === role)?.label || role

const loadTeams = async () => {
  loading.value = true
  try {
    const res: any = await teamApi.list()
    teams.value = res.items || []
    if (teams.value.length > 0 && !selectedTeamId.value) {
      selectedTeamId.value = teams.value[0].id
      await loadTeamMembers()
    }
  } catch (e: any) {
    ElMessage.error('加载团队失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const loadTeamMembers = async () => {
  if (!selectedTeamId.value) return
  try {
    const res: any = await teamApi.listMembers(selectedTeamId.value)
    teamMembers.value = res.items || []
  } catch {}
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

const onTeamChange = () => loadTeamMembers()

const onInvite = async () => {
  if (!selectedTeamId.value || !inviteUserId.value) {
    ElMessage.warning('请选择用户')
    return
  }
  try {
    await teamApi.inviteMember(selectedTeamId.value, {
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
  if (!selectedTeamId.value) return
  try {
    await ElMessageBox.confirm(`确认移除 "${m.username}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await teamApi.removeMember(selectedTeamId.value, m.user_id)
    ElMessage.success('移除成功')
    await loadTeamMembers()
  } catch (e: any) {
    ElMessage.error('移除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onShareDataset = async () => {
  if (!shareDatasetId.value || !shareTeamId.value) {
    ElMessage.warning('请选择数据集和团队')
    return
  }
  try {
    await teamApi.shareDataset(shareDatasetId.value, shareTeamId.value)
    ElMessage.success('共享成功')
    shareDialog.value = false
    shareDatasetId.value = undefined
    shareTeamId.value = undefined
    await loadDatasets()
  } catch (e: any) {
    ElMessage.error('共享失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onUnshare = async (d: DatasetItem) => {
  try {
    await ElMessageBox.confirm(`确认取消共享 "${d.name}"？`, '提示', { type: 'warning' })
  } catch { return }
  try {
    await teamApi.unshareDataset(d.id)
    ElMessage.success('取消共享成功')
    await loadDatasets()
  } catch (e: any) {
    ElMessage.error('取消共享失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(async () => {
  await Promise.all([loadTeams(), loadAllUsers(), loadDatasets()])
})
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>角色权限</h2>
      <p>管理团队成员角色和数据集共享</p>
    </div>

    <el-tabs v-model="tabActive" class="role-tabs">
      <!-- 团队成员 -->
      <el-tab-pane label="团队成员" name="teams">
        <el-card shadow="never" class="main-card">
          <div class="card-toolbar">
            <el-select v-model="selectedTeamId" placeholder="选择团队" style="width: 200px" @change="onTeamChange">
              <el-option v-for="t in teams" :key="t.id" :label="t.name" :value="t.id" />
            </el-select>
            <el-button type="primary" :disabled="!selectedTeamId" @click="inviteDialog = true">邀请成员</el-button>
          </div>
          <el-table :data="teamMembers" v-loading="loading" stripe>
            <el-table-column prop="user_id" label="ID" width="70" />
            <el-table-column prop="username" label="用户名" min-width="120" />
            <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.email || '-' }}</template>
            </el-table-column>
            <el-table-column label="角色" width="120">
              <template #default="{ row }">
                <el-tag :type="row.role === 'leader' ? 'warning' : 'success'" size="small">{{ roleLabel(row.role) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="加入时间" width="170">
              <template #default="{ row }">{{ fmtDate(row.joined_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.role !== 'leader'" size="small" type="danger" plain @click="onRemoveMember(row)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 数据集共享 -->
      <el-tab-pane label="数据集共享" name="share">
        <el-card shadow="never" class="main-card">
          <div class="card-toolbar">
            <el-button type="primary" @click="shareDialog = true">共享给团队</el-button>
          </div>
          <el-table :data="datasets" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="owner_id" label="Owner" width="80" />
            <el-table-column label="团队" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.team_id" size="small" type="success">已共享</el-tag>
                <span v-else class="muted">个人</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.team_id" size="small" type="warning" plain @click="onUnshare(row)">取消共享</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 邀请成员对话框 -->
    <el-dialog v-model="inviteDialog" title="邀请成员" width="440px">
      <el-form label-position="top">
        <el-form-item label="选择用户">
          <el-select v-model="inviteUserId" filterable placeholder="搜索用户名" style="width: 100%">
            <el-option
              v-for="u in allUsers"
              :key="u.id"
              :label="`${u.username} (${u.role})`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="inviteRole">
            <el-radio v-for="r in teamRoles" :key="r.value" :value="r.value">{{ r.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inviteDialog = false">取消</el-button>
        <el-button type="primary" @click="onInvite">确认</el-button>
      </template>
    </el-dialog>

    <!-- 共享数据集对话框 -->
    <el-dialog v-model="shareDialog" title="共享数据集给团队" width="440px">
      <el-form label-position="top">
        <el-form-item label="数据集">
          <el-select v-model="shareDatasetId" filterable placeholder="选择数据集" style="width: 100%">
            <el-option v-for="d in datasets" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="共享给团队">
          <el-select v-model="shareTeamId" filterable placeholder="选择团队" style="width: 100%">
            <el-option v-for="t in teams" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
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
.card-toolbar { margin-bottom: 16px; display: flex; align-items: center; gap: 12px; }
.muted { color: var(--text-placeholder); font-size: 12px; }
.role-tabs :deep(.el-tabs__item) { font-size: 14px; font-weight: 500; }
</style>
