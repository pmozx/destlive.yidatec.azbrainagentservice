<!-- 知识中心 · 文档面板：按空间/分类联动过滤的文档列表 + 上传/预览/编辑/重解析/上下架/打包下载 -->
<template>
  <SectionCard flush class="kb-doc-panel">
    <template #title>
      <h3 class="kb-panel-title">知识文档</h3>
      <span class="kb-panel-hint">{{ scopeLabel }}</span>
    </template>
    <template #extra>
      <el-button size="small" type="primary" @click="openUpload">
        <el-icon><Upload /></el-icon>
        <span>上传文档</span>
      </el-button>
      <el-button size="small" :disabled="!selectedIds.length" @click="openDownloadDialog(selectedRows)">
        <el-icon><Download /></el-icon>
        <span>批量下载<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template></span>
      </el-button>
    </template>

    <div class="kb-doc-toolbar">
      <el-form :inline="true" :model="query" size="small" @submit.prevent>
        <el-form-item label="标题">
          <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 150px" @keyup.enter="search" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
            <el-option v-for="(meta, value) in STATUS_META" :key="value" :label="meta.label" :value="Number(value)" />
          </el-select>
        </el-form-item>
        <el-form-item label="检索">
          <el-select v-model="query.is_enabled" placeholder="全部" clearable style="width: 100px">
            <el-option label="已上架" :value="true" />
            <el-option label="已下架" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <div class="kb-doc-actions">
        <el-button size="small" :disabled="!selectedIds.length" @click="setAvailable(true)">批量上架</el-button>
        <el-button size="small" :disabled="!selectedIds.length" @click="setAvailable(false)">批量下架</el-button>
        <el-button size="small" type="danger" plain :disabled="!selectedIds.length" @click="removeMany()">
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
      <el-table-column prop="title" label="文档标题" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openPreview(row)">
            {{ row.title || row.file_name }}
          </el-button>
        </template>
      </el-table-column>
      <el-table-column prop="space_name" label="空间" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.space_name || `#${row.space_id}` }}</template>
      </el-table-column>
      <el-table-column label="分类" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.category_name || '未分类' }}</template>
      </el-table-column>
      <el-table-column label="类型" width="72" align="center">
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
          <span v-if="isRunning(row)" class="kb-doc-progress">{{ row.parse_progress || 0 }}%</span>
          <!-- 当前流水线节点（解析 → 分片 → 向量化 → 写入 pgvector），由后端按最新任务推导 -->
          <div v-if="row.pipeline_stage_label" class="kb-doc-stage">节点：{{ row.pipeline_stage_label }}</div>
        </template>
      </el-table-column>
      <el-table-column label="分片" width="70" align="center">
        <template #default="{ row }">{{ row.chunk_count || '-' }}</template>
      </el-table-column>
      <el-table-column label="引用" width="70" align="center">
        <template #default="{ row }">{{ row.ref_count || 0 }}</template>
      </el-table-column>
      <el-table-column label="参与检索" width="96" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.is_enabled"
            size="small"
            :loading="switchingId === row.id"
            :disabled="row.status !== 2 || switchingId === row.id"
            @change="(value) => toggleEnabled(row, value)"
          />
        </template>
      </el-table-column>
      <el-table-column label="解析时间" width="168">
        <template #default="{ row }">{{ formatDateTime(row.parsed_time) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="196" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
          <el-button link type="primary" size="small" :disabled="row.status === 1" @click="reparse(row)">
            重解析
          </el-button>
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" @click="removeMany([row])">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty :description="emptyDescription" :image-size="70" />
      </template>
    </el-table>

    <div class="kb-doc-pagination">
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

    <!-- 上传 -->
    <el-dialog v-model="uploadVisible" title="上传知识文档" width="640px" destroy-on-close>
      <el-form label-width="80px" size="small" @submit.prevent>
        <el-form-item label="所属空间" required>
          <el-select v-model="uploadForm.space_id" placeholder="选择空间" style="width: 240px" @change="onUploadSpaceChange">
            <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属分类">
          <el-select v-model="uploadForm.category_id" placeholder="不选即未分类" clearable style="width: 240px">
            <el-option v-for="item in uploadCategories" :key="item.id" :label="item.label" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-upload v-model:file-list="uploadFiles" drag multiple :auto-upload="false" :accept="ACCEPT_SUFFIXES">
        <el-icon class="kb-upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">把文件拖到这里，或<em>点击选择</em></div>
        <template #tip>
          <div class="kb-upload-tip">
            一次最多 {{ UPLOAD_MAX_FILES }} 份、单份不超过 {{ UPLOAD_MAX_SIZE_MB }}MB；上传后为「待解析」，
            需在列表里点「重解析」或等待投递解析。同一空间内相同内容（MD5）会被判为重复并拒绝。
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
            <el-option v-for="item in editCategories" :key="item.id" :label="item.label" :value="item.id" />
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
          <span class="kb-form-hint">关闭后该文档不再被问答召回（需已解析完成）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="editVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 打包下载：源文件 / 解析 Markdown / 版面 JSON -->
    <el-dialog v-model="downloadVisible" :title="downloadTitle" width="560px" destroy-on-close>
      <p class="kb-download-summary">
        将把 <b>{{ downloadTargets.length }}</b> 篇文档勾选的产物打包为一个 zip（包内按「文档标题/产物」分目录），
        至少勾选一项：
      </p>
      <el-checkbox-group v-model="downloadKinds">
        <div v-for="option in DOWNLOAD_KINDS" :key="option.value" class="kb-download-kind">
          <el-checkbox :value="option.value">{{ option.label }}</el-checkbox>
          <p class="kb-download-kind__desc">{{ option.desc }}</p>
        </div>
      </el-checkbox-group>
      <el-alert
        v-if="needParsedArtifact && unparsedCount > 0"
        class="kb-download-alert"
        type="warning"
        :closable="false"
        show-icon
        :title="`所选文档中有 ${unparsedCount} 篇未完成解析，勾选解析类产物将导致整批下载失败`"
      />
      <template #footer>
        <el-button size="small" @click="downloadVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="downloading" @click="submitDownload">下载</el-button>
      </template>
    </el-dialog>

    <!-- 预览：原件 / 解析产物双视图（与知识文档页、问答引用共用同一抽屉） -->
    <DocumentPreviewDrawer v-model="previewVisible" :doc-id="previewDocId" :title="previewTitle" />
  </SectionCard>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  batchSetDocumentAvailable,
  deleteDocuments,
  downloadDocuments,
  getCategoryTree,
  getDocumentList,
  reparseDocument,
  updateDocument,
  uploadDocuments,
} from '@/api'
import DocumentPreviewDrawer from '@/components/common/DocumentPreviewDrawer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'FaKbDocumentPanel' })

