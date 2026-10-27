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
    redirect: '/system/user',
    meta: { title: '系统管理', icon: 'Setting' },
    children: [
      {
        path: 'user',
        name: 'SystemUser',
        component: () => import('@/views/module_system/user/index.vue'),
        meta: { title: '用户管理', icon: 'User' },
      },
    ],
  },
  {
    path: '/monitor',
    name: 'Monitor',
    component: Layout,
    redirect: '/monitor/agent',
    meta: { title: '服务监控', icon: 'Monitor' },
    children: [
      {
        path: 'agent',
        name: 'MonitorAgent',
        component: () => import('@/views/module_monitor/agent/index.vue'),
        meta: { title: '任务监控', icon: 'DataLine' },
      },
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
