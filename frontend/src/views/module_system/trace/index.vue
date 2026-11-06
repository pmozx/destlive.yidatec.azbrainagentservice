<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">接入调用追踪</h2>
        <p class="page-header__desc">
          GSI 业务系统调用契约 /v1 的取证视图：调用明细（默认只看 /v1，含五头校验与被拒原因）、防重放台账、幂等台账、结果台账；
          填 request_id 可把四张表串成一条时间线
        </p>
      </div>
      <div class="page-header__actions">
        <el-input
          v-model="traceId"
          size="small"
          style="width: 250px"
          placeholder="输入 request_id 追踪"
          clearable
          @keyup.enter="openTimeline(traceId)"
        >
          <template #prepend>追踪</template>
        </el-input>
        <el-select v-model="hours" size="small" style="width: 118px" @change="loadSummary">
          <el-option v-for="item in hourOptions" :key="item" :label="`近 ${item} 小时`" :value="item" />
        </el-select>
        <el-button size="small" :loading="summaryLoading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div v-loading="summaryLoading" class="trace-cards">
      <StatCard label="契约调用" :value="summary.call_total" icon="Connection" :tip="`近 ${summary.window_hours} 小时 · 仅 /v1`" />
      <StatCard label="失败调用" :value="summary.call_failed" icon="Warning" color="var(--el-color-danger)" :tip="failRateText" />
      <StatCard label="签名校验失败" :value="summary.signature_failed" icon="Lock" color="var(--el-color-danger)" tip="五头签名比对不通过" />
      <StatCard label="重放拦截" :value="summary.nonce_reused" icon="RefreshLeft" color="var(--el-color-warning)" tip="同 nonce 二次到达" />
      <StatCard label="幂等冲突" :value="summary.idempotency_conflict" icon="CopyDocument" color="var(--el-color-warning)" tip="同 ID 异正文返回 409" />
      <StatCard label="在途请求" :value="summary.request_running" icon="Timer" tip="结果台账中 running" />
    </div>

    <SectionCard flush>
      <el-tabs v-model="activeTab" class="trace-tabs" @tab-change="handleTabChange">
        <el-tab-pane label="调用明细" name="calls">
          <div class="trace-toolbar">
            <el-form :inline="true" size="small" @submit.prevent>
              <el-form-item label="追踪 ID">
                <el-input v-model="calls.query.request_id" placeholder="模糊匹配" clearable style="width: 160px" />
              </el-form-item>
              <el-form-item label="请求路径">
                <el-input v-model="calls.query.request_path" placeholder="留空只看 /v1" clearable style="width: 180px" />
              </el-form-item>
              <el-form-item label="调用方">
                <el-input v-model="calls.query.client_id" placeholder="client_id" clearable style="width: 140px" />
              </el-form-item>
              <el-form-item label="请求体">
                <el-input v-model="calls.query.request_body" placeholder="报文关键字" clearable style="width: 140px" />
              </el-form-item>
              <el-form-item label="响应体">
                <el-input v-model="calls.query.response_body" placeholder="报文关键字" clearable style="width: 140px" />
              </el-form-item>
              <el-form-item label="签名">
                <el-select v-model="calls.query.signature_result" placeholder="全部" clearable style="width: 108px">
                  <el-option label="通过" value="passed" />
                  <el-option label="失败" value="failed" />
                  <el-option label="未校验" value="skipped" />
                </el-select>
              </el-form-item>
              <el-form-item label="nonce">
                <el-select v-model="calls.query.nonce_state" placeholder="全部" clearable style="width: 108px">
                  <el-option label="首用" value="first_use" />
                  <el-option label="重放" value="reused" />
                  <el-option label="未校验" value="not_checked" />
                </el-select>
              </el-form-item>
              <el-form-item label="结果">
                <el-select v-model="calls.query.status" placeholder="全部" clearable style="width: 96px">
                  <el-option label="成功" :value="0" />
                  <el-option label="失败" :value="1" />
                </el-select>
              </el-form-item>
              <el-form-item label="时间">
                <el-date-picker
                  v-model="ranges.calls"
                  type="datetimerange"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  start-placeholder="开始"
                  end-placeholder="结束"
                  style="width: 330px"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="activeState.loading" @click="loadTab('calls')">查询</el-button>
                <el-button @click="resetTab('calls')">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="防重放台账" name="nonces">
          <div class="trace-toolbar">
            <el-form :inline="true" size="small" @submit.prevent>
              <el-form-item label="调用方">
                <el-input v-model="nonces.query.client_id" placeholder="client_id" clearable style="width: 150px" />
              </el-form-item>
              <el-form-item label="nonce">
                <el-input v-model="nonces.query.nonce" placeholder="模糊匹配" clearable style="width: 200px" />
              </el-form-item>
              <el-form-item label="关联请求">
                <el-input v-model="nonces.query.request_id" placeholder="request_id" clearable style="width: 180px" />
              </el-form-item>
              <el-form-item label="时间">
                <el-date-picker
                  v-model="ranges.nonces"
                  type="datetimerange"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  start-placeholder="开始"
                  end-placeholder="结束"
                  style="width: 330px"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="activeState.loading" @click="loadTab('nonces')">查询</el-button>
                <el-button @click="resetTab('nonces')">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="幂等台账" name="idempotency">
          <div class="trace-toolbar">
            <el-form :inline="true" size="small" @submit.prevent>
              <el-form-item label="幂等域">
                <el-select v-model="idempotency.query.scope" placeholder="全部" clearable style="width: 130px">
                  <el-option label="job 解析任务" value="job" />
                  <el-option label="request 写请求" value="request" />
                  <el-option label="event 变更事件" value="event" />
                </el-select>
              </el-form-item>
              <el-form-item label="幂等键">
                <el-input v-model="idempotency.query.biz_id" placeholder="job_id/request_id/event_id" clearable style="width: 200px" />
              </el-form-item>
              <el-form-item label="状态">
                <el-select v-model="idempotency.query.status" placeholder="全部" clearable style="width: 118px">
                  <el-option label="处理中" value="processing" />
                  <el-option label="成功" value="succeeded" />
                  <el-option label="失败" value="failed" />
                  <el-option label="墓碑" value="tombstone" />
                </el-select>
              </el-form-item>
              <el-form-item label="时间">
                <el-date-picker
                  v-model="ranges.idempotency"
                  type="datetimerange"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  start-placeholder="开始"
                  end-placeholder="结束"
                  style="width: 330px"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="activeState.loading" @click="loadTab('idempotency')">查询</el-button>
                <el-button @click="resetTab('idempotency')">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="结果台账" name="requestLogs">
          <div class="trace-toolbar">
            <el-form :inline="true" size="small" @submit.prevent>
              <el-form-item label="请求 ID">
                <el-input v-model="requestLogs.query.request_id" placeholder="模糊匹配" clearable style="width: 190px" />
              </el-form-item>
              <el-form-item label="操作类型">
                <el-select v-model="requestLogs.query.operation" placeholder="全部" clearable style="width: 130px">
                  <el-option label="completion" value="completion" />
                  <el-option label="mutation" value="mutation" />
                  <el-option label="delete" value="delete" />
                </el-select>
              </el-form-item>
              <el-form-item label="状态">
                <el-select v-model="requestLogs.query.status" placeholder="全部" clearable style="width: 118px">
                  <el-option label="执行中" value="running" />
                  <el-option label="成功" value="succeeded" />
                  <el-option label="失败" value="failed" />
                  <el-option label="已过期" value="expired" />
                </el-select>
              </el-form-item>
              <el-form-item label="时间">
                <el-date-picker
                  v-model="ranges.requestLogs"
                  type="datetimerange"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  start-placeholder="开始"
                  end-placeholder="结束"
                  style="width: 330px"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="activeState.loading" @click="loadTab('requestLogs')">查询</el-button>
                <el-button @click="resetTab('requestLogs')">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>
      </el-tabs>

      <el-table v-loading="activeState.loading" :data="activeState.list" size="small" style="width: 100%">
        <el-table-column
          v-for="col in activeColumns"
          :key="col.key"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth"
          :show-overflow-tooltip="Boolean(col.tooltip)"
        >
          <template #default="{ row }">
            <el-tag v-if="col.tag" :type="col.tag(row).type" size="small" effect="light">{{ col.tag(row).text }}</el-tag>
            <span v-else>{{ cellText(col, row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="activeTab === 'calls' ? 128 : 84" fixed="right">
          <template #default="{ row }">
            <el-button v-if="activeTab === 'calls'" link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" :disabled="!traceKeyOf(row)" @click="openTimeline(traceKeyOf(row))">
              追踪
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="emptyTexts[activeTab]" :image-size="80" />
        </template>
      </el-table>

      <div class="trace-pagination">
        <el-pagination
          :current-page="activeState.pagination.page_no"
          :page-size="activeState.pagination.page_size"
          :page-sizes="[10, 20, 50]"
          :total="activeState.total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </SectionCard>

    <el-dialog v-model="detailVisible" title="调用详情" width="820px">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="追踪 ID">{{ detail.request_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="调用方">{{ detail.client_id || detail.username || '-' }}</el-descriptions-item>
            <el-descriptions-item label="请求">{{ detail.request_method }} {{ detail.request_path }}</el-descriptions-item>
            <el-descriptions-item label="响应码">{{ detail.response_code }}</el-descriptions-item>
            <el-descriptions-item label="鉴权方式">{{ detail.auth_mode || '-' }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ detail.process_time ? `${detail.process_time} ms` : '-' }}</el-descriptions-item>
            <el-descriptions-item label="来源 IP">{{ detail.request_ip || '-' }}</el-descriptions-item>
            <el-descriptions-item label="调用时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item v-if="detail.description" label="异常摘要" :span="2">
              {{ detail.description }}
            </el-descriptions-item>
            <el-descriptions-item v-if="detail.request_query" label="查询串" :span="2">
              {{ detail.request_query }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="trace-detail__block">
            <JsonViewer
              title="安全头校验（security_check）"
              :data="detail.security_check"
              max-height="200px"
              placeholder="该请求未进入签名校验（如 JWT 调用或历史数据）"
            />
          </div>
          <div class="trace-detail__block">
            <JsonViewer title="请求参数" :data="detail.request_payload" max-height="200px" placeholder="无请求体（GET 或空载荷）" />
          </div>
          <div class="trace-detail__block">
            <JsonViewer title="响应报文" :data="detail.response_json" max-height="200px" placeholder="无响应体" />
          </div>
        </template>
        <el-empty v-else description="未获取到调用详情" :image-size="80" />
      </div>
    </el-dialog>

    <el-dialog v-model="timelineVisible" :title="`调用链路追踪 · ${timeline?.request_id || traceId}`" width="900px">
      <div v-loading="timelineLoading">
        <el-empty v-if="timeline && !timeline.found" description="四张台账都没有命中该 request_id" :image-size="80" />
        <template v-else-if="timeline">
          <div v-for="group in timelineGroups" :key="group.key" class="trace-timeline__group">
            <h4 class="trace-timeline__title">
              {{ group.title }}
              <span class="trace-timeline__count">{{ group.rows.length }}</span>
              <small class="trace-timeline__hint">{{ group.hint }}</small>
            </h4>
            <el-table :data="group.rows" size="small" style="width: 100%">
              <el-table-column
                v-for="col in group.columns"
                :key="col.key"
                :label="col.label"
                :width="col.width"
                :min-width="col.minWidth"
                :show-overflow-tooltip="Boolean(col.tooltip)"
              >
                <template #default="{ row }">
                  <el-tag v-if="col.tag" :type="col.tag(row).type" size="small" effect="light">{{ col.tag(row).text }}</el-tag>
                  <span v-else>{{ cellText(col, row) }}</span>
                </template>
              </el-table-column>
              <template #empty>
                <span class="trace-timeline__none">无记录</span>
              </template>
            </el-table>
          </div>
        </template>
        <el-empty v-else description="请先填写 request_id" :image-size="80" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getAccessTraceCallDetail,
  getAccessTraceCalls,
  getAccessTraceIdempotency,
  getAccessTraceNonces,
  getAccessTraceRequestLogs,
  getAccessTraceSummary,
  getAccessTraceTimeline,
} from '@/api/module_system/accessTrace'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'SystemAccessTraceView' })

/** 去掉空值再下发：空串会被后端当成条件（like '' 命中全表），但 0 是有效取值不能丢 */
function compact(params) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined),
  )
}

