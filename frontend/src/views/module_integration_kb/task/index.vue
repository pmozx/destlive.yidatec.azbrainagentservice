<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">解析任务</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/task：任务表是文档解析的过程台账，上传与重新解析时投递，
          由进程内解析线程池执行、独立会话回写状态；文档本身也带解析状态与进度，两处口径一致
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
      <div class="task-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="空间">
            <el-select v-model="query.space_id" placeholder="全部空间" clearable style="width: 160px">
              <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="文档ID">
            <el-input v-model="query.doc_id" placeholder="精确匹配" clearable style="width: 130px" @keyup.enter="search" />
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

        <div class="task-actions">
          <span v-if="autoRefresh" class="task-actions__hint">存在未结束任务，列表每 5s 自动刷新</span>
          <el-button size="small" type="danger" plain :disabled="!selectedIds.length" @click="removeMany">
            删除记录<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
          </el-button>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="list"
        row-key="id"
        size="small"
        style="width: 100%"
        @selection-change="handleSelectionChange"
        @expand-change="handleExpandChange"
      >
        <el-table-column type="selection" width="46" />
        <!-- 展开行：任务下的四段节点明细（解析 → 分片 → 向量化 → 写入 pgvector） -->
        <el-table-column type="expand" width="40">
          <template #default="{ row }">
            <div v-loading="stepLoading[row.id]" class="task-steps">
              <el-timeline v-if="stepList(row).length">
                <el-timeline-item
                  v-for="step in stepList(row)"
                  :key="step.id"
                  :type="STEP_TIMELINE_TYPE[step.status] || 'info'"
                  :hollow="step.status === 0 || step.status === 4"
                  :timestamp="stepTimestamp(step)"
                  placement="top"
                >
                  <div class="task-step">
                    <span class="task-step__name">{{ step.stage_label || `节点 ${step.stage}` }}</span>
                    <el-tag :type="statusMeta(step.status).type" size="small" effect="light">
                      {{ statusMeta(step.status).label }}
                    </el-tag>
                    <span class="task-step__meta">{{ stepSummary(step) }}</span>
                  </div>
                  <div v-if="step.error_msg" class="task-error task-step__error">{{ step.error_msg }}</div>
                </el-timeline-item>
              </el-timeline>
              <el-empty
                v-else-if="!stepLoading[row.id]"
                description="该任务没有节点明细（步骤表上线前的历史记录）"
                :image-size="60"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="任务ID" width="90" align="center" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ TASK_TYPE_META[row.task_type] || `未知(${row.task_type})` }}</template>
        </el-table-column>
        <el-table-column prop="space_id" label="空间ID" width="90" align="center" />
        <el-table-column prop="doc_id" label="文档ID" width="90" align="center" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row.status).type" size="small" effect="light">
              {{ statusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.min(100, Math.max(0, row.progress || 0))"
              :status="progressStatus(row.status)"
              :stroke-width="8"
            />
          </template>
        </el-table-column>
        <el-table-column prop="result" label="结果摘要" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.result || '-' }}</template>
        </el-table-column>
        <el-table-column prop="error_msg" label="失败原因" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error_msg" class="task-error">{{ row.error_msg }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="重试" width="70" align="center">
          <template #default="{ row }">{{ row.retry_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="开始 / 结束" width="180">
          <template #default="{ row }">
            <div class="task-time">{{ formatDateTime(row.started_time) }}</div>
            <div class="task-time is-muted">{{ formatDateTime(row.finished_time) }}</div>
          </template>
        </el-table-column>
        <!-- 失败任务支持从断点节点续跑：解析产物与分片快照都复用，不再重跑 MinerU -->
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 3"
              link
              type="primary"
              size="small"
              :loading="retrying === row.id"
              @click="retry(row)"
            >
              重试
            </el-button>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有解析任务：在「知识文档」里上传或重新解析即可产生" :image-size="80" />
        </template>
      </el-table>

      <div class="task-pagination">
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
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { deleteTasks, getSpaceOptions, getTaskDetail, getTaskList, retryTask } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'IntegrationKbTaskView' })

/** 任务状态：与后端 model.TaskStatus 一一对应 */
const STATUS_META = {
  0: { label: '待执行', type: 'info' },
  1: { label: '执行中', type: 'warning' },
  2: { label: '成功', type: 'success' },
  3: { label: '失败', type: 'danger' },
  4: { label: '已取消', type: 'info' },
}

/** 任务类型：当前只有文档解析 */
const TASK_TYPE_META = { 1: '文档解析' }

function statusMeta(value) {
  return STATUS_META[value] ?? { label: `未知(${value})`, type: 'info' }
}

function progressStatus(status) {
  if (status === 2) return 'success'
  if (status === 3) return 'exception'
  return ''
}

