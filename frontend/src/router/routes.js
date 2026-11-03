import Layout from '@/layouts/index.vue'

/**
 * 静态路由表
 * - meta.title：菜单与页签文案
 * - meta.icon：Element Plus 图标组件名（已在 plugins 中全局注册）
 * - meta.hidden：不在菜单中展示
 * - meta.affix：固定页签，不可关闭
 */
export const constantRoutes = [
  {
    path: '/',
    name: 'Root',
    component: Layout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '运行总览', icon: 'Odometer', affix: true },
      },
    ],
  },
  {
    path: '/system',
    name: 'System',
    component: Layout,
    redirect: '/system/log',
    meta: { title: '系统管理', icon: 'Setting' },
    children: [
      {
        path: 'log',
        name: 'SystemLog',
        component: () => import('@/views/module_system/log/index.vue'),
        meta: { title: '日志审计', icon: 'Tickets' },
      },
      {
        path: 'trace',
        name: 'SystemAccessTrace',
        component: () => import('@/views/module_system/trace/index.vue'),
        meta: { title: '接入调用追踪', icon: 'Link' },
      },
    ],
  },
  {
    path: '/kb',
    name: 'KnowledgeBase',
    component: Layout,
    redirect: '/kb/publication',
    meta: { title: '知识平台', icon: 'Collection' },
    children: [
      {
        path: 'publication',
        name: 'KbPublication',
        component: () => import('@/views/module_kb/publication/index.vue'),
        meta: { title: '发布单元', icon: 'Document' },
      },
      {
        path: 'parse-job',
        name: 'KbParseJob',
        component: () => import('@/views/module_kb/parseJob/index.vue'),
        meta: { title: '解析任务', icon: 'Files' },
      },
      {
        path: 'dup-group',
        name: 'KbDupGroup',
        component: () => import('@/views/module_kb/dupGroup/index.vue'),
        meta: { title: '重复治理', icon: 'CopyDocument' },
      },
    ],
  },
  {
    path: '/monitor',
    name: 'Monitor',
    component: Layout,
    redirect: '/monitor/endpoint',
    meta: { title: '服务监控', icon: 'Monitor' },
    children: [
      {
        path: 'endpoint',
        name: 'MonitorEndpoint',
        component: () => import('@/views/module_monitor/endpoint/index.vue'),
        meta: { title: '接口自检', icon: 'Connection' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: { title: '页面不存在', hidden: true },
  },
]

/** 预留：接入权限后由后端返回并按角色追加 */
export const asyncRoutes = []
