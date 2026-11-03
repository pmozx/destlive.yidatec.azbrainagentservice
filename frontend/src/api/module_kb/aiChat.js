import request from '@/utils/request'
import { postSse } from '@/utils/sse'

/**
 * 发起一轮 AI 问答（POST /api/v1/kb/chat/ask）。
 *
 * 一次性返回，只在「需要完整结果再渲染」时用（例如脚本/排查）。页面上用流式版本：
 * 一次性接口的等待时间是「检索 + 全文生成」，几十秒里界面没有任何反馈。
 */
export function askKnowledgeChat(data) {
  return request.post('/kb/chat/ask', data, { timeout: 120000 })
}

/**
 * 发起一轮 AI 问答（POST /api/v1/kb/chat/ask/stream，SSE 逐帧返回）。
 *
 * 帧契约见 `02-AI对话SSE流式输出规范.md`：`meta` → `stage` → `delta`* → `citations`
 * → `done`，失败以 `error` 收尾。这里只做传输，事件语义由页面决定怎么渲染。
 *
 * @param {object} data 请求体（`session_id` 留空则后端首轮自动建会话）
 * @param {{ signal?: AbortSignal, onEvent?: (frame: {event: string, data: any}) => void }} [options]
 */
export function askKnowledgeChatStream(data, { signal, onEvent } = {}) {
  return postSse('/kb/chat/ask/stream', { body: data, signal, onEvent })
}
