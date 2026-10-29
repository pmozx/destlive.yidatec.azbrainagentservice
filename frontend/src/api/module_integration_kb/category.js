import request from '@/utils/request'

/**
 * 集成-知识库-知识分类接口（/api/v1/integration-kb/category）
 *
 * 分类固定两级（一级 + 子级），删除父级时子级自动上提、文档置为未分类。
 */

/** 查询知识分类树（按空间过滤；不传 space_id 返回全部空间的两级树） */
export function getCategoryTree(params) {
  return request.get('/integration-kb/category/tree', { params })
}

/** 创建知识分类 */
export function createCategory(data) {
  return request.post('/integration-kb/category/create', data)
}

/** 修改知识分类 */
export function updateCategory(id, data) {
  return request.put(`/integration-kb/category/update/${id}`, data)
}

/** 批量删除知识分类 */
export function deleteCategory(ids) {
  return request.delete('/integration-kb/category/delete', { data: ids })
}
