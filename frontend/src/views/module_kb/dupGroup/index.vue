<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">重复治理</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/dup-groups：查看重复检测分组、组内成员与合并预览，并标记「合并 / 忽略」治理结论
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
          <el-form-item label="资源类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="治理状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="id" label="分组 ID" width="90" />
        <el-table-column align="center" prop="uuid" label="分组 UUID" min-width="190" show-overflow-tooltip />
        <el-table-column align="center" label="资源类型" width="100">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="治理状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="最高相似度" width="110">
          <template #default="{ row }">{{ similarityText(row.similarity) }}</template>
        </el-table-column>
        <el-table-column align="center" prop="member_count" label="成员数" width="90" />
        <el-table-column align="center" label="创建时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <template v-if="row.status === 'active'">
              <el-button link type="success" size="small" @click="mark(row, 'merged')">标记合并</el-button>
              <el-button link type="warning" size="small" @click="mark(row, 'dismissed')">忽略</el-button>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无重复分组（由重复检测任务物化）" :image-size="80" />
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

    <el-drawer v-model="detailVisible" title="重复分组详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="分组 ID">{{ detail.id }}</el-descriptions-item>
            <el-descriptions-item label="分组 UUID">{{ detail.uuid }}</el-descriptions-item>
            <el-descriptions-item label="资源类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="治理状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="最高相似度">{{ similarityText(detail.similarity) }}</el-descriptions-item>
            <el-descriptions-item label="成员数">{{ detail.member_count }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_time) }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="成员列表" name="members">
              <el-table v-if="memberRows.length" :data="memberRows" size="small" style="width: 100%">
                <el-table-column align="center"
                  v-for="column in memberColumns"
                  :key="column"
                  :label="memberLabel(column)"
                  min-width="130"
                  show-overflow-tooltip
                >
                  <template #default="{ row }">{{ memberCell(row[column]) }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="该分组暂无成员" :image-size="70" />
                </template>
              </el-table>
              <el-empty v-else description="该分组暂无成员" :image-size="70" />
            </el-tab-pane>

            <el-tab-pane label="检测范围" name="scope">
              <JsonViewer :data="detail.scope" title="scope（检测范围）" placeholder="未记录检测范围" />
            </el-tab-pane>

            <el-tab-pane label="合并预览" name="mergedPreview">
              <JsonViewer
                :data="detail.merged_preview"
                title="merged_preview（草稿态，不落正式库）"
                placeholder="未生成合并预览"
                max-height="460px"
              />
            </el-tab-pane>
          </el-tabs>

          <div v-if="detail.status === 'active'" class="kb-detail__actions">
            <el-button type="success" size="small" :loading="marking" @click="mark(detail, 'merged')">标记合并</el-button>
            <el-button type="warning" size="small" :loading="marking" @click="mark(detail, 'dismissed')">忽略该分组</el-button>
          </div>
        </template>
        <el-empty v-else description="未获取到重复分组详情" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getDupGroupDetail, getDupGroupList, updateDupGroupStatus } from '@/api/module_kb/dupGroup'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime, prettyJson } from '@/utils/format'

defineOptions({ name: 'KbDupGroupView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const statusOptions = [
  { value: 'active', label: '待处理' },
  { value: 'merged', label: '已合并' },
  { value: 'dismissed', label: '已忽略' },
]
const STATUS_TAG = { active: 'warning', merged: 'success', dismissed: 'info' }
const MEMBER_LABELS = {
  publication_id: '发布单元',
  record_id: '记录 ID',
  title: '标题',
  summary: '摘要',
  overlap: '切片重叠度',
  chunk_overlap: '切片重叠度',
  similarity: '相似度',
}

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}
function similarityText(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `${(Number(value) * 100).toFixed(1)}%`
}
function memberLabel(key) {
  return MEMBER_LABELS[key] || key
}
function memberCell(value) {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'object') return prettyJson(value)
  return String(value)
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['resource_type', 'status'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getDupGroupList(buildQueryParams(params)),
  {
    defaultQuery: { resource_type: '', status: '' },
  },
)

// ── 详情 ──────────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('members')
const marking = ref(false)

const memberRows = computed(() => (detail.value?.members || []).map((item) => ({ ...item })))
const memberColumns = computed(() => {
  const keys = new Set()
  memberRows.value.forEach((row) => Object.keys(row).forEach((key) => keys.add(key)))
  return [...keys]
})

async function openDetail(row) {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'members'
  await fetchDetail(row.id)
}

async function fetchDetail(groupId) {
  try {
    detail.value = await getDupGroupDetail(groupId)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}

// ── 标记处理 ──────────────────────────────────────────────────────
async function mark(row, status) {
  const action = status === 'merged' ? '标记合并' : '忽略'
  try {
    await ElMessageBox.confirm(
      `确定将分组「${row.uuid}」${action}吗？该操作只改变治理状态，不改写知识内容。`,
      `${action}确认`,
      { type: 'warning' },
    )
  } catch {
    return
  }
  marking.value = true
  try {
    await updateDupGroupStatus(row.id, status)
    ElMessage.success(`${action}成功`)
    load()
    if (detailVisible.value && detail.value?.id === row.id) {
      detailLoading.value = true
      await fetchDetail(row.id)
    }
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    marking.value = false
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

.kb-detail__actions {
  @include flex($justify: flex-end);
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--app-border-color);
}
</style>
