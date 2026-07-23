<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, Message, Promotion, Picture, Lightning } from '@element-plus/icons-vue'
import { authApi } from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const activeTab = ref('login')
const loading = ref(false)
const regLoading = ref(false)

const loginForm = reactive({ username: '', password: '' })
const regForm = reactive({ username: '', password: '', email: '' })

const onLogin = async () => {
  if (!loginForm.username || !loginForm.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res: any = await authApi.login(loginForm.username, loginForm.password)
    const tk = res.access_token
    if (!tk) throw new Error('未获取到 token')
    // token 统一由 store 管理（setAuth 同步写入 localStorage['token']）
    try {
      const me: any = await authApi.me()
      userStore.setAuth(tk, { id: me.id, username: me.username, role: me.role })
    } catch {
      userStore.setAuth(tk, {
        id: res.user_id,
        username: loginForm.username,
        role: 'annotator'
      })
    }
    ElMessage.success('登录成功')
    // 登录后优先回跳原页面（由路由守卫在 query.redirect 中携带）
    const redirect = (router.currentRoute.value.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e: any) {
    ElMessage.error('登录失败: ' + (e?.response?.data?.detail || e?.message || '用户名或密码错误'))
  } finally {
    loading.value = false
  }
}

const onRegister = async () => {
  if (!regForm.username || regForm.password.length < 6) {
    ElMessage.warning('用户名必填，密码至少 6 位')
    return
  }
  regLoading.value = true
  try {
    await authApi.register({
      username: regForm.username,
      password: regForm.password,
      email: regForm.email
    })
    ElMessage.success('注册成功，请登录')
    activeTab.value = 'login'
  } catch (e: any) {
    ElMessage.error('注册失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    regLoading.value = false
  }
}

// 特性卡片
const features = [
  { icon: Picture, title: '智能预标注', desc: '基于深度学习模型自动识别图片内容' },
  { icon: Lightning, title: '快速训练', desc: '微调主流视觉模型, 几分钟即可产出可用模型' },
  { icon: Promotion, title: '完整流程', desc: '数据上传、标注、训练、导出全流程覆盖' }
]
</script>

<template>
  <div class="login-page">
    <!-- 左侧品牌区 -->
    <div class="login-brand">
      <div class="brand-bg" />
      <div class="brand-content">
        <div class="brand-logo">
          <div class="logo-mark">AI</div>
          <div class="logo-text">
            <div class="zh">图像自动标注系统</div>
            <div class="en">Intelligent Image Annotation Platform</div>
          </div>
        </div>
        <div class="brand-headline">
          <h1>让图像标注<br />更高效、更智能</h1>
          <p>基于深度学习的图像分类与目标检测平台,<br />从数据上传到模型训练, 一站式完成。</p>
        </div>
        <div class="brand-features">
          <div v-for="f in features" :key="f.title" class="feature-item">
            <div class="feature-icon">
              <el-icon><component :is="f.icon" /></el-icon>
            </div>
            <div>
              <div class="feature-title">{{ f.title }}</div>
              <div class="feature-desc">{{ f.desc }}</div>
            </div>
          </div>
        </div>
        <div class="brand-footer">
          © 2026 毕业论文项目 · 基于 PyTorch + FastAPI + Vue
        </div>
      </div>
    </div>

    <!-- 右侧表单区 -->
    <div class="login-form-wrap">
      <div class="login-form-inner">
        <div class="form-title">
          <h2>欢迎回来</h2>
          <p>请登录以继续使用系统</p>
        </div>

        <el-tabs v-model="activeTab" class="login-tabs">
          <el-tab-pane label="登 录" name="login">
            <el-form @submit.prevent="onLogin" label-position="top" class="login-form">
              <el-form-item>
                <el-input
                  v-model="loginForm.username" placeholder="请输入用户名"
                  :prefix-icon="User" size="large"
                  class="form-input"
                />
              </el-form-item>
              <el-form-item>
                <el-input
                  v-model="loginForm.password" type="password" placeholder="请输入密码"
                  :prefix-icon="Lock" size="large" show-password
                  class="form-input"
                  @keyup.enter="onLogin"
                />
              </el-form-item>
              <el-button
                type="primary" size="large" :loading="loading"
                @click="onLogin" class="submit-btn"
              >
                登 录
              </el-button>
              <div class="form-tip">
                演示账号: <strong>admin</strong> / <strong>admin123</strong>
              </div>
            </el-form>
          </el-tab-pane>
          <el-tab-pane label="注 册" name="register">
            <el-form @submit.prevent="onRegister" label-position="top" class="login-form">
              <el-form-item>
                <el-input
                  v-model="regForm.username" placeholder="请输入用户名"
                  :prefix-icon="User" size="large" class="form-input"
                />
              </el-form-item>
              <el-form-item>
                <el-input
                  v-model="regForm.email" placeholder="邮箱（可选）"
                  :prefix-icon="Message" size="large" class="form-input"
                />
              </el-form-item>
              <el-form-item>
                <el-input
                  v-model="regForm.password" type="password" placeholder="密码（至少 6 位）"
                  :prefix-icon="Lock" size="large" show-password
                  class="form-input"
                  @keyup.enter="onRegister"
                />
              </el-form-item>
              <el-button
                type="primary" size="large" :loading="regLoading"
                @click="onRegister" class="submit-btn"
              >
                注 册
              </el-button>
            </el-form>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============ 页面布局 ============ */
.login-page {
  height: 100vh;
  display: flex;
  background: var(--bg-page);
  overflow: hidden;
}

/* ============ 左侧品牌区 ============ */
.login-brand {
  flex: 1.2;
  position: relative;
  overflow: hidden;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 60px 56px;
}
.brand-bg {
  position: absolute;
  inset: 0;
  background: var(--gradient-night);
  z-index: 0;
}
.brand-bg::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 15% 20%, rgba(118, 75, 162, 0.5) 0%, transparent 45%),
    radial-gradient(circle at 80% 80%, rgba(79, 124, 255, 0.4) 0%, transparent 50%),
    radial-gradient(circle at 50% 50%, rgba(255, 138, 76, 0.15) 0%, transparent 60%);
}
.brand-bg::after {
  /* 网格背景 */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse at center, #000 30%, transparent 70%);
  -webkit-mask-image: radial-gradient(ellipse at center, #000 30%, transparent 70%);
}
.brand-content {
  position: relative;
  z-index: 1;
  max-width: 460px;
  width: 100%;
}
.brand-logo {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 48px;
}
.logo-mark {
  width: 50px;
  height: 50px;
  border-radius: 14px;
  background: var(--gradient-aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  font-size: 18px;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  letter-spacing: -0.5px;
}
.logo-text .zh {
  font-size: 18px;
  font-weight: 600;
  color: #fff;
}
.logo-text .en {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  margin-top: 2px;
  letter-spacing: 0.5px;
}
.brand-headline h1 {
  font-size: 40px;
  font-weight: 700;
  line-height: 1.2;
  margin: 0 0 16px;
  background: linear-gradient(135deg, #fff 0%, rgba(255, 255, 255, 0.7) 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -1px;
}
.brand-headline p {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.65);
  line-height: 1.7;
  margin: 0 0 40px;
}
.brand-features {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.feature-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  transition: all 0.3s var(--ease-out);
}
.feature-item:hover {
  background: rgba(255, 255, 255, 0.1);
  transform: translateX(4px);
}
.feature-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--gradient-aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.feature-icon :deep(.el-icon) {
  font-size: 20px;
  color: #fff;
}
.feature-title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}
.feature-desc {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
  margin-top: 2px;
}
.brand-footer {
  position: absolute;
  bottom: 32px;
  left: 56px;
  right: 56px;
  text-align: center;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
}

/* ============ 右侧表单区 ============ */
.login-form-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  background: var(--bg-page);
  position: relative;
}
.login-form-wrap::before {
  /* 极淡的网格背景 */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(79, 124, 255, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(79, 124, 255, 0.03) 1px, transparent 1px);
  background-size: 32px 32px;
  pointer-events: none;
}
.login-form-inner {
  width: 100%;
  max-width: 400px;
  position: relative;
  z-index: 1;
}
.form-title h2 {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
  letter-spacing: -0.5px;
}
.form-title p {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0 0 32px;
}

