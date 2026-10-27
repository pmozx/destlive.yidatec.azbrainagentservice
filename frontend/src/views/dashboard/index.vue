<template>
  <div class="page">
    <!-- 顶部 Hero：浅粉渐变 + 居中标题 + 检索条 -->
    <section class="hero">
      <p class="eyebrow hero__eyebrow">AGENT RUNTIME MONITORING</p>
      <h1 class="hero__title">看清每一次智能体运行</h1>
      <p class="hero__desc">
        以运行总览为统一入口，汇总 Agent 服务状态、任务执行进度与接口健康度；支持按任务名、Agent 与任务 ID 快速定位，并对失败任务给出提示。
      </p>

      <div class="hero__search">
        <el-icon class="hero__search-icon" :size="18"><Search /></el-icon>
        <input
          v-model="keyword"
          class="hero__search-input"
          type="search"
          placeholder="输入任务名称 / Agent / 任务 ID ..."
          @keyup.enter="applySearch"
        />
        <button class="hero__search-btn" type="button" @click="applySearch">
          <el-icon :size="16"><Position /></el-icon>
          <span>检索任务</span>
        </button>
      </div>

      <div class="hero__meta">
        <div class="hero__links">
          <span class="hero__link is-active">运行总览</span>
          <em>|</em>
          <button class="hero__link" type="button" @click="router.push('/monitor/agent')">
            去任务监控维护
          </button>
        </div>
        <span class="hero__updated">最近更新 {{ updatedAt }}</span>
      </div>
    </section>

    <!-- 指标条：白色面板内嵌浅粉指标块 -->
    <section class="panel stat-panel">
      <StatCard
        label="Agent 服务"
        :value="health.checking ? '检测中' : health.online ? '在线' : '离线'"
        :color="health.online ? '#1f8a63' : '#c2503a'"
        icon="Cpu"
        :tip="health.tip"
      />
      <StatCard label="运行中任务" :value="overview.running" unit="个" icon="Loading" tip="示例数据" />
      <StatCard label="今日调用量" :value="formatCount(overview.calls)" unit="次" icon="DataLine" tip="示例数据" />
      <StatCard label="平均响应" :value="overview.avgLatency" unit="ms" icon="Timer" tip="示例数据" />
    </section>

    <!-- 内容面板：下划线页签 + 内容区 -->
    <section class="panel">
      <header class="panel__tabs">
        <button
          v-for="pane in panes"
          :key="pane.key"
          class="panel__tab"
          :class="{ 'is-active': activePane === pane.key }"
          type="button"
          @click="activePane = pane.key"
        >
          {{ pane.label }}
        </button>
        <span class="panel__tabs-extra">
          {{ activePane === 'tasks' ? `共 ${filteredTasks.length} 条记录` : `${fastEnterConfig.items.length} 个入口` }}
        </span>
      </header>

      <div v-show="activePane === 'tasks'" class="panel__body">
        <el-table :data="filteredTasks" size="small" style="width: 100%" empty-text="没有匹配的任务">
          <el-table-column prop="name" label="任务" min-width="200" show-overflow-tooltip />
          <el-table-column prop="agent" label="Agent" width="130" />
          <el-table-column label="状态" width="104">
            <template #default="{ row }">
              <TaskStatusTag :status="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="进度" width="130">
            <template #default="{ row }">
              <el-progress :percentage="row.progress" :stroke-width="6" :show-text="false" />
            </template>
          </el-table-column>
          <el-table-column prop="startedAt" label="开始时间" width="170" />
        </el-table>

        <div class="panel__footer">
          <span>任务数据为本地示例，接入后端监控接口后替换</span>
          <el-button link type="primary" @click="router.push('/monitor/agent')">
            查看全部
            <el-icon class="panel__footer-icon"><ArrowRight /></el-icon>
          </el-button>
        </div>
      </div>

      <div v-show="activePane === 'entries'" class="panel__body">
        <div class="cat-section">
          <div class="cat-section__head">
            <p class="eyebrow">QUICK ACCESS</p>
            <div class="cat-section__title-row">
              <h3 class="cat-section__title">按需要直达的功能入口</h3>
              <span class="cat-section__meta">{{ fastEnterConfig.items.length }} 个入口</span>
            </div>
          </div>

          <div class="cat-grid">
            <button
              v-for="item in fastEnterConfig.items"
              :key="item.path"
              class="cat-card"
              type="button"
              @click="openEntry(item)"
            >
              <span class="cat-card__name">
                <el-icon :size="15"><component :is="item.icon" /></el-icon>
                {{ item.title }}
              </span>
              <span class="cat-card__meta">{{ item.desc }}</span>
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- 服务信息 -->
    <SectionCard title="服务信息" subtitle="运行环境与后端连通情况">
      <template #extra>
        <el-button size="small" :loading="health.checking" @click="runHealthCheck">
          <el-icon><Connection /></el-icon>
          <span>重新检测</span>
        </el-button>
      </template>

      <el-descriptions :column="2" size="small" border>
        <el-descriptions-item label="前端版本">v{{ appConfig.version }}</el-descriptions-item>
        <el-descriptions-item label="构建模式">{{ buildMode }}</el-descriptions-item>
        <el-descriptions-item label="接口基础路径">{{ apiBase }}</el-descriptions-item>
        <el-descriptions-item label="后端连通性">
          <el-tag :type="health.online ? 'success' : 'danger'" size="small">
            {{ health.online ? '在线' : '离线' }}
          </el-tag>
          <span v-if="health.latency" class="dashboard__latency">{{ health.latency }} ms</span>
        </el-descriptions-item>
        <el-descriptions-item label="接口文档">
          <el-link type="primary" :underline="false" @click="openDoc(appConfig.swaggerUrl)">Swagger</el-link>
        </el-descriptions-item>
        <el-descriptions-item label="前端托管">app.frontend('/ui')</el-descriptions-item>
      </el-descriptions>
    </SectionCard>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { checkBackendHealth } from '@/api/module_monitor/endpoint'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import TaskStatusTag from '@/components/common/TaskStatusTag.vue'
