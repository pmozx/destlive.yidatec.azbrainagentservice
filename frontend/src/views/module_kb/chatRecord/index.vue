<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">会话记录</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/chat-session：按用户查会话记录与历史消息、引用留痕，并在此改名 / 清空 / 删除会话。会话由「AI 对话」页与契约 /v1/chat/completions 产生；单轮质量与成本指标（耗时 / token / 证据 / 失败码）走只读的 /kb/chat-metric
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="loading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="kb-cards">
      <StatCard label="问答轮数" :value="formatNumber(listStats.turns)" icon="ChatDotRound" tip="与下方筛选条件同口径，失败轮也计入" />
      <StatCard
        label="拒答率"
        :value="noEvidenceRateText"
        icon="WarningFilled"
        :tip="`${listStats.no_evidence_turns ?? 0} 轮因证据不足拒答`"
      />
      <StatCard
        label="失败轮"
        :value="formatNumber(listStats.failed_turns)"
        icon="CircleCloseFilled"
        tip="断连 / 超时 / 生成失败（只有指标、没有消息）"
      />
      <StatCard
        label="平均整轮"
        :value="formatMs(listStats.avg_total_ms)"
        icon="Timer"
        :tip="`平均首字 ${formatMs(listStats.avg_first_token_ms)}（仅流式）`"
      />
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
          <el-descriptions-item label="问答轮数">{{ sessionStats.turns ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="拒答 / 失败轮">
            {{ sessionStats.no_evidence_turns ?? 0 }} / {{ sessionStats.failed_turns ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="平均整轮">{{ formatMs(sessionStats.avg_total_ms) }}</el-descriptions-item>
          <el-descriptions-item label="平均首字">{{ formatMs(sessionStats.avg_first_token_ms) }}</el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="未获取到会话详情（可能已删除）" :image-size="80" />

        <el-alert
          v-if="sessionStats.failed_turns > 0"
          class="kb-alert"
          type="warning"
          :closable="false"
          show-icon
          :title="`本会话有 ${sessionStats.failed_turns} 轮失败：只有指标、没有助手消息，已按发生时间插入对话流`"
          description="断连 / 首字超时 / 生成失败都算失败轮；它跨页时不占消息分页，以本计数为准。清空历史不会删除指标——指标是只增的事实快照。"
        />

        <el-table
          v-loading="messageLoading || metricsLoading"
          :data="turnRows"
          size="small"
          style="width: 100%; margin-top: 16px"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="kb-expand">
                <el-descriptions v-if="row._metric" :column="3" border size="small" title="单轮指标">
                  <el-descriptions-item v-for="field in metricFields(row._metric)" :key="field.label" :label="field.label">
                    {{ field.value }}
                  </el-descriptions-item>
                </el-descriptions>
                <el-alert
                  v-else
                  type="info"
                  :closable="false"
                  show-icon
                  title="本轮没有指标：指标写入失败只告警，不影响问答结果"
                />
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
              <el-tag :type="roleTagType(row)" size="small" effect="plain">{{ roleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="正文" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row._kind === 'failed'" class="kb-failed-text">
                本轮未落助手消息：{{ row._metric.error_code || '生成失败' }}
              </span>
              <span v-else>{{ row.content }}</span>
            </template>
          </el-table-column>
          <el-table-column align="center" label="引用" width="70">
            <template #default="{ row }">{{ row._kind === 'failed' ? '-' : (row.citations || []).length }}</template>
          </el-table-column>
          <el-table-column align="center" label="依赖" width="70">
            <template #default="{ row }">{{ row._kind === 'failed' ? '-' : (row.knowledge_deps || []).length }}</template>
          </el-table-column>
          <el-table-column align="center" label="指标" width="150">
            <template #default="{ row }">
              <el-tag v-if="row._metric?.error_code" type="danger" size="small" effect="light">
                {{ row._metric.error_code }}
              </el-tag>
              <span v-else-if="row._metric" class="kb-metric-text">
                证据 {{ row._metric.evidence_count ?? 0 }} · {{ formatMs(row._metric.total_ms) }}
              </span>
              <span v-else class="kb-metric-text">-</span>
            </template>
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

        <div class="kb-pagination kb-pagination--split">
          <span class="kb-note">分页按消息计；失败轮只有指标，不占消息分页</span>
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
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  clearChatSession,
  deleteChatSession,
  getChatMessageList,
  getChatSessionDetail,
  getChatSessionList,
  getChatTurnMetricList,
  getChatTurnMetricSummary,
  renameChatSession,
} from '@/api/module_kb/chatRecord'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { mergeTurnRows, metricFields } from '@/utils/chatMetric'
import { formatDateTime, formatMs, formatNumber } from '@/utils/format'

defineOptions({ name: 'KbChatRecordView' })

const statusOptions = [
  { value: 'active', label: '进行中' },
  { value: 'cleared', label: '已清空' },
  { value: 'deleted', label: '已删除' },
]
const STATUS_TAG = { active: 'success', cleared: 'warning', deleted: 'info' }
const MESSAGE_STATUS_LABELS = { active: '正常', hidden: '已隐藏', deleted: '已删除', failed: '失败轮' }
const MESSAGE_STATUS_TAGS = { active: 'success', hidden: 'warning', deleted: 'info', failed: 'danger' }

//: 指标概览的兜底形状：接口未返回或请求失败时页面仍能渲染（显示 0 / -，而不是空白）
const EMPTY_TURNS = {
  turns: 0,
  no_evidence_turns: 0,
  no_evidence_rate: 0,
  failed_turns: 0,
  avg_total_ms: null,
  avg_first_token_ms: null,
}
//: 指标按会话一次拉全：两套分页对齐反而会错位（失败轮不占消息分页）
const METRIC_PAGE_SIZE = 100
//: 翻页上限：单会话上千轮远超正常使用，继续翻只会拖慢抽屉打开
const METRIC_MAX_PAGES = 10

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function roleLabel(role) {
  if (role === 'failed') return '失败轮'
  return role === 'user' ? '提问' : role === 'assistant' ? '回答' : role || '-'
}
/** 角色标签色：失败轮不存在于消息表，只看合并行的 `_kind` */
function roleTagType(row) {
  if (row._kind === 'failed') return 'danger'
  return row.role === 'user' ? 'info' : 'success'
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

/** 汇总条件：只送会话维度字段；指标行自身的时间范围不参与（与列表同口径） */
function buildSummaryParams(params) {
  const result = {}
  ;['session_id', 'user_id', 'title', 'status'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  return result
}

const {
  loading,
  list,
  total,
  pagination,
  query,
  load: loadSessions,
  search: searchSessions,
  reset: resetSessions,
  handlePageChange,
  handleSizeChange,
} = useTable((params) => getChatSessionList(buildQueryParams(params)), {
  defaultQuery: { session_id: '', user_id: '', title: '', status: '' },
})

// ── 列表页指标概览 ────────────────────────────────
const listStats = ref({ ...EMPTY_TURNS })
const noEvidenceRateText = computed(() => `${(Number(listStats.value.no_evidence_rate || 0) * 100).toFixed(1)}%`)

async function loadListStats() {
  try {
    listStats.value = await getChatTurnMetricSummary(buildSummaryParams(query))
  } catch {
    // 请求拦截器已统一提示错误：概览不可用不应把整个列表页拖红
    listStats.value = { ...EMPTY_TURNS }
  }
}

/** 查询 / 重置 / 刷新都要带上概览：两个数据源来自不同表，漏刷一个就会“数字不动” */
async function search() {
  await Promise.all([searchSessions(), loadListStats()])
}

async function reset() {
  await resetSessions()
  await loadListStats()
}

async function refreshAll() {
  await Promise.all([loadSessions(), loadListStats()])
}

onMounted(loadListStats)

// ── 消息抽屉 ──────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const activeSession = ref(null)
//: 本会话指标汇总：与列表页概览分开存，否则关掉抽屉后列表数字会被单会话值覆盖
const sessionStats = ref({ ...EMPTY_TURNS })

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

//: 本会话全部指标：按 `assistant_message_id` 对齐消息，失败轮单独成行
const metrics = ref([])
const metricsLoading = ref(false)
const turnRows = computed(() => mergeTurnRows(messages.value, metrics.value))

async function loadTurnMetrics(sessionId) {
  metricsLoading.value = true
  try {
    const items = []
    for (let pageNo = 1; pageNo <= METRIC_MAX_PAGES; pageNo += 1) {
      const page = await getChatTurnMetricList({ session_id: sessionId, page_no: pageNo, page_size: METRIC_PAGE_SIZE })
      const batch = page?.items ?? []
      items.push(...batch)
      if (!page?.has_next || batch.length === 0) break
    }
    metrics.value = items
  } catch {
    // 指标拿不到不影响看消息（指标写入本来就只告警）
    metrics.value = []
  } finally {
    metricsLoading.value = false
  }
}

async function loadSessionStats(sessionId) {
  try {
    sessionStats.value = await getChatTurnMetricSummary({ session_id: sessionId })
  } catch {
    sessionStats.value = { ...EMPTY_TURNS }
  }
}

/** 抽屉里的四个数据源（详情 / 消息 / 指标明细 / 指标汇总）互不依赖，并发拉 */
async function loadDrawer(sessionId) {
  await Promise.all([loadMessages(), loadTurnMetrics(sessionId), loadSessionStats(sessionId)])
}

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
  await loadDrawer(row.session_id)
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
  await loadDrawer(sessionId)
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
    refreshAll()
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
    refreshAll()
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
    refreshAll()
  } catch {
    // 请求拦截器已统一提示错误
  }
}
</script>

<style scoped lang="scss">
.kb-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.kb-toolbar {
  padding: 20px 26px 4px;
}

.kb-alert {
  margin-top: 16px;
}

.kb-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.kb-pagination--split {
  justify-content: space-between;
}

.kb-note {
  font-size: 12px;
  color: var(--app-text-muted);
}

.kb-expand {
  padding: 8px 20px 16px;
  display: grid;
  gap: 12px;
}

.kb-metric-text {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-failed-text {
  color: var(--el-color-danger);
}
</style>
