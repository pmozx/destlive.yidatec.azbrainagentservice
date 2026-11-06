import request from '@/utils/request'

/**
 * GSI 组织架构与组织成员（契约 §四共享表：sbp_sys_org / sbp_sys_org_member）
 *
 * 组织树按 category 分组返回全量层级；组织成员关系按用户反查（`user-orgs`）
 * 是「用户与组织关系」页详情弹窗的数据源。
 * 成员联表分页（page-with-org）在 Swagger 仍可调，前端已不再使用——
 * 统一页面以用户为主体分页，组织只是筛选维度。
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

/** 按用户反查组织归属（含层级路径；GET /api/v1/gsi/sys/org-member/user-orgs） */
export function getGsiUserOrgs(userId) {
  return request.get('/gsi/sys/org-member/user-orgs', { params: { user_id: userId } })
}