import { appConfig, fastEnterConfig } from '@/config'
import { AgentTaskStatus } from '@/enums'

defineOptions({ name: 'DashboardView' })

const router = useRouter()

const buildMode = import.meta.env.MODE
const apiBase = import.meta.env.VITE_APP_BASE_API || '/api/v1'

const panes = [
  { key: 'tasks', label: '最近任务' },
  { key: 'entries', label: '快捷入口' },
]
const activePane = ref('tasks')
const keyword = ref('')

// TODO: 后端提供监控接口后替换为真实数据
const overview = reactive({ running: 3, calls: 1286, avgLatency: 186 })

const recentTasks = ref([
  { id: 'task-1001', name: '知识库问答：合同条款抽取', agent: 'RAG Agent', status: AgentTaskStatus.RUNNING, progress: 62, startedAt: '2026-10-26 09:12:08' },
  { id: 'task-1002', name: '周报生成：研发数据汇总', agent: 'Report Agent', status: AgentTaskStatus.SUCCESS, progress: 100, startedAt: '2026-10-26 08:52:41' },
  { id: 'task-1003', name: '工单分类：客服会话打标', agent: 'Classify Agent', status: AgentTaskStatus.RUNNING, progress: 34, startedAt: '2026-10-26 09:05:17' },
  { id: 'task-1004', name: '外部接口巡检：供应商 API', agent: 'Tool Agent', status: AgentTaskStatus.FAILED, progress: 78, startedAt: '2026-10-26 08:31:02' },
  { id: 'task-1005', name: '向量库增量同步', agent: 'ETL Agent', status: AgentTaskStatus.PENDING, progress: 0, startedAt: '2026-10-26 09:20:00' },
])

// 取最近一条任务的时间作为「最近更新」
const updatedAt = computed(() => {
  const latest = recentTasks.value
    .map((task) => task.startedAt)
    .sort()
    .pop()
  return latest ? latest.slice(11, 16) : '-'
})

const filteredTasks = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return recentTasks.value
  return recentTasks.value.filter(
    (task) =>
      task.name.toLowerCase().includes(text) ||
      task.agent.toLowerCase().includes(text) ||
      task.id.toLowerCase().includes(text),
  )
})

const health = reactive({ online: false, checking: false, tip: '尚未检测', latency: 0 })

function formatCount(value) {
  return value.toLocaleString('zh-CN')
}

function applySearch() {
  activePane.value = 'tasks'
  if (keyword.value.trim() && filteredTasks.value.length === 0) {
    ElMessage.warning('没有匹配的任务，请调整关键字')
    return
  }
  if (keyword.value.trim()) {
    ElMessage.success(`已筛选出 ${filteredTasks.value.length} 条任务`)
  }
}

async function runHealthCheck() {
  health.checking = true
  try {
    const result = await checkBackendHealth()
    health.online = result.status === 200
    health.latency = result.duration
    health.tip = `HTTP ${result.status} · ${result.duration} ms`
    ElMessage.success('后端服务连接正常')
  } catch (error) {
    health.online = false
    health.latency = 0
    health.tip = error.message || '连接失败'
    ElMessage.error(`后端连接失败：${health.tip}`)
  } finally {
    health.checking = false
  }
}

function openDoc(url) {
  window.open(url, '_blank')
}

function openEntry(item) {
  if (item.external) {
    window.open(item.path, '_blank')
    return
  }
  router.push(item.path)
}

onMounted(runHealthCheck)
</script>

