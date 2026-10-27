import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 将逗号分隔的路径前缀转换成 Vite 代理配置。
 * 后端接口都挂在根路径下（/api00_start、/system、/item ...），
 * 所以开发环境需要把这些前缀逐一转发到 FastAPI 服务，避免跨域。
 */
function createProxy(target, prefixes) {
  return prefixes.reduce((proxy, prefix) => {
    proxy[prefix] = { target, changeOrigin: true }
    return proxy
  }, {})
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyPrefixes = (env.VITE_PROXY_PREFIXES || '')
    .split(',')
    .map((prefix) => prefix.trim())
    .filter(Boolean)

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
      proxy: createProxy(env.VITE_PROXY_TARGET, proxyPrefixes),
    },
    preview: {
      port: 4173,
    },
    build: {
      // 构建产物直接落到后端静态目录，FastAPI 无需额外拷贝
      outDir: '../static',
      assetsDir: 'assets',
      // static 下还有后端演示资源（images/1.jpeg 等），不做整体清空，由 scripts/clean.mjs 精确清理
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
