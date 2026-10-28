import request from '@/utils/request'

/** 分页查询接口审计日志（GET /api/v1/system/api-log/list） */
export function getOperationLogList(params) {
  return request.get('/system/api-log/list', { params })
}

/** 查询接口审计日志详情（GET /api/v1/system/api-log/detail/{id}） */
export function getOperationLogDetail(id) {
  return request.get(`/system/api-log/detail/${id}`)
}

/** 批量删除接口审计日志（DELETE /api/v1/system/api-log/delete，body 为 ID 列表） */
export function deleteOperationLog(ids) {
  return request.delete('/system/api-log/delete', { data: ids })
}

/** 按保留天数清理过期日志（POST /api/v1/system/api-log/clean） */
export function cleanOperationLog(days) {
  return request.post('/system/api-log/clean', { days })
}