/** 分页参数 + 台账过滤项 + 时间范围（后端 BaseQueryParam.created_time 收 [开始, 结束] 数组） */
function buildParams(params, extra, range) {
  const merged = { page_no: params.page_no, page_size: params.page_size, ...extra }
  if (Array.isArray(range) && range.length === 2) merged.created_time = range
  return compact(merged)
}

// ── 概览指标 ────────────────────────────────────────────────────────────

const hourOptions = [1, 6, 24, 72, 168]
const hours = ref(24)
const summaryLoading = ref(false)
const summary = ref(emptySummary())

function emptySummary() {
  return {
    window_hours: hours.value,
    call_total: 0,
    call_failed: 0,
    signature_failed: 0,
    nonce_reused: 0,
    idempotency_conflict: 0,
    request_running: 0,
  }
}

const failRateText = computed(() => {
  const total = summary.value.call_total
  if (!total) return '窗口内暂无调用'
  return `失败率 ${((summary.value.call_failed / total) * 100).toFixed(1)}%`
})

async function loadSummary() {
  summaryLoading.value = true
  try {
    summary.value = { ...emptySummary(), ...((await getAccessTraceSummary({ hours: hours.value })) || {}) }
  } catch {
    summary.value = emptySummary()
  } finally {
    summaryLoading.value = false
  }
}

