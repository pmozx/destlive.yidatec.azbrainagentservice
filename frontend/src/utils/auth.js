import { StorageKey } from '@/enums'

/** 读取 token */
export function getToken() {
  return localStorage.getItem(StorageKey.TOKEN) || ''
}

/** 写入 token */
export function setToken(token) {
  localStorage.setItem(StorageKey.TOKEN, token)
}

/** 清除 token */
export function removeToken() {
  localStorage.removeItem(StorageKey.TOKEN)
}

/** 读取本地缓存的用户信息 */
export function getCachedUser() {
  try {
    return JSON.parse(localStorage.getItem(StorageKey.USER_INFO) || 'null')
  } catch {
    return null
  }
}

/** 缓存用户信息 */
export function setCachedUser(user) {
  localStorage.setItem(StorageKey.USER_INFO, JSON.stringify(user))
}

/** 清除用户信息 */
export function removeCachedUser() {
  localStorage.removeItem(StorageKey.USER_INFO)
}
