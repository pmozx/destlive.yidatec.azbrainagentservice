import request from '@/utils/request'

/**
 * 集成-知识库-知识文档接口（/api/v1/integration-kb/document）
 *
 * 上传只落库 + 写入对象存储，解析由后台线程池异步完成；前端按 status 轮询进度。
 */

/** 业务接口前缀（与 request.js 的 baseURL 一致，供 <a>/<img>/<iframe> 直接拼地址） */
const API_BASE = import.meta.env.VITE_APP_BASE_API || '/api/v1'

/** 分页查询知识文档 */
export function getDocumentList(params) {
  return request.get('/integration-kb/document/list', { params })
}

/** 查询知识文档详情 */
export function getDocumentDetail(id) {
  return request.get(`/integration-kb/document/detail/${id}`)
}

/**
 * 批量上传知识文档：multipart 提交，单份失败只回显原因，不影响同批其它文件
 *
 * Content-Type 不在前端声明：request 拦截器发现 FormData 会摘掉默认的
 * application/json，交给浏览器补 boundary。超时放宽到 3 分钟（大文件先落 S3）。
 */
export function uploadDocuments(spaceId, files, categoryId) {
  const form = new FormData()
  form.append('space_id', spaceId)
  if (categoryId) form.append('category_id', categoryId)
  files.forEach((file) => form.append('files', file))
  return request.post('/integration-kb/document/upload', form, { timeout: 180000 })
}

/** 修改知识文档（标题 / 分类 / 关键词 / 来源 / 版本 / 上架） */
export function updateDocument(id, data) {
  return request.put(`/integration-kb/document/update/${id}`, data)
}

/** 批量上下架（下架后文档不再参与检索） */
export function batchSetDocumentAvailable(ids, isEnabled) {
  return request.put('/integration-kb/document/batch-available', { ids, is_enabled: isEnabled })
}

/** 提交重新解析（异步：返回任务台账，进度看文档状态） */
export function reparseDocument(id) {
  return request.post(`/integration-kb/document/reparse/${id}`)
}

/** 批量删除知识文档（级联分片、对象存储原件与解析产物） */
export function deleteDocuments(ids) {
  return request.delete('/integration-kb/document/delete', { data: ids })
}

/** 查询文档预览（pdf 走原件接口，markdown 直接回带已重写图片引用的正文） */
export function getDocumentPreview(id) {
  return request.get(`/integration-kb/document/preview/${id}`)
}

/** 读取文档原件（下载或内联查看；pdf 预览直接在 <iframe> 里用该地址） */
export function documentSourceUrl(id, download = false) {
  return `${API_BASE}/integration-kb/document/source/${id}${download ? '?download=true' : ''}`
}

/**
 * 批量下载解析产物（内存 zip：原件 + full.md + content.json + images）
 * 返回 Blob，由调用方自行触发浏览器下载
 */
export function downloadDocuments(payload) {
  return request.post('/integration-kb/document/download', payload, {
    responseType: 'blob',
    timeout: 180000,
  })
}
