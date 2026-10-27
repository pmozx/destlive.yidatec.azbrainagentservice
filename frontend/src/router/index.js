import NProgress from 'nprogress'
import { createRouter, createWebHistory } from 'vue-router'

import { appConfig } from '@/config'
import { useTabsStore } from '@/store/modules/tabs'

import { constantRoutes } from './routes'

NProgress.configure({ showSpinner: false, minimum: 0.15 })

const router = createRouter({
  // 与 Vite base 保持一致（生产环境为 /ui/），保证 FastAPI 托管时的路径正确
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: constantRoutes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to, from, next) => {
  NProgress.start()
  next()
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · ${appConfig.title}` : appConfig.title
  useTabsStore().addView(to)
  NProgress.done()
})

router.onError(() => {
  NProgress.done()
})

export default router
