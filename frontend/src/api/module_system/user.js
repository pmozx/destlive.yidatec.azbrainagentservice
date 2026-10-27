import request from '@/utils/request'

/** 分页查询用户（GET /system/users/list） */
export function getUserList(params) {
  return request.get('/system/users/list', { params })
}

/** 查询用户详情（GET /system/users/detail/{id}） */
export function getUserDetail(id) {
  return request.get(`/system/users/detail/${id}`)
}

/** 创建用户（POST /system/users/create） */
export function createUser(data) {
  return request.post('/system/users/create', data)
}

/** 修改用户（PUT /system/users/update/{id}） */
export function updateUser(id, data) {
  return request.put(`/system/users/update/${id}`, data)
}

/** 批量删除用户（DELETE /system/users/delete，body 为 ID 列表） */
export function deleteUser(ids) {
  return request.delete('/system/users/delete', { data: ids })
}
