import request from '@/utils/request'

/**
 * 集成-AI 问答接口（/api/v1/integration-chat）
 *
 * 问答主链路是 POST + SSE（见 utils/sse.js），本文件只放范围查询与反馈这类
 * 普通 JSON 接口，避免把流式细节混进 axios 拦截器的「统一响应解包」逻辑里。
 */

/** 业务接口前缀（与 request.js 的 baseURL 一致） */
const API_BASE = import.meta.env.VITE_APP_BASE_API || '/api/v1'

/** 流式问答地址（POST + text/event-stream，由 utils/sse.js 的 streamPost 消费） */
export const CHAT_STREAM_URL = `${API_BASE}/integration-chat/stream`

/** 查询问答范围（启用空间清单，供范围选择器与引用卡片展示名称） */
export function getChatScope() {
  return request.get('/integration-chat/scope')
}

/** 提交答案反馈（1 有用 / -1 无用），仅助手消息可评 */
export function setMessageFeedback(messageId, feedback) {
  return request.put(`/integration-chat/message/feedback/${messageId}`, { feedback })
}
