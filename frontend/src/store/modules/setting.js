import { defineStore } from 'pinia'

import { StorageKey, ThemeMode } from '@/enums'
import { applyPrimaryColor } from '@/utils/color'

const DEFAULT_PRIMARY_COLOR = '#8b1e56'

/** 外观设置：主题模式、主题色、页签与面包屑开关 */
export const useSettingStore = defineStore('setting', {
  state: () => ({
    mode: localStorage.getItem(StorageKey.THEME_MODE) || ThemeMode.LIGHT,
    primaryColor: localStorage.getItem(StorageKey.PRIMARY_COLOR) || DEFAULT_PRIMARY_COLOR,
    showTabs: false,
    showBreadcrumb: false,
    uniqueOpened: false,
    // 界面设置抽屉的显隐
    settingVisible: false,
  }),
  getters: {
    isDark: (state) => state.mode === ThemeMode.DARK,
  },
  actions: {
    /** 应用启动时恢复外观设置 */
    initTheme() {
      this.applyMode()
      applyPrimaryColor(this.primaryColor)
    },
    applyMode() {
      document.documentElement.classList.toggle('dark', this.isDark)
    },
    setMode(mode) {
      this.mode = mode
      localStorage.setItem(StorageKey.THEME_MODE, mode)
      this.applyMode()
    },
    toggleMode() {
      this.setMode(this.isDark ? ThemeMode.LIGHT : ThemeMode.DARK)
    },
    setPrimaryColor(color) {
      this.primaryColor = color
      localStorage.setItem(StorageKey.PRIMARY_COLOR, color)
      applyPrimaryColor(color)
    },
    reset() {
      this.setMode(ThemeMode.LIGHT)
      this.setPrimaryColor(DEFAULT_PRIMARY_COLOR)
      this.showTabs = true
      this.showBreadcrumb = true
      this.uniqueOpened = false
      this.settingVisible = false
    },
  },
})
