<script setup lang="ts">
/**
 * 团队管理主页面 (v3.3.1)
 * =========================
 *
 * 架构: 主页面是唯一的协调层, 负责:
 *   1. 视图切换 (列表视图 / 详情视图)
 *   2. 集中发起 API 请求
 *   3. 编排弹窗 (创建/编辑/转让/邀请/改角色/退队)
 *   4. 维护一份完整的响应式数据 (selectedTeam / members / datasets)
 *
 * 子组件均为纯展示型, 接收 props 渲染, 通过 emit 抛出操作意图,
 * 所有数据加载和请求均由本页面完成 (符合规范 §三-2 单向数据流)。
 */
import { ref, onMounted, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { teamApi, userApi, type TeamItem, type TeamMemberItem, type TeamDatasetItem } from '@/api'
import { useUserStore } from '@/stores/user'

import TeamList from './components/TeamList.vue'
import TeamDetailHeader from './components/TeamDetailHeader.vue'
import TeamMemberTable from './components/TeamMemberTable.vue'
import TeamDatasetList from './components/TeamDatasetList.vue'

import CreateTeamDialog from './components/dialogs/CreateTeamDialog.vue'
import EditTeamDialog from './components/dialogs/EditTeamDialog.vue'
import InviteMemberDialog from './components/dialogs/InviteMemberDialog.vue'
import EditRoleDialog from './components/dialogs/EditRoleDialog.vue'
import TransferOwnerDialog from './components/dialogs/TransferOwnerDialog.vue'

const userStore = useUserStore()

// ============== 响应式状态 ==============

/** 我的团队列表 */
const teams = ref<TeamItem[]>([])
/** 全平台用户 (用于邀请下拉) */
const allUsers = ref<{ id: number; username: string; role: string }[]>([])

/** 当前选中的团队 (详情视图) */
const selectedTeam = ref<TeamItem | null>(null)
/** 当前团队的成员 */
const teamMembers = ref<TeamMemberItem[]>([])
/** 当前团队共享的数据集 */
const teamDatasets = ref<TeamDatasetItem[]>([])

/** loading 状态 */
const loading = ref(false)
const memberLoading = ref(false)
const datasetLoading = ref(false)

/** 视图模式 */
const view = ref<'list' | 'detail'>('list')

/** 详情视图活动 Tab */
const activeTab = ref<'members' | 'datasets'>('members')

// ============== 弹窗可见性 ==============
const showCreate = ref(false)
const showEdit = ref(false)
const showInvite = ref(false)
const showEditRole = ref(false)
const showTransfer = ref(false)

/** 邀请弹窗上下文 */
const inviteContext = ref<{ teamId: number; members: TeamMemberItem[] } | null>(null)
/** 改角色弹窗上下文 */
const editingMember = ref<TeamMemberItem | null>(null)

// ============== 计算属性 ==============

const currentUserId = computed(() => userStore.user?.id || 0)

const isOwner = computed(
  () => !!selectedTeam.value && selectedTeam.value.owner_id === currentUserId.value
)
const isManager = computed(
  () => !!selectedTeam.value && selectedTeam.value.my_role === 'manager'
)

/** 团队已满 (用于禁用邀请按钮) */
const isFull = computed(
  () => !!selectedTeam.value && teamMembers.value.length >= selectedTeam.value.max_members
)

/** 已加入团队 ID 集合 (用于过滤邀请下拉) */
const memberIdSet = computed(() => new Set(teamMembers.value.map((m) => m.user_id)))

// ============== 视图切换 ==============

const onEnterTeam = async (team: TeamItem) => {
  selectedTeam.value = team
  view.value = 'detail'
  activeTab.value = 'members'
  await Promise.all([loadTeamMembers(), loadTeamDatasets()])
}

const onBackToList = () => {
  view.value = 'list'
  selectedTeam.value = null
  teamMembers.value = []
  teamDatasets.value = []
  // 刷新团队列表 (可能成员数 / max_members 已变更)
  loadTeams()
}

// ============== 数据加载 ==============

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
  } catch {
    // 非 admin 用户无权访问 /api/users, 静默失败
    allUsers.value = []
  }
}

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

