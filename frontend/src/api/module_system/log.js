import request from '@/utils/request'

/** 分页查询操作日志（GET /api/v1/system/log/operation/list） */
export function getOperationLogList(params) {
  return request.get('/system/log/operation/list', { params })
}

/** 查询操作日志详情（GET /api/v1/system/log/operation/detail/{id}） */
export function getOperationLogDetail(id) {
  return request.get(`/system/log/operation/detail/${id}`)
}

/** 批量删除操作日志（DELETE /api/v1/system/log/operation/delete，body 为 ID 列表） */
export function deleteOperationLog(ids) {
  return request.delete('/system/log/operation/delete', { data: ids })
}

/** 按保留天数清理过期日志（POST /api/v1/system/log/operation/clean） */
export function cleanOperationLog(days) {
  return request.post('/system/log/operation/clean', { days })
}
