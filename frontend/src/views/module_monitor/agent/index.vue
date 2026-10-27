<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">任务监控</h2>
        <p class="page-header__desc">Agent 任务执行态监控；当前为本地示例数据，接入后端后改用 useTable + 真实接口</p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="task-toolbar">
        <el-input
          v-model="filters.keyword"
          class="task-toolbar__search"
          placeholder="搜索任务名称或 ID"
          clearable
          size="small"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select v-model="filters.status" placeholder="全部状态" clearable size="small" class="task-toolbar__status">
          <el-option v-for="(meta, value) in AgentTaskStatusMeta" :key="value" :label="meta.label" :value="value" />
        </el-select>
        <span class="task-toolbar__count">共 {{ filteredTasks.length }} 条</span>
      </div>

      <el-table v-loading="loading" :data="pagedTasks" size="small" style="width: 100%">
        <el-table-column prop="id" label="任务 ID" width="110" />
        <el-table-column prop="name" label="任务名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="agent" label="Agent" width="130" />
        <el-table-column label="状态" width="104">
          <template #default="{ row }">
            <TaskStatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :stroke-width="6"
              :status="row.status === 'failed' ? 'exception' : row.status === 'success' ? 'success' : undefined"
            />
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ formatDuration(row.duration) }}</template>
        </el-table-column>
        <el-table-column prop="startedAt" label="开始时间" width="160" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="task-pagination">
        <el-pagination
          v-model:current-page="page.page_no"
          v-model:page-size="page.page_size"
          :total="filteredTasks.length"
          :page-sizes="[8, 15, 30]"
          layout="total, sizes, prev, pager, next"
          background
          size="small"
        />
      </div>
    </SectionCard>

    <el-drawer v-model="detailVisible" title="任务详情" size="420px">
      <template v-if="currentTask">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="任务 ID">{{ currentTask.id }}</el-descriptions-item>
          <el-descriptions-item label="任务名称">{{ currentTask.name }}</el-descriptions-item>
          <el-descriptions-item label="执行 Agent">{{ currentTask.agent }}</el-descriptions-item>
          <el-descriptions-item label="当前状态">
            <TaskStatusTag :status="currentTask.status" />
          </el-descriptions-item>
          <el-descriptions-item label="执行进度">{{ currentTask.progress }}%</el-descriptions-item>
          <el-descriptions-item label="已耗时">{{ formatDuration(currentTask.duration) }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ currentTask.startedAt }}</el-descriptions-item>
        </el-descriptions>
        <p class="task-detail__hint">
          任务链路、Token 消耗与工具调用明细待后端监控接口提供后补充展示。
        </p>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import SectionCard from '@/components/common/SectionCard.vue'
import TaskStatusTag from '@/components/common/TaskStatusTag.vue'
import { AgentTaskStatus, AgentTaskStatusMeta } from '@/enums'
import { formatDuration } from '@/utils/format'

defineOptions({ name: 'MonitorAgentView' })

const loading = ref(false)
const filters = reactive({ keyword: '', status: '' })
const page = reactive({ page_no: 1, page_size: 8 })

// TODO: 后端提供 /monitor/tasks 后改为 useTable + 服务端分页
const tasks = ref(buildMockTasks())

function buildMockTasks() {
  const samples = [
    ['知识库问答：合同条款抽取', 'RAG Agent', AgentTaskStatus.RUNNING, 62, 42.5],
    ['周报生成：研发数据汇总', 'Report Agent', AgentTaskStatus.SUCCESS, 100, 18.2],
    ['工单分类：客服会话打标', 'Classify Agent', AgentTaskStatus.RUNNING, 34, 12.7],
    ['外部接口巡检：供应商 API', 'Tool Agent', AgentTaskStatus.FAILED, 78, 96.4],
    ['向量库增量同步', 'ETL Agent', AgentTaskStatus.PENDING, 0, 0],
    ['舆情摘要：行业资讯聚合', 'Summary Agent', AgentTaskStatus.SUCCESS, 100, 26.9],
    ['SQL 生成：销售数据口径校验', 'SQL Agent', AgentTaskStatus.SUCCESS, 100, 8.4],
    ['文档解析：招投标 PDF', 'Parser Agent', AgentTaskStatus.RUNNING, 88, 55.1],
    ['风险预警：合同履约监控', 'Risk Agent', AgentTaskStatus.FAILED, 41, 63.8],
    ['多轮澄清：需求补全对话', 'Planner Agent', AgentTaskStatus.SUCCESS, 100, 31.6],
    ['批量翻译：产品手册', 'Translate Agent', AgentTaskStatus.PENDING, 0, 0],
    ['图片理解：质检报告附件', 'Vision Agent', AgentTaskStatus.SUCCESS, 100, 22.3],
  ]

  return samples.map(([name, agent, status, progress, duration], index) => ({
    id: `task-${1001 + index}`,
    name,
    agent,
    status,
    progress,
    duration,
    startedAt: `2026-10-26 0${8 + Math.floor(index / 4)}:${String((index * 7) % 60).padStart(2, '0')}:${String((index * 13) % 60).padStart(2, '0')}`,
  }))
}

const filteredTasks = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return tasks.value.filter((task) => {
    const matchStatus = !filters.status || task.status === filters.status
    const matchKeyword =
      !keyword || task.name.toLowerCase().includes(keyword) || task.id.toLowerCase().includes(keyword)
    return matchStatus && matchKeyword
  })
})

const pagedTasks = computed(() => {
  const start = (page.page_no - 1) * page.page_size
  return filteredTasks.value.slice(start, start + page.page_size)
})

watch(filters, () => {
  page.page_no = 1
})

watch(
  () => filteredTasks.value.length,
  (total) => {
    const maxPage = Math.max(1, Math.ceil(total / page.page_size))
    if (page.page_no > maxPage) page.page_no = maxPage
  },
)

const detailVisible = ref(false)
const currentTask = ref(null)

function openDetail(task) {
  currentTask.value = task
  detailVisible.value = true
}

function refresh() {
  loading.value = true
  // 模拟一次拉取动作，替换为真实接口后直接调用 load()
  setTimeout(() => {
    loading.value = false
    ElMessage.success('任务状态已刷新')
  }, 400)
}

onMounted(refresh)
</script>

<style scoped lang="scss">
.task-toolbar {
  @include flex($gap: 12px);
  flex-wrap: wrap;
  padding: 20px 26px 12px;

  &__search {
    width: 240px;
  }

  &__status {
    width: 140px;
  }

  &__count {
    margin-left: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.task-pagination {
  @include flex($justify: flex-end);
  padding: 8px 26px 20px;
}

.task-detail__hint {
  margin-top: 16px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--app-text-secondary);
}
</style>
