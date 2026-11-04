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
 * 代理读取对象内容的地址：预览/下载都走它
 * 内网桶地址浏览器通常直连不到，预签名链接同理，所以保留同源通道兜底
 */
export function storageContentUrl(key, download = false) {
  const base = import.meta.env.VITE_APP_BASE_API || '/api/v1'
  const suffix = download ? '&download=true' : ''
  return `${base}/system/storage/content?key=${encodeURIComponent(key)}${suffix}`
}
