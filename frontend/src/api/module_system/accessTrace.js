import request from '@/utils/request'

/** 近 N 小时接入概览（GET /api/v1/system/access-trace/summary） */
export function getAccessTraceSummary(params) {
  return request.get('/system/access-trace/summary', { params })
}

/** 契约调用明细分页（GET /api/v1/system/access-trace/calls） */
export function getAccessTraceCalls(params) {
  return request.get('/system/access-trace/calls', { params })
}

/** 调用详情，含请求/响应体（GET /api/v1/system/access-trace/calls/detail/{id}） */
export function getAccessTraceCallDetail(id) {
  return request.get(`/system/access-trace/calls/detail/${id}`)
}

/** 防重放台账分页（GET /api/v1/system/access-trace/nonces） */
export function getAccessTraceNonces(params) {
  return request.get('/system/access-trace/nonces', { params })
}

/** 幂等台账分页（GET /api/v1/system/access-trace/idempotency） */
export function getAccessTraceIdempotency(params) {
  return request.get('/system/access-trace/idempotency', { params })
}

/** 结果台账分页（GET /api/v1/system/access-trace/request-logs） */
export function getAccessTraceRequestLogs(params) {
  return request.get('/system/access-trace/request-logs', { params })
}

/** 按 request_id 串联四张表（GET /api/v1/system/access-trace/timeline） */
export function getAccessTraceTimeline(requestId) {
  return request.get('/system/access-trace/timeline', { params: { request_id: requestId } })
}
