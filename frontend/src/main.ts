import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/dist/locale/zh-cn.min.mjs'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'dayjs/locale/zh-cn'

import App from './App.vue'
import router from './router'
import './style.css'
import './styles/theme.css'  // 全局设计系统 (圆角/阴影/按钮渐变/卡片悬浮等)
import './styles/select.css' // 全局下拉框统一样式 (基准: Training .filter-dataset 200px)

const app = createApp(App)

// Pinia 持久化插件（简化版）
const pinia = createPinia()
pinia.use(({ store }) => {
  const key = `pinia-${store.$id}`
  const saved = localStorage.getItem(key)
  if (saved) store.$patch(JSON.parse(saved))
  store.$subscribe((_mutation, state) => {
    localStorage.setItem(key, JSON.stringify(state))
  }, { detached: true })
})

app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// 注册所有 Element Plus 图标
for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, comp as any)
}

app.mount('#app')