// ── 四个台账视图 ────────────────────────────────────────────────────────

const activeTab = ref('calls')
const ranges = reactive({ calls: [], nonces: [], idempotency: [], requestLogs: [] })
const loaded = reactive({ calls: false, nonces: false, idempotency: false, requestLogs: false })

const calls = useTable(
  (params) =>
    getAccessTraceCalls(
      buildParams(
        params,
        {
          request_id: params.request_id,
          request_path: params.request_path,
          client_id: params.client_id,
          signature_result: params.signature_result,
          nonce_state: params.nonce_state,
          status: params.status,
          request_body: params.request_body,
          response_body: params.response_body,
        },
        ranges.calls,
      ),
    ),
  {
    pageSize: 20,
    immediate: false,
    defaultQuery: {
      request_id: '',
      request_path: '',
      client_id: '',
      signature_result: '',
      nonce_state: '',
      status: '',
      request_body: '',
      response_body: '',
    },
  },
)

const nonces = useTable(
  (params) =>
    getAccessTraceNonces(
      buildParams(
        params,
        { client_id: params.client_id, nonce: params.nonce, request_id: params.request_id },
        ranges.nonces,
      ),
    ),
  { pageSize: 20, immediate: false, defaultQuery: { client_id: '', nonce: '', request_id: '' } },
)

