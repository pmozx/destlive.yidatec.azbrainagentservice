import request from '@/utils/request'

/** 分页查询解析任务（GET /api/v1/kb/parse-jobs/list） */
export function getParseJobList(params) {
  return request.get('/kb/parse-jobs/list', { params })
}

/** 查询解析任务详情（GET /api/v1/kb/parse-jobs/detail/{job_id}） */
export function getParseJobDetail(jobId) {
  return request.get(`/kb/parse-jobs/detail/${jobId}`)
}
