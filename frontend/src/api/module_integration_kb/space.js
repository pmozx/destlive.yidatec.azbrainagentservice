import request from '@/utils/request'

/**
 * 集成-知识库-知识空间接口（/api/v1/integration-kb/space）
 *
 * 空间是知识组织的第一层：空间 → 分类 → 文档 → 分片，问答按空间圈定检索范围。
 */

/** 分页查询知识空间（含文档数统计） */
export function getSpaceList(params) {
  return request.get('/integration-kb/space/list', { params })
}

/** 查询启用空间下拉选项（分类、文档页共用） */
export function getSpaceOptions() {
  return request.get('/integration-kb/space/options')
}

/** 查询知识空间详情 */
export function getSpaceDetail(id) {
  return request.get(`/integration-kb/space/detail/${id}`)
}

/** 新增知识空间（code 创建后不可修改） */
export function createSpace(data) {
  return request.post('/integration-kb/space/create', data)
}

/** 更新知识空间（名称 / 说明 / 密级 / 排序 / 启停） */
export function updateSpace(id, data) {
  return request.put(`/integration-kb/space/update/${id}`, data)
}

/** 批量删除知识空间（级联分类、文档、分片与对象存储） */
export function deleteSpace(ids) {
  return request.delete('/integration-kb/space/delete', { data: ids })
}
