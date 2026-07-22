import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// https://vitejs.dev/config/
// 通过回调形式接收 mode 上下文，加载对应的 .env / .env.[mode] / .env.local / .env.[mode].local
export default defineConfig(({ mode }) => {
  // loadEnv 第三个参数传 '' 表示不限制前缀，这样无需强制 VITE_ 前缀也能读取
  // 仅在 vite.config.ts（Node 端）使用，不会泄漏到客户端
  const env = loadEnv(mode, process.cwd(), '')

  // 前端 dev server 端口
  const APP_PORT = Number(env.VITE_APP_PORT) || 5173

  // 后端服务地址（dev proxy 转发目标）
  const BACKEND_HOST = env.VITE_BACKEND_HOST || '127.0.0.1'
  const BACKEND_PORT = Number(env.VITE_BACKEND_PORT) || 5000

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      port: APP_PORT,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
          changeOrigin: true
        }
      }
    },
    preview: {
      // 生产构建预览（vite preview）也使用相同端口
      port: APP_PORT,
      host: '0.0.0.0'
    }
  }
})
