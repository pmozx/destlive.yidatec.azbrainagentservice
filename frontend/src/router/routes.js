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
    path: '/integration-kb',
    name: 'IntegrationKb',
    component: Layout,
    redirect: '/integration-kb/center',
    meta: { title: '集成-知识库', icon: 'Collection' },
    children: [
      {
        path: 'center',
        name: 'IntegrationKbCenter',
        component: () => import('@/views/module_integration_kb/center/index.vue'),
        meta: { title: '知识中心', icon: 'Management' },
      },
      {
        path: 'space',
        name: 'IntegrationKbSpace',
        component: () => import('@/views/module_integration_kb/space/index.vue'),
        meta: { title: '知识空间', icon: 'FolderOpened' },
      },
      {
        path: 'category',
        name: 'IntegrationKbCategory',
        component: () => import('@/views/module_integration_kb/category/index.vue'),
        meta: { title: '知识分类', icon: 'Files' },
      },
      {
        path: 'document',
        name: 'IntegrationKbDocument',
        component: () => import('@/views/module_integration_kb/document/index.vue'),
        meta: { title: '知识文档', icon: 'Document' },
      },
      {
        path: 'chunk',
        name: 'IntegrationKbChunk',
        component: () => import('@/views/module_integration_kb/chunk/index.vue'),
        meta: { title: '知识分片', icon: 'Grid' },
      },
      {
        path: 'task',
        name: 'IntegrationKbTask',
        component: () => import('@/views/module_integration_kb/task/index.vue'),
        meta: { title: '解析任务', icon: 'List' },
      },
      {
        path: 'stats',
        name: 'IntegrationKbStats',
        component: () => import('@/views/module_integration_kb/stats/index.vue'),
        meta: { title: '运营统计', icon: 'DataAnalysis' },
      },
    ],
  },
  {
    path: '/integration-chat',
    name: 'IntegrationChat',
    component: Layout,
    redirect: '/integration-chat/chat',
    meta: { title: '集成-AI问答', icon: 'ChatDotRound' },
    children: [
      {
        path: 'chat',
        name: 'IntegrationChatChat',
        component: () => import('@/views/module_integration_chat/chat/index.vue'),
        meta: { title: 'AI 问答', icon: 'ChatLineRound' },
      },
      {
        path: 'session',
        name: 'IntegrationChatSession',
        component: () => import('@/views/module_integration_chat/session/index.vue'),
        meta: { title: '问答会话', icon: 'ChatSquare' },
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
