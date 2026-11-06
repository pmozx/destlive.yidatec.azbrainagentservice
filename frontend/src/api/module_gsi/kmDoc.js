import request from '@/utils/request'

/**
 * GSI 知识文档与发布链路（契约 §四共享表：sbp_km_doc / sbp_km_doc_record / sbp_km_doc_publication）
 *
 * 文档列表只给元数据（标题/状态/类型/归属团队/有效期），正文不在授权范围；
 * 快照详情含 snapshot_json（Text），页面按 JSON 展开。
 * 发布关系不再单独成页，随文档详情抽屉按 doc_id 下钻。
 */

/** 分页查询知识文档（GET /api/v1/gsi/km/doc/list） */
export function getGsiKmDocList(params) {
  return request.get('/gsi/km/doc/list', { params })
}

/** 查询知识文档详情（GET /api/v1/gsi/km/doc/detail/{id}） */
export function getGsiKmDocDetail(id) {
  return request.get(`/gsi/km/doc/detail/${id}`)
}

/** 分页查询知识生效快照（GET /api/v1/gsi/km/doc-record/list） */
export function getGsiKmDocRecordList(params) {
  return request.get('/gsi/km/doc-record/list', { params })
}

/** 查询知识生效快照详情（含 snapshot_json；GET /api/v1/gsi/km/doc-record/detail/{id}） */
export function getGsiKmDocRecordDetail(id) {
  return request.get(`/gsi/km/doc-record/detail/${id}`)
}

/**
 * 按文档查知识发布关系（含团队名称与编码命中；GET /api/v1/gsi/km/doc-publication/page-with-team）
 *
 * team 列存的是受众团队编码 group_code，接口联表补齐团队名称，并用 team_matched / team_valid
 * 暴露「编码在受众团队表里根本没命中」的历史数据——这类行在检索侧会直接落空，是排障重点。
 */
export function getGsiDocPublicationPageWithTeam(params) {
  return request.get('/gsi/km/doc-publication/page-with-team', { params })
}
