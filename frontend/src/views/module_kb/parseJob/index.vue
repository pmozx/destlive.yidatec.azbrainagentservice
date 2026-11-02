<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">解析任务</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/parse-jobs：查看契约链路写入的解析任务状态机、原始请求快照与解析产物（只读视图）
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="kb-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="任务 ID">
            <el-input v-model="query.uuid" placeholder="模糊匹配" clearable style="width: 190px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="资源类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="任务状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="失败码">
            <el-input v-model="query.error_code" placeholder="精确匹配" clearable style="width: 130px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="uuid" label="任务 ID" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" label="资源类型" width="100">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="进度" min-width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress ?? 0"
              :status="progressStatus(row)"
              :stroke-width="10"
              size="small"
            />
          </template>
        </el-table-column>
        <el-table-column align="center" prop="error_code" label="失败码" width="140" show-overflow-tooltip />
        <el-table-column align="center" prop="error_message" label="失败说明" min-width="180" show-overflow-tooltip />
        <el-table-column align="center" label="创建时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无解析任务（由契约链路提交后生成）" :image-size="80" />
        </template>
      </el-table>

      <div class="kb-pagination">
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

    <el-drawer v-model="detailVisible" title="解析任务详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="任务 ID" :span="2">{{ detail.uuid }}</el-descriptions-item>
            <el-descriptions-item label="资源类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="进度">
              <el-progress
                :percentage="detail.progress ?? 0"
                :status="progressStatus(detail)"
                :stroke-width="10"
                size="small"
                style="width: 100%"
              />
            </el-descriptions-item>
            <el-descriptions-item label="失败码">{{ detail.error_code || '-' }}</el-descriptions-item>
            <el-descriptions-item label="失败说明" :span="2">{{ detail.error_message || '-' }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_time) }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="请求快照" name="fileMeta">
              <JsonViewer :data="detail.file_meta" title="file_meta（原始请求快照）" placeholder="任务未写入请求快照" />
            </el-tab-pane>

            <el-tab-pane label="解析产物" name="result">
              <template v-if="detail.result">
                <el-descriptions :column="2" border size="small" class="kb-detail__block">
                  <el-descriptions-item label="关联任务">{{ detail.result.job_id }}</el-descriptions-item>
                  <el-descriptions-item label="产物哈希">{{ detail.result.content_hash }}</el-descriptions-item>
                  <el-descriptions-item label="写入时间">{{ formatDateTime(detail.result.created_time) }}</el-descriptions-item>
                  <el-descriptions-item label="切片数量">{{ (detail.result.chunks || []).length }}</el-descriptions-item>
                </el-descriptions>
                <JsonViewer :data="detail.result.suggested" title="suggested（AI 建议元数据）" placeholder="无建议元数据" class="kb-detail__block" />
                <JsonViewer :data="detail.result.warnings" title="warnings（解析告警）" placeholder="无解析告警" class="kb-detail__block" />
                <JsonViewer :data="detail.result.chunks" title="chunks（切片数组）" placeholder="无切片产物" max-height="460px" class="kb-detail__block" />
              </template>
              <el-empty v-else description="任务尚未产出解析结果" :image-size="80" />
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="未获取到解析任务详情" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref } from 'vue'

import { getParseJobDetail, getParseJobList } from '@/api/module_kb/parseJob'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbParseJobView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '解析中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
]
const STATUS_TAG = { queued: 'info', running: 'warning', succeeded: 'success', failed: 'danger' }
const PROGRESS_STATUS = { succeeded: 'success', failed: 'exception' }

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}
function progressStatus(row) {
  return PROGRESS_STATUS[row.status]
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['uuid', 'resource_type', 'status', 'error_code'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getParseJobList(buildQueryParams(params)),
  {
    defaultQuery: { uuid: '', resource_type: '', status: '', error_code: '' },
  },
)

// ── 详情 ──────────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('fileMeta')

async function openDetail(row) {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'fileMeta'
  try {
    detail.value = await getParseJobDetail(row.uuid)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}
</script>

<style scoped lang="scss">
.kb-toolbar {
  padding: 20px 26px 4px;
}

.kb-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.kb-detail__tabs {
  margin-top: 16px;
}

.kb-detail__block {
  margin-top: 12px;
}
</style>
