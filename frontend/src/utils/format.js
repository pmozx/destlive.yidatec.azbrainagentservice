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

/** 千分位数字 */
export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

/** 安全地美化输出 JSON */
export function prettyJson(data) {
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}
