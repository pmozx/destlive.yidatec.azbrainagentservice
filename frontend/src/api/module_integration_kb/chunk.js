import request from '@/utils/request'

/**
 * 集成-知识库-知识分片接口（/api/v1/integration-kb/chunk）
 *
 * 分片是向量化与检索的最小单位，由解析流水线写入；这里只做查询与人工清理。
 */

/** 分页查询知识分片 */
export function getChunkList(params) {
  return request.get('/integration-kb/chunk/list', { params })
}

/** 批量删除知识分片（人工清理脏数据，删除后需重新解析文档补齐） */
export function deleteChunks(ids) {
  return request.delete('/integration-kb/chunk/delete', { data: ids })
}
