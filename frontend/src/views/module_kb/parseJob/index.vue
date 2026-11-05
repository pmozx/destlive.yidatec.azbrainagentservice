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

    <el-drawer v-model="detailVisible" title="解析任务详情" size="1180px" destroy-on-close>
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

                <!-- 契约 03.1 等价入口：把当前产物（含人工修订）的切片集合原样写成一版正式知识 -->
                <div class="kb-publish kb-detail__block">
                  <div class="kb-publish__info">
                    <span class="kb-publish__title">写入知识平台</span>
                    <span class="kb-publish__desc">{{ publishHint }}</span>
                  </div>
                  <div class="kb-publish__actions">
                    <el-tag v-if="detail.result.publication_id" type="success" effect="plain">
                      已写入 · {{ detail.result.publication_id }}
                    </el-tag>
                    <el-button
                      v-else
                      type="primary"
                      size="small"
                      :disabled="!resultChunks.length"
                      :loading="publishDialog.saving"
                      @click="openPublishDialog"
                    >
                      写入知识平台
                    </el-button>
                  </div>
                </div>

                <JsonViewer :data="detail.result.suggested" title="suggested（AI 建议元数据）" placeholder="无建议元数据" class="kb-detail__block" />
                <JsonViewer :data="detail.result.warnings" title="warnings（解析告警）" placeholder="无解析告警" class="kb-detail__block" />
                <el-collapse class="kb-detail__block">
                  <el-collapse-item title="chunks 原始 JSON（排障用）" name="rawChunks">
                    <JsonViewer :data="detail.result.chunks" title="chunks（切片数组）" placeholder="无切片产物" max-height="360px" />
                  </el-collapse-item>
                </el-collapse>

                <!-- 页图与切片合并成三栏：左侧目录定位、中间原页对照、右侧切块正文 -->
                <div class="kb-merge kb-detail__block">
                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">目录 / 页码</span>
                      <span class="kb-merge__meta">{{ pageGroups.length }} 项</span>
                    </header>
                    <div class="kb-merge__body kb-toc">
                      <button
                        v-for="group in pageGroups"
                        :key="group.key"
                        type="button"
                        class="kb-toc__item"
                        :class="{ 'is-active': group.key === activeGroup?.key }"
                        @click="activePageKey = group.key"
                      >
                        <span class="kb-toc__page">{{ group.pageText }}</span>
                        <span class="kb-toc__title">{{ group.summary }}</span>
                      </button>
                      <el-empty v-if="!pageGroups.length" description="无切片产物" :image-size="60" />
                    </div>
                  </section>

                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">{{ activePage ? `第 ${activePage.page_no} 页` : '页图预览' }}</span>
                      <span class="kb-merge__meta">{{ sourceFileName }}</span>
                    </header>
                    <div class="kb-merge__body kb-merge__body--preview">
                      <el-image
                        v-if="activePage"
                        :key="`${activePage.page_no}-${previewRetryKey}`"
                        :src="activePage.image_url"
                        :preview-src-list="pageUrls"
                        :initial-index="activePageIndex"
                        fit="contain"
                        preview-teleported
                        hide-on-click-modal
                        class="kb-merge__image"
                      >
                        <template #error>
                          <div class="kb-merge__error">
                            <span>页面图片加载失败或预览地址已过期</span>
                            <el-button size="small" @click.stop="previewRetryKey += 1">重试</el-button>
                          </div>
                        </template>
                      </el-image>
                      <el-empty v-else :description="previewHint" :image-size="70" />
                    </div>
                  </section>

                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">Chunk（{{ pageChunks.length }}）</span>
                      <span class="kb-merge__meta">{{ activeGroup?.pageText }}</span>
                    </header>
                    <div class="kb-merge__body">
                      <article v-for="chunk in pageChunks" :key="chunk.seq" class="kb-chunk">
                        <div class="kb-chunk__head">
                          <el-tag size="small" type="info" effect="plain">#{{ chunk.seq }}</el-tag>
                          <el-tag v-if="chunk.edited" size="small" type="warning" effect="plain">已修订</el-tag>
                          <span class="kb-chunk__meta">{{ chunkPageText(chunk) }} · {{ (chunk.content || '').length }} 字</span>
                          <el-button link type="primary" size="small" class="kb-chunk__edit" @click="openChunkEditor(chunk)">
                            <el-icon><Edit /></el-icon>
                            <span>编辑</span>
                          </el-button>
                        </div>
                        <h4 class="kb-chunk__title">{{ chunk.heading || chunkExcerpt(chunk, 40) }}</h4>
                        <!-- 切片正文按 Markdown 渲染：表格、列表、代码块与最终入库形态一致 -->
                        <div class="kb-chunk__content">
                          <MarkdownView :content="chunk.content" />
                        </div>
                      </article>
                      <el-empty v-if="!pageChunks.length" description="该页无切片" :image-size="60" />
                    </div>
                  </section>
                </div>
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

    <!-- 切片编辑：左侧写 Markdown 源码，右侧实时预览（与列表共用同一个渲染器） -->
    <el-dialog v-model="chunkEditor.visible" title="编辑切片" width="1000px" destroy-on-close>
      <div class="kb-editor">
        <section class="kb-editor__pane">
          <header class="kb-editor__head">
            <span>Markdown 源码</span>
            <span class="kb-editor__meta">#{{ chunkEditor.seq }} · {{ chunkEditor.content.length }} 字</span>
          </header>
          <el-input
            v-model="chunkEditor.content"
            type="textarea"
            :rows="17"
            maxlength="20000"
            resize="none"
            placeholder="支持 Markdown：标题、列表、表格、代码块"
            class="kb-editor__input"
          />
        </section>
        <section class="kb-editor__pane">
          <header class="kb-editor__head">
            <span>实时预览</span>
          </header>
          <div class="kb-editor__preview">
            <MarkdownView v-if="chunkEditor.content.trim()" :content="chunkEditor.content" />
            <el-empty v-else description="暂无内容" :image-size="60" />
          </div>
        </section>
      </div>
      <template #footer>
        <div class="kb-editor__footer">
          <span class="kb-editor__hint">保存后仅覆盖该条切片正文，产物哈希与其余切片不变</span>
          <el-button size="small" @click="chunkEditor.visible = false">取消</el-button>
          <el-button
            size="small"
            type="primary"
            :loading="chunkEditor.saving"
            :disabled="!canSaveChunkEdit"
            @click="saveChunkEdit"
          >
            保存
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 写入知识平台：切片由服务端取产物（含人工修订），这里只补发布单元展示字段 -->
    <el-dialog v-model="publishDialog.visible" title="写入知识平台" width="620px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="`将入库 ${resultChunks.length} 条切片（${editedChunkCount} 条已修订），不再二次切分`"
        class="kb-publish__alert"
      />
      <el-form label-width="88px">
        <el-form-item label="发布单元">
          <el-input v-model="publishForm.publication_id" maxlength="64" placeholder="留空自动生成" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="publishForm.title" maxlength="200" placeholder="留空取原文件名" />
        </el-form-item>
        <el-form-item label="租户 ID">
          <el-input v-model="publishForm.tenant_id" maxlength="64" placeholder="默认 gsi-default" />
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="publishForm.resource_id" maxlength="64" placeholder="留空取任务 doc_id（管理台上传没有）" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="publishForm.category" maxlength="64" placeholder="如：it / hr / 产品" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="publishForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="回车创建标签"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="维护人">
          <el-input v-model="publishForm.owner" maxlength="64" placeholder="留空不记录" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="publishForm.summary" type="textarea" :rows="3" placeholder="留空不记录" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="kb-publish__footer">
          <span class="kb-publish__hint">写入后到发布单元页修订新版本；本产物不可重复写入</span>
          <el-button size="small" @click="publishDialog.visible = false">取消</el-button>
          <el-button size="small" type="primary" :loading="publishDialog.saving" @click="submitPublish">
            确认写入
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, genFileId } from 'element-plus'

