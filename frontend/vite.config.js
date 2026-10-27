import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 将路径前缀转换成 Vite 代理配置。
 * 业务接口统一挂在 VITE_APP_BASE_API（/api/v1）下，只需转发该前缀；
 * 接口文档 / 健康检查等应用级入口在后端根路径，开发环境一并转发，
 * 保证页面里的文档链接与连通性探测可用。
 */
function createProxy(target, prefixes) {
  return prefixes.reduce((proxy, prefix) => {
    proxy[prefix] = { target, changeOrigin: true }
    return proxy
  }, {})
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 代理目标：后端地址，仅 dev server 使用
  const apiTarget = env.VITE_API_BASE_URL || 'http://127.0.0.1:8002'
  // 业务接口前缀：须与后端 API_PREFIX 一致，不能 rewrite，否则后端匹配不上
  const apiBase = env.VITE_APP_BASE_API || '/api/v1'
  const proxyPrefixes = [apiBase, '/docs', '/openapi.json', '/healthz']

  return {
    // 后端通过 app.frontend('/ui', ...) 托管，开发与生产保持同一 base，路由无需区分环境
    base: env.VITE_APP_BASE || '/',
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
        '@utils': fileURLToPath(new URL('./src/utils', import.meta.url)),
      },
    },
    css: {
      preprocessorOptions: {
        // 全局注入 mixins，组件内 <style lang="scss"> 可直接 @include，无需逐个 @use
        scss: {
          additionalData: '@use "@/assets/styles/mixins.scss" as *;\n',
        },
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: false,
      proxy: createProxy(apiTarget, proxyPrefixes),
    },
    preview: {
      port: 4173,
    },
    build: {
      // 构建产物直接落到后端静态目录（backend/static），FastAPI 无需额外拷贝
      outDir: '../backend/static',
      assetsDir: 'assets',
      // backend/static 下还有后端演示资源（images/1.jpeg 等），不做整体清空，由 scripts/clean.mjs 精确清理
      emptyOutDir: false,
      sourcemap: false,
      chunkSizeWarningLimit: 1500,
      rollupOptions: {
        output: {
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia'],
            element: ['element-plus', '@element-plus/icons-vue'],
          },
          chunkFileNames: 'assets/js/[name]-[hash].js',
          entryFileNames: 'assets/js/[name]-[hash].js',
          assetFileNames: 'assets/[ext]/[name]-[hash].[ext]',
        },
      },
    },
  }
})
