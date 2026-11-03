import request from '@/utils/request'

/** 查询检索通道配置（GET /api/v1/kb/retrieval/config） */
export function getRetrievalConfig() {
  return request.get('/kb/retrieval/config')
}

/** 执行知识检索（POST /api/v1/kb/retrieval/search）—— 自然语言通道含向量化与精排，放宽超时 */
export function searchKnowledge(data) {
  return request.post('/kb/retrieval/search', data, { timeout: 60000 })
}
