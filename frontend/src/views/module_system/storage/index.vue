<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">文件存储</h2>
        <p class="page-header__desc">
          查看当前环境配置的 S3 兼容对象存储：按目录逐层浏览文件夹与文件，可预览、下载、复制预签名链接，并向当前目录上传或删除文件；删除前请确认对象未被知识库记录引用
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" :disabled="!config.enabled" @click="openUpload">
          <el-icon><UploadFilled /></el-icon>
          <span>上传文件</span>
        </el-button>
        <el-button size="small" :loading="loading || configLoading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="!config.enabled"
      type="warning"
      :closable="false"
      show-icon
      title="对象存储未启用"
      description="当前环境 S3_ENABLE=false 或缺少 S3_ENDPOINT / 凭据，本页无法列出目录；请在 backend/env/.env.<env> 补齐后重启服务。"
    />

    <div class="storage-cards">
      <StatCard label="本页文件" :value="files.length" icon="Document" :tip="`第 ${pageNo} 页 · 每页 ${pageSize}`" />
      <StatCard label="子目录" :value="folders.length" icon="FolderOpened" tip="目录按 S3 键前缀折叠" />
      <StatCard label="本页大小" :value="pageSizeText" icon="Coin" tip="仅统计当前页文件" />
      <StatCard
        label="翻页"
        :value="hasNext ? '还有' : '到末页'"
        icon="Right"
        :tip="hasNext ? 'S3 只给续传令牌，不给总数' : '当前目录已无更多对象'"
      />
    </div>

    <SectionCard flush>
      <div class="storage-panel">
        <div class="storage-toolbar">
          <div class="storage-toolbar__path">
            <el-breadcrumb separator="/">
              <el-breadcrumb-item>
                <a class="storage-crumb" @click="openDir('')">{{ rootLabel }}</a>
              </el-breadcrumb-item>
              <el-breadcrumb-item v-for="(name, index) in segments" :key="`${name}-${index}`">
                <a class="storage-crumb" @click="openDir(prefixOf(index))">{{ name }}</a>
              </el-breadcrumb-item>
            </el-breadcrumb>
            <span class="storage-toolbar__hint">
              {{ config.key_prefix ? `桶 ${config.bucket} · 应用前缀 ${config.key_prefix}/` : `桶 ${config.bucket}` }}
            </span>
          </div>
          <el-form :inline="true" size="small" @submit.prevent>
            <el-form-item label="名称过滤">
              <el-input
                v-model="keywordInput"
                placeholder="当前目录内前缀匹配"
                clearable
                style="width: 200px"
                @keyup.enter="applySearch"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="applySearch">查询</el-button>
              <el-button :disabled="!keywordInput && !appliedKeyword" @click="resetSearch">重置</el-button>
            </el-form-item>
          </el-form>
        </div>

        <div class="storage-bulk">
          <span class="storage-bulk__hint">
            已选 {{ selectedRows.length }} 个文件（目录不支持删除，需进入后逐个删）
          </span>
          <el-button
            size="small"
            type="danger"
            plain
            :disabled="!selectedRows.length || deleting"
            :loading="deleting"
            @click="deleteSelected"
          >
            删除选中
          </el-button>
        </div>

        <el-table
          ref="tableRef"
          v-loading="loading"
          :data="rows"
          row-key="rowKey"
          size="small"
          style="width: 100%"
          @row-dblclick="handleRowDblClick"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="44" :selectable="isFileRow" />
          <el-table-column label="名称" min-width="300">
            <template #default="{ row }">
              <span class="storage-name" :class="{ 'is-folder': row.kind === 'folder' }" @click="handleNameClick(row)">
                <el-icon><Folder v-if="row.kind === 'folder'" /><Document v-else /></el-icon>
                <span class="storage-name__text">{{ row.name }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="92">
            <template #default="{ row }">
              <el-tag size="small" effect="light" :type="row.kind === 'folder' ? 'primary' : 'info'">
                {{ row.kind === 'folder' ? '目录' : '文件' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="110">
            <template #default="{ row }">{{ row.kind === 'folder' ? '-' : formatFileSize(row.size) }}</template>
          </el-table-column>
          <el-table-column label="最后修改" width="170">
            <template #default="{ row }">{{ row.kind === 'folder' ? '-' : formatDateTime(row.last_modified) }}</template>
          </el-table-column>
          <el-table-column label="对象键" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">{{ row.kind === 'folder' ? row.prefix : row.key }}</template>
          </el-table-column>
          <el-table-column label="操作" width="268" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.kind === 'folder'" link type="primary" size="small" @click="openDir(row.prefix)">
                进入
              </el-button>
              <template v-else>
                <el-button link type="primary" size="small" @click="openObject(row)">查看</el-button>
                <el-button link type="primary" size="small" @click="copyPresign(row)">复制链接</el-button>
                <el-button link type="primary" size="small" @click="downloadObject(row)">下载</el-button>
                <el-button link type="danger" size="small" :disabled="deleting" @click="deleteRow(row)">删除</el-button>
              </template>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="emptyText" :image-size="80" />
          </template>
        </el-table>

        <div class="storage-pagination">
          <span class="storage-pagination__hint">
            {{ appliedKeyword ? `过滤词「${appliedKeyword}」` : '按目录浏览' }} · 第 {{ pageNo }} 页 ·
            {{ hasNext ? '还有下一页' : '已到当前目录末页' }}
          </span>
          <el-button-group>
            <el-button size="small" :disabled="pageNo <= 1 || loading" @click="prevPage">上一页</el-button>
            <el-button size="small" :disabled="!hasNext || loading" @click="nextPage">下一页</el-button>
          </el-button-group>
          <el-select v-model="pageSize" size="small" style="width: 112px" @change="changePageSize">
            <el-option v-for="size in pageSizeOptions" :key="size" :label="`每页 ${size}`" :value="size" />
          </el-select>
        </div>
      </div>
    </SectionCard>

    <el-dialog v-model="previewVisible" :title="previewTitle" width="820px" destroy-on-close>
      <div v-loading="previewLoading" class="storage-preview">
        <img
          v-if="isImage(previewRow)"
          :src="previewUrl"
          :alt="previewRow?.name"
          class="storage-preview__image"
          @load="previewLoading = false"
          @error="handlePreviewError"
        />
        <iframe
          v-else-if="previewRow?.content_type === 'application/pdf'"
          :src="previewUrl"
          class="storage-preview__frame"
          @load="previewLoading = false"
        />
        <div v-else class="storage-preview__fallback">
          <p>该类型（{{ previewRow?.content_type || '未知类型' }}）不支持内联预览，请下载后用本地应用打开。</p>
          <el-button type="primary" @click="downloadObject(previewRow)">下载文件</el-button>
        </div>
      </div>
      <template #footer>
        <span class="storage-preview__meta">{{ previewRow?.key }}</span>
        <el-button size="small" @click="copyPresign(previewRow)">复制预签名链接</el-button>
        <el-button size="small" type="primary" @click="downloadObject(previewRow)">下载</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="uploadVisible" title="上传文件" width="560px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="`上传到：${prefix || rootLabel}`"
        :description="`单文件上限 ${config.upload_max_size_mb} MB；同名文件会先询问是否覆盖，覆盖后原内容不可恢复`"
      />
      <el-upload
        v-model:file-list="uploadFiles"
        class="storage-upload"
        drag
        multiple
        :auto-upload="false"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">把文件拖到这里，或<em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">选中后点「开始上传」；上传期间不要关闭窗口</div>
        </template>
      </el-upload>
      <template #footer>
        <el-button size="small" :disabled="uploading" @click="uploadVisible = false">取消</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="uploading"
          :disabled="!uploadFiles.length"
          @click="submitUpload"
        >
          开始上传
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteStorageObjects,
  getStorageConfig,
  getStorageObjects,
  getStoragePresign,
  storageContentUrl,
  uploadStorageObject,
} from '@/api/module_system/storage'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { formatDateTime, formatFileSize } from '@/utils/format'

defineOptions({ name: 'SystemStorageView' })

const pageSizeOptions = [50, 100, 200]

function emptyConfig() {
  return {
    enabled: false,
    provider: '',
    endpoint: '',
    bucket: '',
    key_prefix: '',
    region: '',
    addressing_style: '',
    use_ssl: false,
    auto_create: false,
    presign_expire: 0,
    upload_max_size_mb: 0,
  }
}

// ── 配置回显 ────────────────────────────────────────────────────────────

const config = ref(emptyConfig())
const configLoading = ref(false)

async function loadConfig() {
  configLoading.value = true
  try {
    config.value = { ...emptyConfig(), ...((await getStorageConfig()) || {}) }
  } catch {
    config.value = emptyConfig()
  } finally {
    configLoading.value = false
  }
}

const rootLabel = computed(() => {
  if (config.value.key_prefix) return config.value.key_prefix
  return config.value.bucket || '根目录'
})

// ── 目录浏览 ────────────────────────────────────────────────────────────

const loading = ref(false)
const tableRef = ref(null)
const selectedRows = ref([])
const folders = ref([])
const files = ref([])
const prefix = ref('')
const keywordInput = ref('')
const appliedKeyword = ref('')

const pageSize = ref(100)
const pageNo = ref(1)
// tokens[n-1] 是第 n 页的续传令牌；S3 只给令牌，页码只能是顺序栈
const tokens = ref([null])
const nextToken = ref(null)

const hasNext = computed(() => Boolean(nextToken.value))

const rows = computed(() => [
  ...folders.value.map((item) => ({ kind: 'folder', rowKey: `d:${item.prefix}`, ...item })),
  ...files.value.map((item) => ({ kind: 'file', rowKey: `f:${item.key}`, ...item })),
])

const segments = computed(() => prefix.value.split('/').filter(Boolean))

const pageSizeText = computed(() => formatFileSize(files.value.reduce((sum, item) => sum + (item.size || 0), 0)))

const emptyText = computed(() => {
  if (!config.value.enabled) return '对象存储未启用，无法列出目录'
  if (appliedKeyword.value) return `当前目录下没有以「${appliedKeyword.value}」开头的对象`
  return '当前目录为空'
})

/** 目录前缀归一：与后端一致，非空必带尾斜杠 */
function normalizeDir(dir) {
  const cleaned = String(dir || '')
    .split('/')
    .filter(Boolean)
    .join('/')
  return cleaned ? `${cleaned}/` : ''
}

function prefixOf(index) {
  return `${segments.value.slice(0, index + 1).join('/')}/`
}

function resetPaging() {
  tokens.value = [null]
  pageNo.value = 1
  nextToken.value = null
}

async function load() {
  if (!config.value.enabled) {
    folders.value = []
    files.value = []
    nextToken.value = null
    return
  }
  loading.value = true
  try {
    const result = await getStorageObjects({
      prefix: prefix.value,
      keyword: appliedKeyword.value || undefined,
      page_size: pageSize.value,
      continuation_token: tokens.value[pageNo.value - 1] || undefined,
    })
    folders.value = result?.folders || []
    files.value = result?.files || []
    nextToken.value = result?.next_token || null
  } catch {
    // 请求拦截器已统一提示错误
    folders.value = []
    files.value = []
    nextToken.value = null
  } finally {
    loading.value = false
    // 翻页/刷新后旧选中项已不在当前页，必须清掉，否则批量删除会带上看不见的文件
    tableRef.value?.clearSelection()
  }
}

function openDir(dir) {
  prefix.value = normalizeDir(dir)
  keywordInput.value = ''
  appliedKeyword.value = ''
  resetPaging()
  load()
}

function applySearch() {
  appliedKeyword.value = keywordInput.value.trim()
  resetPaging()
  load()
}

function resetSearch() {
  keywordInput.value = ''
  appliedKeyword.value = ''
  resetPaging()
  load()
}

function nextPage() {
  if (!nextToken.value) return
  tokens.value[pageNo.value] = nextToken.value
  pageNo.value += 1
  load()
}

function prevPage() {
  if (pageNo.value <= 1) return
  pageNo.value -= 1
  load()
}

function changePageSize(size) {
  pageSize.value = size
  resetPaging()
  load()
}

async function refreshAll() {
  await loadConfig()
  resetPaging()
  await load()
}

function handleNameClick(row) {
  if (row.kind === 'folder') openDir(row.prefix)
}

function handleRowDblClick(row) {
  if (row.kind === 'folder') openDir(row.prefix)
}

// ── 预览与下载 ──────────────────────────────────────────────────────────

const previewVisible = ref(false)
const previewLoading = ref(false)
const previewRow = ref(null)
const previewUrl = ref('')

const previewTitle = computed(() => (previewRow.value ? `查看 · ${previewRow.value.name}` : '查看对象'))

function isImage(row) {
  return Boolean(row?.content_type && row.content_type.startsWith('image/'))
}

function openObject(row) {
  if (!row) return
  previewRow.value = row
  previewUrl.value = storageContentUrl(row.key)
  previewLoading.value = isImage(row) || row.content_type === 'application/pdf'
  previewVisible.value = true
}

function handlePreviewError() {
  previewLoading.value = false
  ElMessage.warning('对象内容无法内联预览（可能过大或已被清理），请尝试下载')
}

function downloadObject(row) {
  if (!row?.key) return
  window.open(storageContentUrl(row.key, true), '_blank')
}

async function copyPresign(row) {
  if (!row?.key) return
  try {
    const result = await getStoragePresign(row.key)
    await copyText(result?.url || '')
    const minutes = Math.round((result?.expires_in || 0) / 60)
    ElMessage.success(minutes ? `预签名链接已复制（${minutes} 分钟内有效）` : '预签名链接已复制')
  } catch {
    // 请求拦截器已统一提示错误
  }
}

/** 复制文本：非 HTTPS 环境下 navigator.clipboard 不可用，回退到临时 textarea */
async function copyText(text) {
  if (!text) return
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

// ── 选择与删除 ────────────────────────────────────────

const deleting = ref(false)

/** 只有文件行可勾选：目录删除不在本页能力范围 */
function isFileRow(row) {
  return row.kind === 'file'
}

function handleSelectionChange(selection) {
  selectedRows.value = selection
}

async function deleteRow(row) {
  if (!row?.key) return
  await runDelete([row.key])
}

async function deleteSelected() {
  const keys = selectedRows.value.map((row) => row.key).filter(Boolean)
  if (!keys.length) return
  await runDelete(keys)
}

/** 删前必确认：桶内对象常被知识库记录引用，删掉即死链且不可恢复 */
async function runDelete(keys) {
  if (deleting.value) return
  const target = keys.length > 1 ? `这 ${keys.length} 个文件` : `「${keys[0]}」`
  try {
    await ElMessageBox.confirm(
      `将永久删除${target}。若对象已被知识库记录引用（如解析页图），相关预览与下载会变成死链且无法恢复。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  deleting.value = true
  try {
    const result = await deleteStorageObjects(keys, { quiet: true })
    const deleted = result?.deleted?.length || 0
    const failed = result?.failed || []
    if (deleted) ElMessage.success(`已删除 ${deleted} 个文件`)
    if (failed.length) {
      const detail = failed.map((item) => `${item.key}（${item.reason}）`).join('；')
      ElMessage.warning(`${failed.length} 个文件删除失败：${detail}`)
    }
    await load()
  } catch (error) {
    ElMessage.error(error?.message || '删除失败')
  } finally {
    deleting.value = false
  }
}

// ── 上传 ────────────────────────────────────────────

const uploadVisible = ref(false)
const uploadFiles = ref([])
const uploading = ref(false)

function openUpload() {
  if (!config.value.enabled) return
  uploadFiles.value = []
  uploadVisible.value = true
}

/** 单个文件上传：同名时问一次是否覆盖，取消则跳过该文件 */
async function uploadOne(file) {
  const form = new FormData()
  form.append('file', file.raw, file.name)
  form.append('prefix', prefix.value)
  form.append('overwrite', 'false')

  try {
    return await uploadStorageObject(form, { quiet: true })
  } catch (error) {
    if (error?.response?.status !== 409) throw error
    await ElMessageBox.confirm(
      `「${file.name}」在当前目录已存在，覆盖后原对象内容不可恢复（引用它的记录会读到新内容）。是否覆盖？`,
      '同名对象',
      { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '跳过' },
    )
    form.set('overwrite', 'true')
    return uploadStorageObject(form, { quiet: true })
  }
}

/** 逐个上传：串行避免多份大文件同时占满带宽与服务端线程池 */
async function submitUpload() {
  if (uploading.value) return
  const pending = uploadFiles.value.filter((file) => file.raw)
  if (!pending.length) return

  const limitMb = config.value.upload_max_size_mb || 0
  const failures = []
  let uploaded = 0
  let overwritten = 0
  let skipped = 0

  uploading.value = true
  for (const file of pending) {
    if (limitMb && file.size > limitMb * 1024 * 1024) {
      failures.push(`${file.name}：超过 ${limitMb} MB 上限`)
      continue
    }
    try {
      const result = await uploadOne(file)
      uploaded += 1
      if (result?.overwritten) overwritten += 1
    } catch (error) {
      // ElMessageBox 取消是 'cancel'/'close'，与 HTTP 错误分开统计
      if (error === 'cancel' || error === 'close') {
        skipped += 1
        continue
      }
      failures.push(`${file.name}：${error?.message || '上传失败'}`)
    }
  }
  uploading.value = false

  if (uploaded) {
    ElMessage.success(`已上传 ${uploaded} 个文件${overwritten ? `（覆盖 ${overwritten} 个）` : ''}`)
  }
  if (skipped) ElMessage.info(`已跳过 ${skipped} 个同名文件`)
  if (failures.length) ElMessage.error(`上传失败：${failures.join('；')}`)

  uploadVisible.value = false
  await load()
}

onMounted(async () => {
  await loadConfig()
  await load()
})
</script>

<style scoped lang="scss">
.storage-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.storage-panel {
  padding: 18px 26px 20px;
}

.storage-toolbar {
  @include flex($justify: space-between, $align: flex-start, $gap: 12px);
  flex-wrap: wrap;
  margin-bottom: 8px;

  &__path {
    @include flex($direction: column, $align: flex-start, $gap: 4px);
    min-width: 0;
  }

  &__hint {
    @include ellipsis;
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.storage-crumb {
  font-size: 13px;
  color: var(--el-color-primary);
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}

.storage-name {
  @include flex($align: center, $gap: 6px);
  min-width: 0;
  color: var(--app-text-primary);

  &.is-folder {
    color: var(--el-color-primary);
    cursor: pointer;
  }

  &__text {
    @include ellipsis;
  }
}

.storage-bulk {
  @include flex($justify: flex-end, $align: center, $gap: 10px);
  margin-bottom: 8px;

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.storage-upload {
  margin-top: 16px;
}

.storage-pagination {
  @include flex($justify: flex-end, $align: center, $gap: 10px);
  flex-wrap: wrap;
  padding-top: 14px;

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.storage-preview {
  min-height: 160px;

  &__image {
    display: block;
    max-width: 100%;
    max-height: 560px;
    margin: 0 auto;
    border-radius: 6px;
  }

  &__frame {
    width: 100%;
    height: 560px;
    border: 0;
  }

  &__fallback {
    @include flex($direction: column, $align: center, $gap: 12px);
    padding: 40px 0;
    font-size: 13px;
    color: var(--app-text-secondary);
  }

  &__meta {
    margin-right: 12px;
    font-size: 12px;
    color: var(--app-text-muted);
  }
}
</style>
