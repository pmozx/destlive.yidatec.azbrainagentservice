import { defineStore } from 'pinia'

/** 顶部页签：记录访问过的页面，支持关闭与批量关闭 */
export const useTabsStore = defineStore('tabs', {
  state: () => ({
    /** @type {Array<{ path: string, fullPath: string, name: string, title: string, affix: boolean }>} */
    visitedViews: [],
  }),
  getters: {
    affixViews: (state) => state.visitedViews.filter((view) => view.affix),
  },
  actions: {
    addView(route) {
      if (!route.name || route.meta?.hidden) return
      const exist = this.visitedViews.find((view) => view.path === route.path)
      if (exist) {
        exist.fullPath = route.fullPath
        return
      }
      this.visitedViews.push({
        path: route.path,
        fullPath: route.fullPath,
        name: route.name,
        title: route.meta?.title || '未命名页面',
        affix: Boolean(route.meta?.affix),
      })
    },
    removeView(path) {
      const target = this.visitedViews.find((view) => view.path === path)
      if (!target || target.affix) return
      this.visitedViews = this.visitedViews.filter((view) => view.path !== path)
    },
    closeOthers(path) {
      this.visitedViews = this.visitedViews.filter((view) => view.affix || view.path === path)
    },
    closeAll() {
      this.visitedViews = this.visitedViews.filter((view) => view.affix)
    },
    reset() {
      this.visitedViews = []
    },
  },
})