<style scoped lang="scss">
/* ==================== Hero ==================== */
.hero {
  position: relative;
  padding: 44px 48px 26px;
  border-radius: var(--app-radius-lg);
  background-image: var(--app-hero-gradient);
  background-color: var(--app-card-bg);
  box-shadow: var(--app-shadow-card);
  text-align: center;
  overflow: hidden;

  &__eyebrow {
    margin-bottom: 14px;
  }

  &__title {
    font-size: 42px;
    font-weight: 800;
    line-height: 1.22;
    letter-spacing: -1px;
    color: var(--app-text-primary);
  }

  &__desc {
    max-width: 760px;
    margin: 14px auto 0;
    font-size: 14px;
    line-height: 1.85;
    color: var(--app-text-secondary);
  }

  &__search {
    @include flex($gap: 10px);
    max-width: 830px;
    margin: 26px auto 0;
    padding: 6px 6px 6px 16px;
    border-radius: var(--app-radius);
    background-color: var(--app-card-bg);
    box-shadow: 0 8px 24px rgba(74, 14, 51, 0.08);
  }

  &__search-icon {
    flex-shrink: 0;
    color: var(--app-text-muted);
  }

  &__search-input {
    flex: 1;
    min-width: 0;
    height: 38px;
    border: none;
    outline: none;
    background: transparent;
    font-family: inherit;
    font-size: 14px;
    color: var(--app-text-primary);

    &::placeholder {
      color: var(--app-text-muted);
    }
  }

  &__search-btn {
    @include flex($gap: 6px, $inline: true);
    height: 42px;
    padding: 0 26px;
    border-radius: var(--app-radius-sm);
    background-image: var(--app-brand-gradient);
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.4px;
    box-shadow: 0 8px 18px rgba(139, 30, 86, 0.24);
    transition: filter 0.18s ease;

    &:hover {
      filter: brightness(1.08);
    }
  }

  &__meta {
    @include flex($justify: space-between, $gap: 16px);
    flex-wrap: wrap;
    max-width: 830px;
    margin: 18px auto 0;
    font-size: 12px;
  }

  &__links {
    @include flex($gap: 10px);
    color: var(--app-text-muted);
  }

  &__link {
    font-size: 13px;
    color: var(--el-color-primary);
    opacity: 0.72;
    transition: opacity 0.18s ease;

    &:hover,
    &.is-active {
      opacity: 1;
    }

    &.is-active {
      font-weight: 600;
      color: var(--app-text-regular);
    }
  }

  &__updated {
    color: var(--app-text-muted);
    letter-spacing: 0.2px;
  }
}

/* ==================== 指标条 ==================== */
.stat-panel {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  padding: 18px;
}

/* ==================== 内容面板 ==================== */
.panel {
  &__tabs {
    @include flex($gap: 26px);
    padding: 10px 26px 0;
    border-bottom: 1px solid var(--app-border-color);
  }

  &__tab {
    position: relative;
    padding: 10px 0 14px;
    font-size: 14px;
    color: var(--app-text-secondary);
    transition: color 0.18s ease;

    &:hover {
      color: var(--app-text-primary);
    }

    &.is-active {
      color: var(--el-color-primary);
      font-weight: 600;

      &::after {
        content: '';
        position: absolute;
        right: 0;
        bottom: -1px;
        left: 0;
        height: 2px;
        border-radius: 2px;
        background-color: var(--el-color-primary);
      }
    }
  }

  &__tabs-extra {
    margin-left: auto;
    padding-bottom: 12px;
    font-size: 12px;
    color: var(--app-text-muted);
  }

  &__body {
    /* 表格与其他列表页保持一致，左右贴边撑满卡片 */
    padding: 20px 0 24px;
  }

  &__footer {
    @include flex($justify: space-between, $gap: 12px);
    margin-top: 14px;
    padding: 0 26px;
    font-size: 12px;
    color: var(--app-text-muted);
  }

  &__footer-icon {
    margin-left: 2px;
  }
}

/* ==================== 快捷入口网格 ==================== */
.cat-section {
  padding: 20px 22px 22px;
  border-radius: var(--app-radius);
  background-color: var(--app-tint-bg);

  &__head {
    margin-bottom: 16px;
  }

  &__title-row {
    @include flex($justify: space-between, $align: baseline, $gap: 12px);
    margin-top: 8px;
  }

  &__title {
    font-size: 16px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.cat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.cat-card {
  @include flex($direction: column, $align: stretch, $gap: 6px);
  padding: 15px 16px;
  border: 1px solid var(--app-border-color);
  border-radius: var(--app-radius-sm);
  background-color: var(--app-card-bg);
  text-align: left;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background-color 0.18s ease;

  &:hover {
    border-color: color-mix(in srgb, var(--el-color-primary) 42%, var(--app-border-color));
    background-color: color-mix(in srgb, var(--el-color-primary) 6%, var(--app-card-bg));
    box-shadow: var(--app-shadow-soft);
  }

  &__name {
    @include flex($gap: 6px);
    font-size: 14px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.dashboard__latency {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

@media (max-width: 1100px) {
  .stat-panel {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .cat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .hero {
    padding: 30px 22px 20px;

    &__title {
      font-size: 28px;
      letter-spacing: -0.4px;
    }
  }

  .stat-panel,
  .cat-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .panel {
    &__tabs {
      padding: 8px 18px 0;
      gap: 18px;
    }

    &__body {
      padding: 16px 0 20px;
    }
  }
}
</style>