const idempotency = useTable(
  (params) =>
    getAccessTraceIdempotency(
      buildParams(params, { scope: params.scope, biz_id: params.biz_id, status: params.status }, ranges.idempotency),
    ),
  { pageSize: 20, immediate: false, defaultQuery: { scope: '', biz_id: '', status: '' } },
)

const requestLogs = useTable(
  (params) =>
    getAccessTraceRequestLogs(
      buildParams(
        params,
        { request_id: params.request_id, operation: params.operation, status: params.status },
        ranges.requestLogs,
      ),
    ),
  { pageSize: 20, immediate: false, defaultQuery: { request_id: '', operation: '', status: '' } },
)

const states = { calls, nonces, idempotency, requestLogs }

const activeState = computed(() => {
  const state = states[activeTab.value]
  return {
    loading: state.loading.value,
    list: state.list.value,
    total: state.total.value,
    pagination: state.pagination,
  }
})

function loadTab(name) {
  loaded[name] = true
  return states[name].load()
}

function handleTabChange(name) {
  if (!loaded[name]) loadTab(name)
}

function resetTab(name) {
  ranges[name] = []
  states[name].reset()
}

function handlePageChange(page) {
  const name = activeTab.value
  states[name].pagination.page_no = page
  loadTab(name)
}

function handleSizeChange(size) {
  const name = activeTab.value
  states[name].pagination.page_size = size
  states[name].pagination.page_no = 1
  loadTab(name)
}

function refreshAll() {
  loadSummary()
  loadTab(activeTab.value)
}

// ── 列定义（四个台账共用一个表格）────────────────────────────────────────

