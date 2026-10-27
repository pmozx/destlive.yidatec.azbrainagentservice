import { onMounted, reactive, ref } from 'vue'

/**
 * 列表页通用逻辑：分页、加载态、查询条件、重置
 *
 * @param {(params: object) => Promise<PageResult>} fetcher 数据请求函数
 * @param {{ pageSize?: number, defaultQuery?: object, immediate?: boolean }} options 配置
 */
export function useTable(fetcher, options = {}) {
  const { pageSize = 10, defaultQuery = {}, immediate = true } = options

  const loading = ref(false)
  const list = ref([])
  const total = ref(0)
  const pagination = reactive({ page_no: 1, page_size: pageSize })
  const query = reactive({ ...defaultQuery })

  async function load() {
    loading.value = true
    try {
      const result = await fetcher({ ...pagination, ...query })
      list.value = result?.items ?? []
      total.value = result?.total ?? 0
      // 删除最后一页数据后回退一页，避免停留在空白页
      if (!list.value.length && total.value > 0 && pagination.page_no > 1) {
        pagination.page_no -= 1
        await load()
      }
    } catch (error) {
      list.value = []
      total.value = 0
      console.warn('[useTable] 数据加载失败', error)
    } finally {
      loading.value = false
    }
  }

  /** 条件变更后从第一页重新查询 */
  function search() {
    pagination.page_no = 1
    return load()
  }

  /** 重置查询条件 */
  function reset() {
    Object.keys(query).forEach((key) => {
      query[key] = defaultQuery[key] ?? ''
    })
    return search()
  }

  function handlePageChange(page) {
    pagination.page_no = page
    load()
  }

  function handleSizeChange(size) {
    pagination.page_size = size
    pagination.page_no = 1
    load()
  }

  if (immediate) onMounted(load)

  return { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange }
}
