import { getToken } from './auth'

/**
 * POST + SSE 流式请求
 *
 * 为什么不用 EventSource：它有两条硬限制——只能 GET、不能带自定义请求头。
 * 问答入参（问题 + 问答范围 + 会话ID）必须放 body，鉴权走 Authorization，
 * 因此用 fetch + ReadableStream 手工解析 ``event:`` / ``data:`` 帧。
 *
 * 帧格式与后端 ``integration_chat.service.format_sse`` 严格对应：
 * 一个事件块以空行结束，块内 ``event: <name>`` 一行、``data: <单行 JSON>`` 一行。
 *
 * @param {string} path 接口路径（含 API 前缀，如 /api/v1/integration-chat/stream）
 * @param {object} body 请求体（序列化为 JSON）
 * @param {{ onEvent: (event: string, data: object) => void, onError?: (message: string) => void, onClose?: () => void }} handlers
 * @returns {{ abort: () => void }}
 */
export function streamPost(path, body, handlers = {}) {
  const { onEvent, onError, onClose } = handlers
  const controller = new AbortController()

  const run = async () => {
    let closed = false
    const finish = () => {
      if (closed) return
      closed = true
      onClose?.()
    }

    try {
      const token = getToken()
      const response = await fetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json;charset=utf-8',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      // 后端校验失败（422）与业务异常都会走 JSON 响应，这里要把 msg 取出来给人看
      if (!response.ok || !response.body) {
        onError?.(await readErrorMessage(response))
        finish()
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const event = parseFrame(block)
          if (event) {
            onEvent?.(event.name, event.data)
            // done 是契约里的末帧：收到它就没必要继续等连接关闭
            if (event.name === 'done') {
              await reader.cancel().catch(() => {})
              finish()
              return
            }
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
      finish()
    } catch (error) {
      // 主动 abort（用户切会话/离开页面）不算错误，不弹提示
      if (error?.name === 'AbortError') {
        finish()
        return
      }
      onError?.(error?.message || '流式请求失败，请稍后重试')
      finish()
    }
  }

  void run()

  return {
    abort: () => controller.abort(),
  }
}

/** 解析单个 SSE 事件块；无 data 行或 JSON 非法时返回 null（跳过脏帧而不是中断整条流） */
function parseFrame(block) {
  let name = 'message'
  const dataLines = []

  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      name = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!dataLines.length) return null
  try {
    return { name, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}

/** 读取非流式错误响应里的可读文案（统一响应结构优先 msg，其次 FastAPI 的 detail） */
async function readErrorMessage(response) {
  try {
    const text = await response.text()
    if (!text) return `请求失败（HTTP ${response.status}）`
    const payload = JSON.parse(text)
    const detail = payload?.msg ?? payload?.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      return detail.map((item) => item?.msg).filter(Boolean).join('；') || `请求失败（HTTP ${response.status}）`
    }
    return `请求失败（HTTP ${response.status}）`
  } catch {
    return `请求失败（HTTP ${response.status}）`
  }
}
