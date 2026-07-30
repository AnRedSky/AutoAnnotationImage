<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Odometer, Folder, EditPen, Grid, Promotion, User, SwitchButton,
  Expand, Fold, ArrowDown
} from '@element-plus/icons-vue'
import { authApi } from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const collapsed = ref(false)

const menus = [
  { path: '/dashboard', icon: Odometer, title: '仪表盘', subtitle: 'Overview' },
  { path: '/datasets', icon: Folder, title: '数据集', subtitle: 'Datasets' },
  { path: '/annotate', icon: EditPen, title: '标注工作台', subtitle: 'Annotate' },
  { path: '/models', icon: Grid, title: '模型版本', subtitle: 'Models' },
  { path: '/training', icon: Promotion, title: '训练任务', subtitle: 'Training' }
]

// v3.2.0 MT-10: 管理员菜单组 (仅 admin 可见)
const adminMenus = [
  { path: '/admin/users', icon: User, title: '用户管理', subtitle: 'Users' },
  { path: '/admin/teams', icon: Folder, title: '团队管理', subtitle: 'Teams' },
  { path: '/admin/roles', icon: EditPen, title: '角色权限', subtitle: 'Roles' },
  { path: '/admin/audit', icon: Odometer, title: '审计日志', subtitle: 'Audit' }
]

const isAdmin = computed(() => userStore.user?.role?.includes('admin'))

/**
 * 选中的菜单: 之前用 route.path 精确匹配, 导致 /datasets/29 落不到 /datasets
 * 改用前缀匹配, 让详情页也能高亮所属的一级菜单
 */
const activePath = computed(() => route.path)
const onSelect = (path: string) => router.push(path)

const onLogout = async () => {
  try {
    await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
  } catch { return }
  try { await authApi.logout() } catch {}
  // token 统一由 store 管理（clear 同步移除 localStorage['token']）
  userStore.clear()
  ElMessage.success('已退出登录')
  router.push('/login')
}

/** 命中的菜单: 优先 longest prefix 匹配, 都没有则回退 dashboard */
const matchedMenu = computed(() => {
  const path = activePath.value
  const allMenus = isAdmin.value ? [...menus, ...adminMenus] : menus
  const candidates = allMenus.filter((m) => path === m.path || path.startsWith(m.path + '/'))
  if (candidates.length === 0) return menus[0]
  return candidates.sort((a, b) => b.path.length - a.path.length)[0]
})

/**
 * 顶部面包屑: [{ title, icon?, to? }, ...]
 * 规则: 始终以命中的菜单为根; 详情/标注页追加二级面包屑 (来自 route.meta.subtitle)
 */
const breadcrumbs = computed(() => {
  const root = matchedMenu.value
  const items: { title: string; icon?: any; to?: string }[] = [
    { title: root.title, icon: root.icon, to: root.path }
  ]
  // 二级: route.meta.subtitle (各页面可自定义)
  if (route.meta?.subtitle) {
    items.push({ title: String(route.meta.subtitle) })
  }
  return items
})
</script>

<template>
  <el-container class="app-shell">
    <el-aside
      :width="collapsed ? '72px' : '232px'"
      class="app-aside"
    >
      <!-- Logo 区 -->
      <div class="app-logo">
        <div class="logo-mark">
          <span>AI</span>
        </div>
        <transition name="fade-text">
          <div v-show="!collapsed" class="logo-text">
            <div class="title">图像标注系统</div>
            <div class="subtitle">Annotation v1.0</div>
          </div>
        </transition>
      </div>

      <!-- 菜单 -->
      <el-menu
        :default-active="matchedMenu.path"
        :collapse="collapsed"
        :collapse-transition="false"
        @select="onSelect"
        class="app-menu"
      >
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon class="menu-icon">
            <component :is="m.icon" />
          </el-icon>
          <template #title>
            <div class="menu-title">
              <span class="zh">{{ m.title }}</span>
              <span class="en">{{ m.subtitle }}</span>
            </div>
          </template>
        </el-menu-item>

        <!-- v3.2.0 MT-10: 管理员菜单组 -->
        <template v-if="isAdmin && !collapsed">
          <div class="menu-divider">管理</div>
        </template>
        <el-menu-item v-for="m in (isAdmin ? adminMenus : [])" :key="m.path" :index="m.path">
          <el-icon class="menu-icon">
            <component :is="m.icon" />
          </el-icon>
          <template #title>
            <div class="menu-title">
              <span class="zh">{{ m.title }}</span>
              <span class="en">{{ m.subtitle }}</span>
            </div>
          </template>
        </el-menu-item>
      </el-menu>

      <!-- 底部状态条 -->
      <transition name="fade-text">
        <div v-show="!collapsed" class="aside-footer">
          <span class="status-dot status-dot--success status-dot--pulse" />
          <span>系统运行中</span>
        </div>
      </transition>
    </el-aside>

    <el-container class="app-body">
      <el-header class="app-header">
        <div class="header-left">
          <el-button
            text
            class="collapse-btn"
            :icon="collapsed ? Expand : Fold"
            @click="collapsed = !collapsed"
          />
          <div class="breadcrumb">
            <template v-for="(bc, i) in breadcrumbs" :key="i">
              <el-icon v-if="bc.icon" class="bc-icon"><component :is="bc.icon" /></el-icon>
              <span
                class="bc-title"
                :class="{ 'bc-title--last': i === breadcrumbs.length - 1, 'bc-title--link': !!bc.to && i < breadcrumbs.length - 1 }"
                @click="bc.to && router.push(bc.to)"
              >{{ bc.title }}</span>
              <span v-if="i < breadcrumbs.length - 1" class="bc-divider">/</span>
            </template>
          </div>
        </div>
        <div class="header-right">
          <el-dropdown trigger="click">
            <div class="user-chip">
              <el-avatar :size="34" class="user-avatar">
                {{ (userStore.user?.username || 'U').charAt(0).toUpperCase() }}
              </el-avatar>
              <div class="user-info">
                <div class="username">{{ userStore.user?.username || '用户' }}</div>
                <div class="role">
                  {{ userStore.user?.role || 'annotator' }}
                </div>
              </div>
              <el-icon class="caret"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item :icon="SwitchButton" @click="onLogout">
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
  background: var(--bg-page);
}

