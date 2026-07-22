import axios, { AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

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

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error?.response?.status
    if (status === 401) {
      ElMessage.error('登录已失效，请重新登录')
      localStorage.removeItem('token')
      window.location.href = '/login'
    } else if (status === 403) {
      ElMessage.error('无权限访问')
    } else if (status === 404) {
      // 让业务代码处理 404 即可, 全局不刷
    } else if (status >= 500) {
      const detail = error?.response?.data?.detail || error?.message || '服务器内部错误'
      ElMessage.error('服务器错误: ' + detail)
    } else {
      // 4xx 业务错误, 抛出由调用方 catch + 显示
    }
    return Promise.reject(error)
  }
)

export default http