// ── 节点明细（展开行按需拉取，缓存按任务ID存活于列表刷新之外） ─────────────

/** 时间轴圆点颜色：待执行/取消走 hollow，执行中与表头状态色保持一致 */
const STEP_TIMELINE_TYPE = { 1: 'primary', 2: 'success', 3: 'danger', 4: 'info' }

const stepCache = ref({})
const stepLoading = ref({})

function stepList(row) {
  return stepCache.value[row.id] ?? []
}

async function handleExpandChange(row, expandedRows) {
  if (!expandedRows.includes(row)) return
  // 已结束任务的明细不会变，有缓存就直接用；未结束的每次展开都重新拉一次
  if (stepCache.value[row.id] && row.status !== 0 && row.status !== 1) return
  stepLoading.value[row.id] = true
  try {
    const detail = await getTaskDetail(row.id)
    stepCache.value[row.id] = detail?.steps ?? []
  } catch {
    stepCache.value[row.id] = []
  } finally {
    stepLoading.value[row.id] = false
  }
}

function formatElapsed(ms) {
  if (!ms) return '0ms'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function stepTimestamp(step) {
  if (!step.started_time) return '未开始'
  const end = step.finished_time ? ` → ${formatDateTime(step.finished_time)}` : ''
  return `${formatDateTime(step.started_time)}${end}`
}

/** 节点摘要：耗时 + 产出条数 + 该节点自己的关键信息（取 detail 里已落库的字段） */
function stepSummary(step) {
  const parts = []
  if (step.status === 2) parts.push(`耗时 ${formatElapsed(step.elapsed_ms)}`)
  if (step.output_count) parts.push(`产出 ${step.output_count}`)
  const detail = step.detail || {}
  if (detail.chars) parts.push(`正文 ${detail.chars} 字符`)
  if (detail.pages) parts.push(`${detail.pages} 页`)
  if (detail.chunks) parts.push(`分片 ${detail.chunks} 条`)
  if (detail.vector_dim) parts.push(`向量 ${detail.vector_dim} 维`)
  if (detail.written) parts.push(`入库 ${detail.written} 条`)
  return parts.join(' · ')
}

const spaces = ref([])

function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.space_id) result.space_id = params.space_id
  if (params.doc_id) result.doc_id = params.doc_id
  if (params.status !== '' && params.status !== null && params.status !== undefined) result.status = params.status
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getTaskList(buildParams(params)),
  { defaultQuery: { space_id: '', doc_id: '', status: '' } },
)

const selectedIds = ref([])
/** 正在提交重试的任务ID：单行 loading，避免重复点击投递两次 */
const retrying = ref(null)

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

// ── 未结束任务自动刷新 ─────────────────────────────────────────────────

/** 待执行/执行中都算未结束：解析是分钟级任务，5s 轮询足够 */
const autoRefresh = computed(() => list.value.some((row) => row.status === 0 || row.status === 1))

let timer = null

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, (active) => {
  stopTimer()
  if (!active) return
  timer = setInterval(() => load(), 5000)
})

onUnmounted(stopTimer)

async function removeMany() {
  if (!selectedIds.value.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedIds.value.length} 条任务记录吗？只删台账，不影响已解析文档与其分片。`,
      '删除任务记录确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteTasks(selectedIds.value)
    ElMessage.success('删除成功')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}

async function retry(row) {
  try {
    await ElMessageBox.confirm(
      '将从失败节点继续执行：解析产物与分片快照已保留，不会重新调用 MinerU。确定重试吗？',
      '重试失败节点确认',
      { type: 'warning', confirmButtonText: '确定重试', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  retrying.value = row.id
  try {
    await retryTask(row.id)
    ElMessage.success('已提交重试，解析进度可在文档与任务列表查看')
    // 节点明细已重置，展开行缓存一并失效
    delete stepCache.value[row.id]
    await load()
  } catch {
    // 请求拦截器已统一提示
  } finally {
    retrying.value = null
  }
}

onMounted(async () => {
  try {
    spaces.value = (await getSpaceOptions()) ?? []
  } catch {
    spaces.value = []
  }
})
</script>

<style scoped lang="scss">
.task-toolbar {
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 20px 26px 4px;
}

.task-actions {
  @include flex($justify: flex-end, $align: center, $gap: 10px);
  padding-bottom: 8px;

  &__hint {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.task-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.task-error {
  color: var(--el-color-danger);
}

.task-time {
  font-size: 12px;
  line-height: 1.6;

  &.is-muted {
    color: var(--app-text-secondary);
  }
}

.task-steps {
  min-height: 64px;
  padding: 14px 26px 4px 48px;
}

.task-step {
  @include flex($align: center, $gap: 8px);
  flex-wrap: wrap;

  &__name {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__error {
    margin-top: 4px;
    font-size: 12px;
  }
}
</style>
