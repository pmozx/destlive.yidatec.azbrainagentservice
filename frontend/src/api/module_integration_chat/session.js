import request from '@/utils/request'

/**
 * 集成-AI 问答-会话接口（/api/v1/integration-chat/session）
 *
 * 会话只存标题与问答范围，消息明细在 detail 里一次性返回（时间升序）。
 */

/** 分页查询问答会话（按最近消息时间倒序） */
export function getSessionList(params) {
  return request.get('/integration-chat/session/list', { params })
}

/** 查询会话详情（含消息明细，供历史回显） */
export function getSessionDetail(id) {
  return request.get(`/integration-chat/session/detail/${id}`)
}

/** 创建问答会话（scope_type=2 时 space_ids 必填） */
export function createSession(data) {
  return request.post('/integration-chat/session/create', data)
}

/** 重命名问答会话 */
export function updateSession(id, title) {
  return request.put(`/integration-chat/session/update/${id}`, { title })
}

/** 批量删除问答会话（消息一并清理） */
export function deleteSessions(ids) {
  return request.delete('/integration-chat/session/delete', { data: ids })
}
