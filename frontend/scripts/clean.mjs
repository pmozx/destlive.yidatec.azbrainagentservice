import { existsSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 精确清理上一次构建产物。
 *
 * 构建输出目录是后端的 static/，其中还存放着后端演示资源（如 images/1.jpeg），
 * 因此这里只删除 Vite 生成的文件，避免误删后端静态资源。
 */
const currentDir = dirname(fileURLToPath(import.meta.url))
const staticDir = resolve(currentDir, '../../static')

const targets = ['assets', 'index.html', 'favicon.svg', 'vite.svg']

targets.forEach((target) => {
  const targetPath = resolve(staticDir, target)
  if (!existsSync(targetPath)) return
  rmSync(targetPath, { recursive: true, force: true })
  console.log(`[clean] 已删除 ${targetPath}`)
})
