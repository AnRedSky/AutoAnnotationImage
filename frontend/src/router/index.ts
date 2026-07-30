import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login/index.vue')
    },
    {
      path: '/',
      component: () => import('@/views/Layout/index.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/Dashboard/index.vue'), meta: { title: '系统总览' } },
        { path: 'datasets', name: 'Datasets', component: () => import('@/views/Datasets/index.vue'), meta: { title: '数据集管理' } },
        { path: 'datasets/:id', name: 'DatasetDetail', component: () => import('@/views/DatasetDetail/index.vue'), meta: { title: '数据集详情', subtitle: '数据集详情' }, props: true },
        { path: 'annotate', name: 'Annotate', component: () => import('@/views/Annotate/index.vue'), meta: { title: '人工标注' } },
        { path: 'annotate/:datasetId', name: 'AnnotateWithDs', component: () => import('@/views/Annotate/index.vue'), meta: { title: '人工标注' } },
        { path: 'training', name: 'Training', component: () => import('@/views/Training/index.vue'), meta: { title: '模型训练' } },
        { path: 'models', name: 'Models', component: () => import('@/views/Models/index.vue'), meta: { title: '模型管理' } },
        // v3.3.0: 团队管理 (全部用户可见)
        { path: 'teams', name: 'Teams', component: () => import('@/views/Admin/Teams.vue'), meta: { title: '团队管理', subtitle: '团队管理' } },
        // v3.3.0: 个人中心 (全部用户可见)
        { path: 'profile', name: 'Profile', component: () => import('@/views/Profile/index.vue'), meta: { title: '个人中心', subtitle: '个人中心' } },
        // v3.3.0: 管理员功能页面 (仅 admin 可见)
        { path: 'admin/users', name: 'AdminUsers', component: () => import('@/views/Admin/Users.vue'), meta: { title: '用户管理', subtitle: '用户管理', requireAdmin: true } },
        { path: 'admin/roles', name: 'AdminRoles', component: () => import('@/views/Admin/Roles.vue'), meta: { title: '角色权限', subtitle: '角色权限', requireAdmin: true } },
        { path: 'admin/audit', name: 'AdminAudit', component: () => import('@/views/Admin/AuditLog.vue'), meta: { title: '审计日志', subtitle: '审计日志', requireAdmin: true } }
      ]
    },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

router.beforeEach((to, _from, next) => {
  const store = useUserStore()
  // 动态设置浏览器标题（来自路由 meta.title）
  if (to.meta.title) {
    document.title = `${to.meta.title} · 图像标注平台`
  }
  if (to.path === '/login') {
    next()
  } else if (!store.token) {
    // token 过期/未登录：携带 redirect，登录后回跳原页面
    next({ path: '/login', query: { redirect: to.fullPath } })
  } else if (to.meta.requireAdmin && !store.user?.role?.includes('admin')) {
    // v3.2.0 MT-10: 管理员页面权限守卫
    ElMessage.warning('需要管理员权限')
    next({ path: '/dashboard' })
  } else {
    next()
  }
})

export default router
