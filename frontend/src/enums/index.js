/**
 * 全局枚举与常量映射
 * 与后端 app/common/enums.py 保持一致，避免前端散落魔法值
 */

/** 本地存储键名（index.html 中的首屏脚本也依赖这些字面量） */
export const StorageKey = Object.freeze({
  TOKEN: 'agent_token',
  USER_INFO: 'agent_user_info',
  THEME_MODE: 'agent_theme_mode',
  PRIMARY_COLOR: 'agent_primary_color',
  SIDEBAR_COLLAPSED: 'agent_sidebar_collapsed',
})

/** 主题模式 */
export const ThemeMode = Object.freeze({
  LIGHT: 'light',
  DARK: 'dark',
})

/** 后端业务码（app/common/enums.py RET） */
export const BizCode = Object.freeze({
  OK: 0,
  SUCCESS: 200,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
})

/** 请求方法，用于接口自检页的标签配色 */
export const HttpMethod = Object.freeze({
  GET: 'GET',
  POST: 'POST',
  PUT: 'PUT',
  DELETE: 'DELETE',
})

/** 智能体任务状态 */
export const AgentTaskStatus = Object.freeze({
  PENDING: 'pending',
  RUNNING: 'running',
  SUCCESS: 'success',
  FAILED: 'failed',
})

/** 任务状态的展示文案与标签类型 */
export const AgentTaskStatusMeta = Object.freeze({
  [AgentTaskStatus.PENDING]: { label: '排队中', type: 'info' },
  [AgentTaskStatus.RUNNING]: { label: '运行中', type: 'primary' },
  [AgentTaskStatus.SUCCESS]: { label: '已完成', type: 'success' },
  [AgentTaskStatus.FAILED]: { label: '异常', type: 'danger' },
})

