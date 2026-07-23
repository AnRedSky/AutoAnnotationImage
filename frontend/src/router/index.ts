import { createRouter, createWebHistory } from 'vue-router'
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
        { path: 'models', name: 'Models', component: () => import('@/views/Models/index.vue'), meta: { title: '模型管理' } }
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
  } else {
    next()
  }
})

export default router
