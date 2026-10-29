<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识文档</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/document：上传只写入对象存储并落库为「待解析」，解析由后台线程池完成
          「下载 → 解析 → 分片 → 向量化 → 写分片表」，产物（Markdown / 版面 JSON / 图片）可直接预览与打包下载；
          下架文档不再参与问答检索，列表按解析状态自动轮询
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" :disabled="!spaceId" @click="openUpload">
          <el-icon><Upload /></el-icon>
          <span>上传文档</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="doc-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="空间">
            <el-select v-model="spaceId" placeholder="全部空间" clearable style="width: 160px" @change="onSpaceChange">
              <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="分类">
            <el-select v-model="query.category_id" placeholder="全部" clearable style="width: 150px">
              <el-option v-for="item in categoryOptions" :key="item.id" :label="item.label" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 160px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 120px">
              <el-option v-for="(meta, value) in STATUS_META" :key="value" :label="meta.label" :value="Number(value)" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>

        <div class="doc-actions">
          <el-button size="small" :disabled="!selectedIds.length" @click="setAvailable(true)">批量上架</el-button>
          <el-button size="small" :disabled="!selectedIds.length" @click="setAvailable(false)">批量下架</el-button>
          <el-button size="small" :loading="packing" :disabled="!selectedIds.length" @click="packDownload">
            <el-icon><Download /></el-icon>
            <span>批量下载</span>
          </el-button>
          <el-button size="small" type="danger" plain :disabled="!selectedIds.length" @click="removeMany">
            删除选中<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
          </el-button>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="list"
        size="small"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="title" label="文档标题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openPreview(row)">{{ row.title || row.file_name }}</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="space_name" label="空间" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.space_name || row.space_id }}</template>
        </el-table-column>
        <el-table-column label="分类" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.category_name || '未分类' }}</template>
        </el-table-column>
        <el-table-column prop="file_type" label="类型" width="80" align="center">
          <template #default="{ row }">{{ (row.file_type || '').replace('.', '') || '-' }}</template>
        </el-table-column>
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column label="解析状态" width="170">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row.status).type" size="small" effect="light">
              {{ statusMeta(row.status).label }}
            </el-tag>
            <span v-if="isRunning(row)" class="doc-progress">{{ row.parse_progress || 0 }}%</span>
            <!-- 当前流水线节点（解析 → 分片 → 向量化 → 写入 pgvector），由后端按最新任务推导 -->
            <div v-if="row.pipeline_stage_label" class="doc-stage">节点：{{ row.pipeline_stage_label }}</div>
          </template>
        </el-table-column>
        <el-table-column label="分片" width="70" align="center">
          <template #default="{ row }">{{ row.chunk_count || '-' }}</template>
        </el-table-column>
        <el-table-column label="引用" width="70" align="center">
          <template #default="{ row }">{{ row.ref_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="参与检索" width="100" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_enabled"
              size="small"
              :disabled="row.status !== 2 || switchingId === row.id"
              @change="(value) => toggleEnabled(row, value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="解析时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.parsed_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
            <el-button link type="primary" size="small" @click="reparse(row)">重解析</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeMany([row])">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="spaceId ? '该空间还没有文档：点右上角「上传文档」' : '还没有文档：先上传材料'" :image-size="80" />
        </template>
      </el-table>

      <div class="doc-pagination">
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

    <!-- 上传 -->
    <el-dialog v-model="uploadVisible" title="上传知识文档" width="640px" destroy-on-close>
      <el-form label-width="80px" size="small">
        <el-form-item label="所属空间" required>
          <el-select v-model="uploadForm.space_id" placeholder="选择空间" style="width: 240px">
            <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属分类">
          <el-select v-model="uploadForm.category_id" placeholder="不选即未分类" clearable style="width: 240px">
            <el-option v-for="item in categoryOptions" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-upload v-model:file-list="uploadFiles" drag multiple :auto-upload="false" :accept="ACCEPT_SUFFIXES">
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">把文件拖到这里，或<em>点击选择</em></div>
        <template #tip>
          <div class="upload-tip">
            一次最多 {{ UPLOAD_MAX_FILES }} 份、单份不超过 {{ UPLOAD_MAX_SIZE_MB }}MB；上传后为「待解析」，
            需在列表里点「重解析」或等待投递解析。同一空间内相同内容（MD5）会被判为重复并拒绝。
            <br />
            已接入后缀：{{ ACCEPT_SUFFIXES }}
          </div>
        </template>
      </el-upload>
      <template #footer>
        <el-button size="small" @click="uploadVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="uploading" @click="submitUpload">
          上传 {{ uploadFiles.length ? `（${uploadFiles.length}）` : '' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑 -->
    <el-dialog v-model="editVisible" title="编辑文档" width="560px" destroy-on-close>
      <el-form :model="editForm" label-width="88px" size="small" @submit.prevent>
        <el-form-item label="标题" required>
          <el-input v-model="editForm.title" maxlength="255" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category_id" placeholder="不选即未分类" clearable style="width: 100%">
            <el-option v-for="item in categoryOptions" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="editForm.keywords" placeholder="逗号分隔，用于检索提示词与引用卡片" maxlength="255" />
        </el-form-item>
        <el-form-item label="来源">
          <el-input v-model="editForm.source" placeholder="如：客服中心 / 官网" maxlength="64" />
        </el-form-item>
        <el-form-item label="版本号">
          <el-input-number v-model="editForm.version" :min="1" :max="9999" size="small" controls-position="right" />
        </el-form-item>
        <el-form-item label="参与检索">
          <el-switch v-model="editForm.is_enabled" />
          <span class="form-hint">关闭后该文档不再被问答召回（需已解析完成）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="editVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 预览：原件 / 解析产物双视图（与知识中心、问答引用共用同一抽屉） -->
    <DocumentPreviewDrawer v-model="previewVisible" :doc-id="previewDocId" :title="previewTitle" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  batchSetDocumentAvailable,
  deleteDocuments,
  downloadDocuments,
  getCategoryTree,
  getDocumentList,
  getSpaceOptions,
  reparseDocument,
  updateDocument,
  uploadDocuments,
} from '@/api'
import DocumentPreviewDrawer from '@/components/common/DocumentPreviewDrawer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'IntegrationKbDocumentView' })

/** 解析状态：与后端 model.DocumentStatus 一一对应 */
const STATUS_META = {
  0: { label: '待解析', type: 'info' },
  1: { label: '解析中', type: 'warning' },
  2: { label: '已完成', type: 'success' },
  3: { label: '解析失败', type: 'danger' },
}

/** 与后端 supported_suffixes() 对齐，仅用于文件选择器过滤；真正的校验在服务端 */
const ACCEPT_SUFFIXES =
  '.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,.txt,.md,.markdown,.csv,.log,.json,.yaml,.yml,.html,.htm'

/** 与后端 SPACE_UPLOAD_* 默认值一致，仅做前端提示，超限由服务端逐份报错 */
const UPLOAD_MAX_FILES = 10
const UPLOAD_MAX_SIZE_MB = 50

/** 轮询间隔：解析是分钟级任务，3s 足够让用户看到状态推进 */
const POLL_INTERVAL = 3000

function statusMeta(value) {
  return STATUS_META[value] ?? { label: `未知(${value})`, type: 'info' }
}

/** 是否仍在流水线中：文档状态与任务推导出的流水线状态任一为「执行中」都继续轮询 */
function isRunning(row) {
  return row.status === 1 || row.pipeline_status === 1
}

function formatSize(bytes) {
  const size = Number(bytes || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

// ── 空间 / 分类选项 ────────────────────────────────────────────────────

const spaces = ref([])
const spaceId = ref(null)
const categoryTree = ref([])

/** 分类下拉：一级分类 + 带缩进的子分类，避免树形控件在窄工具栏里放不下 */
const categoryOptions = computed(() =>
  categoryTree.value.flatMap((parent) => [
    { id: parent.id, label: parent.name },
    ...(parent.children ?? []).map((child) => ({ id: child.id, label: `　└ ${child.name}` })),
  ]),
)

async function fetchSpaces() {
  try {
    spaces.value = (await getSpaceOptions()) ?? []
  } catch {
    spaces.value = []
  }
}

async function fetchCategories() {
  try {
    const params = spaceId.value ? { space_id: spaceId.value } : {}
    categoryTree.value = (await getCategoryTree(params)) ?? []
  } catch {
    categoryTree.value = []
  }
}

/** 切换空间时分类选项与查询条件一起收敛，避免出现跨空间的孤儿分类筛选 */
async function onSpaceChange() {
  query.category_id = ''
  await fetchCategories()
  search()
}

// ── 文档列表 ───────────────────────────────────────────────────────────

/** 空值不下发；status 可能是 0（待解析），不能用真值判断 */
function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (spaceId.value) result.space_id = spaceId.value
  if (params.category_id) result.category_id = params.category_id
  if (params.title) result.title = params.title
  if (params.status !== '' && params.status !== null && params.status !== undefined) result.status = params.status
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getDocumentList(buildParams(params)),
  { defaultQuery: { category_id: '', title: '', status: '' } },
)

const selectedIds = ref([])

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

/** 有「解析中」就轮询，全部结束自动停表 */
let pollTimer = null

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function checkPolling() {
  if (!list.value.some(isRunning)) {
    stopPolling()
    return
  }
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    if (!list.value.some(isRunning)) {
      stopPolling()
      return
    }
    await load()
  }, POLL_INTERVAL)
}

