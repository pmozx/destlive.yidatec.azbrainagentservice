import request from '@/utils/request'

/**
 * GSI 知识按团队生效关系（契约 §四共享表：sbp_km_doc_publication）
 *
 * team 列存的是受众团队编码 group_code，`page-with-team` 联表后补齐团队名称，
 * 并用 team_matched / team_valid 暴露「编码在受众团队表里根本没命中」的历史数据
 * ——这类行在检索侧会直接落空，是排障重点。
 */

/** 分页查询生效关系（含团队编码与名称；GET /api/v1/gsi/km/doc-publication/page-with-team） */
export function getGsiDocPublicationPageWithTeam(params) {
  return request.get('/gsi/km/doc-publication/page-with-team', { params })
}
