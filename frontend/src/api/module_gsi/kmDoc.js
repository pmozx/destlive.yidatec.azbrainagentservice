import request from '@/utils/request'

/**
 * GSI 知识文档与送审快照（契约 §四共享表：sbp_km_doc / sbp_km_doc_record）
 *
 * 文档列表只给元数据（标题/状态/类型/归属团队/有效期），正文不在授权范围；
 * 快照详情含 snapshot_json（Text），页面按 JSON 展开。
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
