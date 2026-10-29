import request from '@/utils/request'

/**
 * 集成-知识库-运营统计接口（/api/v1/integration-kb/stats）
 *
 * 口径与文档表字段一致：解析状态分布、文档引用排行、空间知识量、上传趋势。
 */

/** 知识库总览指标 */
export function getStatsOverview() {
  return request.get('/integration-kb/stats/overview')
}

/** 文档解析状态分布 */
export function getDocumentStatusStats() {
  return request.get('/integration-kb/stats/document-status')
}

/** 文档引用排行（ref_count 由问答命中引用累加） */
export function getHotDocuments(limit = 10) {
  return request.get('/integration-kb/stats/hot-documents', { params: { limit } })
}

/** 空间知识量排行 */
export function getSpaceRanking(limit = 10) {
  return request.get('/integration-kb/stats/space-ranking', { params: { limit } })
}

/** 近 N 天上传趋势 */
export function getUploadTrend(days = 7) {
  return request.get('/integration-kb/stats/upload-trend', { params: { days } })
}
