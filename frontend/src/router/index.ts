import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login.vue')
    },
    {
      path: '/',
      component: () => import('@/views/Layout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '系统总览' } },
        { path: 'datasets', name: 'Datasets', component: () => import('@/views/Datasets.vue'), meta: { title: '数据集管理' } },
        { path: 'datasets/:id', name: 'DatasetDetail', component: () => import('@/views/DatasetDetail.vue'), meta: { title: '数据集详情', subtitle: '数据集详情' }, props: true },
        { path: 'annotate', name: 'Annotate', component: () => import('@/views/Annotate.vue'), meta: { title: '人工标注' } },
        { path: 'annotate/:datasetId', name: 'AnnotateWithDs', component: () => import('@/views/Annotate.vue'), meta: { title: '人工标注' } },
        { path: 'training', name: 'Training', component: () => import('@/views/Training.vue'), meta: { title: '模型训练' } },
        { path: 'models', name: 'Models', component: () => import('@/views/Models.vue'), meta: { title: '模型管理' } }
      ]
    },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

router.beforeEach((to, _from, next) => {
  const store = useUserStore()
  if (to.path === '/login') {
    next()
  } else if (!store.token && !localStorage.getItem('token')) {
    next('/login')
  } else {
    next()
  }
})

export default router
