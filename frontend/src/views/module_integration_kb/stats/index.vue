<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">运营统计</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/stats：文档状态分布、文档引用排行（ref_count 由问答命中引用累加）、
          空间知识量排行与近 N 天上传趋势，口径与文档表字段一致
        </p>
      </div>
      <div class="page-header__actions">
        <el-select v-model="days" size="small" style="width: 120px" @change="loadTrend">
          <el-option label="近 7 天" :value="7" />
          <el-option label="近 14 天" :value="14" />
          <el-option label="近 30 天" :value="30" />
        </el-select>
        <el-button size="small" :loading="loading" @click="loadAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="stats-cards">
      <StatCard label="知识空间" :value="overview.space_count" icon="FolderOpened" tip="含启用与停用" />
      <StatCard label="文档总数" :value="overview.document_count" icon="Document" :tip="`原件共 ${overview.total_size_mb} MB`" />
      <StatCard
        label="解析完成"
        :value="overview.parsed_count"
        icon="CircleCheck"
        color="var(--el-color-success)"
        :tip="`解析中 ${overview.parsing_count} · 待解析 ${overview.pending_count}`"
      />
      <StatCard
        label="解析失败"
        :value="overview.failed_count"
        icon="WarningFilled"
        color="var(--el-color-danger)"
        tip="失败文档可在文档页重新解析"
      />
      <StatCard label="参与检索" :value="overview.enabled_count" icon="Search" tip="已上架且解析完成的文档" />
      <StatCard label="分片总数" :value="overview.chunk_count" icon="Files" tip="向量化后的检索最小单位" />
    </div>

    <div class="stats-grid">
      <SectionCard title="文档解析状态分布" subtitle="状态机可视化">
        <div class="status-list">
          <div v-for="item in documentStatus" :key="item.status" class="status-item">
            <div class="status-item__head">
              <span class="status-item__label">
                <el-tag :type="statusTagType(item.status)" size="small" effect="light">{{ item.label }}</el-tag>
              </span>
              <span class="status-item__count">{{ item.count }}</span>
            </div>
            <el-progress
              :percentage="statusPercent(item.count)"
              :status="progressStatus(item.status)"
              :stroke-width="8"
              :show-text="false"
            />
          </div>
          <el-empty v-if="!documentStatus.length" description="暂无文档" :image-size="70" />
        </div>
      </SectionCard>

      <SectionCard title="上传趋势" :subtitle="`近 ${days} 天`">
        <div class="trend-list">
          <div v-for="item in trend" :key="item.date" class="trend-item">
            <span class="trend-item__date">{{ item.date.slice(5) }}</span>
            <div class="trend-item__bar">
              <span class="trend-item__fill" :style="{ width: trendWidth(item.count) }" />
            </div>
            <span class="trend-item__count">{{ item.count }}</span>
          </div>
          <el-empty v-if="!trend.length" description="暂无上传记录" :image-size="70" />
        </div>
      </SectionCard>
    </div>

    <div class="stats-grid">
      <SectionCard title="文档引用排行" subtitle="问答命中引用次数 Top 10">
        <el-table :data="hotDocuments" size="small" style="width: 100%">
          <el-table-column type="index" label="#" width="50" align="center" />
          <el-table-column prop="title" label="文档标题" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">{{ row.title || `文档 #${row.doc_id}` }}</template>
          </el-table-column>
          <el-table-column prop="doc_id" label="文档ID" width="90" align="center" />
          <el-table-column label="引用次数" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="success" effect="plain">{{ row.ref_count }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分片数" width="90" align="center">
            <template #default="{ row }">{{ row.chunk_count || '-' }}</template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有被问答引用的文档" :image-size="70" />
          </template>
        </el-table>
      </SectionCard>

      <SectionCard title="空间知识量排行" subtitle="按分片数排序 Top 10">
        <el-table :data="spaceRanking" size="small" style="width: 100%">
          <el-table-column type="index" label="#" width="50" align="center" />
          <el-table-column prop="space_name" label="空间" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.space_name || `空间 #${row.space_id}` }}</template>
          </el-table-column>
          <el-table-column label="文档数" width="90" align="center">
            <template #default="{ row }">{{ row.doc_count }}</template>
          </el-table-column>
          <el-table-column label="分片数" width="90" align="center">
            <template #default="{ row }">{{ row.chunk_count }}</template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无空间数据" :image-size="70" />
          </template>
        </el-table>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

