import request from '@/utils/request'

/** 分页查询重复分组（GET /api/v1/kb/dup-groups/list） */
export function getDupGroupList(params) {
  return request.get('/kb/dup-groups/list', { params })
}

/** 查询重复分组详情（GET /api/v1/kb/dup-groups/detail/{group_id}） */
export function getDupGroupDetail(groupId) {
  return request.get(`/kb/dup-groups/detail/${groupId}`)
}

/** 标记重复分组处理结果（PATCH /api/v1/kb/dup-groups/status/{group_id}） */
export function updateDupGroupStatus(groupId, status) {
  return request.patch(`/kb/dup-groups/status/${groupId}`, { status })
}
