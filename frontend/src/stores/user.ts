/**
 * Pinia 用户状态管理
 * ==================================================
 * token 单一真值源: localStorage['token']
 *   - Login 写入、http 拦截器读取、SSE/authQuery 拼接 均以此 key 为准
 *   - store.token 仅作为响应式镜像, setAuth/clear 同步写 localStorage
 *   - 移除了旧版手写 persist()/STORAGE_KEY (与 main.ts 的 Pinia 持久化插件双写,
 *     且 key 不一致导致状态不可预测). 现在用户信息 (id/username/role) 由插件
 *     持久化到 pinia-user, token 走 localStorage['token'], 不再交叉.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface UserInfo {
  id: number
  username: string
  role: string
  tenant_id: number  // v3.2.0 MT-9: 多租户
}

/** token 在 localStorage 中的唯一 key */
export const TOKEN_KEY = 'token'

/**
 * 从 JWT payload 解析 tenant_id (v3.2.0 MT-9)
 * JWT 格式: header.payload.signature, payload 是 base64url JSON
 */
export function parseTenantFromToken(token: string): number {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return 1
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
    return payload.tenant_id || 1
  } catch {
    return 1
  }
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<UserInfo | null>(null)

  const setAuth = (t: string, u: UserInfo) => {
    token.value = t
    user.value = u
    localStorage.setItem(TOKEN_KEY, t)
  }

  const clear = () => {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  return { token, user, setAuth, clear }
})
