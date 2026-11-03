import request from '@/utils/request'

/** 分页查询发布单元（GET /api/v1/kb/publication/list） */
export function getPublicationList(params) {
  return request.get('/kb/publication/list', { params })
}

/** 查询发布单元详情（GET /api/v1/kb/publication/detail/{publication_id}） */
export function getPublicationDetail(publicationId) {
  return request.get(`/kb/publication/detail/${publicationId}`)
}

/** 新增发布单元（POST /api/v1/kb/publication/create） */
export function createPublication(data) {
  return request.post('/kb/publication/create', data)
}

/** 编辑发布单元（PUT /api/v1/kb/publication/update/{publication_id}） */
export function updatePublication(publicationId, data) {
  return request.put(`/kb/publication/update/${publicationId}`, data)
}

/** 变更发布单元状态（PATCH /api/v1/kb/publication/status/{publication_id}） */
export function updatePublicationStatus(publicationId, status) {
  return request.patch(`/kb/publication/status/${publicationId}`, { status })
}

/** 删除发布单元（DELETE /api/v1/kb/publication/delete/{publication_id}） */
export function deletePublication(publicationId) {
  return request.delete(`/kb/publication/delete/${publicationId}`)
}

/** 分页查询版本快照（GET /api/v1/kb/publication/versions/{publication_id}） */
export function getPublicationVersions(publicationId, params) {
  return request.get(`/kb/publication/versions/${publicationId}`, { params })
}

/** 查询版本快照详情（GET /api/v1/kb/publication/versions/detail/{revision_id}） */
export function getPublicationVersionDetail(revisionId) {
  return request.get(`/kb/publication/versions/detail/${revisionId}`)
}

/** 分页查询知识切片（GET /api/v1/kb/publication/chunks/{publication_id}） */
export function getPublicationChunks(publicationId, params) {
  return request.get(`/kb/publication/chunks/${publicationId}`, { params })
}

/** 查询知识切片详情（GET /api/v1/kb/publication/chunks/detail/{chunk_id}） */
export function getPublicationChunkDetail(chunkId) {
  return request.get(`/kb/publication/chunks/detail/${chunkId}`)
}

/** 读取已批准基线（03.2；GET /api/v1/kb/publication/baseline/{publication_id}） */
export function getPublicationBaseline(publicationId, params) {
  return request.get(`/kb/publication/baseline/${publicationId}`, { params })
}

/** 删除已批准切片集合（03.4；DELETE /api/v1/kb/publication/snapshot/{publication_id}） */
export function deletePublicationSnapshot(publicationId, data) {
  return request.delete(`/kb/publication/snapshot/${publicationId}`, { data })
}