const loadTeamDatasets = async () => {
  if (!selectedTeam.value) return
  datasetLoading.value = true
  try {
    const res: any = await teamApi.listDatasets(selectedTeam.value.id)
    teamDatasets.value = res.items || []
  } catch (e: any) {
    ElMessage.error('加载数据集失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    datasetLoading.value = false
  }
}

// ============== 列表视图操作 ==============

const onCreateClick = () => {
  showCreate.value = true
}

const onCreated = async () => {
  await loadTeams()
}

const onDeleteTeam = async (t: TeamItem) => {
  try {
    await ElMessageBox.confirm(
      `确认删除团队 "${t.name}"?所有共享数据集将变为个人数据集。`,
      '提示',
      { type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await teamApi.remove(t.id)
    ElMessage.success('删除成功')
    await loadTeams()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 详情视图: 编辑/转让/退队 ==============

const onEditClick = () => {
  showEdit.value = true
}

const onEdited = async () => {
  await Promise.all([loadTeams(), loadTeamDatasets()])
  // 同步 selectedTeam 引用
  if (selectedTeam.value) {
    const updated = teams.value.find((t) => t.id === selectedTeam.value!.id)
    if (updated) selectedTeam.value = updated
  }
}

const onTransferClick = () => {
  showTransfer.value = true
}

const onTransferred = async () => {
  // 转让后当前用户不再是 owner, 退回列表
  ElMessage.info('已退出该团队管理视图')
  onBackToList()
}

const onLeaveClick = async () => {
  if (!selectedTeam.value) return
  try {
    await ElMessageBox.confirm(
      `确认退出团队 "${selectedTeam.value.name}"?退出后您将无法访问该团队及其共享数据集。`,
      '提示',
      { type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await teamApi.leaveTeam(selectedTeam.value.id)
    ElMessage.success('已退出团队')
    onBackToList()
  } catch (e: any) {
    ElMessage.error('退出失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 详情视图: 成员管理 ==============

const onInviteClick = () => {
  if (!selectedTeam.value) return
  inviteContext.value = {
    teamId: selectedTeam.value.id,
    members: teamMembers.value,
  }
  showInvite.value = true
}

const onInvited = async () => {
  await Promise.all([loadTeamMembers(), loadTeams()])
}

const onEditRoleClick = (m: TeamMemberItem) => {
  editingMember.value = m
  showEditRole.value = true
}

const onRoleUpdated = async () => {
  await Promise.all([loadTeamMembers(), loadTeams()])
}

const onMemberRemoved = async () => {
  await Promise.all([loadTeamMembers(), loadTeams()])
}

// ============== 详情视图: 数据集 ==============

const onViewDataset = (d: TeamDatasetItem) => {
  // 路由跳转由子组件内部完成 (TeamDatasetList 已封装)
  // 这里只打点 (未来可加审计)
  console.info('[TeamDetail] View dataset:', d.id)
}

const onUnshareDataset = async (d: TeamDatasetItem) => {
  try {
    await ElMessageBox.confirm(
      `确认取消数据集 "${d.name}" 在当前团队的共享?\n\n该团队所有成员将立即失去访问权限。`,
      '提示',
      { type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await teamApi.unshareDataset(d.id)
    ElMessage.success('已取消共享')
    await loadTeamDatasets()
  } catch (e: any) {
    ElMessage.error('取消失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 生命周期 ==============

onMounted(async () => {
  await Promise.all([loadTeams(), loadAllUsers()])
})

/** 监听视图切换, 当回到列表时刷新一次 (确保成员数同步) */
watch(view, (v) => {
  if (v === 'list') loadTeams()
})
</script>

<template>
  <div class="admin-page">
    <!-- ============ 列表视图 ============ -->
    <TeamList
      v-if="view === 'list'"
      :teams="teams"
      :loading="loading"
      :current-user-id="currentUserId"
      @create="onCreateClick"
      @enter="onEnterTeam"
      @delete-team="onDeleteTeam"
    />

    <!-- ============ 详情视图 ============ -->
    <template v-if="view === 'detail' && selectedTeam">
      <TeamDetailHeader
        :team="selectedTeam"
        :is-owner="isOwner"
        :is-manager="isManager"
        @back="onBackToList"
        @edit="onEditClick"
        @transfer="onTransferClick"
        @leave="onLeaveClick"
      />

      <el-tabs v-model="activeTab" class="detail-tabs">
        <el-tab-pane label="成员" name="members">
          <TeamMemberTable
            :team-id="selectedTeam.id"
            :members="teamMembers"
            :loading="memberLoading"
            :can-manage="isManager"
            :max-members="selectedTeam.max_members"
            @invite="onInviteClick"
            @edit-role="onEditRoleClick"
            @remove="onMemberRemoved"
            @changed="onMemberRemoved"
          />
        </el-tab-pane>
        <el-tab-pane :label="`共享数据集 (${teamDatasets.length})`" name="datasets">
          <TeamDatasetList
            :datasets="teamDatasets"
            :loading="datasetLoading"
            :can-manage="isManager"
            @view-dataset="onViewDataset"
            @unshare="onUnshareDataset"
          />
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- ============ 创建团队弹窗 ============ -->
    <CreateTeamDialog v-model="showCreate" @created="onCreated" />

    <!-- ============ 编辑团队弹窗 ============ -->
    <EditTeamDialog
      v-model="showEdit"
      :team="selectedTeam"
      @updated="onEdited"
    />

    <!-- ============ 邀请成员弹窗 ============ -->
    <InviteMemberDialog
      v-if="inviteContext"
      v-model="showInvite"
      :team-id="inviteContext.teamId"
      :all-users="allUsers"
      :member-ids="memberIdSet"
      @invited="onInvited"
    />

    <!-- ============ 改角色弹窗 ============ -->
    <EditRoleDialog
      v-model="showEditRole"
      :team-id="selectedTeam?.id || 0"
      :member="editingMember"
      @updated="onRoleUpdated"
    />

    <!-- ============ 转让所有权弹窗 ============ -->
    <TransferOwnerDialog
      v-if="selectedTeam"
      v-model="showTransfer"
      :team-id="selectedTeam.id"
      :team-name="selectedTeam.name"
      :members="teamMembers"
      @transferred="onTransferred"
    />
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

.detail-tabs {
  margin-top: 8px;
}
.detail-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
}
</style>
