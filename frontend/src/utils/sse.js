/**
 * SSE（Server-Sent Events）客户端：帧解析 + 带鉴权的 POST 流。
 *
 * 为什么不用 `EventSource`：它只支持 GET、不能写请求体也不能带 `Authorization`，而问答是
 * 「POST + JSON 正文 + 登录态」——所以只能 `fetch` + `ReadableStream` 边收边解（S30）。
 *
 * 本模块只做传输与解析，不认识业务事件名；页面拿到的永远是 `{ event, data }`，
 * 未知事件与未知字段原样交给调用方忽略（S10）。
 */

import { getToken } from './auth'

/** 帧边界：空行。代理可能把 `\n` 改写成 `\r\n`，两种都要认 */
const FRAME_BOUNDARY = /\r?\n\r?\n/

/** 拼上 axios 实例同一个 baseURL，避免两套前缀口径各写一份 */
function resolveUrl(url) {
  if (/^https?:\/\//i.test(url)) return url
  const base = (import.meta.env.VITE_APP_BASE_API || '/api/v1').replace(/\/+$/, '')
  return `${base}${url.startsWith('/') ? url : `/${url}`}`
}

/**
 * 解析单个帧块 → `{ event, data }`；畸形帧返回 `null`。
 *
 * 只认 `event:` 与 `data:`：`id:` / `retry:` / `Last-Event-ID` 按规范不使用（S9）。
 * 多行 `data:` 在 SSE 规范里用 `\n` 拼接，本项目只写一行，这里仍按规范兼容。
 */
function parseBlock(block) {
  let event = 'message'
  const dataLines = []
  for (const raw of block.split(/\r?\n/)) {
    const line = raw.trimEnd()
    if (!line || line.startsWith(':')) continue // 注释行（保活用）直接跳过
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      // 规范允许 `data:` 后跟一个空格，只去掉这一个
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    // 畸形帧丢弃而不是抛错：抛错会掐掉整条流，后面的正常帧也跟着丢
    console.warn('[sse] 丢弃无法解析的帧', block)
    return null
  }
}

/** 帧解析器：分块喂文本，吐出已完整的帧；流结束时 `flush()` 收尾（S37 的客户端对应物） */
export class SseParser {
  constructor() {
    this.buffer = ''
  }

  /** @param {string} text 新到的一段文本 @returns {Array<{event: string, data: any}>} */
  push(text) {
    this.buffer += text
    const frames = []
    let match = this.buffer.match(FRAME_BOUNDARY)
    while (match) {
      const block = this.buffer.slice(0, match.index)
      this.buffer = this.buffer.slice(match.index + match[0].length)
      const frame = parseBlock(block)
      if (frame) frames.push(frame)
      match = this.buffer.match(FRAME_BOUNDARY)
    }
    return frames
  }

  /** 连接被掐断时最后一帧常常缺结束空行：能解析就解析 */
  flush() {
    const block = this.buffer.trim()
    this.buffer = ''
    const frame = block ? parseBlock(block) : null
    return frame ? [frame] : []
  }
}

/** 首帧前的拒绝返回的是 JSON，不是流：把后端文案捞出来当异常信息 */
async function toHttpError(response) {
  let message = `请求失败（HTTP ${response.status}）`
  try {
    const payload = await response.json()
    const detail = payload?.detail ?? payload?.msg
    if (Array.isArray(detail)) {
      message = detail.map((item) => item.msg || item.message).filter(Boolean).join('；') || message
    } else if (typeof detail === 'string' && detail) {
      message = detail
    }
  } catch {
    // 错误体不是 JSON：保留默认文案
  }
  const error = new Error(message)
  error.status = response.status
  return error
}

/**
 * POST 一个 SSE 接口，逐帧回调。
 *
 * @param {string} url 相对路径（补 baseURL）或绝对地址
 * @param {object} options
 * @param {any} [options.body] 请求体，会被 JSON 序列化
 * @param {Record<string, string>} [options.headers] 附加请求头
 * @param {AbortSignal} [options.signal] 中止信号（「停止」按钮 / 页面卸载）
 * @param {(frame: {event: string, data: any}) => void} [options.onEvent] 每帧回调
 */
export async function postSse(url, { body, headers = {}, signal, onEvent } = {}) {
  const token = getToken()
  const response = await fetch(resolveUrl(url), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json;charset=utf-8',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })

  if (!response.ok) throw await toHttpError(response)

  // 200 却不是流：后端写错了或代理改写了响应，早点报错好过解析一堆 HTML
  const contentType = response.headers.get('Content-Type') || ''
  if (!contentType.includes('text/event-stream')) {
    throw new Error('服务端未按流式返回（Content-Type 不是 text/event-stream）')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = new SseParser()
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) onEvent?.(frame)
    }
    for (const frame of parser.flush()) onEvent?.(frame)
  } finally {
    // 主动中止时取消上游读取：服务端靠断连判定停止燃烧 token（S24）
    reader.cancel().catch(() => {})
  }
}

/** 是否由 `AbortController.abort()` 触发（用户点停止 / 页面卸载），与网络失败区分 */
export function isAbortError(error) {
  return error?.name === 'AbortError'
}
