/**
 * Pinia 用户状态管理（含 localStorage 持久化）
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface UserInfo {
  id: number
  username: string
  role: string
}

const STORAGE_KEY = 'image-annotation-user'

export const useUserStore = defineStore('user', () => {
  const initial = (() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      return raw ? JSON.parse(raw) : { token: null, user: null }
    } catch {
      return { token: null, user: null }
    }
  })()

  const token = ref<string | null>(initial.token)
  const user = ref<UserInfo | null>(initial.user)

  const persist = () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token: token.value, user: user.value })
    )
  }

  const setAuth = (t: string, u: UserInfo) => {
    token.value = t
    user.value = u
    persist()
  }

  const clear = () => {
    token.value = null
    user.value = null
    localStorage.removeItem(STORAGE_KEY)
  }

  return { token, user, setAuth, clear }
})
