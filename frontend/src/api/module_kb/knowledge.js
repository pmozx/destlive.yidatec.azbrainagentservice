import request from '@/utils/request'

/** 分页查询知识条目（GET /api/v1/kb/knowledge/list） */
export function getKnowledgeList(params) {
  return request.get('/kb/knowledge/list', { params })
}

/** 查询知识条目详情（GET /api/v1/kb/knowledge/detail/{publication_id}） */
export function getKnowledgeDetail(publicationId) {
  return request.get(`/kb/knowledge/detail/${publicationId}`)
}

/** 新增知识条目（POST /api/v1/kb/knowledge/create） */
export function createKnowledge(data) {
  return request.post('/kb/knowledge/create', data)
}

/** 编辑知识条目（PUT /api/v1/kb/knowledge/update/{publication_id}） */
export function updateKnowledge(publicationId, data) {
  return request.put(`/kb/knowledge/update/${publicationId}`, data)
}

/** 变更知识条目状态（PATCH /api/v1/kb/knowledge/status/{publication_id}） */
export function updateKnowledgeStatus(publicationId, status) {
  return request.patch(`/kb/knowledge/status/${publicationId}`, { status })
}

/** 删除知识条目（DELETE /api/v1/kb/knowledge/delete/{publication_id}） */
export function deleteKnowledge(publicationId) {
  return request.delete(`/kb/knowledge/delete/${publicationId}`)
}

/** 分页查询版本快照（GET /api/v1/kb/knowledge/versions/{publication_id}） */
export function getKnowledgeVersions(publicationId, params) {
  return request.get(`/kb/knowledge/versions/${publicationId}`, { params })
}

/** 查询版本快照详情（GET /api/v1/kb/knowledge/versions/detail/{revision_id}） */
export function getKnowledgeVersionDetail(revisionId) {
  return request.get(`/kb/knowledge/versions/detail/${revisionId}`)
}

/** 分页查询知识切片（GET /api/v1/kb/knowledge/chunks/{publication_id}） */
export function getKnowledgeChunks(publicationId, params) {
  return request.get(`/kb/knowledge/chunks/${publicationId}`, { params })
}

/** 查询知识切片详情（GET /api/v1/kb/knowledge/chunks/detail/{chunk_id}） */
export function getKnowledgeChunkDetail(chunkId) {
  return request.get(`/kb/knowledge/chunks/detail/${chunkId}`)
}
