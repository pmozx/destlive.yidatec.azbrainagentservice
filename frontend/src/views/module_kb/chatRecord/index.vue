<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">会话记录</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/chat-session：按用户查会话记录与历史消息、引用留痕，并在此改名 / 清空 / 删除会话。会话由「AI 对话」页与契约 /v1/chat/completions 产生
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
          <el-form-item label="会话 ID">
            <el-input v-model="query.session_id" placeholder="会话 ID" clearable style="width: 210px" />
          </el-form-item>
          <el-form-item label="用户 ID">
            <el-input v-model="query.user_id" placeholder="sbp_sys_user.id" clearable style="width: 130px" />
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="标题关键字" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 120px">
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
        <el-table-column align="center" prop="session_id" label="会话 ID" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" prop="user_id" label="用户 ID" width="90" />
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column align="center" prop="message_count" label="消息数" width="90" />
        <el-table-column align="center" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="最近活跃" width="165">
          <template #default="{ row }">{{ formatDateTime(row.last_active_at) }}</template>
        </el-table-column>
        <el-table-column align="center" label="创建时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openMessages(row)">消息</el-button>
            <template v-if="row.status !== 'deleted'">
              <el-button link type="primary" size="small" @click="rename(row)">改名</el-button>
              <el-button link type="warning" size="small" @click="clear(row)">清空</el-button>
              <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无会话记录（由「AI 对话」页或 /v1/chat/completions 产生）" :image-size="80" />
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

    <el-drawer v-model="detailVisible" title="会话消息" size="980px" destroy-on-close>
      <div v-loading="detailLoading">
        <el-descriptions v-if="activeSession" :column="2" border size="small">
          <el-descriptions-item label="会话 ID">{{ activeSession.session_id }}</el-descriptions-item>
          <el-descriptions-item label="用户 ID">{{ activeSession.user_id }}</el-descriptions-item>
          <el-descriptions-item label="标题">{{ activeSession.title }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(activeSession.status)" size="small" effect="light">
              {{ statusLabel(activeSession.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="消息数">{{ activeSession.message_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="未删除消息">{{ activeSession.message_total ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="最近活跃">{{ formatDateTime(activeSession.last_active_at) }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(activeSession.created_time) }}</el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="未获取到会话详情（可能已删除）" :image-size="80" />

        <el-table v-loading="messageLoading" :data="messages" size="small" style="width: 100%; margin-top: 16px">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="kb-expand">
                <JsonViewer :data="row.citations" title="citations（引用八元组）" placeholder="本条消息没有引用" />
                <JsonViewer
                  :data="row.knowledge_deps"
                  title="knowledge_deps（依赖失效即整条隐藏）"
                  placeholder="本条消息没有知识依赖"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column align="center" label="角色" width="90">
            <template #default="{ row }">
              <el-tag :type="row.role === 'user' ? 'info' : 'success'" size="small" effect="plain">
                {{ roleLabel(row.role) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="content" label="正文" min-width="320" show-overflow-tooltip />
          <el-table-column align="center" label="引用" width="70">
            <template #default="{ row }">{{ (row.citations || []).length }}</template>
          </el-table-column>
          <el-table-column align="center" label="依赖" width="70">
            <template #default="{ row }">{{ (row.knowledge_deps || []).length }}</template>
          </el-table-column>
          <el-table-column align="center" label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="messageTagType(row.status)" size="small" effect="light">
                {{ messageStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column align="center" label="时间" width="165">
            <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
          </el-table-column>
          <template #empty>
            <el-empty description="该会话没有消息（可能已清空）" :image-size="70" />
          </template>
        </el-table>

        <div class="kb-pagination">
          <el-pagination
            :current-page="messagePagination.page_no"
            :page-size="messagePagination.page_size"
            :page-sizes="[10, 20, 50]"
            :total="messageTotal"
            layout="total, prev, pager, next"
            background
            size="small"
            @current-change="handleMessagePageChange"
            @size-change="handleMessageSizeChange"
          />
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  clearChatSession,
  deleteChatSession,
  getChatMessageList,
  getChatSessionDetail,
  getChatSessionList,
  renameChatSession,
} from '@/api/module_kb/chatRecord'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbChatRecordView' })

const statusOptions = [
  { value: 'active', label: '进行中' },
  { value: 'cleared', label: '已清空' },
  { value: 'deleted', label: '已删除' },
]
const STATUS_TAG = { active: 'success', cleared: 'warning', deleted: 'info' }
const MESSAGE_STATUS_LABELS = { active: '正常', hidden: '已隐藏', deleted: '已删除' }
const MESSAGE_STATUS_TAGS = { active: 'success', hidden: 'warning', deleted: 'info' }

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function roleLabel(role) {
  return role === 'user' ? '提问' : role === 'assistant' ? '回答' : role || '-'
}
function messageStatusLabel(status) {
  return MESSAGE_STATUS_LABELS[status] || status || '-'
}
function messageTagType(status) {
  return MESSAGE_STATUS_TAGS[status] || 'info'
}

/** 空值不下发：后端把空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['session_id', 'user_id', 'title', 'status'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getChatSessionList(buildQueryParams(params)),
  { defaultQuery: { session_id: '', user_id: '', title: '', status: '' } },
)

// ── 消息抽屉 ──────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const activeSession = ref(null)

const {
  loading: messageLoading,
  list: messages,
  total: messageTotal,
  pagination: messagePagination,
  query: messageQuery,
  load: loadMessages,
  handlePageChange: handleMessagePageChange,
  handleSizeChange: handleMessageSizeChange,
} = useTable((params) => getChatMessageList(params), {
  defaultQuery: { session_id: '' },
  immediate: false,
})

async function openMessages(row) {
  activeSession.value = { ...row }
  messageVisibleGuard(row.session_id)
  detailVisible.value = true
  detailLoading.value = true
  try {
    activeSession.value = await getChatSessionDetail(row.session_id)
  } catch {
    // 请求拦截器已统一提示错误；详情拿不到时仍用列表行展示
  } finally {
    detailLoading.value = false
  }
  await loadMessages()
}

function messageVisibleGuard(sessionId) {
  messageQuery.session_id = sessionId
  messagePagination.page_no = 1
}

async function refreshDetail(sessionId) {
  if (!detailVisible.value || activeSession.value?.session_id !== sessionId) return
  detailLoading.value = true
  try {
    activeSession.value = await getChatSessionDetail(sessionId)
  } catch {
    // 会话可能已被删除：保留抽屉，只提示消息列表以实际查询结果为准
  } finally {
    detailLoading.value = false
  }
  await loadMessages()
}

// ── 改名 / 清空 / 删除 ────────────────────────────────────────────
async function rename(row) {
  let value
  try {
    const result = await ElMessageBox.prompt('请输入新的会话标题（最多 100 字符）', '修改会话标题', {
      inputValue: row.title,
      inputValidator: (text) => {
        const title = (text || '').trim()
        if (!title) return '标题不能为空'
        if (title.length > 100) return '标题最多 100 字符'
        return true
      },
    })
    value = (result.value || '').trim()
  } catch {
    return
  }
  try {
    await renameChatSession(row.session_id, value)
    ElMessage.success('修改会话标题成功')
    load()
    refreshDetail(row.session_id)
  } catch {
    // 请求拦截器已统一提示错误
  }
}

async function clear(row) {
  try {
    await ElMessageBox.confirm(
      `确定清空会话「${row.title || row.session_id}」的历史消息吗？会话保留，此前的消息不再展示。`,
      '清空确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await clearChatSession(row.session_id)
    ElMessage.success('清空会话历史成功')
    load()
    refreshDetail(row.session_id)
  } catch {
    // 请求拦截器已统一提示错误
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${row.title || row.session_id}」吗？删除后该会话不再出现在列表中。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteChatSession(row.session_id)
    ElMessage.success('删除会话记录成功')
    if (activeSession.value?.session_id === row.session_id) {
      detailVisible.value = false
    }
    load()
  } catch {
    // 请求拦截器已统一提示错误
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

.kb-expand {
  padding: 8px 20px 16px;
  display: grid;
  gap: 12px;
}
</style>
