import request from '@/utils/request'

/** 分页查询模型配置（GET /api/v1/system/model-config/list） */
export function getModelConfigList(params) {
  return request.get('/system/model-config/list', { params })
}

/** 模型配置详情（GET /api/v1/system/model-config/detail/{id}，密钥只回显掩码） */
export function getModelConfigDetail(id) {
  return request.get(`/system/model-config/detail/${id}`)
}

/** 新增模型配置（POST /api/v1/system/model-config/create） */
export function createModelConfig(data) {
  return request.post('/system/model-config/create', data)
}

/** 更新模型配置（PUT /api/v1/system/model-config/update/{id}，api_key 传空串表示沿用原值） */
export function updateModelConfig(id, data) {
  return request.put(`/system/model-config/update/${id}`, data)
}

/** 批量删除模型配置（DELETE /api/v1/system/model-config/delete，默认模型不允许删除） */
export function deleteModelConfig(ids) {
  return request.delete('/system/model-config/delete', { data: { ids } })
}

/**
 * 连通性测试（POST /api/v1/system/model-config/test/{id}）
 * 后端预算 30s，这里放宽到 45s：默认 15s 会在慢模型上先超时，页面只能看到「请求超时」
 */
export function testModelConfig(id) {
  return request.post(`/system/model-config/test/${id}`, null, { timeout: 45000 })
}
