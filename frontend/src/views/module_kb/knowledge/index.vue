<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识维护</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/knowledge：维护知识条目的标题、分类、标签与维护人，并查看契约链路写入的版本快照与知识切片
        </p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" size="small" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新增知识</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="kb-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="发布单元">
            <el-input v-model="query.publication_id" placeholder="模糊匹配" clearable style="width: 160px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 160px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="分类">
            <el-input v-model="query.category" placeholder="精确匹配" clearable style="width: 120px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="维护人">
            <el-input v-model="query.owner" placeholder="模糊匹配" clearable style="width: 120px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" prop="publication_id" label="发布单元" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="category" label="分类" width="110" show-overflow-tooltip />
        <el-table-column align="center" label="类型" width="90">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="标签" min-width="150">
          <template #default="{ row }">
            <el-tag v-for="tag in row.tags || []" :key="tag" size="small" effect="plain" class="kb-tag">{{ tag }}</el-tag>
            <span v-if="!(row.tags || []).length" class="kb-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column align="center" label="当前版本" width="100">
          <template #default="{ row }">v{{ row.current_revision ?? 0 }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="owner" label="维护人" width="110" show-overflow-tooltip />
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button
              v-if="row.status === 'active'"
              link
              type="warning"
              size="small"
              @click="changeStatus(row, 'inactive')"
            >
              停用
            </el-button>
            <el-button v-else-if="row.status === 'inactive'" link type="success" size="small" @click="changeStatus(row, 'active')">
              启用
            </el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无知识条目，点击右上角新增" :image-size="80" />
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

    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新增知识条目' : '编辑知识条目'" width="640px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" size="small" @submit.prevent>
        <el-form-item label="发布单元" prop="publication_id">
          <el-input v-model="form.publication_id" :disabled="dialogMode === 'edit'" placeholder="全局唯一的发布单元 ID" />
        </el-form-item>
        <el-form-item label="知识类型" prop="resource_type">
          <el-select v-model="form.resource_type" style="width: 100%">
            <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="请输入知识标题" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" maxlength="64" placeholder="如：it / hr / 产品" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入后回车创建标签"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="维护人">
          <el-input v-model="form.owner" maxlength="64" placeholder="负责人" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="form.summary" type="textarea" :rows="3" placeholder="内容摘要" />
        </el-form-item>
        <el-form-item label="租户 ID">
          <el-input v-model="form.tenant_id" maxlength="64" placeholder="多租户归属，可留空" />
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="form.resource_id" maxlength="64" placeholder="原始资源 ID，可留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">确定</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="知识条目详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="标题" :span="2">{{ detail.title }}</el-descriptions-item>
            <el-descriptions-item label="发布单元">{{ detail.publication_id }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="知识类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="分类">{{ detail.category || '-' }}</el-descriptions-item>
            <el-descriptions-item label="维护人">{{ detail.owner || '-' }}</el-descriptions-item>
            <el-descriptions-item label="租户 ID">{{ detail.tenant_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="资源 ID">{{ detail.resource_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前记录">{{ detail.current_record_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前版本">v{{ detail.current_revision ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="版本数">{{ detail.version_count }}</el-descriptions-item>
            <el-descriptions-item label="切片数">{{ detail.chunk_count }}</el-descriptions-item>
            <el-descriptions-item label="同步时间">{{ formatDateTime(detail.last_synced_at) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="标签" :span="2">
              <el-tag v-for="tag in detail.tags || []" :key="tag" size="small" effect="plain" class="kb-tag">{{ tag }}</el-tag>
              <span v-if="!(detail.tags || []).length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="摘要" :span="2">{{ detail.summary || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="版本快照" name="versions">
              <el-table v-loading="versions.loading.value" :data="versions.list.value" size="small" style="width: 100%">
                <el-table-column align="center" prop="revision" label="版本号" width="90">
                  <template #default="{ row }">v{{ row.revision }}</template>
                </el-table-column>
                <el-table-column align="center" prop="record_id" label="记录 ID" min-width="150" show-overflow-tooltip />
                <el-table-column align="center" prop="version_id" label="契约版本" min-width="130" show-overflow-tooltip />
                <el-table-column align="center" prop="chunk_count" label="切片数" width="90" />
                <el-table-column align="center" label="可检索" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.searchable ? 'success' : 'info'" size="small" effect="light">
                      {{ row.searchable ? '是' : '否' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" prop="status" label="状态" width="90" />
                <el-table-column align="center" label="创建时间" width="165">
                  <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="该条目还没有版本快照（等待契约写入）" :image-size="70" />
                </template>
              </el-table>
              <div class="kb-pagination">
                <el-pagination
                  :current-page="versions.pagination.page_no"
                  :page-size="versions.pagination.page_size"
                  :total="versions.total.value"
                  layout="total, prev, pager, next"
                  background
                  size="small"
                  @current-change="versions.handlePageChange"
                />
              </div>
            </el-tab-pane>

            <el-tab-pane label="知识切片" name="chunks">
              <el-table v-loading="chunks.loading.value" :data="chunks.list.value" size="small" style="width: 100%">
                <el-table-column align="center" prop="seq" label="序号" width="70" />
                <el-table-column align="center" prop="heading" label="标题路径" min-width="160" show-overflow-tooltip />
                <el-table-column align="center" prop="content" label="正文" min-width="280" show-overflow-tooltip />
                <el-table-column align="center" prop="source_type" label="来源" width="90" />
                <el-table-column align="center" label="页范围" width="100">
                  <template #default="{ row }">
                    {{ row.page_from || row.page_to ? `${row.page_from ?? '-'} ~ ${row.page_to ?? '-'}` : '-' }}
                  </template>
                </el-table-column>
                <el-table-column align="center" prop="status" label="状态" width="90" />
                <template #empty>
                  <el-empty description="该条目还没有切片（等待契约写入）" :image-size="70" />
                </template>
              </el-table>
              <div class="kb-pagination">
                <el-pagination
                  :current-page="chunks.pagination.page_no"
                  :page-size="chunks.pagination.page_size"
                  :total="chunks.total.value"
                  layout="total, prev, pager, next"
                  background
                  size="small"
                  @current-change="chunks.handlePageChange"
                />
              </div>
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="未获取到知识条目详情" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createKnowledge,
  deleteKnowledge,
  getKnowledgeChunks,
  getKnowledgeDetail,
  getKnowledgeList,
  getKnowledgeVersions,
  updateKnowledge,
  updateKnowledgeStatus,
} from '@/api/module_kb/knowledge'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbKnowledgeView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const statusOptions = [
  { value: 'active', label: '生效' },
  { value: 'inactive', label: '停用' },
  { value: 'deleted', label: '已删除' },
]
const STATUS_TAG = { active: 'success', inactive: 'warning', deleted: 'info' }

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['publication_id', 'title', 'category', 'resource_type', 'status', 'owner'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getKnowledgeList(buildQueryParams(params)),
  {
    defaultQuery: {
      publication_id: '',
      title: '',
      category: '',
      resource_type: '',
      status: '',
      owner: '',
    },
  },
)

// ── 新增 / 编辑 ────────────────────────────────────────────────────
const formRef = ref()
const dialogVisible = ref(false)
const dialogMode = ref('create')
const submitting = ref(false)
const form = reactive({
  publication_id: '',
  resource_type: 'document',
  tenant_id: '',
  resource_id: '',
  title: '',
  category: '',
  summary: '',
  tags: [],
  owner: '',
})

const rules = {
  publication_id: [
    { required: true, message: '请输入发布单元 ID', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_.:-]+$/, message: '仅支持字母、数字与 _ . : -', trigger: 'blur' },
  ],
  resource_type: [{ required: true, message: '请选择知识类型', trigger: 'change' }],
  title: [
    { required: true, message: '请输入标题', trigger: 'blur' },
    { max: 200, message: '标题不能超过 200 个字符', trigger: 'blur' },
  ],
}

function resetForm() {
  Object.assign(form, {
    publication_id: '',
    resource_type: 'document',
    tenant_id: '',
    resource_id: '',
    title: '',
    category: '',
    summary: '',
    tags: [],
    owner: '',
  })
  formRef.value?.clearValidate?.()
}

function openCreate() {
  dialogMode.value = 'create'
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  Object.assign(form, {
    publication_id: row.publication_id,
    resource_type: row.resource_type || 'document',
    tenant_id: row.tenant_id || '',
    resource_id: row.resource_id || '',
    title: row.title || '',
    category: row.category || '',
    summary: row.summary || '',
    tags: [...(row.tags || [])],
    owner: row.owner || '',
  })
  dialogVisible.value = true
}

function buildPayload() {
  return {
    resource_type: form.resource_type,
    tenant_id: form.tenant_id?.trim() || null,
    resource_id: form.resource_id?.trim() || null,
    title: form.title.trim(),
    category: form.category?.trim() || null,
    summary: form.summary?.trim() || null,
    tags: form.tags,
    owner: form.owner?.trim() || null,
  }
}

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    if (dialogMode.value === 'create') {
      await createKnowledge({ ...buildPayload(), publication_id: form.publication_id.trim() })
      ElMessage.success('新增知识条目成功')
    } else {
      await updateKnowledge(form.publication_id, buildPayload())
      ElMessage.success('编辑知识条目成功')
    }
    dialogVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    submitting.value = false
  }
}

// ── 状态 / 删除 ────────────────────────────────────────────────────
async function changeStatus(row, status) {
  const action = status === 'active' ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(`确定${action}知识条目「${row.title}」吗？`, `${action}确认`, { type: 'warning' })
  } catch {
    return
  }
  try {
    await updateKnowledgeStatus(row.publication_id, status)
    ElMessage.success(`${action}成功`)
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除知识条目「${row.title}」吗？将同时删除版本快照与切片，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteKnowledge(row.publication_id)
    ElMessage.success('删除成功')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

// ── 详情（含版本 / 切片） ──────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('versions')
const currentPublicationId = ref('')

const versions = useTable(
  (params) => getKnowledgeVersions(currentPublicationId.value, { page_no: params.page_no, page_size: params.page_size }),
  { pageSize: 5, immediate: false },
)
const chunks = useTable(
  (params) => getKnowledgeChunks(currentPublicationId.value, { page_no: params.page_no, page_size: params.page_size }),
  { pageSize: 5, immediate: false },
)

async function openDetail(row) {
  currentPublicationId.value = row.publication_id
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'versions'
  try {
    detail.value = await getKnowledgeDetail(row.publication_id)
    versions.load()
    chunks.load()
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

.kb-tag {
  margin: 0 4px 4px 0;
}

.kb-muted {
  color: var(--app-text-muted);
}

.kb-detail__tabs {
  margin-top: 16px;
}
</style>
