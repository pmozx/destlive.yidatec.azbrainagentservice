import { defineStore } from 'pinia'

import { getCachedUser, getToken, removeCachedUser, removeToken, setCachedUser, setToken } from '@/utils/auth'

/**
 * 用户状态
 * 当前后端尚未提供登录接口，这里先保存本地会话信息，接入鉴权后替换为真实登录流程
 */
export const useUserStore = defineStore('user', {
  state: () => ({
    token: getToken(),
    profile: getCachedUser() || { name: '管理员', username: 'admin', avatar: '' },
  }),
  getters: {
    isLogin: (state) => Boolean(state.token),
    displayName: (state) => state.profile?.name || state.profile?.username || '管理员',
    avatarText: (state) => (state.profile?.name || state.profile?.username || 'A').slice(0, 1).toUpperCase(),
  },
  actions: {
    setSession({ token, profile }) {
      if (token) {
        this.token = token
        setToken(token)
      }
      if (profile) {
        this.profile = profile
        setCachedUser(profile)
      }
    },
    logout() {
      this.token = ''
      this.profile = { name: '管理员', username: 'admin', avatar: '' }
      removeToken()
      removeCachedUser()
    },
  },
})