const props = defineProps({
  /** 当前空间（null = 全部空间） */
  spaceId: { type: Number, default: null },
  /** 当前分类（null = 全部分类；仅在已选空间下生效） */
  categoryId: { type: Number, default: null },
  /** 空间清单：上传弹窗的空间下拉数据源 */
  spaces: { type: Array, default: () => [] },
})

const emit = defineEmits(['data-change'])

/** 解析状态：与后端 model.DocumentStatus 一一对应 */
const STATUS_META = {
  0: { label: '待解析', type: 'info' },
  1: { label: '解析中', type: 'warning' },
  2: { label: '已完成', type: 'success' },
  3: { label: '解析失败', type: 'danger' },
}

/** 是否仍在流水线中：文档状态与任务推导出的流水线状态任一为「执行中」都继续轮询 */
function isRunning(row) {
  return row.status === 1 || row.pipeline_status === 1
}

/** 与后端 supported_suffixes() 对齐，仅用于文件选择器过滤；真正校验在服务端 */
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

function formatSize(bytes) {
  const size = Number(bytes || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 标题栏的作用域说明：让用户随时知道当前列表被哪一层过滤 */
const scopeLabel = computed(() => {
  const space = props.spaceId ? `空间：${props.spaces.find((item) => item.id === props.spaceId)?.name ?? props.spaceId}` : '空间：全部'
  return props.categoryId ? `${space} · 已按分类过滤` : `${space} · 分类：全部`
})

const emptyDescription = computed(() =>
  props.spaceId ? '该空间还没有文档：点右上角「上传文档」' : '还没有文档：先上传材料或选择空间',
)

// ── 列表查询 ───────────────────────────────────────────────────────────

/** 空值不下发；status 可能是 0（待解析）、is_enabled 可能是 false，都不能用真值判断 */
function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (props.spaceId) result.space_id = props.spaceId
  // 分类过滤只在已选空间下生效：父级切空间会清空分类，这里再兜一层
  if (props.spaceId && props.categoryId) result.category_id = props.categoryId
  if (params.title) result.title = params.title
  if (params.status !== '' && params.status !== null && params.status !== undefined) result.status = params.status
  if (params.is_enabled !== '' && params.is_enabled !== null && params.is_enabled !== undefined) {
    result.is_enabled = params.is_enabled
  }
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getDocumentList(buildParams(params)),
  { defaultQuery: { title: '', status: '', is_enabled: '' } },
)

// 空间/分类切换：由父级联动，列表回到第一页重查
watch(
  () => [props.spaceId, props.categoryId],
  () => search(),
)

// ── 多选 ───────────────────────────────────────────────────────────────

const selectedRows = ref([])
const selectedIds = computed(() => selectedRows.value.map((row) => row.id))

function handleSelectionChange(rows) {
  selectedRows.value = rows
}

// ── 解析进度轮询 ───────────────────────────────────────────────────────

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

// ── 分类选项（上传/编辑弹窗共用） ──────────────────────────────────────

/** 分类树拉平成「一级 / 二级」文本选项，避免窄弹窗里放树形控件 */
function flattenCategoryTree(nodes, prefix = '') {
  return nodes.flatMap((node) => {
    const label = prefix ? `${prefix} / ${node.name}` : node.name
    return [{ id: node.id, label }, ...flattenCategoryTree(node.children ?? [], label)]
  })
}

async function fetchCategoryOptions(spaceId) {
  if (!spaceId) return []
  const tree = (await getCategoryTree({ space_id: spaceId })) ?? []
  return flattenCategoryTree(tree)
}

// ── 上传 ───────────────────────────────────────────────────────────────

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadFiles = ref([])
const uploadCategories = ref([])
const uploadForm = reactive({ space_id: null, category_id: '' })

function openUpload() {
  const spaceId = props.spaceId || props.spaces[0]?.id || null
  uploadFiles.value = []
  uploadForm.space_id = spaceId
  // 预填当前空间下的分类筛选，上传仍在同一弹窗里完成
  uploadForm.category_id = props.spaceId ? (props.categoryId ?? '') : ''
  uploadCategories.value = []
  void loadUploadCategories(spaceId)
  uploadVisible.value = true
}

/** 空间切换：分类候选整体换掉，旧空间的选择一律清空 */
function onUploadSpaceChange(spaceId) {
  uploadForm.category_id = ''
  uploadCategories.value = []
  void loadUploadCategories(spaceId)
}

/** 分类拉取失败不阻塞上传（分类是选填项），只留空下拉 */
async function loadUploadCategories(spaceId) {
  try {
    uploadCategories.value = await fetchCategoryOptions(spaceId)
  } catch {
    uploadCategories.value = []
  }
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
  if (files.length > UPLOAD_MAX_FILES) {
    ElMessage.warning(`一次最多上传 ${UPLOAD_MAX_FILES} 份文件`)
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
    emit('data-change')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    uploading.value = false
  }
}

// ── 编辑 ───────────────────────────────────────────────────────────────

const editVisible = ref(false)
const editing = ref(false)
const editingId = ref(null)
const editCategories = ref([])
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
  editCategories.value = []
  editVisible.value = true
  // 分类候选按文档所属空间拉取：未选空间时列表里可能混着多个空间
  void loadEditCategories(row.space_id)
}

