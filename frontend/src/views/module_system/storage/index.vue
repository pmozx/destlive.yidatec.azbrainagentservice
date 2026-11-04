<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">文件存储</h2>
        <p class="page-header__desc">
          查看当前环境配置的 S3 兼容对象存储：按目录逐层浏览文件夹与文件，可预览、下载或复制预签名链接；只读视图，不提供删除
        </p>
      </div>
      <div class="page-header__actions">
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

        <el-table v-loading="loading" :data="rows" row-key="rowKey" size="small" style="width: 100%" @row-dblclick="handleRowDblClick">
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
          <el-table-column label="操作" width="212" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.kind === 'folder'" link type="primary" size="small" @click="openDir(row.prefix)">
                进入
              </el-button>
              <template v-else>
                <el-button link type="primary" size="small" @click="openObject(row)">查看</el-button>
                <el-button link type="primary" size="small" @click="copyPresign(row)">复制链接</el-button>
                <el-button link type="primary" size="small" @click="downloadObject(row)">下载</el-button>
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
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getStorageConfig, getStorageObjects, getStoragePresign, storageContentUrl } from '@/api/module_system/storage'
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
