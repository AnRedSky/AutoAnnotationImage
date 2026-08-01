import axios, { AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

// API 基础路径：优先使用 .env.* 中配置的 VITE_API_BASE_URL，未配置时回退到 '/api'（依赖 Vite proxy 转发）
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || '/api'

const http: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  // 默认 30s；上传/训练/AI 推理这类长操作可单独传 timeout 覆盖
  timeout: 30000
})

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('token')
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 并发 401 去重：多个请求同时失败时只弹一次提示 + 只跳一次登录页
let isRedirectingToLogin = false

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error?.response?.status
    // 业务自定义标记: 该请求即便 401 也不触发"清 auth + 跳登录页"副作用
    // 用于登录流程中探测 /me 等必须等新 token 写入 store 之后才能成功的请求
    const skip401Redirect = (error?.config as any)?.__skip401Redirect === true
    if (status === 401 && !skip401Redirect) {
      // 登录已失效：清理鉴权态并跳登录页（保留 SPA 状态，避免全页刷新丢失未保存标注）
      if (!isRedirectingToLogin) {
        isRedirectingToLogin = true
        ElMessage.error('登录已失效，请重新登录')
        try { useUserStore().clear() } catch { localStorage.removeItem('token') }
        // 懒加载 router 规避 http ↔ router ↔ api 循环引用
        import('@/router').then((r) => {
          r.default.push({ path: '/login', query: { redirect: window.location.pathname + window.location.search } })
        }).finally(() => { isRedirectingToLogin = false })
      }
    } else if (status === 401 && skip401Redirect) {
      // 登录流探测请求的 401: 仅记录, 不清 auth 也不跳页 (由调用方 catch 处理)
      // 当前由 console.warn 暴露, 生产可对接前端监控
      // eslint-disable-next-line no-console
      console.warn('[http] 401 on skip401Redirect request', error?.config?.url)
    } else if (status === 403) {
      ElMessage.error('无权限访问')
    } else if (status >= 500) {
      const detail = error?.response?.data?.detail || error?.message || '服务器内部错误'
      ElMessage.error('服务器错误: ' + detail)
    }
    // 404 / 4xx 业务错误交给调用方 catch 处理，全局不刷消息
    return Promise.reject(error)
  }
)

export default http
