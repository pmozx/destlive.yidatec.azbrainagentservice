/**
 * 全局类型声明（供 JS 代码通过 JSDoc 引用，不参与构建）
 */

/** 后端统一响应结构，对应 app/common/response.py 的 ResponseSchema */
declare interface ApiResponse<T = unknown> {
  code: number
  msg: string
  data: T
  status_code: number
  success: boolean
}

/** 分页结果，对应 app/core/base_schema.py 的 PageResultSchema */
declare interface PageResult<T = unknown> {
  page_no: number
  page_size: number
  total: number
  has_next: boolean
  items: T[]
}

/** 分页查询参数 */
declare interface PageQuery {
  page_no: number
  page_size: number
  order_by?: string
  [key: string]: unknown
}

/** 用户管理列表项，对应 app/api/v1/module_system/user/schema.py 的 UserOutSchema */
declare interface UserItem {
  id: number
  uuid?: string
  username?: string
  name?: string
  mobile?: string
  email?: string
  gender?: string
  created_time?: string
  updated_time?: string
}

/** 创建 / 修改用户的请求体 */
declare interface UserPayload {
  username?: string
  password?: string
  name?: string
  mobile?: string
  email?: string
  gender?: string
}

/** 路由元信息 */
declare interface AppRouteMeta {
  title?: string
  icon?: string
  hidden?: boolean
  affix?: boolean
  activeMenu?: string
}

/** 侧边栏菜单节点 */
declare interface MenuNode {
  path: string
  title: string
  icon?: string
  children?: MenuNode[]
}

/** 前端内置的监控任务模型（后端接口就绪后替换为真实类型） */
declare interface AgentTaskItem {
  id: string
  name: string
  agent: string
  status: string
  progress: number
  duration: number
  startedAt: string
}
