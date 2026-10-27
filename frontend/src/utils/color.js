/**
 * 颜色工具：把主题色转换成 Element Plus 需要的一整套色阶变量
 */

/** 解析 3 位或 6 位十六进制颜色为 [r, g, b] */
function parseHex(hex) {
  const value = hex.replace('#', '').trim()
  const normalized =
    value.length === 3
      ? value
          .split('')
          .map((char) => char + char)
          .join('')
      : value
  return [0, 2, 4].map((offset) => Number.parseInt(normalized.slice(offset, offset + 2), 16))
}

/** 将颜色与目标色按权重混合（weight 越大越接近 target） */
function mixColor(source, target, weight) {
  const sourceRgb = parseHex(source)
  const targetRgb = parseHex(target)
  const mixed = sourceRgb.map((channel, index) =>
    Math.round(channel + (targetRgb[index] - channel) * weight),
  )
  return `#${mixed.map((channel) => channel.toString(16).padStart(2, '0')).join('')}`
}

/**
 * 应用主题色：生成 9 级浅色与 1 级深色，覆盖 Element Plus 的 CSS 变量
 * @param {string} color 六位十六进制颜色
 */
export function applyPrimaryColor(color) {
  const style = document.documentElement.style
  style.setProperty('--el-color-primary', color)

  for (let level = 1; level <= 9; level += 1) {
    style.setProperty(`--el-color-primary-light-${level}`, mixColor(color, '#ffffff', level / 10))
  }

  style.setProperty('--el-color-primary-dark-2', mixColor(color, '#000000', 0.2))
}
