import request from '@/utils/request'

/** 对象存储配置回显（GET /api/v1/system/storage/config） */
export function getStorageConfig() {
  return request.get('/system/storage/config')
}

/** 按目录列出文件夹与文件（GET /api/v1/system/storage/objects） */
export function getStorageObjects(params) {
  return request.get('/system/storage/objects', { params })
}

/** 签发预签名下载链接（GET /api/v1/system/storage/presign） */
export function getStoragePresign(key) {
  return request.get('/system/storage/presign', { params: { key } })
}

/**
 * 上传单个文件（POST /api/v1/system/storage/upload，multipart）
 * data 须为 FormData：拦截器会清掉实例默认的 Content-Type，交给浏览器补 boundary
 */
export function uploadStorageObject(data, config = {}) {
  // 上传含网络传输，默认 15s 对 100MB 量级偏紧，单独放宽超时
  return request.post('/system/storage/upload', data, { timeout: 300000, ...config })
}

/** 批量删除对象文件（DELETE /api/v1/system/storage/objects，仅文件） */
export function deleteStorageObjects(keys, config = {}) {
  return request.delete('/system/storage/objects', { data: { keys }, ...config })
}

/**
 * 代理读取对象内容的地址：预览/下载都走它
 * 内网桶地址浏览器通常直连不到，预签名链接同理，所以保留同源通道兜底
 */
export function storageContentUrl(key, download = false) {
  const base = import.meta.env.VITE_APP_BASE_API || '/api/v1'
  const suffix = download ? '&download=true' : ''
  return `${base}/system/storage/content?key=${encodeURIComponent(key)}${suffix}`
}