const SIGNATURE_TAGS = { passed: ['通过', 'success'], failed: ['失败', 'danger'], skipped: ['未校验', 'info'] }
const NONCE_TAGS = { first_use: ['首用', 'success'], reused: ['重放', 'danger'], not_checked: ['未校验', 'info'] }
const SCOPE_TAGS = { job: ['job 解析', 'primary'], request: ['request 写请求', 'success'], event: ['event 变更', 'warning'] }
const IDEMPOTENCY_TAGS = {
  processing: ['处理中', 'warning'],
  succeeded: ['成功', 'success'],
  failed: ['失败', 'danger'],
  tombstone: ['墓碑', 'info'],
}
const REQUEST_LOG_TAGS = {
  running: ['执行中', 'warning'],
  succeeded: ['成功', 'success'],
  failed: ['失败', 'danger'],
  expired: ['已过期', 'info'],
}
const OPERATION_TAGS = { completion: 'primary', mutation: 'warning', delete: 'danger' }

function tagOf(map, value) {
  const [text, type] = map[value] || [value || '-', 'info']
  return { text, type }
}

function codeTag(code) {
  if (code >= 500) return { text: `${code}`, type: 'danger' }
  if (code >= 400) return { text: `${code}`, type: 'warning' }
  return { text: `${code}`, type: 'success' }
}

const signatureOf = (row) => row.security_check?.signature_result
const nonceStateOf = (row) => row.security_check?.nonce_state

const columnSets = {
  calls: [
    { key: 'created_time', label: '调用时间', width: 165, format: (value) => formatDateTime(value) },
    { key: 'request_method', label: '方法', width: 78 },
    { key: 'request_path', label: '请求路径', minWidth: 200, tooltip: true },
    { key: 'response_code', label: '状态码', width: 88, tag: (row) => codeTag(row.response_code) },
    { key: 'signature', label: '签名', width: 88, get: signatureOf, tag: (row) => tagOf(SIGNATURE_TAGS, signatureOf(row)) },
    { key: 'nonce_state', label: 'nonce', width: 96, get: nonceStateOf, tag: (row) => tagOf(NONCE_TAGS, nonceStateOf(row)) },
    { key: 'request_id', label: '追踪 ID', minWidth: 170, tooltip: true },
    { key: 'client_id', label: '调用方', width: 120, tooltip: true },
    { key: 'process_time', label: '耗时', width: 88, format: (value) => (value ? `${value} ms` : '-') },
  ],
  nonces: [
    { key: 'created_time', label: '首用时间', width: 165, format: (value) => formatDateTime(value) },
    { key: 'client_id', label: '调用方', width: 130, tooltip: true },
    { key: 'nonce', label: 'nonce', minWidth: 220, tooltip: true },
    { key: 'request_id', label: '关联请求 ID', minWidth: 180, tooltip: true },
    { key: 'expires_at', label: '过期时间', width: 165, format: (value) => formatDateTime(value) },
  ],
  idempotency: [
    { key: 'created_time', label: '创建时间', width: 165, format: (value) => formatDateTime(value) },
    { key: 'scope', label: '幂等域', width: 120, tag: (row) => tagOf(SCOPE_TAGS, row.scope) },
    { key: 'biz_id', label: '幂等键（biz_id）', minWidth: 200, tooltip: true },
    { key: 'status', label: '状态', width: 96, tag: (row) => tagOf(IDEMPOTENCY_TAGS, row.status) },
    { key: 'body_hash', label: '正文摘要', width: 140, format: (value) => (value ? `${String(value).slice(0, 12)}…` : '-') },
    { key: 'expires_at', label: '到期', width: 170, format: (value) => (value ? formatDateTime(value) : '永不清理（墓碑）') },
  ],
  requestLogs: [
    { key: 'created_time', label: '创建时间', width: 165, format: (value) => formatDateTime(value) },
    { key: 'request_id', label: '请求 ID', minWidth: 190, tooltip: true },
    {
      key: 'operation',
      label: '操作类型',
      width: 116,
      tag: (row) => ({ text: row.operation, type: OPERATION_TAGS[row.operation] || 'info' }),
    },
    { key: 'status', label: '状态', width: 96, tag: (row) => tagOf(REQUEST_LOG_TAGS, row.status) },
    {
      key: 'result_visible',
      label: '结果可查',
      width: 94,
      tag: (row) => (row.result_visible ? { text: '可查', type: 'success' } : { text: '已隐藏', type: 'info' }),
    },
    { key: 'error_code', label: '失败码', width: 170, tooltip: true },
    { key: 'expires_at', label: '可查询期限', width: 165, format: (value) => formatDateTime(value) },
  ],
}

