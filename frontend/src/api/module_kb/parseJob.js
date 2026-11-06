import request from '@/utils/request'

/** 分页查询解析任务（GET /api/v1/kb/parse-jobs/list） */
export function getParseJobList(params) {
  return request.get('/kb/parse-jobs/list', { params })
}

/** 查询解析任务详情（GET /api/v1/kb/parse-jobs/detail/{job_id}） */
export function getParseJobDetail(jobId) {
  return request.get(`/kb/parse-jobs/detail/${jobId}`)
}

/** 查询流水线步骤台账（GET /api/v1/kb/parse-jobs/steps/{job_id}，含历史执行轮次） */
export function getParseJobSteps(jobId) {
  return request.get(`/kb/parse-jobs/steps/${jobId}`)
}

/**
 * 从失败步骤重跑（POST /api/v1/kb/parse-jobs/retry/{job_id}）
 * data: { from_step? }；缺省取上一轮失败的那一步，之前的步骤复用台账快照不重新解析
 */
export function retryParseJob(jobId, data = {}) {
  return request.post(`/kb/parse-jobs/retry/${jobId}`, data, { timeout: 60000 })
}

/**
 * 编辑解析产物里的单条切片（PUT /api/v1/kb/parse-jobs/update-chunk/{job_id}）
 * data: { seq, content }；只覆盖该条正文并打 edited 标记，不重算产物哈希
 */
export function updateParseChunk(jobId, data) {
  return request.put(`/kb/parse-jobs/update-chunk/${jobId}`, data)
}

/**
 * 把解析产物（含人工修订）的切片集合写成一份正式知识（POST /api/v1/kb/parse-jobs/publish/{job_id}）
 * data: { publication_id?, title?, tenant_id?, resource_id?, category?, summary?, tags?, owner?, knowledge_no? }
 * 切片取自服务端产物，不在前端重传；同一产物只能写一次，二次提交返回 409
 */
export function publishParseChunks(jobId, data) {
  return request.post(`/kb/parse-jobs/publish/${jobId}`, data)
}

/**
 * 上传文件并提交解析（POST /api/v1/kb/parse-jobs/upload，multipart）
 * data 须为 FormData：拦截器会清掉实例默认的 Content-Type，交给浏览器补 boundary
 */
export function submitParseJob(data) {
  // 上传 + 落原件走同一个请求，默认 15s 对大文件偏紧，单独放宽超时
  return request.post('/kb/parse-jobs/upload', data, { timeout: 120000 })
}
