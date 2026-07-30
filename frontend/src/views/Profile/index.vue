<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { userApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { User, Lock, Message } from '@element-plus/icons-vue'

const userStore = useUserStore()

interface Profile {
  id: number
  username: string
  email: string | null
  role: string
  is_active: boolean
  created_at: string | null
}

const profile = ref<Profile | null>(null)
const loading = ref(false)

// 编辑邮箱
const editEmail = ref('')
const savingEmail = ref(false)

// 修改密码
const passwordForm = ref({ old_password: '', new_password: '', confirm_password: '' })
const savingPassword = ref(false)

const roleLabel = (role: string) => {
  const map: Record<string, string> = {
    super_admin: '超级管理员', admin: '管理员',
    annotator: '标注员', viewer: '观察者'
  }
  return map[role] || role
}

const loadProfile = async () => {
  loading.value = true
  try {
    const res: any = await userApi.getProfile()
    profile.value = res
    editEmail.value = res.email || ''
  } catch (e: any) {
    ElMessage.error('加载个人信息失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const onSaveEmail = async () => {
  savingEmail.value = true
  try {
    const res: any = await userApi.updateProfile({ email: editEmail.value || undefined })
    profile.value = res
    ElMessage.success('邮箱修改成功')
  } catch (e: any) {
    ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    savingEmail.value = false
  }
}

const onChangePassword = async () => {
  if (!passwordForm.value.old_password || !passwordForm.value.new_password) {
    ElMessage.warning('请填写完整')
    return
  }
  if (passwordForm.value.new_password !== passwordForm.value.confirm_password) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  if (passwordForm.value.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  savingPassword.value = true
  try {
    await userApi.changePassword({
      old_password: passwordForm.value.old_password,
      new_password: passwordForm.value.new_password
    })
    ElMessage.success('密码修改成功')
    passwordForm.value = { old_password: '', new_password: '', confirm_password: '' }
  } catch (e: any) {
    ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    savingPassword.value = false
  }
}

const fmtDate = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN') : '-'

onMounted(loadProfile)
</script>

<template>
  <div class="profile-page" v-loading="loading">
    <div class="page-header">
      <h2>个人中心</h2>
      <p>管理你的个人信息和密码</p>
    </div>

    <!-- 基本信息 -->
    <el-card shadow="never" class="info-card">
      <template #header>
        <div class="card-title">
          <el-icon><User /></el-icon>
          <span>基本信息</span>
        </div>
      </template>
      <div v-if="profile" class="info-grid">
        <div class="info-item">
          <span class="info-label">用户名</span>
          <span class="info-value">{{ profile.username }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">角色</span>
          <el-tag size="small" :type="profile.role === 'super_admin' ? 'danger' : profile.role === 'admin' ? 'warning' : 'success'">{{ roleLabel(profile.role) }}</el-tag>
        </div>
        <div class="info-item">
          <span class="info-label">状态</span>
          <el-tag size="small" :type="profile.is_active ? 'success' : 'danger'" effect="plain">{{ profile.is_active ? '活跃' : '停用' }}</el-tag>
        </div>
        <div class="info-item">
          <span class="info-label">注册时间</span>
          <span class="info-value">{{ fmtDate(profile.created_at) }}</span>
        </div>
      </div>
    </el-card>

    <!-- 修改邮箱 -->
    <el-card shadow="never" class="info-card">
      <template #header>
        <div class="card-title">
          <el-icon><Message /></el-icon>
          <span>修改邮箱</span>
        </div>
      </template>
      <el-form label-position="top" class="profile-form">
        <el-form-item label="邮箱地址">
          <el-input v-model="editEmail" placeholder="your@email.com" />
        </el-form-item>
        <el-button type="primary" :loading="savingEmail" @click="onSaveEmail">保存</el-button>
      </el-form>
    </el-card>

    <!-- 修改密码 -->
    <el-card shadow="never" class="info-card">
      <template #header>
        <div class="card-title">
          <el-icon><Lock /></el-icon>
          <span>修改密码</span>
        </div>
      </template>
      <el-form label-position="top" :model="passwordForm" class="profile-form">
        <el-form-item label="当前密码">
          <el-input v-model="passwordForm.old_password" type="password" show-password placeholder="输入当前密码" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="passwordForm.new_password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="passwordForm.confirm_password" type="password" show-password placeholder="再次输入新密码" @keyup.enter="onChangePassword" />
        </el-form-item>
        <el-button type="primary" :loading="savingPassword" @click="onChangePassword">修改密码</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

.profile-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  max-width: 720px;
}
.page-header { margin-bottom: 24px; }
.info-card {
  border-radius: 12px;
  margin-bottom: 20px;
  border: 1px solid var(--border-soft);
}
.info-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--bg-soft);
}
.card-title { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; color: var(--text-primary); }
.card-title :deep(.el-icon) { font-size: 18px; color: var(--brand-primary); }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 32px; }
.info-item { display: flex; flex-direction: column; gap: 4px; }
.info-label { font-size: 12px; color: var(--text-secondary); }
.info-value { font-size: 14px; font-weight: 500; color: var(--text-primary); }
.profile-form { max-width: 400px; }
</style>
