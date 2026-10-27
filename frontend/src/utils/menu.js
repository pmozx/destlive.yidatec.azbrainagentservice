/**
 * 菜单构建：从路由表推导侧边栏菜单，避免菜单与路由两处维护
 */

/** 拼接父子路由路径 */
function joinPath(basePath, path) {
  if (!path) return basePath
  if (path.startsWith('/')) return path
  return `${basePath}/${path}`.replace(/\/{2,}/g, '/')
}

/**
 * 递归生成菜单树
 * - 过滤 meta.hidden 的路由
 * - 无 title 的父级（如根布局路由）自动拍平成唯一的子节点
 * @param {Array} routes 路由表
 * @param {string} basePath 父级路径
 * @returns {MenuNode[]}
 */
export function buildMenus(routes, basePath = '') {
  return routes
    .filter((route) => !route.meta?.hidden)
    .map((route) => {
      const path = joinPath(basePath, route.path)
      const children = route.children ? buildMenus(route.children, path) : []
      return {
        path,
        title: route.meta?.title || '',
        icon: route.meta?.icon,
        children,
      }
    })
    .map((menu) => {
      if (menu.title) return menu
      if (menu.children?.length === 1) return menu.children[0]
      return null
    })
    .filter(Boolean)
}

/**
 * 生成面包屑数据
 * @param {Array} matched 当前路由的 matched 列表
 */
export function buildBreadcrumb(matched) {
  return matched
    .filter((record) => record.meta?.title)
    .map((record) => ({ path: record.path, title: record.meta.title }))
}