import { getPublicationList } from '@/api/module_kb/publication'
import { getParseJobDetail, getParseJobList, publishParseChunks, submitParseJob, updateParseChunk } from '@/api/module_kb/parseJob'
import JsonViewer from '@/components/common/JsonViewer.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
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

// ── 页图与切片的联动：左栏按页去重，中栏跟随该页页图，右栏列出该页切片 ────────
const resultChunks = computed(() => detail.value?.result?.chunks || [])
/** 目录选中项（页分组 key）；null 时按第一页兜底，避免打开详情后三栏全空 */
const activePageKey = ref(null)
/** 预签名地址失效或瞬时失败时改 key 重建 el-image，让它重新发一次请求 */
const previewRetryKey = ref(0)

/**
 * 目录按起始页去重：同一页的多条切片并成一条目录项（一页一条）。
 * 页码缺失的切片单独归为「未标注页码」一组，不伪造归属。
 */
const pageGroups = computed(() => {
  const groups = []
  const index = new Map()
  resultChunks.value.forEach((chunk) => {
    const pageNo = chunk.page_from ?? null
    const key = pageNo === null ? 'unpaged' : `page-${pageNo}`
    let group = index.get(key)
    if (!group) {
      group = { key, pageNo, pageText: chunkPageText(chunk), chunks: [] }
      index.set(key, group)
      groups.push(group)
    }
    group.chunks.push(chunk)
  })
  return groups.map((group) => ({
    ...group,
    // 目录第二行：条数 + 首条标题，兼顾「这页切了几块」与「这页讲什么」
    summary: `${group.chunks.length} 条${group.chunks[0]?.heading ? ` · ${group.chunks[0].heading}` : ''}`,
  }))
})

