/** 时间与数值格式化工具 */

/** 补零 */
function pad(value) {
  return String(value).padStart(2, '0')
}

/**
 * 格式化日期时间
 * @param {string|number|Date} input 时间
 * @param {string} withTime 是否包含时分秒
 */
export function formatDateTime(input, withTime = true) {
  if (!input) return '-'
  const date = input instanceof Date ? input : new Date(input)
  if (Number.isNaN(date.getTime())) return String(input)

  const datePart = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  if (!withTime) return datePart
  return `${datePart} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** 秒数转可读时长 */
export function formatDuration(seconds) {
  if (!seconds || seconds < 1) return `${Math.round((seconds || 0) * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`
}

/** 毫秒转可读耗时（指标/耗时列统一口径，避免每个页面各写一份） */
export function formatMs(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '-'
  const value = Number(ms)
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} s`
}

/** 千分位数字 */
export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

/** 字节数转可读大小（对象存储列表用，保留一位小数） */
export function formatFileSize(bytes) {
  const size = Number(bytes)
  if (!Number.isFinite(size) || size < 0) return '-'
  if (size < 1024) return `${size} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = size / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

/** 安全地美化输出 JSON */
export function prettyJson(data) {
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

/**
 * 字符串载荷美化：本身是 JSON 文本时展开，否则原样返回
 * @param {string} text 载荷字符串（历史审计行、非 JSON 请求体等）
 */
export function prettyJsonString(text) {
  const trimmed = (text || '').trim()
  if (!trimmed || (trimmed[0] !== '{' && trimmed[0] !== '[')) return text
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return text
  }
}
