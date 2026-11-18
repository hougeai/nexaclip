import api from '@/api'
import { getToken, setToken, setRefreshToken, clearTokens } from '@/utils'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null, // { user_id, user_name, email, ... }
    // 响应式登录标志：isLoggedIn 不能直接读 localStorage（非响应式），故用此标志驱动 UI
    authed: !!getToken()
  }),
  getters: {
    isLoggedIn: state => state.authed,
    displayName: state => state.user?.user_name || '游客'
  },
  actions: {
    async login(payload) {
      const body = await api.login(payload)
      setToken(body.data.access_token)
      setRefreshToken(body.data.refresh_token)
      this.authed = true
      await this.fetchUser()
    },
    async register(payload) {
      await api.register(payload)
    },
    async sendCode(email) {
      await api.sendVerifyCode({ email })
    },
    async fetchUser() {
      try {
        const body = await api.userinfo()
        this.user = body.data
        this.authed = true
      } catch {
        this.user = null
        this.authed = false
      }
    },
    // 有 token 但没拉过用户信息时补一次（刷新页面后恢复身份，并校准 authed）
    async ensureUser() {
      if (getToken() && !this.user) await this.fetchUser()
    },
    logout() {
      clearTokens()
      this.user = null
      this.authed = false
    }
  },
  persist: {
    pick: ['user'] // 只持久化 user 展示信息；token 单独存 localStorage，authed 启动时由 getToken 校准
  }
})
