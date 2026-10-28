import request from '@/utils/request'
import { HttpMethod } from '@/enums'

/**
 * 接口自检目录
 * 业务接口写相对路径，由 axios 统一加 /api/v1 前缀；
 * 文档、健康检查等应用级入口标 absolute: true，直探根路径。
 */
export const endpointCatalog = [
  {
    group: '系统管理',
    items: [
      { name: '接口审计日志列表', method: HttpMethod.GET, path: '/system/api-log/list?page_no=1&page_size=5' },
    ],
  },
  {
    group: '文档与服务',
    items: [
      { name: '健康检查', method: HttpMethod.GET, path: '/healthz', absolute: true },
      { name: 'OpenAPI 规范', method: HttpMethod.GET, path: '/openapi.json', absolute: true },
      { name: 'Swagger UI', method: HttpMethod.GET, path: '/docs', absolute: true },
    ],
  },
]

/**
 * 发起一次接口自检请求
 * 返回完整响应信息，便于页面展示状态码、耗时与原始报文
 * @param {string} method 请求方法
 * @param {string} path 接口路径（默认为业务接口，由 axios 统一加 /api/v1 前缀）
 * @param {{ absolute?: boolean }} options absolute=true 时不加前缀，用于文档、健康检查等根路径接口
 */
export async function probeEndpoint(method, path, options = {}) {
  const startedAt = performance.now()
  const response = await request.request({
    url: path,
    method,
    raw: true,
    quiet: true,
    // 根路径接口需清空 baseURL，否则会被拼成 /api/v1/docs
    ...(options.absolute ? { baseURL: '' } : {}),
  })
  return {
    status: response.status,
    statusText: response.statusText,
    duration: Math.round(performance.now() - startedAt),
    data: response.data,
  }
}

/** 后端连通性探测（用于首页服务状态卡片） */
export function checkBackendHealth() {
  return probeEndpoint(HttpMethod.GET, '/healthz', { absolute: true })
}
