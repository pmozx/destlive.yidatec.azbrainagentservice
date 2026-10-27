import ElementPlus from 'element-plus'
import * as ElementPlusIcons from '@element-plus/icons-vue'

import router from '@/router'
import { setupStore } from '@/store'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import 'nprogress/nprogress.css'
import '@/assets/styles/index.scss'

/**
 * 统一注册第三方插件
 * main.js 只负责创建应用与挂载，插件初始化全部收敛到这里
 * @param {import('vue').App} app
 */
export function initPlugins(app) {
  setupStore(app)

  app.use(ElementPlus, { size: 'default', zIndex: 3000 })

  // 全局注册图标，模板中可直接 <el-icon><Odometer /></el-icon> 或 <component :is="'Odometer'" />
  Object.entries(ElementPlusIcons).forEach(([name, component]) => {
    app.component(name, component)
  })

  app.use(router)
}