watch(list, checkPolling)
onUnmounted(stopPolling)

// ── 上传 ───────────────────────────────────────────────────────────────

const uploadVisible = ref(false)
const uploadFiles = ref([])
const uploading = ref(false)
const uploadForm = reactive({ space_id: null, category_id: '' })

function openUpload() {
  uploadFiles.value = []
  uploadForm.space_id = spaceId.value || spaces.value[0]?.id || null
  uploadForm.category_id = ''
  uploadVisible.value = true
}

async function submitUpload() {
  const files = uploadFiles.value.map((item) => item.raw).filter(Boolean)
  if (!uploadForm.space_id) {
    ElMessage.warning('请选择所属空间')
    return
  }
  if (!files.length) {
    ElMessage.warning('请先选择要上传的文件')
    return
  }

  uploading.value = true
  try {
    const result = await uploadDocuments(
      uploadForm.space_id,
      files,
      uploadForm.category_id ? uploadForm.category_id : undefined,
    )
    if (result?.success_count) ElMessage.success(`上传成功 ${result.success_count} 份，已置为待解析`)
    if (result?.failed?.length) {
      ElMessage({
        type: 'warning',
        duration: 8000,
        showClose: true,
        message: `以下文件未上传：${result.failed.map((item) => `${item.file_name}（${item.reason}）`).join('；')}`,
      })
    }
    uploadVisible.value = false
    uploadFiles.value = []
    await load()
  } catch {
    // 请求拦截器已统一提示
  } finally {
    uploading.value = false
  }
}

