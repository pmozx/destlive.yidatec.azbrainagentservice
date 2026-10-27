import { defineStore } from 'pinia'

import { StorageKey } from '@/enums'

/** 布局与设备状态 */
export const useAppStore = defineStore('app', {
  state: () => ({
    collapsed: localStorage.getItem(StorageKey.SIDEBAR_COLLAPSED) === '1',
    // 用于强制刷新当前页面
    refreshKey: 0,
  }),
  actions: {
    toggleCollapsed() {
      this.collapsed = !this.collapsed
      localStorage.setItem(StorageKey.SIDEBAR_COLLAPSED, this.collapsed ? '1' : '0')
    },
    setCollapsed(collapsed) {
      this.collapsed = collapsed
      localStorage.setItem(StorageKey.SIDEBAR_COLLAPSED, collapsed ? '1' : '0')
    },
    refreshPage() {
      this.refreshKey += 1
    },
  },
})
