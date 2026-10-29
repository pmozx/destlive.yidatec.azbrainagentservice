<template>
  <div class="page kb-center">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识中心</h2>
        <p class="page-header__desc">
          空间 / 分类 / 文档一体化维护：上方选空间，左侧选分类，右侧直接上传、预览、重解析与批量操作；
          解析状态与知识量在右栏实时汇总，与「知识空间 / 知识分类 / 知识文档」三个独立页共用同一套接口
        </p>
      </div>
      <div class="page-header__actions">
        <span class="kb-center__summary">
          共 <b>{{ overview?.document_count ?? '—' }}</b> 份文档 ·
          <b>{{ overview?.space_count ?? '—' }}</b> 个知识空间
        </span>
        <el-button size="small" :loading="spacesLoading" @click="reloadSpaces">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 空间卡片：选中空间联动左侧分类树与右侧文档列表 -->
    <SectionCard flush>
      <FaKbSpacePanel
        v-model="currentSpaceId"
        :spaces="spaces"
        :loading="spacesLoading"
        @refresh="reloadSpaces"
      />
    </SectionCard>

    <div class="kb-center__layout">
      <SectionCard class="kb-center__col-left" flush>
        <FaKbCategoryPanel
          v-model:category-id="currentCategoryId"
          :space-id="currentSpaceId"
          :space-doc-count="currentSpaceDocCount"
          :spaces="spaces"
          @change="onCategoryChanged"
        />
      </SectionCard>

      <FaKbDocumentPanel
        ref="docPanelRef"
        class="kb-center__col-main"
        :space-id="currentSpaceId"
        :category-id="currentCategoryId"
        :spaces="spaces"
        @data-change="onDocDataChanged"
      />

      <SectionCard class="kb-center__col-side" title="知识统计" flush>
        <div class="kb-side">
          <div class="kb-side__grid">
            <div v-for="item in sideMetrics" :key="item.label" class="kb-side__item">
              <span>{{ item.label }}</span>
              <b>{{ item.value ?? '—' }}</b>
            </div>
          </div>

          <div class="kb-side__title">解析状态分布</div>
          <div v-if="statusDist.length" class="kb-side__dist">
            <div v-for="item in statusDist" :key="item.status" class="kb-dist-row">
              <span class="kb-dist-row__name">{{ item.label }}</span>
              <div class="kb-dist-row__bar">
                <div class="kb-dist-row__inner" :style="{ width: distPercent(item.count) }" />
              </div>
              <b class="kb-dist-row__value">{{ item.count }}</b>
            </div>
          </div>
          <el-empty v-else description="暂无文档" :image-size="56" />
        </div>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { getDocumentStatusStats, getSpaceList, getStatsOverview } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import FaKbCategoryPanel from './components/FaKbCategoryPanel.vue'
import FaKbDocumentPanel from './components/FaKbDocumentPanel.vue'
import FaKbSpacePanel from './components/FaKbSpacePanel.vue'

defineOptions({ name: 'IntegrationKbCenterView' })

// ── 空间清单（单一数据源：空间面板 CRUD 后由本页统一回源） ───────────────

const spaces = ref([])
const spacesLoading = ref(false)
const currentSpaceId = ref(null)
const currentCategoryId = ref(null)

const docPanelRef = ref(null)

/**
 * 取全量空间（含停用）：知识中心是维护入口，停用空间同样要能改名、恢复或删除，
 * 所以不用只返回启用项的 getSpaceOptions。
 */
async function loadSpaces() {
  spacesLoading.value = true
  try {
    // 后端分页基类限制 page_size ≤ 100，超出会直接 422
    const result = await getSpaceList({ page_no: 1, page_size: 100 })
    spaces.value = result?.items ?? []
    // 空间被删或首屏为空时清理选中项，避免联动面板带着孤儿 ID 请求
    if (currentSpaceId.value && !spaces.value.some((item) => item.id === currentSpaceId.value)) {
      currentSpaceId.value = null
    }
    if (currentSpaceId.value == null && spaces.value.length) {
      currentSpaceId.value = spaces.value[0].id
    }
  } catch {
    spaces.value = []
  } finally {
    spacesLoading.value = false
  }
}

