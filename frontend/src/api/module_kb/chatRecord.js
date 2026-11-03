import request from '@/utils/request'

/**
 * 会话记录（管理端只读 + 维护）：对接 /api/v1/kb/chat-session 与 /kb/chat-message。
 *
 * 会话与消息由「AI 对话」页和契约 #14 ``/v1/chat/completions`` 产生，这里只做查询与
 * 改名 / 清空 / 删除，不提供正文编辑——对话内容是审计证据。
 */

/** 分页查询会话记录（GET /api/v1/kb/chat-session/list） */
export function getChatSessionList(params) {
  return request.get('/kb/chat-session/list', { params })
}

/** 查询会话记录详情（GET /api/v1/kb/chat-session/detail/{session_id}） */
export function getChatSessionDetail(sessionId) {
  return request.get(`/kb/chat-session/detail/${sessionId}`)
}

/** 修改会话标题（PUT /api/v1/kb/chat-session/rename/{session_id}） */
export function renameChatSession(sessionId, title) {
  return request.put(`/kb/chat-session/rename/${sessionId}`, { title })
}

/** 清空会话历史（PUT /api/v1/kb/chat-session/clear/{session_id}） */
export function clearChatSession(sessionId) {
  return request.put(`/kb/chat-session/clear/${sessionId}`)
}

/** 删除会话记录（DELETE /api/v1/kb/chat-session/delete/{session_id}） */
export function deleteChatSession(sessionId) {
  return request.delete(`/kb/chat-session/delete/${sessionId}`)
}

/** 分页查询会话消息（GET /api/v1/kb/chat-message/list，按发生顺序升序） */
export function getChatMessageList(params) {
  return request.get('/kb/chat-message/list', { params })
}
