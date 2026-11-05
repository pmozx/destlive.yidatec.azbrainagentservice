import request from '@/utils/request'

/**
 * GSI FAQ 侧三张表（契约 §四共享表：sbp_faq / sbp_faq_publication / sbp_faq_record）
 *
 * 与知识侧语义不同：FAQ 的受众与有效期随审批「快照化」到 access_rules_json，
 * 优先读 publication 上的规则、为空才回落 record.snapshot_json，两者都必须含
 * invisibleTeams 数组。后端已解析好来源与有效性，页面不再二次判断。
 */

/** 分页查询 FAQ 主记录（GET /api/v1/gsi/faq/list）：仅编号/状态，正文未授权 */
export function getGsiFaqList(params) {
  return request.get('/gsi/faq/list', { params })
}

/** 查询 FAQ 主记录详情（GET /api/v1/gsi/faq/detail/{id}） */
export function getGsiFaqDetail(id) {
  return request.get(`/gsi/faq/detail/${id}`)
}

/** 分页查询 FAQ 发布生效关系（含生效规则解析；GET /api/v1/gsi/faq/publication/list） */
export function getGsiFaqPublicationList(params) {
  return request.get('/gsi/faq/publication/list', { params })
}

/** 分页查询 FAQ 审批快照（GET /api/v1/gsi/faq/record/list） */
export function getGsiFaqRecordList(params) {
  return request.get('/gsi/faq/record/list', { params })
}

/** 查询 FAQ 审批快照详情（含 snapshot_json；GET /api/v1/gsi/faq/record/detail/{id}） */
export function getGsiFaqRecordDetail(id) {
  return request.get(`/gsi/faq/record/detail/${id}`)
}
