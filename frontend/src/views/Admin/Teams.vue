<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { teamApi, userApi } from '@/api'

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

interface UserItem {
  id: number
  username: string
  role: string
}

const teams = ref<TeamItem[]>([])
const allUsers = ref<UserItem[]>([])
const loading = ref(false)
const createDialog = ref(false)
const createForm = ref({ name: '', slug: '', description: '', max_members: 20 })

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

const loadUsers = async () => {
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
    ElMessage.success('团队创建成功')
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

const roleLabel = (role: string) => {
  if (role === 'leader') return '队长'
  if (role === 'annotator') return '标注员'
  return '观察者'
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(async () => {
  await Promise.all([loadTeams(), loadUsers()])
})
</script>

<template>
  <div class="admin-page">
    <div class="page-header">
      <h2>团队管理</h2>
      <p>创建团队、邀请成员、共享数据集进行协同标注</p>
    </div>

    <el-card shadow="never" class="main-card">
      <div class="card-toolbar">
        <el-button type="primary" @click="createDialog = true">创建团队</el-button>
      </div>

      <el-table :data="teams" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="slug" label="短标识" min-width="120">
          <template #default="{ row }"><code>{{ row.slug }}</code></template>
        </el-table-column>
        <el-table-column label="我的角色" width="100">
          <template #default="{ row }">
            <el-tag :type="row.my_role === 'leader' ? 'warning' : 'success'" size="small">
              {{ roleLabel(row.my_role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="max_members" label="成员上限" width="100" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.my_role === 'leader'"
              size="small" type="danger" plain
              @click="onDelete(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

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
  </div>
</template>

<style scoped>
.admin-page { max-width: 1200px; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 22px; }
.page-header p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.main-card { border-radius: 12px; }
.card-toolbar { margin-bottom: 16px; }
</style>
