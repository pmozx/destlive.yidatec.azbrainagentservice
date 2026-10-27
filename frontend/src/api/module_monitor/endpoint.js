import request from '@/utils/request'
import { HttpMethod } from '@/enums'

/**
 * 接口自检目录
 * 与后端 app/api 下的示例控制器保持一致，新增接口时在此登记即可
 */
export const endpointCatalog = [
  {
    group: 'FastAPI 基础示例',
    items: [
      { name: '路由定义', method: HttpMethod.GET, path: '/api01_basic/d01_router' },
      { name: '路径参数', method: HttpMethod.GET, path: '/api01_basic/d02_path_param/1' },
      { name: '查询参数', method: HttpMethod.GET, path: '/api01_basic/d03_query_param?skip=0&limit=5' },
      { name: '响应体模型', method: HttpMethod.GET, path: '/api01_basic/d05_response_body/1' },
      { name: '文件响应', method: HttpMethod.GET, path: '/api01_basic/d07_response_file' },
      { name: '中间件', method: HttpMethod.GET, path: '/api02_middleware/d01_middleware' },
      { name: '依赖注入', method: HttpMethod.GET, path: '/api03_depends/d01_common_parameters' },
    ],
  },
  {
    group: '业务模块',
    items: [
      { name: '用户分页列表', method: HttpMethod.GET, path: '/system/users/list?page_no=1&page_size=5' },
      { name: '项目列表', method: HttpMethod.GET, path: '/item/list' },
    ],
  },
  {
    group: '文档与服务',
    items: [
      { name: 'OpenAPI 规范', method: HttpMethod.GET, path: '/openapi.json' },
      { name: 'Swagger UI', method: HttpMethod.GET, path: '/docs' },
    ],
  },
]

/**
 * 发起一次接口自检请求
 * 返回完整响应信息，便于页面展示状态码、耗时与原始报文
 * @param {string} method 请求方法
 * @param {string} path 接口路径
 */
export async function probeEndpoint(method, path) {
  const startedAt = performance.now()
  const response = await request.request({ url: path, method, raw: true, quiet: true })
  return {
    status: response.status,
    statusText: response.statusText,
    duration: Math.round(performance.now() - startedAt),
    data: response.data,
  }
}

/** 后端连通性探测（用于首页服务状态卡片） */
export function checkBackendHealth() {
  return probeEndpoint(HttpMethod.GET, '/api01_basic/d01_router')
}