// ── 上下架 / 重解析 / 删除 ─────────────────────────────────────────────

const switchingId = ref(null)

async function toggleEnabled(row, enabled) {
  switchingId.value = row.id
  try {
    await batchSetDocumentAvailable([row.id], enabled)
    row.is_enabled = enabled
    ElMessage.success(enabled ? '已上架（参与检索）' : '已下架（不再参与检索）')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    switchingId.value = null
  }
}

async function setAvailable(enabled) {
  if (!selectedIds.value.length) return
  try {
    await batchSetDocumentAvailable(selectedIds.value, enabled)
    ElMessage.success(`已${enabled ? '上架' : '下架'} ${selectedIds.value.length} 份文档`)
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}

async function reparse(row) {
  try {
    await ElMessageBox.confirm(
      `重新解析「${row.title || row.file_name}」：会覆盖原分片与解析产物，解析期间该文档暂不参与检索。`,
      '重新解析确认',
      { type: 'info', confirmButtonText: '提交解析', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await reparseDocument(row.id)
    ElMessage.success('已提交重新解析，进度见「解析状态」')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}

async function removeMany(rows) {
  const ids = rows?.length ? rows.map((row) => row.id) : selectedIds.value
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${ids.length} 份文档吗？会连带删除其分片、对象存储原件与解析产物，不可撤销。`,
      '删除文档确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteDocuments(ids)
    ElMessage.success('删除成功')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}

// ── 编辑 ───────────────────────────────────────────────────────────────

const editVisible = ref(false)
const editing = ref(false)
const editingId = ref(null)
const editForm = reactive({ title: '', category_id: '', keywords: '', source: '', version: 1, is_enabled: true })

function openEdit(row) {
  editingId.value = row.id
  Object.assign(editForm, {
    title: row.title ?? '',
    category_id: row.category_id ?? '',
    keywords: row.keywords ?? '',
    source: row.source ?? '',
    version: row.version ?? 1,
    is_enabled: row.is_enabled ?? true,
  })
  editVisible.value = true
}

async function submitEdit() {
  if (!editForm.title.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }

  editing.value = true
  try {
    await updateDocument(editingId.value, {
      title: editForm.title,
      // 传 null 表示「移出分类」，空串在 Pydantic 那里过不了 ge=1
      category_id: editForm.category_id || null,
      keywords: editForm.keywords,
      source: editForm.source,
      version: editForm.version,
      is_enabled: editForm.is_enabled,
    })
    ElMessage.success('修改成功')
    editVisible.value = false
    await load()
  } catch {
    // 请求拦截器已统一提示
  } finally {
    editing.value = false
  }
}

// ── 预览 / 下载 ────────────────────────────────────────────────────────

const previewVisible = ref(false)
const previewDocId = ref(null)
const previewTitle = ref('')

/** 预览入口：交给公共抽屉，原件/解析产物在抽屉内切换 */
function openPreview(row) {
  previewDocId.value = row.id
  previewTitle.value = row.title || row.file_name || '文档预览'
  previewVisible.value = true
}

/** 浏览器端触发下载：Blob 必须立刻 revoke，否则大文件会一直占内存 */
function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

const packing = ref(false)

async function packDownload() {
  if (!selectedIds.value.length) return
  packing.value = true
  try {
    const blob = await downloadDocuments({
      ids: selectedIds.value,
      with_source: true,
      with_markdown: true,
      with_json: true,
    })
    saveBlob(blob, `kb-documents-${Date.now()}.zip`)
    ElMessage.success('打包完成，已开始下载')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    packing.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchSpaces(), fetchCategories()])
})
</script>

<style scoped lang="scss">
.doc-toolbar {
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 20px 26px 4px;
}

.doc-actions {
  @include flex($justify: flex-end, $gap: 8px);
  padding-bottom: 8px;
}

.doc-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.doc-progress {
  margin-left: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.doc-stage {
  margin-top: 2px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.upload-icon {
  font-size: 42px;
  color: var(--app-text-secondary);
}

.upload-tip {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-secondary);
}

.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
