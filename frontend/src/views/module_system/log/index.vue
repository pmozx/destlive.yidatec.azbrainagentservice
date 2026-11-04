<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">日志审计</h2>
        <p class="page-header__desc">对接后端 /api/v1/system/api-log 接口，查看写操作的请求留痕、响应摘要与耗时</p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" @click="openClean">
          <el-icon><Delete /></el-icon>
          <span>清理过期日志</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="log-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="操作人">
            <el-input v-model="query.username" placeholder="模糊匹配" clearable @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="请求路径">
            <el-input v-model="query.request_path" placeholder="模糊匹配" clearable @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="请求方式">
            <el-select v-model="query.request_method" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="method in methodOptions" :key="method" :label="method" :value="method" />
            </el-select>
          </el-form-item>
          <el-form-item label="操作状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
            <el-button type="danger" plain :disabled="!selectedIds.length" @click="removeSelected">
              删除选中<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table
        v-loading="loading"
        :data="list"
        size="small"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="username" label="操作人" width="120" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'danger' : 'success'" size="small" effect="light">
              {{ row.status === 1 ? '失败' : '成功' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="方法" width="90">
          <template #default="{ row }">
            <span class="log-method" :class="`is-${(row.request_method || '').toLowerCase()}`">{{ row.request_method }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="request_path" label="请求路径" min-width="220" show-overflow-tooltip />
        <el-table-column prop="response_code" label="状态码" width="90" />
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">{{ row.process_time ? `${row.process_time} ms` : '-' }}</template>
        </el-table-column>
        <el-table-column prop="request_ip" label="来源 IP" width="140" show-overflow-tooltip />
        <el-table-column label="操作时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="danger" size="small" @click="removeRows([row.id], `日志 #${row.id}`)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无审计记录，写操作（POST/PUT/PATCH/DELETE）会自动入库" :image-size="80" />
        </template>
      </el-table>

      <div class="log-pagination">
        <el-pagination
          :current-page="pagination.page_no"
          :page-size="pagination.page_size"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </SectionCard>

    <el-dialog v-model="detailVisible" title="操作详情" width="860px">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="ID">{{ detail.id }}</el-descriptions-item>
            <el-descriptions-item label="操作人">{{ detail.username || '-' }}</el-descriptions-item>
            <el-descriptions-item label="操作状态">
              <el-tag :type="detail.status === 1 ? 'danger' : 'success'" size="small" effect="light">
                {{ detail.status === 1 ? '失败' : '成功' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="状态码">{{ detail.response_code }}</el-descriptions-item>
            <el-descriptions-item label="请求方式">{{ detail.request_method }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ detail.process_time ? `${detail.process_time} ms` : '-' }}</el-descriptions-item>
            <el-descriptions-item label="请求路径" :span="2">{{ detail.request_path }}</el-descriptions-item>
            <el-descriptions-item label="查询字符串" :span="2">{{ detail.request_query || '-' }}</el-descriptions-item>
            <el-descriptions-item label="来源 IP">{{ detail.request_ip || '-' }}</el-descriptions-item>
            <el-descriptions-item label="操作时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="浏览器 UA" :span="2">{{ detail.user_agent || '-' }}</el-descriptions-item>
            <el-descriptions-item v-if="detail.description" label="异常摘要" :span="2">
              {{ detail.description }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="log-detail__block">
            <JsonViewer title="请求参数（JSON）" :data="detail.request_payload" max-height="300px" placeholder="无请求体（GET/HEAD 或空载荷）" />
          </div>
          <div class="log-detail__block">
            <JsonViewer title="响应报文（JSON）" :data="detail.response_json" max-height="300px" placeholder="无响应体（非 JSON 或流式响应不采集）" />
          </div>
        </template>
        <el-empty v-else description="未获取到日志详情" :image-size="80" />
      </div>
    </el-dialog>

    <el-dialog v-model="cleanVisible" title="清理过期日志" width="420px">
      <el-form label-width="90px" size="small" @submit.prevent>
        <el-form-item label="保留天数">
          <el-input-number v-model="cleanDays" :min="1" :max="3650" :step="10" controls-position="right" />
        </el-form-item>
      </el-form>
      <p class="log-clean__tip">早于该天数的审计记录将被物理删除，不可恢复。</p>
      <template #footer>
        <el-button size="small" @click="cleanVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="cleaning" @click="submitClean">确定清理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { cleanOperationLog, deleteOperationLog, getOperationLogDetail, getOperationLogList } from '@/api/module_system/log'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'SystemLogView' })

const methodOptions = ['POST', 'PUT', 'PATCH', 'DELETE']
const statusOptions = [
  { value: 0, label: '成功' },
  { value: 1, label: '失败' },
]

/** 组装查询参数：空值不下发，status 为空时不按状态过滤 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.username) result.username = params.username
  if (params.request_path) result.request_path = params.request_path
  if (params.request_method) result.request_method = params.request_method
  if (params.status === 0 || params.status === 1) result.status = params.status
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getOperationLogList(buildQueryParams(params)),
  { defaultQuery: { username: '', request_path: '', request_method: '', status: '' } },
)

const selectedIds = ref([])

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getOperationLogDetail(row.id)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}

async function removeRows(ids, tip) {
  try {
    await ElMessageBox.confirm(`确定删除${tip}吗？该操作不可撤销。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  try {
    await deleteOperationLog(ids)
    ElMessage.success('删除成功')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

function removeSelected() {
  if (!selectedIds.value.length) return
  removeRows([...selectedIds.value], `选中的 ${selectedIds.value.length} 条日志`)
}

const cleanVisible = ref(false)
const cleaning = ref(false)
const cleanDays = ref(90)

function openClean() {
  cleanVisible.value = true
}

async function submitClean() {
  cleaning.value = true
  try {
    const deleted = await cleanOperationLog(cleanDays.value)
    ElMessage.success(`已清理 ${deleted} 条过期日志`)
    cleanVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    cleaning.value = false
  }
}
</script>

<style scoped lang="scss">
.log-toolbar {
  padding: 20px 26px 4px;
}

.log-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.log-method {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.4px;

  &.is-post {
    color: var(--el-color-primary);
  }

  &.is-put {
    color: #d98c00;
  }

  &.is-patch {
    color: #6b3fa0;
  }

  &.is-delete {
    color: #d64545;
  }
}

.log-detail__block {
  margin-top: 14px;
}

.log-clean__tip {
  margin: 0;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
