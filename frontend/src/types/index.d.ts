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

/** 接口审计日志列表项，对应 app/modules/system/api_log/schema.py 的 SystemApiLogOutSchema */
declare interface OperationLogItem {
  id: number
  username: string
  status: number
  description?: string
  request_path: string
  request_method: string
  response_code: number
  process_time?: string
  request_ip?: string
  created_time?: string
}

/** 接口审计日志详情，对应 SystemApiLogDetailOutSchema */
declare interface OperationLogDetail extends OperationLogItem {
  request_query?: string
  request_payload?: string
  response_json?: string
  user_agent?: string
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