async function loadEditCategories(spaceId) {
  try {
    editCategories.value = await fetchCategoryOptions(spaceId)
  } catch {
    editCategories.value = []
  }
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
    emit('data-change')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    editing.value = false
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
    emit('data-change')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    switchingId.value = null
  }
}

async function setAvailable(enabled) {
  const ids = selectedIds.value
  if (!ids.length) return
  try {
    await batchSetDocumentAvailable(ids, enabled)
    ElMessage.success(`已${enabled ? '上架' : '下架'} ${ids.length} 份文档`)
    await load()
    emit('data-change')
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
    emit('data-change')
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
    emit('data-change')
  } catch {
    // 请求拦截器已统一提示
  }
}

// ── 预览 ───────────────────────────────────────────────────────────────

const previewVisible = ref(false)
const previewDocId = ref(null)
const previewTitle = ref('')

/** 预览入口：交给公共抽屉，原件/解析产物在抽屉内切换 */
function openPreview(row) {
  previewDocId.value = row.id
  previewTitle.value = row.title || row.file_name || '文档预览'
  previewVisible.value = true
}

// ── 打包下载 ───────────────────────────────────────────────────────────

const DOWNLOAD_KINDS = [
  { value: 'source', label: '源文件', desc: '上传时的原始文档（pdf / docx / 表格 / 图片等）' },
  { value: 'markdown', label: '解析 Markdown', desc: '解析生成的 full.md 全文（含内嵌图片目录，解压即可渲染）' },
  { value: 'json', label: '版面 JSON', desc: 'content.json，记录版面块与页码溯源信息（体积较大）' },
]