/* ============ 侧边栏 ============ */
.app-aside {
  background: var(--gradient-night);
  transition: width 0.25s var(--ease-out);
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}
.app-aside::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 20% 10%, rgba(118, 75, 162, 0.18) 0%, transparent 50%),
    radial-gradient(circle at 80% 80%, rgba(79, 124, 255, 0.12) 0%, transparent 50%);
  pointer-events: none;
}

/* Logo */
.app-logo {
  height: 70px;
  padding: 0 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  position: relative;
  z-index: 1;
}
.logo-mark {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--gradient-aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  letter-spacing: -0.5px;
  box-shadow: 0 4px 14px rgba(102, 126, 234, 0.4);
  flex-shrink: 0;
}
.logo-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
}
.logo-text .title {
  color: #fff;
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
}
.logo-text .subtitle {
  color: rgba(255, 255, 255, 0.4);
  font-size: 11px;
  white-space: nowrap;
}

/* 菜单 */
.app-menu {
  background: transparent !important;
  border-right: none !important;
  flex: 1;
  padding: 12px 8px;
  position: relative;
  z-index: 1;
}
.app-menu :deep(.el-menu-item) {
  background: transparent !important;
  color: rgba(255, 255, 255, 0.7) !important;
  height: 48px;
  line-height: 48px;
  margin: 4px 0;
  border-radius: 10px;
  transition: all 0.2s var(--ease-out);
  position: relative;
  overflow: hidden;
}
.app-menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.06) !important;
  color: #fff !important;
}
.app-menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.25) 0%, rgba(110, 81, 233, 0.25) 100%) !important;
  color: #fff !important;
  font-weight: 500;
}
.app-menu :deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 3px;
  background: var(--gradient-warm);
  border-radius: 0 3px 3px 0;
}
.app-menu :deep(.el-menu-item .el-icon) {
  font-size: 18px !important;
  margin-right: 10px;
}
.menu-title {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
  gap: 2px;
}
.menu-title .zh {
  font-size: 14px;
}
.menu-title .en {
  font-size: 10px;
  opacity: 0.5;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* 底部状态条 */
.aside-footer {
  margin: 12px 18px 16px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
  position: relative;
  z-index: 1;
}

/* ============ 头部 ============ */
.app-body { flex: 1; min-width: 0; height: 100%; }
.app-header {
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: saturate(180%) blur(10px);
  -webkit-backdrop-filter: saturate(180%) blur(10px);
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
  z-index: 5;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.collapse-btn {
  font-size: 18px !important;
  color: var(--text-secondary);
  padding: 8px;
  border-radius: 8px;
}
.collapse-btn:hover { background: var(--bg-hover); color: var(--brand-primary); }
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 14px;
}
.bc-icon { color: var(--brand-primary); font-size: 16px; }
.bc-title { font-weight: 600; color: var(--text-primary); }
.bc-title--last { color: var(--text-primary); font-weight: 700; }
.bc-title--link {
  cursor: pointer;
  color: var(--text-secondary);
  font-weight: 500;
  transition: color 0.18s var(--ease-out);
}
.bc-title--link:hover { color: var(--brand-primary); }
.bc-divider { color: var(--text-placeholder); margin: 0 6px; }

.header-right { display: flex; align-items: center; gap: 8px; }
.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 10px 4px 4px;
  border-radius: 24px;
  cursor: pointer;
  transition: all 0.2s var(--ease-out);
}
.user-chip:hover {
  background: var(--bg-hover);
}
.user-avatar {
  background: var(--gradient-brand) !important;
  color: #fff !important;
  font-weight: 600;
  font-size: 14px;
}
.user-info {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}
.user-info .username {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}
.user-info .role {
  font-size: 10px;
  color: var(--text-placeholder);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.tenant-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--brand-primary);
  color: #fff;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0;
}

.menu-divider {
  padding: 12px 20px 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-placeholder);
  text-transform: uppercase;
  letter-spacing: 1px;
}

.caret { color: var(--text-placeholder); font-size: 12px; }

/* ============ 主区 ============ */
.app-main {
  padding: 24px;
  flex: 1;
  min-height: 0;
  overflow: auto;
}

/* 转场 */
.fade-text-enter-active, .fade-text-leave-active {
  transition: opacity 0.18s var(--ease-out);
}
.fade-text-enter-from, .fade-text-leave-to { opacity: 0; }
.page-enter-active, .page-leave-active {
  transition: opacity 0.25s var(--ease-out), transform 0.25s var(--ease-out);
}
.page-enter-from { opacity: 0; transform: translateY(8px); }
.page-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
