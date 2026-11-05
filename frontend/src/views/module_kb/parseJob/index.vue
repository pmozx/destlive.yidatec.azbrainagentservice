<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">解析任务</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/parse-jobs：页面自助上传并提交解析（与契约 #01 同一条流水线），查看任务状态机、原始请求快照与解析产物
        </p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" size="small" @click="openCreate">
          <el-icon><Upload /></el-icon>
          <span>新建解析</span>
        </el-button>
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
          <el-empty description="暂无解析任务，点击右上角「新建解析」上传文件" :image-size="80" />
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

    <el-dialog v-model="createVisible" title="新建解析" width="620px" destroy-on-close>
      <el-form :model="createForm" label-width="96px" size="small" @submit.prevent>
        <el-form-item label="文件" required>
          <el-upload
            ref="uploadRef"
            v-model:file-list="createFileList"
            class="kb-upload"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.markdown,.csv,.log,.json,.yaml,.yml,.html,.htm"
            :on-exceed="handleFileExceed"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">
                单文件不超过 10MB；PDF/Office 需服务端启用 MinerU 解析器，文本类后缀始终可用
              </div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="资源类型">
          <el-radio-group v-model="createForm.resource_type">
            <el-radio v-for="item in resourceTypeOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="生成切片">
          <el-switch v-model="createForm.chunk" :disabled="createForm.resource_type === 'faq'" />
          <span class="kb-form__tip">
            {{ createForm.resource_type === 'faq' ? 'FAQ 按问答提取，忽略此项' : '按标题层级切分正文' }}
          </span>
        </el-form-item>
        <el-form-item label="FAQ 语言">
          <el-input
            v-model="createForm.language"
            maxlength="16"
            :disabled="createForm.resource_type !== 'faq'"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="FAQ 提示词">
          <el-input
            v-model="createForm.prompt_text"
            type="textarea"
            :rows="3"
            maxlength="4000"
            show-word-limit
            :disabled="createForm.resource_type !== 'faq'"
            placeholder="留空使用默认问答提取提示词"
          />
        </el-form-item>
        <el-form-item label="模型键">
          <el-input v-model="createForm.model_key" maxlength="64" placeholder="留空使用默认模型" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="createVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="creating" @click="submitCreate">提交解析</el-button>
      </template>
    </el-dialog>

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
                  <el-descriptions-item label="页图数量" :span="2">{{ pageItems.length }}</el-descriptions-item>
                </el-descriptions>

                <div class="kb-detail__block">
                  <h3 class="kb-detail__subtitle">页图预览</h3>
                  <div v-if="pageItems.length" class="kb-pages">
                    <div v-for="(page, index) in pageItems" :key="page.page_no" class="kb-pages__item">
                      <el-image
                        :src="page.image_url"
                        :preview-src-list="pageUrls"
                        :initial-index="index"
                        fit="contain"
                        preview-teleported
                        hide-on-click-modal
                        class="kb-pages__img"
                      >
                        <template #error>
                          <div class="kb-pages__error">加载失败或预览地址已过期</div>
                        </template>
                      </el-image>
                      <div class="kb-pages__meta">第 {{ page.page_no }} 页 · {{ page.width }}×{{ page.height }}</div>
                    </div>
                  </div>
                  <el-alert
                    v-else
                    type="info"
                    :closable="false"
                    show-icon
                    title="无页图预览：仅 PDF 且服务启用页图能力时生成，其余文档返回空数组"
                  />
                </div>
                <JsonViewer :data="detail.result.suggested" title="suggested（AI 建议元数据）" placeholder="无建议元数据" class="kb-detail__block" />
                <JsonViewer :data="detail.result.warnings" title="warnings（解析告警）" placeholder="无解析告警" class="kb-detail__block" />
                <JsonViewer :data="detail.result.chunks" title="chunks（切片数组）" placeholder="无切片产物" max-height="460px" class="kb-detail__block" />
              </template>
              <el-empty v-else description="任务尚未产出解析结果" :image-size="80" />
            </el-tab-pane>

            <el-tab-pane label="关联发布单元" name="publications">
              <el-alert :title="relatedHint" type="info" :closable="false" show-icon />
              <el-table
                v-loading="related.loading"
                :data="related.list"
                size="small"
                style="width: 100%"
                class="kb-detail__block"
              >
                <el-table-column align="center" prop="publication_id" label="发布单元" min-width="170" show-overflow-tooltip />
                <el-table-column align="center" prop="title" label="标题" min-width="180" show-overflow-tooltip />
                <el-table-column align="center" prop="resource_id" label="资源 ID" min-width="140" show-overflow-tooltip />
                <el-table-column align="center" label="状态" width="90">
                  <template #default="{ row }">{{ row.status || '-' }}</template>
                </el-table-column>
                <el-table-column align="center" label="当前版本" width="90">
                  <template #default="{ row }">v{{ row.current_revision ?? 0 }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="没有按 doc_id 匹配到发布单元" :image-size="70" />
                </template>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="未获取到解析任务详情" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, genFileId } from 'element-plus'