const activeGroup = computed(
  () => pageGroups.value.find((group) => group.key === activePageKey.value) || pageGroups.value[0] || null,
)

/** 右栏只列当前页的切片：目录已按页归并，这里直接取组内成员 */
const pageChunks = computed(() => activeGroup.value?.chunks || [])

/** 选中页：严格按页码取（超出渲染上限则留空，不拿第一页充数）；未标注页码的组不预览页图 */
const activePage = computed(() => {
  const pageNo = activeGroup.value?.pageNo
  if (pageNo === null || pageNo === undefined) return null
  return pageItems.value.find((page) => page.page_no === pageNo) || null
})

const activePageIndex = computed(() => Math.max(pageItems.value.indexOf(activePage.value), 0))

/** 中栏空态文案：区分「整个任务无页图」「该组无页码」「选中页超出渲染上限」 */
const previewHint = computed(() => {
  if (!pageItems.value.length) return '无页图预览：仅 PDF 且服务启用页图能力时生成'
  if (!activeGroup.value?.pageNo) return '该组切片未标注页码，无法定位页图'
  return `第 ${activeGroup.value.pageNo} 页无页图（可能超出单文档渲染上限）`
})

const sourceFileName = computed(() => detail.value?.file_meta?.file_name || detail.value?.result?.file_name || '-')

/** 页码文案：解析产物只在定位到页时回填 page_from/page_to，两者相等即单页 */
function chunkPageText(chunk) {
  if (chunk?.page_from === undefined || chunk?.page_from === null) return '未标注页码'
  const from = chunk.page_from
  const to = chunk.page_to
  return to === undefined || to === null || to === from ? `第 ${from} 页` : `第 ${from}-${to} 页`
}

