<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">问答会话</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-chat/session：会话保存标题、问答范围与消息条数，消息明细随会话一并清理；
          详情里可回看每一轮的引用来源与用量，便于复盘答案质量
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
      <div class="session-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 180px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="问答范围">
            <el-select v-model="query.scope_type" placeholder="全部" clearable style="width: 140px">
              <el-option label="全部空间" :value="1" />
              <el-option label="指定空间" :value="2" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>

        <div class="session-actions">
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
        <el-table-column prop="title" label="会话标题" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">
              {{ row.title || '未命名会话' }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="问答范围" width="150">
          <template #default="{ row }">
            <el-tag :type="row.scope_type === 2 ? 'warning' : 'info'" size="small" effect="plain">
              {{ row.scope_type === 2 ? `指定空间 ${(row.space_ids || []).length} 个` : '全部空间' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="消息数" width="90" align="center">
          <template #default="{ row }">{{ row.message_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="最近消息" width="170">
          <template #default="{ row }">{{ formatDateTime(row.last_message_time) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">明细</el-button>
            <el-button link type="primary" size="small" @click="rename(row)">重命名</el-button>
            <el-button link type="danger" size="small" @click="removeMany([row])">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有问答会话：在「AI 问答」里提问即可生成" :image-size="80" />
        </template>
      </el-table>

      <div class="session-pagination">
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

    <el-drawer v-model="detailVisible" title="会话明细" size="58%" destroy-on-close>
      <div v-loading="detailLoading" class="session-detail">
        <template v-if="detail">
          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item label="会话标题" :span="2">
              {{ detail.session?.title || '未命名会话' }}
            </el-descriptions-item>
            <el-descriptions-item label="问答范围">
              {{ detail.session?.scope_type === 2 ? `指定空间 ${(detail.session?.space_ids || []).length} 个` : '全部空间' }}
            </el-descriptions-item>
            <el-descriptions-item label="消息数">{{ detail.session?.message_count ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="最近消息">{{ formatDateTime(detail.session?.last_message_time) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.session?.created_time) }}</el-descriptions-item>
          </el-descriptions>

          <div v-for="(item, index) in detail.messages" :key="item.id ?? index" class="detail-message">
            <div class="detail-message__head">
              <el-tag :type="item.role === 'user' ? 'primary' : 'success'" size="small" effect="light">
                {{ item.role === 'user' ? '用户' : '助手' }}
              </el-tag>
              <span v-if="item.not_found" class="detail-message__flag">未命中知识库</span>
              <span v-if="item.feedback" class="detail-message__flag">
                {{ item.feedback === 1 ? '有用' : '无用' }}
              </span>
              <span class="detail-message__usage">
                {{ item.model_name || '-' }} · in {{ item.tokens_in }} / out {{ item.tokens_out }} ·
                {{ item.latency_ms }} ms
              </span>
            </div>

            <MarkdownView v-if="item.role !== 'user'" :content="item.content" />
            <pre v-else class="detail-message__text">{{ item.content }}</pre>

            <div v-if="item.cites?.length" class="detail-message__cites">
              <span class="detail-message__label">引用</span>
              <el-tag v-for="(cite, citeIndex) in item.cites" :key="cite.chunkId" size="small" effect="plain">
                [{{ citeIndex + 1 }}] {{ cite.doc }}<template v-if="cite.page"> · P{{ cite.page }}</template>
              </el-tag>
            </div>

            <details v-if="item.analysis" class="detail-message__analysis">
              <summary>思考链路</summary>
              <pre>{{ prettyJson(item.analysis) }}</pre>
            </details>

            <div v-if="item.follow_ups?.length" class="detail-message__followups">
              <span class="detail-message__label">追问建议</span>
              <el-tag v-for="text in item.follow_ups" :key="text" size="small" effect="plain">{{ text }}</el-tag>
            </div>
          </div>

          <el-empty v-if="!detail.messages?.length" description="该会话还没有消息" :image-size="70" />
        </template>
      </div>
      <template #footer>
        <el-button size="small" @click="detailVisible = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { deleteSessions, getSessionDetail, getSessionList, updateSession } from '@/api'
import MarkdownView from '@/components/common/MarkdownView.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime, prettyJson } from '@/utils/format'

defineOptions({ name: 'IntegrationChatSessionView' })

/** 空值不下发；scope_type 可能是 1，不能用真值判断 */
function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.title) result.title = params.title
  if (params.scope_type !== '' && params.scope_type !== null && params.scope_type !== undefined) {
    result.scope_type = params.scope_type
  }
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getSessionList(buildParams(params)),
  { defaultQuery: { title: '', scope_type: '' } },
)

const selectedIds = ref([])

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

// ── 明细 ───────────────────────────────────────────────────────────────

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getSessionDetail(row.id)
  } catch {
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

// ── 重命名 / 删除 ──────────────────────────────────────────────────────

async function rename(row) {
  let title = ''
  try {
    const result = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
      inputValue: row.title || '',
      inputValidator: (value) => (value && value.trim() ? true : '标题不能为空'),
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    title = result.value.trim()
  } catch {
    return
  }

  try {
    await updateSession(row.id, title)
    row.title = title
    ElMessage.success('重命名成功')
  } catch {
    // 请求拦截器已统一提示
  }
}

async function removeMany(rows) {
  const ids = rows?.length ? rows.map((row) => row.id) : selectedIds.value
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${ids.length} 个会话吗？会话内的消息会一并清理，不可撤销。`,
      '删除会话确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteSessions(ids)
    ElMessage.success('删除成功')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}
</script>

<style scoped lang="scss">
.session-toolbar {
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 20px 26px 4px;
}

.session-actions {
  @include flex($justify: flex-end);
  padding-bottom: 8px;
}

.session-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.session-detail {
  min-height: 200px;
}

.detail-message {
  padding: 12px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
  }

  &__head {
    @include flex($align: center, $gap: 8px);
    flex-wrap: wrap;
    margin-bottom: 8px;
  }

  &__flag {
    font-size: 12px;
    color: var(--el-color-warning);
  }

  &__usage {
    margin-left: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__text {
    max-height: 240px;
    margin: 0;
    padding: 10px 12px;
    overflow: auto;
    font-family: var(--app-font-mono, monospace);
    font-size: 12px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
    background-color: var(--el-fill-color-light);
    border-radius: 10px;
  }

  &__label {
    margin-right: 6px;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__cites,
  &__followups {
    @include flex($align: center, $gap: 6px);
    flex-wrap: wrap;
    margin-top: 8px;
  }

  &__analysis {
    margin-top: 8px;
    font-size: 12px;
    color: var(--app-text-secondary);

    summary {
      cursor: pointer;
    }

    pre {
      max-height: 220px;
      margin: 6px 0 0;
      padding: 10px 12px;
      overflow: auto;
      font-family: var(--app-font-mono, monospace);
      line-height: 1.6;
      background-color: var(--el-fill-color-light);
      border-radius: 10px;
    }
  }
}
</style>
