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

/**
 * 发起重复检测（POST /api/v1/kb/dup-groups/detect，#17）
 * 范围内全量比对后落 30 分钟快照并物化分组，耗时随范围增长，单独放宽超时
 */
export function detectDupGroups(data) {
  return request.post('/kb/dup-groups/detect', data, { timeout: 120000 })
}

/** 保存前查重（POST /api/v1/kb/dup-groups/check，#16）：只报命中，不落任何正式表 */
export function checkDupGroups(data) {
  return request.post('/kb/dup-groups/check', data, { timeout: 120000 })
}

/** 生成合并预览（POST /api/v1/kb/dup-groups/preview/{group_id}，#19） */
export function previewDupGroup(groupId, data) {
  return request.post(`/kb/dup-groups/preview/${groupId}`, data, { timeout: 120000 })
}
