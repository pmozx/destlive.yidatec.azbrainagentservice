import request from '@/utils/request'

/** 分页查询解析任务（GET /api/v1/kb/parse-jobs/list） */
export function getParseJobList(params) {
  return request.get('/kb/parse-jobs/list', { params })
}

/** 查询解析任务详情（GET /api/v1/kb/parse-jobs/detail/{job_id}） */
export function getParseJobDetail(jobId) {
  return request.get(`/kb/parse-jobs/detail/${jobId}`)
}

/**
 * 上传文件并提交解析（POST /api/v1/kb/parse-jobs/upload，multipart）
 * data 须为 FormData：拦截器会清掉实例默认的 Content-Type，交给浏览器补 boundary
 */
export function submitParseJob(data) {
  // 上传 + 落原件走同一个请求，默认 15s 对大文件偏紧，单独放宽超时
  return request.post('/kb/parse-jobs/upload', data, { timeout: 120000 })
}