/** 空间面板/文档面板要求回源时重新拉列表（文档数等冗余计数会变） */
async function reloadSpaces() {
  await loadSpaces()
  loadOverview()
}

const currentSpaceDocCount = computed(
  () => spaces.value.find((item) => item.id === currentSpaceId.value)?.doc_count,
)

// 切空间时清空分类选中：分类树属于空间，跨空间选中一定是脏值
watch(currentSpaceId, () => {
  currentCategoryId.value = null
})

// ── 面板联动 ───────────────────────────────────────────────────────────

/** 分类增删改：文档列表的分类名与「未分类」归属可能变化 */
function onCategoryChanged() {
  docPanelRef.value?.reload?.()
  loadOverview()
}

/** 文档增删改：空间卡片计数与右栏统计需要刷新 */
async function onDocDataChanged() {
  await reloadSpaces()
}

// ── 统计侧栏 ───────────────────────────────────────────────────────────

const overview = ref(null)
const statusDist = ref([])

const sideMetrics = computed(() => [
  { label: '空间总数', value: overview.value?.space_count },
  { label: '文档总数', value: overview.value?.document_count },
  { label: '分片总数', value: overview.value?.chunk_count },
  { label: '参与检索', value: overview.value?.enabled_count },
])

const distTotal = computed(() => statusDist.value.reduce((sum, item) => sum + (item.count || 0), 0))

function distPercent(count) {
  if (!distTotal.value) return '0%'
  return `${Math.round(((count || 0) / distTotal.value) * 100)}%`
}

async function loadOverview() {
  // 两个统计接口互不依赖，并行拉取；任一失败只让对应区块留空，不影响维护主流程
  const [overviewResult, distResult] = await Promise.allSettled([getStatsOverview(), getDocumentStatusStats()])
  overview.value = overviewResult.status === 'fulfilled' ? overviewResult.value : null
  statusDist.value = distResult.status === 'fulfilled' ? (distResult.value ?? []) : []
}

onMounted(async () => {
  await loadSpaces()
  loadOverview()
})
</script>

<style scoped lang="scss">
.page-header__actions {
  @include flex($gap: 12px);
}

.kb-center__summary {
  font-size: 13px;
  color: var(--app-text-secondary);

  b {
    color: var(--app-brand);
  }
}

.kb-center__layout {
  @include flex($align: stretch, $gap: 16px);
  min-height: 0;

  // 窄屏优先保证文档列表宽度：统计侧栏整体折叠
  @media (width <= 1500px) {
    .kb-center__col-side {
      display: none;
    }
  }
}

.kb-center__col-left {
  flex-shrink: 0;
  width: 264px;
}

.kb-center__col-main {
  flex: 1;
  min-width: 0;
}

.kb-center__col-side {
  flex-shrink: 0;
  width: 232px;

  :deep(.section-card__header) {
    padding: 18px 18px 8px;
  }
}

.kb-side {
  @include flex($direction: column, $gap: 6px);
  padding: 0 18px 18px;
}

.kb-side__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  padding-top: 4px;

  .kb-side__item {
    @include flex($direction: column, $gap: 2px);
    padding: 8px;
    background-color: var(--app-tint-bg);
    border-radius: var(--app-radius-sm);

    span {
      font-size: 12px;
      color: var(--app-text-secondary);
    }

    b {
      font-size: 16px;
      color: var(--app-text-primary);
    }
  }
}

.kb-side__title {
  margin-top: 10px;
  padding-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-primary);
  border-bottom: 1px solid var(--app-border-color);
}

.kb-side__dist {
  @include flex($direction: column, $gap: 8px);
  padding-top: 8px;
}

.kb-dist-row {
  @include flex($align: center, $gap: 8px);

  &__name {
    flex-shrink: 0;
    width: 52px;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__bar {
    flex: 1;
    height: 8px;
    overflow: hidden;
    background-color: var(--app-tint-bg);
    border-radius: 4px;
  }

  &__inner {
    height: 100%;
    background-color: var(--app-brand-light);
    border-radius: 4px;
    transition: width 0.3s;
  }

  &__value {
    flex-shrink: 0;
    width: 32px;
    font-size: 12px;
    color: var(--app-text-regular);
    text-align: right;
  }
}
</style>
