/**
 * 应用级配置
 * 与业务无关的开关、布局尺寸、快捷入口等集中在此维护
 */

/** 应用基础信息 */
export const appConfig = Object.freeze({
  title: import.meta.env.VITE_APP_TITLE || 'Service Brain Agent Dashboard',
  subtitle: import.meta.env.VITE_APP_SUBTITLE || 'AGENT CONSOLE',
  description: import.meta.env.VITE_APP_DESCRIPTION || '服务大脑智能体控制台',
  version: import.meta.env.VITE_APP_VERSION || '0.1.0',
  copyright: import.meta.env.VITE_APP_DESCRIPTION || '服务大脑智能体控制台',
  // 后端接口文档入口（YaoAPI 聚合文档）
  swaggerUrl: '/docs',
  yaoApiUrl: '/docs/yao-api',
})

/** 顶栏功能区开关 */
export const headerBarConfig = Object.freeze({
  showBreadcrumb: true,
  showRefresh: true,
  showFullscreen: true,
  showThemeToggle: true,
  showSetting: true,
  showUserCenter: true,
})

/** 快捷入口（fastEnter）：后续接入更多监控页面时在此登记 */
export const fastEnterConfig = Object.freeze({
  enabled: true,
  items: [
    { title: '接口自检', path: '/monitor/endpoint', icon: 'Connection', desc: '逐个探测后端接口' },
    { title: '任务监控', path: '/monitor/agent', icon: 'DataLine', desc: '查看任务执行进度' },
    { title: '用户管理', path: '/system/user', icon: 'User', desc: '维护平台用户' },
    { title: '接口文档', path: '/docs', icon: 'Document', desc: 'Swagger 与 YaoAPI', external: true },
  ],
})

/** 主题色预设：默认沿用参考页的酒红紫 */
export const themeColorPresets = Object.freeze([
  { name: '酒红紫', value: '#8b1e56' },
  { name: '品红', value: '#a32b68' },
  { name: '黛紫', value: '#6b3fa0' },
  { name: '拂晓蓝', value: '#2f6ff5' },
  { name: '极光绿', value: '#0f9d78' },
])