import {
  getDocumentStatusStats,
  getHotDocuments,
  getSpaceRanking,
  getStatsOverview,
  getUploadTrend,
} from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'

defineOptions({ name: 'IntegrationKbStatsView' })

const loading = ref(false)
const days = ref(7)

const overview = ref({
  space_count: 0,
  document_count: 0,
  parsed_count: 0,
  parsing_count: 0,
  failed_count: 0,
  pending_count: 0,
  enabled_count: 0,
  chunk_count: 0,
  total_size_mb: 0,
})
const documentStatus = ref([])
const hotDocuments = ref([])
const spaceRanking = ref([])
const trend = ref([])

/** 状态总数：百分比按「文档总数」算，避免状态分布自身求和为 0 时除零 */
const statusTotal = computed(() =>
  documentStatus.value.reduce((sum, item) => sum + (item.count || 0), 0) || overview.value.document_count || 0,
)

const trendMax = computed(() => Math.max(1, ...trend.value.map((item) => item.count || 0)))

const STATUS_TAG = { 0: 'info', 1: 'warning', 2: 'success', 3: 'danger' }

function statusTagType(status) {
  return STATUS_TAG[status] ?? 'info'
}

function progressStatus(status) {
  if (status === 2) return 'success'
  if (status === 3) return 'exception'
  return ''
}

function statusPercent(count) {
  if (!statusTotal.value) return 0
  return Math.round(((count || 0) / statusTotal.value) * 100)
}

function trendWidth(count) {
  return `${Math.round(((count || 0) / trendMax.value) * 100)}%`
}

async function loadTrend() {
  try {
    trend.value = (await getUploadTrend(days.value)) ?? []
  } catch {
    trend.value = []
  }
}

async function loadAll() {
  loading.value = true
  try {
    // 五个接口互不依赖，并行拉取；单个失败不影响其它卡片显示
    const [overviewData, statusData, hotData, rankingData, trendData] = await Promise.all([
      getStatsOverview().catch(() => null),
      getDocumentStatusStats().catch(() => []),
      getHotDocuments(10).catch(() => []),
      getSpaceRanking(10).catch(() => []),
      getUploadTrend(days.value).catch(() => []),
    ])
    if (overviewData) overview.value = overviewData
    documentStatus.value = statusData ?? []
    hotDocuments.value = hotData ?? []
    spaceRanking.value = rankingData ?? []
    trend.value = trendData ?? []
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped lang="scss">
.stats-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
  align-items: start;
}

.status-list {
  @include flex($direction: column, $gap: 12px);
}

.status-item {
  &__head {
    @include flex($justify: space-between, $align: center);
    margin-bottom: 6px;
  }

  &__count {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }
}

.trend-list {
  @include flex($direction: column, $gap: 8px);
}

.trend-item {
  @include flex($align: center, $gap: 10px);

  &__date {
    width: 48px;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__bar {
    flex: 1;
    height: 8px;
    overflow: hidden;
    background-color: var(--el-fill-color-light);
    border-radius: 4px;
  }

  &__fill {
    display: block;
    height: 100%;
    background-color: var(--el-color-primary);
    border-radius: 4px;
    transition: width 0.3s ease;
  }

  &__count {
    width: 32px;
    font-size: 12px;
    text-align: right;
    color: var(--app-text-primary);
  }
}
</style>