/** 无 heading 时的兜底标题：压平空白并去掉 Markdown 标记后截断，避免目录里出现 `##` */
function chunkExcerpt(chunk, max = 24) {
  const text = String(chunk?.content || '')
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return '（空切片）'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

// ── 切片编辑（Markdown 源码 + 实时预览，保存后只回写这一条） ──────────
const chunkEditor = reactive({ visible: false, seq: null, content: '', original: '', saving: false })

/** 内容未改或清空时不允许提交：空正文入产物后检索侧会拿到空白切片 */
const canSaveChunkEdit = computed(() => {
  const text = chunkEditor.content
  return !chunkEditor.saving && text.trim().length > 0 && text !== chunkEditor.original
})

function openChunkEditor(chunk) {
  chunkEditor.seq = chunk.seq
  chunkEditor.content = chunk.content || ''
  chunkEditor.original = chunk.content || ''
  chunkEditor.visible = true
}

async function saveChunkEdit() {
  const jobId = detail.value?.uuid
  if (!jobId || chunkEditor.seq === null) return
  chunkEditor.saving = true
  try {
    const out = await updateParseChunk(jobId, { seq: chunkEditor.seq, content: chunkEditor.content })
    // 局部回写：只替换这一条，避免整份详情重拉导致选中页与滚动位置丢失
    const hit = resultChunks.value.find((chunk) => chunk.seq === out.seq)
    if (hit) {
      hit.content = out.content
      hit.edited = out.edited
      hit.edited_time = out.edited_time
    }
    ElMessage.success(`切片 #${out.seq} 已保存`)
    chunkEditor.visible = false
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    chunkEditor.saving = false
  }
}

// ── 写入知识平台（03.1 等价：产物切片原样入库，不再二次切分） ─────────
const publishDialog = reactive({ visible: false, saving: false })
const publishForm = reactive({
  publication_id: '',
  title: '',
  tenant_id: 'gsi-default',
  resource_id: '',
  category: '',
  tags: [],
  owner: '',
  summary: '',
})

/** 已修订条数：写入前先告诉用户「改过的正文会一起入库」，避免以为写的是原始解析结果 */
const editedChunkCount = computed(() => resultChunks.value.filter((chunk) => chunk.edited).length)

const publishHint = computed(() => {
  const result = detail.value?.result
  if (!result) return ''
  const base = `${resultChunks.value.length} 条切片 · ${editedChunkCount.value} 条已修订`
  return result.publication_id
    ? `${base} · 写入于 ${formatDateTime(result.published_time)}`
    : `${base} · 按产物原样入库，一份产物只能写一次`
})

/** 标题缺省：原文件名去扩展名（与后端缺省口径一致） */
function defaultPublishTitle() {
  const base = String(sourceFileName.value || '').split(/[\\/]/).pop() || ''
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

function openPublishDialog() {
  Object.assign(publishForm, {
    publication_id: '',
    title: defaultPublishTitle(),
    tenant_id: 'gsi-default',
    resource_id: detail.value?.file_meta?.doc_id || '',
    category: '',
    tags: [],
    owner: '',
    summary: '',
  })
  publishDialog.visible = true
}

async function submitPublish() {
  const jobId = detail.value?.uuid
  if (!jobId || publishDialog.saving) return
  publishDialog.saving = true
  try {
    const out = await publishParseChunks(jobId, {
      publication_id: publishForm.publication_id.trim() || null,
      title: publishForm.title.trim() || null,
      tenant_id: publishForm.tenant_id.trim() || null,
      resource_id: publishForm.resource_id.trim() || null,
      category: publishForm.category.trim() || null,
      summary: publishForm.summary.trim() || null,
      tags: publishForm.tags,
      owner: publishForm.owner.trim() || null,
    })
    // 局部回写「已写入」标识：不重拉详情，保留当前选中页与滚动位置
    const result = detail.value?.result
    if (result) {
      result.publication_id = out.publication_id
      result.published_time = out.published_time
    }
    publishDialog.visible = false
    ElMessage.success(`已写入知识平台：${out.publication_id}（${out.chunk_count} 条切片）`)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    publishDialog.saving = false
  }
}

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
  // 三栏联动状态随任务切换重置，避免沿用上一条任务的选中页
  activePageKey.value = null
  previewRetryKey.value = 0
  chunkEditor.visible = false
  publishDialog.visible = false
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

/* 解析产物三栏：目录 / 页图 / 切片，三栏等高，各自内部滚动 */
.kb-merge {
  display: grid;
  grid-template-columns: 212px minmax(0, 1fr) 336px;
  gap: 12px;
  height: 620px;

  &__panel {
    @include flex($direction: column);
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--app-border-color);
    border-radius: var(--app-radius-sm);
    background-color: var(--app-card-bg);
  }

  &__head {
    @include flex($justify: space-between, $gap: 8px);
    flex: none;
    padding: 10px 12px;
    border-bottom: 1px solid var(--app-border-color);
    background-color: var(--app-tint-bg);
  }

  &__title {
    @include ellipsis;
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    @include ellipsis;
    flex: none;
    max-width: 60%;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 10px 12px;

    &--preview {
      @include flex(center, center);
      padding: 12px;
      background-color: var(--app-tint-bg);
    }
  }

  &__image {
    width: 100%;
    height: 100%;
    cursor: zoom-in;

    :deep(img) {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
  }

  &__error {
    @include flex(center, center, 10px, column);
    height: 100%;
    padding: 0 8px;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-align: center;
  }
}

.kb-toc {
  @include flex($direction: column, $gap: 2px);
  padding: 6px;

  &__item {
    @include flex($direction: column, $gap: 2px);
    width: 100%;
    padding: 8px 10px;
    border: 0;
    border-radius: 8px;
    background-color: transparent;
    cursor: pointer;
    text-align: left;
    transition: background-color 0.18s ease;

    &:hover {
      background-color: var(--app-tint-bg);
    }

    &.is-active {
      background-color: rgba(163, 43, 104, 0.1);

      .kb-toc__page {
        font-weight: 600;
        color: var(--app-brand);
      }
    }
  }

  &__page {
    @include ellipsis;
    font-size: 13px;
    color: var(--app-text-primary);
  }

  &__title {
    @include ellipsis;
    width: 100%;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.kb-chunk {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid var(--app-border-color);
  border-radius: var(--app-radius-sm);

  &:last-child {
    margin-bottom: 0;
  }

  &__head {
    @include flex($gap: 8px);
  }

  &__meta {
    @include ellipsis;
    flex: 1;
    min-width: 0;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__edit {
    flex: none;
    margin-left: auto;
  }

  &__title {
    margin: 8px 0 6px;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.5;
    color: var(--app-text-primary);
  }

  &__content {
    margin: 0;
    padding: 8px 10px;
    border-radius: 8px;
    background-color: var(--app-tint-bg);
    word-break: break-word;

    /* 右栏只有 336px，沿用全局 14px 会把一行挤成两三个字 */
    :deep(.markdown-view) {
      font-size: 12px;
      line-height: 1.7;
    }
  }
}

/* 切片编辑：左写右预览，两栏等高 */
.kb-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  height: 440px;

  &__pane {
    @include flex($direction: column);
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--app-border-color);
    border-radius: var(--app-radius-sm);
    background-color: var(--app-card-bg);
  }

  &__head {
    @include flex($justify: space-between, $gap: 8px);
    flex: none;
    padding: 8px 12px;
    border-bottom: 1px solid var(--app-border-color);
    background-color: var(--app-tint-bg);
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    flex: none;
    font-size: 12px;
    font-weight: 400;
    color: var(--app-text-secondary);
  }

  &__input {
    flex: 1;
    min-height: 0;

    :deep(.el-textarea__inner) {
      height: 100%;
      font-family: var(--app-font-mono, monospace);
      font-size: 12px;
      line-height: 1.7;
      box-shadow: none;

      &:focus {
        box-shadow: 0 0 0 1px var(--el-color-primary) inset;
      }
    }
  }

  &__preview {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 10px 12px;
  }

  &__footer {
    @include flex($justify: flex-end, $gap: 8px);
  }

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

/* 写入知识平台：一条工具条，未写入时给按钮、已写入时只剩状态标签 */
.kb-publish {
  @include flex($justify: space-between, $gap: 12px);
  padding: 10px 14px;
  border: 1px dashed var(--app-border-color);
  border-radius: var(--app-radius-sm);
  background-color: var(--app-tint-bg);

  &__info {
    @include flex($direction: column, $gap: 2px);
    min-width: 0;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__desc {
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__actions {
    @include flex($gap: 8px);
    flex: none;
  }

  &__alert {
    margin-bottom: 12px;
  }

  &__footer {
    @include flex($justify: flex-end, $gap: 8px);
  }

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}
</style>