const activeColumns = computed(() => columnSets[activeTab.value])

const emptyTexts = {
  calls: '暂无契约调用记录：GSI 调用 /v1 后自动留痕（被拒请求同样入库）',
  nonces: '暂无防重放登记：验签通过的请求才会登记 nonce，重放不新增行',
  idempotency: '暂无幂等台账：写入类接口首次受理时登记，重试命中后回放首次结果',
  requestLogs: '暂无结果台账：生成/写操作受理后登记，超时可用 #15 按 request_id 查询',
}

function cellText(col, row) {
  const raw = col.get ? col.get(row) : row[col.key]
  if (col.format) return col.format(raw, row)
  return raw === null || raw === undefined || raw === '' ? '-' : raw
}

/** 追踪主键：调用/结果台账用 request_id，幂等台账用 biz_id（业务键） */
function traceKeyOf(row) {
  return row.request_id || row.biz_id || ''
}

// ── 详情与链路追踪 ──────────────────────────────────────────────────────

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getAccessTraceCallDetail(row.id)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}

const traceId = ref('')
const timelineVisible = ref(false)
const timelineLoading = ref(false)
const timeline = ref(null)

async function openTimeline(requestId) {
  const rid = String(requestId || '').trim()
  if (!rid) {
    ElMessage.warning('请先填写要追踪的 request_id')
    return
  }
  traceId.value = rid
  timelineVisible.value = true
  timelineLoading.value = true
  timeline.value = null
  try {
    timeline.value = await getAccessTraceTimeline(rid)
  } catch {
    timeline.value = null
  } finally {
    timelineLoading.value = false
  }
}

const timelineGroups = computed(() => {
  const data = timeline.value
  if (!data) return []
  return [
    {
      key: 'calls',
      title: '调用明细',
      hint: 'sbp_ai_system_api_log · request_id 精确匹配',
      rows: data.calls || [],
      columns: columnSets.calls.filter((col) => ['created_time', 'request_method', 'request_path', 'response_code', 'signature'].includes(col.key)),
    },
    {
      key: 'nonces',
      title: '防重放登记',
      hint: 'sbp_ai_system_nonce · 只登记首用',
      rows: data.nonces || [],
      columns: columnSets.nonces,
    },
    {
      key: 'idempotency',
      title: '幂等台账',
      hint: 'sbp_ai_system_idempotency · biz_id 精确或 request_id-* 前缀',
      rows: data.idempotency || [],
      columns: columnSets.idempotency.filter((col) => ['created_time', 'scope', 'biz_id', 'status'].includes(col.key)),
    },
    {
      key: 'request_logs',
      title: '结果台账',
      hint: 'sbp_ai_system_request_log · 超时结果查询的承接方',
      rows: data.request_logs || [],
      columns: columnSets.requestLogs.filter((col) => ['created_time', 'operation', 'status', 'error_code'].includes(col.key)),
    },
  ]
})

onMounted(() => {
  loadSummary()
  loadTab('calls')
})
</script>

<style scoped lang="scss">
.trace-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.trace-tabs {
  padding: 10px 26px 0;

  :deep(.el-tabs__header) {
    margin-bottom: 0;
  }
}

.trace-toolbar {
  padding: 16px 0 2px;
}

.trace-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.trace-detail__block {
  margin-top: 14px;
}

.trace-timeline__group + .trace-timeline__group {
  margin-top: 18px;
}

.trace-timeline__title {
  @include flex($align: baseline, $gap: 8px);
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.trace-timeline__count {
  padding: 0 8px;
  border-radius: 10px;
  background-color: var(--app-tint-bg);
  font-size: 12px;
  font-weight: 500;
  color: var(--el-color-primary);
}

.trace-timeline__hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--app-text-muted);
}

.trace-timeline__none {
  font-size: 12px;
  color: var(--app-text-muted);
}
</style>