import { getPublicationList } from '@/api/module_kb/publication'
import { getParseJobDetail, getParseJobList, submitParseJob } from '@/api/module_kb/parseJob'
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

// ── 新建解析（管理端自助上传） ─────────────────────────────────────
const createVisible = ref(false)
const creating = ref(false)
const uploadRef = ref()
const createFileList = ref([])
const createForm = reactive({
  resource_type: 'document',
  chunk: true,
  language: 'zh-CN',
  prompt_text: '',
  model_key: '',
})

function openCreate() {
  createFileList.value = []
  Object.assign(createForm, {
    resource_type: 'document',
    chunk: true,
    language: 'zh-CN',
    prompt_text: '',
    model_key: '',
  })
  createVisible.value = true
}

/** 只收单文件：超出时用新选中的文件替换已选文件（el-upload 默认会忽略第二次选择） */
function handleFileExceed(files) {
  uploadRef.value?.clearFiles()
  const file = files[0]
  file.uid = genFileId()
  uploadRef.value?.handleStart(file)
}

/** multipart 提交：文件 + 解析选项；解析在响应后由后台任务执行，结果看列表状态 */
async function submitCreate() {
  const file = createFileList.value[0]?.raw
  if (!file) {
    ElMessage.warning('请先选择待解析文件')
    return
  }
  const data = new FormData()
  data.append('file', file)
  data.append('resource_type', createForm.resource_type)
  data.append('chunk', createForm.chunk ? 'true' : 'false')
  data.append('language', createForm.language || 'zh-CN')
  if (createForm.prompt_text) data.append('prompt_text', createForm.prompt_text)
  if (createForm.model_key) data.append('model_key', createForm.model_key)

  creating.value = true
  try {
    const job = await submitParseJob(data)
    ElMessage.success(`解析任务已受理${job?.uuid ? `：${job.uuid}` : ''}`)
    createVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    creating.value = false
  }
}

// ── 详情 ──────────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('fileMeta')

/** 页图预览数组：只保留带 image_url 的项，供缩略图与放大预览共用 */
const pageItems = computed(() => (detail.value?.result?.pages || []).filter((page) => page?.image_url))
const pageUrls = computed(() => pageItems.value.map((page) => page.image_url))

// ── 关联发布单元（反向溯源：用 file_meta.doc_id 比对 publication.resource_id）───
// 声明顺序：必须在 activeTab 之后（watch 在 setup 阶段立即读取它），且在 openDetail 之前
const related = reactive({
  loading: false,
  data: null,
  list: [],
})

/** 契约任务才有 doc_id；管理台上传的任务没有这个字段，因此不存在可匹配的发布单元 */
const relatedHint = computed(() => {
  const docId = detail.value?.file_meta?.doc_id
  return docId
    ? `按 doc_id=${docId} 匹配发布单元的 resource_id：线索，不构成强关联`
    : '该任务没有 doc_id（管理台上传），不参与发布单元匹配'
})

async function loadRelated() {
  const docId = detail.value?.file_meta?.doc_id
  if (!docId) {
    related.list = []
    return
  }
  related.loading = true
  try {
    const result = await getPublicationList({ resource_id: docId, page_size: 20 })
    related.data = result
    related.list = result.items || []
  } catch {
    related.list = []
  } finally {
    related.loading = false
  }
}

watch(activeTab, (name) => {
  if (name === 'publications' && related.data === null) {
    loadRelated()
  }
})

async function openDetail(row) {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'fileMeta'
  related.data = null
  related.list = []
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

.kb-upload {
  width: 100%;

  :deep(.el-upload) {
    width: 100%;
  }
}

.kb-form__tip {
  margin-left: 10px;
  font-size: 12px;
  color: var(--app-text-secondary);
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

.kb-detail__subtitle {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.kb-pages {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;

  &__item {
    padding: 8px;
    border: 1px solid var(--app-border-color);
    border-radius: 8px;
    background: var(--app-bg);
  }

  &__img {
    display: block;
    width: 100%;
    height: 180px;
    cursor: zoom-in;
    border-radius: 4px;
  }

  &__meta {
    margin-top: 6px;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-align: center;
  }

  &__error {
    @include flex($justify: center);
    height: 100%;
    padding: 0 8px;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-align: center;
  }
}
</style>