const downloadVisible = ref(false)
const downloading = ref(false)
const downloadTargets = ref([])
/** 默认只下载源文件：一定存在且体积可控 */
const downloadKinds = ref(['source'])

const downloadTitle = computed(() =>
  downloadTargets.value.length > 1 ? `批量下载（${downloadTargets.value.length} 篇）` : '下载文档',
)

/** 是否勾选了依赖解析产物的类型 */
const needParsedArtifact = computed(
  () => downloadKinds.value.includes('markdown') || downloadKinds.value.includes('json'),
)

const unparsedCount = computed(() => downloadTargets.value.filter((row) => row.status !== 2).length)

function openDownloadDialog(rows) {
  const targets = (rows ?? []).filter((row) => row.id)
  if (!targets.length) {
    ElMessage.warning('请先勾选要下载的文档')
    return
  }
  downloadTargets.value = targets
  downloadKinds.value = ['source']
  downloadVisible.value = true
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

async function submitDownload() {
  if (!downloadKinds.value.length) {
    ElMessage.warning('请至少选择一种要下载的内容')
    return
  }

  downloading.value = true
  try {
    const blob = await downloadDocuments({
      ids: downloadTargets.value.map((row) => row.id),
      with_source: downloadKinds.value.includes('source'),
      with_markdown: downloadKinds.value.includes('markdown'),
      with_json: downloadKinds.value.includes('json'),
    })
    const only = downloadTargets.value.length === 1 ? downloadTargets.value[0] : null
    saveBlob(blob, only ? `${only.title || only.file_name || 'document'}.zip` : '知识库文档包.zip')
    ElMessage.success('打包完成，已开始下载')
    downloadVisible.value = false
  } catch {
    // 请求拦截器已统一提示
  } finally {
    downloading.value = false
  }
}

defineExpose({
  /** 重新加载列表（分类数据变化后父级调用） */
  reload: () => load(),
})
</script>

<style scoped lang="scss">
.kb-panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.kb-panel-hint {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-doc-toolbar {
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 0 26px 4px;

  :deep(.el-form-item) {
    margin-bottom: 8px;
  }
}

.kb-doc-actions {
  @include flex($justify: flex-end, $gap: 8px);
  padding-bottom: 8px;
}

.kb-doc-progress {
  margin-left: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-doc-stage {
  margin-top: 2px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-doc-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.kb-upload-icon {
  font-size: 42px;
  color: var(--app-text-secondary);
}

.kb-upload-tip {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-secondary);
}

.kb-form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-download-summary {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--app-text-regular);

  b {
    color: var(--el-color-primary);
  }
}

.kb-download-kind {
  padding: 8px 12px;
  margin-bottom: 8px;
  background-color: var(--app-tint-bg);
  border-radius: var(--app-radius-sm);

  &:last-child {
    margin-bottom: 0;
  }

  .kb-download-kind__desc {
    padding-left: 24px;
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--app-text-secondary);
  }
}

.kb-download-alert {
  margin-top: 12px;
}
</style>
