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
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json;charset=utf-8' },
})

request.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

request.interceptors.response.use(
  (response) => {
    const { config, data } = response
    // 需要完整响应（状态码、耗时、原始报文）时传 { raw: true }
    if (config.raw) return response

    // 非统一响应结构（如 /api00_start 直接返回 dict）原样返回
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
