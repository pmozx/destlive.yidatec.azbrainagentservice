import request from '@/utils/request'

/**
 * GSI 受众团队成员（契约 §四共享表：sbp_sys_audience_team_member / sbp_sys_user）
 *
 * 关联表本身只有 team_id + user_id 两个数字，`page-with-team` 联表后
 * 同一行即可看到团队编码/名称/兼容标识与用户在职状态，`user-teams`
 * 用于按用户反查他到底落在哪些团队（授权范围排查的入口）。
 */

/** 分页查询受众团队成员（含团队三标识；GET /api/v1/gsi/sys/audience-team-member/page-with-team） */
export function getGsiTeamMemberPageWithTeam(params) {
  return request.get('/gsi/sys/audience-team-member/page-with-team', { params })
}

/** 按用户反查受众团队标识（GET /api/v1/gsi/sys/audience-team-member/user-teams） */
export function getGsiUserTeams(userId) {
  return request.get('/gsi/sys/audience-team-member/user-teams', { params: { user_id: userId } })
}
