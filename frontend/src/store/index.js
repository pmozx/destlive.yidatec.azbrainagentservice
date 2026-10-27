import { createPinia } from 'pinia'

const pinia = createPinia()

/** 注册 Pinia（由 plugins/index.js 统一调用） */
export function setupStore(app) {
  app.use(pinia)
}

export default pinia
