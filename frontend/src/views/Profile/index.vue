<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { userApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { User, Lock, Message, CircleCheck, CircleClose } from '@element-plus/icons-vue'

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

const editEmail = ref('')
const savingEmail = ref(false)

const passwordForm = ref({ old_password: '', new_password: '', confirm_password: '' })
const savingPassword = ref(false)

const roleMap: Record<string, { label: string; tag: string }> = {
  super_admin: { label: '超级管理员', tag: 'danger' },
  admin: { label: '管理员', tag: 'warning' },
  annotator: { label: '标注员', tag: 'success' },
  viewer: { label: '观察者', tag: 'info' },
}
const roleLabel = (r: string) => roleMap[r]?.label || r
const roleTag = (r: string) => roleMap[r]?.tag || 'info'

// 密码强度评估
const passwordStrength = computed(() => {
  const p = passwordForm.value.new_password
  if (!p) return { level: 0, label: '', color: '' }
  let score = 0
  if (p.length >= 6) score++
  if (p.length >= 12) score++
  if (/[A-Z]/.test(p)) score++
  if (/[0-9]/.test(p)) score++
  if (/[^A-Za-z0-9]/.test(p)) score++
  if (score <= 1) return { level: 1, label: '弱', color: '#ff4d4f' }
  if (score <= 3) return { level: 2, label: '中', color: '#ffa940' }
  return { level: 3, label: '强', color: '#00c48c' }
})

const passwordsMatch = computed(() => {
  const { new_password, confirm_password } = passwordForm.value
  if (!confirm_password) return null
  return new_password === confirm_password
})

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
    ElMessage.warning('请填写完整'); return
  }
  if (passwordForm.value.new_password !== passwordForm.value.confirm_password) {
    ElMessage.warning('两次输入的新密码不一致'); return
  }
  if (passwordForm.value.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位'); return
  }
  savingPassword.value = true
  try {
    await userApi.changePassword({
      old_password: passwordForm.value.old_password,
      new_password: passwordForm.value.new_password,
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
    <!-- 页头 -->
    <div class="page-header">
      <h2>个人中心 <span class="subtitle">Profile</span></h2>
      <p>管理你的个人信息、邮箱和密码</p>
    </div>

    <!-- 主体: 双栏布局 -->
    <div class="profile-body">
      <!-- 左栏: 身份卡 -->
      <div class="profile-left">
        <div class="identity-card">
          <!-- 头像 -->
          <div class="avatar-wrap">
            <div class="avatar">
              {{ (profile?.username || 'U').charAt(0).toUpperCase() }}
            </div>
          </div>
          <!-- 用户名 + 角色 -->
          <div class="identity-info" v-if="profile">
            <h3 class="identity-name">{{ profile.username }}</h3>
            <div class="identity-badges">
              <el-tag :type="roleTag(profile.role)" size="small" effect="dark">
                {{ roleLabel(profile.role) }}
              </el-tag>
              <el-tag :type="profile.is_active ? 'success' : 'danger'" size="small" effect="plain">
                <el-icon style="margin-right: 2px; font-size: 12px;">
                  <CircleCheck v-if="profile.is_active" /><CircleClose v-else />
                </el-icon>
                {{ profile.is_active ? '活跃' : '停用' }}
              </el-tag>
            </div>
          </div>
          <!-- 分割线 -->
          <div class="identity-divider" />
          <!-- 信息列表 -->
          <div class="identity-list" v-if="profile">
            <div class="identity-row">
              <span class="identity-label">用户 ID</span>
              <span class="identity-value">{{ profile.id }}</span>
            </div>
            <div class="identity-row">
              <span class="identity-label">邮箱</span>
              <span class="identity-value">{{ profile.email || '未设置' }}</span>
            </div>
            <div class="identity-row">
              <span class="identity-label">注册时间</span>
              <span class="identity-value">{{ fmtDate(profile.created_at) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右栏: 操作区 -->
      <div class="profile-right">
        <!-- 修改邮箱 -->
        <el-card shadow="never" class="action-card">
          <template #header>
            <div class="card-title">
              <el-icon><Message /></el-icon>
              <span>修改邮箱</span>
            </div>
          </template>
          <el-form label-position="top" class="action-form">
            <el-form-item label="邮箱地址">
              <el-input v-model="editEmail" placeholder="your@email.com" clearable>
                <template #prefix><el-icon><Message /></el-icon></template>
              </el-input>
            </el-form-item>
            <div class="form-footer">
              <el-button type="primary" :loading="savingEmail" @click="onSaveEmail">保存</el-button>
            </div>
          </el-form>
        </el-card>

        <!-- 修改密码 -->
        <el-card shadow="never" class="action-card">
          <template #header>
            <div class="card-title">
              <el-icon><Lock /></el-icon>
              <span>修改密码</span>
            </div>
          </template>
          <el-form label-position="top" :model="passwordForm" class="action-form">
            <el-form-item label="当前密码">
              <el-input v-model="passwordForm.old_password" type="password" show-password placeholder="输入当前密码" />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input v-model="passwordForm.new_password" type="password" show-password placeholder="至少 6 位">
                <template #suffix v-if="passwordForm.new_password">
                  <span :style="{ color: passwordStrength.color, fontSize: '12px', fontWeight: 600 }">
                    {{ passwordStrength.label }}
                  </span>
                </template>
              </el-input>
              <!-- 密码强度条 -->
              <div class="strength-bar" v-if="passwordForm.new_password">
                <div class="strength-track">
                  <div class="strength-fill" :style="{ width: `${passwordStrength.level * 33.3}%`, background: passwordStrength.color }" />
                </div>
              </div>
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input
                v-model="passwordForm.confirm_password" type="password" show-password
                placeholder="再次输入新密码"
                @keyup.enter="onChangePassword"
              >
                <template #suffix v-if="passwordForm.confirm_password">
                  <el-icon style="font-size: 16px;">
                    <CircleCheck v-if="passwordsMatch" style="color: #00c48c" />
                    <CircleClose v-else style="color: #ff4d4f" />
                  </el-icon>
                </template>
              </el-input>
            </el-form-item>
            <div class="form-footer">
              <el-button type="primary" :loading="savingPassword" @click="onChangePassword">修改密码</el-button>
            </div>
          </el-form>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import '@/styles/admin.css';

/* ============ 页面布局 ============ */
.profile-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
}

/* ============ 双栏主体 ============ */
.profile-body {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 20px;
  align-items: start;
}

/* ============ 左栏: 身份卡 ============ */
.identity-card {
  background: #fff;
  border: 1px solid var(--border-soft);
  border-radius: 14px;
  padding: 28px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  position: sticky;
  top: 16px;
}

.avatar-wrap { margin-bottom: 16px; }
.avatar {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--gradient-brand);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36px;
  font-weight: 700;
  color: #fff;
  box-shadow: 0 8px 24px rgba(79, 124, 255, 0.25);
}

.identity-info { text-align: center; }
.identity-name {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}
.identity-badges {
  display: flex;
  gap: 6px;
  justify-content: center;
  flex-wrap: wrap;
}

.identity-divider {
  width: 100%;
  height: 1px;
  background: var(--border-soft);
  margin: 20px 0 16px;
}

.identity-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.identity-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
.identity-label { color: var(--text-secondary); }
.identity-value { color: var(--text-primary); font-weight: 500; }

/* ============ 右栏: 操作卡 ============ */
.action-card {
  border-radius: 12px;
  border: 1px solid var(--border-soft);
  margin-bottom: 20px;
}
.action-card :deep(.el-card__header) {
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--bg-soft);
}
.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.card-title :deep(.el-icon) { font-size: 18px; color: var(--brand-primary); }

.action-form { max-width: 420px; }

.form-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

/* ============ 密码强度条 ============ */
.strength-bar { margin-top: 6px; }
.strength-track {
  width: 100%;
  height: 4px;
  border-radius: 2px;
  background: var(--border-soft);
  overflow: hidden;
}
.strength-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease, background 0.3s ease;
}

/* ============ 响应式 ============ */
/* 平板: 单栏 */
@media (max-width: 1024px) {
  .profile-body {
    grid-template-columns: 1fr;
    max-width: 600px;
    margin: 0 auto;
  }
  .identity-card {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: 20px;
    padding: 20px;
  }
  .avatar-wrap { margin-bottom: 0; }
  .identity-info { text-align: left; flex: 1; min-width: 200px; }
  .identity-divider {
    width: 1px;
    height: auto;
    align-self: stretch;
    margin: 0;
  }
  .identity-list {
    flex: 1;
    min-width: 200px;
  }
}

/* 移动端: 紧凑 */
@media (max-width: 640px) {
  .profile-page { padding: 12px; }
  .identity-card {
    flex-direction: column;
    padding: 24px 16px;
  }
  .identity-divider {
    width: 100%;
    height: 1px;
    margin: 16px 0 12px;
  }
  .identity-info { text-align: center; }
  .action-form { max-width: 100%; }
  .form-footer { justify-content: center; }
}
</style>
