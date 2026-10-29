import request from '@/utils/request'

/**
 * 集成-知识库-解析任务接口（/api/v1/integration-kb/task）
 *
 * 任务表是解析过程的台账：上传/重新解析时投递，worker 独立会话回写状态，
 * 文档本身也带 status/parse_progress，两处口径一致。
 */

/** 分页查询解析任务 */
export function getTaskList(params) {
  return request.get('/integration-kb/task/list', { params })
}

/** 查询解析任务详情（含结果摘要与失败原因） */
export function getTaskDetail(id) {
  return request.get(`/integration-kb/task/detail/${id}`)
}

/** 重试失败节点（复用解析产物与分片快照，只补跑失败节点及其之后的部分） */
export function retryTask(id) {
  return request.post(`/integration-kb/task/retry/${id}`)
}

/** 批量删除任务记录（只删台账，不影响已解析文档） */
export function deleteTasks(ids) {
  return request.delete('/integration-kb/task/delete', { data: ids })
}
