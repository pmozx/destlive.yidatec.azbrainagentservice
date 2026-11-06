import request from '@/utils/request'

/**
 * GSI 用户档案（契约 §四共享表：sbp_sys_user + sbp_sys_rank）
 *
 * 只读维护视图：账号、中英文名、职位、职级（联表带出名称）与三个有效性列。
 * password_hash、邮箱、手机号既不在后端模型上，也不在接口返回里。
 */

/** 分页查询用户档案（GET /api/v1/gsi/sys/user/list） */
export function getGsiUserList(params) {
  return request.get('/gsi/sys/user/list', { params })
}

/** 查询用户档案详情（GET /api/v1/gsi/sys/user/detail/{id}）
 *
 * 第二个参数透传 axios config：成员关系残留时用户可能已被物理删除，
 * 详情弹窗需要 `{ quiet: true }` 自行渲染缺失提示，而不是弹两次错误。
 */
export function getGsiUserDetail(id, config = {}) {
  return request.get(`/gsi/sys/user/detail/${id}`, config)
}