.login-tabs {
  --el-tabs-header-height: 48px;
}
.login-tabs :deep(.el-tabs__nav-wrap::after) {
  background: var(--border-soft) !important;
}
.login-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 0 20px;
  height: 48px;
  line-height: 48px;
}
.login-tabs :deep(.el-tabs__item.is-active) {
  color: var(--brand-primary);
  font-weight: 600;
}
.login-tabs :deep(.el-tabs__active-bar) {
  background: var(--gradient-brand) !important;
  height: 3px;
  border-radius: 3px;
}

.login-form { padding: 8px 0 0; }
.form-input :deep(.el-input__wrapper) {
  padding: 4px 12px;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 0 0 1px var(--border-strong) inset;
  transition: all 0.2s var(--ease-out);
}
.form-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px var(--brand-primary) inset;
}
.form-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 2px var(--brand-primary) inset, var(--shadow-glow) !important;
}
.form-input :deep(.el-input__inner) {
  height: 42px;
  font-size: 14px;
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  font-weight: 500;
  margin-top: 8px;
  border-radius: 10px;
}
.form-tip {
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
  margin-top: 16px;
}
.form-tip strong {
  color: var(--brand-primary);
  font-weight: 500;
}

/* ============ 响应式 ============ */
@media (max-width: 900px) {
  .login-brand { display: none; }
  .login-form-wrap { flex: 1; }
}
</style>
