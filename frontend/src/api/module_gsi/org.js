import request from '@/utils/request'

/**
 * GSI 组织架构与组织成员（契约 §四共享表：sbp_sys_org / sbp_sys_org_member）
 *
 * 组织树与组织成员是两套读法：树按 category 分组返回全量层级，
 * 成员列表走联表分页，同一行即可看到组织名称/编码与用户在职状态。
 */

/** 分页查询组织架构（GET /api/v1/gsi/sys/org/list） */
export function getGsiOrgList(params) {
  return request.get('/gsi/sys/org/list', { params })
}

/** 查询组织架构详情（GET /api/v1/gsi/sys/org/detail/{id}） */
export function getGsiOrgDetail(id) {
  return request.get(`/gsi/sys/org/detail/${id}`)
}

/** 查询组织树（GET /api/v1/gsi/sys/org/tree）：全量层级，超 5000 节点后端报 413 */
export function getGsiOrgTree(params) {
  return request.get('/gsi/sys/org/tree', { params })
}

/** 分页查询组织成员（含组织上下文；GET /api/v1/gsi/sys/org-member/page-with-org） */
export function getGsiOrgMemberPageWithOrg(params) {
  return request.get('/gsi/sys/org-member/page-with-org', { params })
}
