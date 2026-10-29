import axios from 'axios'
import { ElMessage } from 'element-plus'

import { getToken } from './auth'

/** 后端统一响应中代表成功的业务码：0=OK，200~204=HTTP 语义成功 */
const SUCCESS_CODES = [0, 200, 201, 202, 204]

/** 判断响应体是否为后端统一响应结构（app.common.response.ResponseSchema） */
function isApiResponse(data) {
  return Boolean(data) && typeof data === 'object' && 'code' in data && 'success' in data
}

/** 提取 FastAPI 错误响应中的 detail / msg 文案 */
function resolveErrorMessage(error) {
  const payload = error.response?.data
  const detail = payload?.detail ?? payload?.msg

  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || item.message).filter(Boolean).join('；')
  }
  if (typeof detail === 'string' && detail) return detail
  if (error.code === 'ECONNABORTED') return '请求超时，请检查后端服务是否已启动'
  return error.message || '网络异常，请稍后重试'
}

const request = axios.create({
  // 业务接口统一前缀，与后端 app/api/v1/routers.py 的 API_PREFIX 一致
  baseURL: import.meta.env.VITE_APP_BASE_API || '/api/v1',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json;charset=utf-8' },
})

request.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  // FormData 必须交给浏览器自己定 Content-Type（它要补 boundary）：
  // 若沿用实例默认的 application/json，axios 1.x 的 transformRequest 会把 FormData
  // 直接 JSON.stringify 掉，后端 multipart 解析拿到的 body 是空的，只会报字段缺失。
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    config.headers.setContentType(null)
  }
  return config
})

request.interceptors.response.use(
  (response) => {
    const { config, data } = response
    // 需要完整响应（状态码、耗时、原始报文）时传 { raw: true }
    if (config.raw) return response

    // 非统一响应结构（如 /healthz 直接返回 dict）原样返回
    if (!isApiResponse(data)) return data

    if (data.success === false || !SUCCESS_CODES.includes(data.code)) {
      const message = data.msg || '请求处理失败'
      if (!config.quiet) ElMessage.error(message)
      return Promise.reject(Object.assign(new Error(message), { code: data.code, data }))
    }

    return data.data
  },
  (error) => {
    const message = resolveErrorMessage(error)
    if (!error.config?.quiet) ElMessage.error(message)
    error.message = message
    return Promise.reject(error)
  },
)

export default request
