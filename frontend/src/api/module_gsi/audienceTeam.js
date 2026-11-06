import request from '@/utils/request'

/**
 * GSI 受众团队（契约 §四共享表：sbp_sys_audience_team）
 *
 * 授权判定只认 group_code；audience_team 是历史兼容标识，
 * 知识文档的 team 列与 FAQ 的 team 列存的都是 group_code。
 */

/** 分页查询受众团队（GET /api/v1/gsi/sys/audience-team/list） */
export function getGsiAudienceTeamList(params) {
  return request.get('/gsi/sys/audience-team/list', { params })
}

/** 查询受众团队详情（GET /api/v1/gsi/sys/audience-team/detail/{id}） */
export function getGsiAudienceTeamDetail(id) {
  return request.get(`/gsi/sys/audience-team/detail/${id}`)
}

/**
 * 按用户反查受众团队（GET /api/v1/gsi/sys/audience-team-member/user-teams）
 *
 * 用户详情弹窗用它回答「这个人到底在哪些团队」——空集合等价于无知识授权。
 */
export function getGsiUserTeams(userId) {
  return request.get('/gsi/sys/audience-team-member/user-teams', { params: { user_id: userId } })
}
