<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">FAQ 生效关系</h2>
        <p class="page-header__desc">
          只读查看 GSI FAQ 侧三张表：主记录（sbp_faq）、按团队生效关系（sbp_faq_publication）、审批快照（sbp_faq_record）。
          FAQ 的受众与有效期随审批「快照化」：生效规则优先读 publication 上的 access_rules_json，为空才回落
          record.snapshot_json，两者都必须含 invisibleTeams 数组，缺失即视为该发布单元不可用。
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="publicationLoading || recordLoading || faqLoading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="gsi-cards">
      <StatCard label="生效关系" :value="formatNumber(publicationTotal)" icon="Link" tip="sbp_faq_publication 记录数" />
      <StatCard label="本页规则无效" :value="formatNumber(invalidRulesCount)" icon="WarningFilled" tip="解析失败或缺少 invisibleTeams" />
      <StatCard label="本页黑名单团队" :value="formatNumber(invisibleTeamCount)" icon="Hide" tip="本页生效关系累计不可见团队数" />
      <StatCard label="本页索引待同步" :value="formatNumber(pendingCount)" icon="RefreshRight" tip="index_status=pending" />
    </div>

    <SectionCard flush>
      <el-tabs v-model="activeTab" class="gsi-tabs">
        <!-- ① 发布生效关系 -->
        <el-tab-pane label="发布生效关系" name="publication">
          <div class="gsi-toolbar">
            <el-form :inline="true" :model="publicationQuery" size="small" @submit.prevent>
              <el-form-item label="FAQ ID">
                <el-input v-model="publicationQuery.faq_id" placeholder="faq_id 精确" clearable style="width: 160px" />
              </el-form-item>
              <el-form-item label="团队编码">
                <el-input v-model="publicationQuery.team" placeholder="team / group_code 精确" clearable style="width: 180px" />
              </el-form-item>
              <el-form-item label="快照ID">
                <el-input v-model="publicationQuery.record_id" placeholder="record_id 精确" clearable style="width: 160px" />
              </el-form-item>
              <el-form-item label="索引状态">
                <el-select v-model="publicationQuery.index_status" placeholder="全部" clearable style="width: 120px">
                  <el-option label="pending" value="pending" />
                  <el-option label="succeeded" value="succeeded" />
                  <el-option label="failed" value="failed" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="publicationLoading" @click="searchPublications">查询</el-button>
                <el-button @click="resetPublications">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-table v-loading="publicationLoading" :data="publicationList" size="small" style="width: 100%">
            <el-table-column align="center" prop="id" label="生效记录ID" min-width="155" show-overflow-tooltip />
            <el-table-column align="center" prop="faq_id" label="FAQ ID" min-width="150" show-overflow-tooltip />
            <el-table-column align="center" prop="team" label="团队编码 team" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="gsi-code">{{ row.team }}</span>
              </template>
            </el-table-column>
            <el-table-column align="center" prop="record_id" label="生效快照 record_id" min-width="160" show-overflow-tooltip />
            <el-table-column align="center" label="索引状态" width="105">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="indexTagType(row.index_status)">{{ row.index_status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="规则来源" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.rules_source === 'publication' ? 'primary' : 'info'">
                  {{ rulesSourceLabel(row.rules_source) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="规则有效" width="95">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.rules_valid ? 'success' : 'danger'">
                  {{ row.rules_valid ? '有效' : '无效' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="不可见团队" min-width="180">
              <template #default="{ row }">
                <template v-if="row.invisible_teams?.length">
                  <el-tag
                    v-for="team in row.invisible_teams"
                    :key="team"
                    size="small"
                    type="danger"
                    effect="plain"
                    class="gsi-team-tag"
                  >
                    {{ team }}
                  </el-tag>
                </template>
                <span v-else class="gsi-muted">无</span>
              </template>
            </el-table-column>
            <el-table-column align="center" label="更新时间" width="165">
              <template #default="{ row }">{{ row.updated_at || '-' }}</template>
            </el-table-column>
            <el-table-column align="center" label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRules(row)">规则</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无 FAQ 生效关系" :image-size="80" />
            </template>
          </el-table>

          <div class="gsi-pagination">
            <el-pagination
              :current-page="publicationPagination.page_no"
              :page-size="publicationPagination.page_size"
              :page-sizes="[10, 20, 50]"
              :total="publicationTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              size="small"
              @current-change="handlePublicationPageChange"
              @size-change="handlePublicationSizeChange"
            />
          </div>
        </el-tab-pane>

        <!-- ② 审批快照 -->
        <el-tab-pane label="审批快照" name="record">
          <div class="gsi-toolbar">
            <el-form :inline="true" :model="recordQuery" size="small" @submit.prevent>
              <el-form-item label="FAQ ID">
                <el-input v-model="recordQuery.faq_id" placeholder="faq_id 精确" clearable style="width: 170px" />
              </el-form-item>
              <el-form-item label="阶段">
                <el-select v-model="recordQuery.stage" placeholder="全部" clearable style="width: 140px">
                  <el-option label="version 保存内容" value="version" />
                  <el-option label="owner 内容审" value="owner" />
                  <el-option label="audience 受众审" value="audience" />
                  <el-option label="owner_final 终审发布" value="owner_final" />
                </el-select>
              </el-form-item>
              <el-form-item label="状态">
                <el-select v-model="recordQuery.status" placeholder="全部" clearable style="width: 120px">
                  <el-option label="saved" value="saved" />
                  <el-option label="pending" value="pending" />
                  <el-option label="approved" value="approved" />
                  <el-option label="returned" value="returned" />
                  <el-option label="cancelled" value="cancelled" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="recordLoading" @click="searchRecords">查询</el-button>
                <el-button @click="resetRecords">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-table v-loading="recordLoading" :data="recordList" size="small" style="width: 100%">
            <el-table-column align="center" prop="id" label="快照ID" min-width="170" show-overflow-tooltip />
            <el-table-column align="center" prop="faq_id" label="FAQ ID" min-width="170" show-overflow-tooltip />
            <el-table-column align="center" label="阶段" width="150">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ stageLabel(row.stage) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="recordStatusTagType(row.status)">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRecordDetail(row)">详情</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无 FAQ 审批快照" :image-size="80" />
            </template>
          </el-table>

          <div class="gsi-pagination">
            <el-pagination
              :current-page="recordPagination.page_no"
              :page-size="recordPagination.page_size"
              :page-sizes="[10, 20, 50]"
              :total="recordTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              size="small"
              @current-change="handleRecordPageChange"
              @size-change="handleRecordSizeChange"
            />
          </div>
        </el-tab-pane>

        <!-- ③ FAQ 主记录 -->
        <el-tab-pane label="FAQ 主记录" name="faq">
          <div class="gsi-toolbar">
            <el-form :inline="true" :model="faqQuery" size="small" @submit.prevent>
              <el-form-item label="FAQ 编号">
                <el-input v-model="faqQuery.faq_no" placeholder="FAQ-0000001" clearable style="width: 170px" />
              </el-form-item>
              <el-form-item label="业务状态">
                <el-input v-model="faqQuery.status" placeholder="Draft / Published" clearable style="width: 160px" />
              </el-form-item>
              <el-form-item label="删除">
                <el-select v-model="faqQuery.deleted" placeholder="全部" clearable style="width: 100px">
                  <el-option label="未删除" :value="0" />
                  <el-option label="已删除" :value="1" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="faqLoading" @click="searchFaqs">查询</el-button>
                <el-button @click="resetFaqs">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="FAQ 正文不在契约授权范围内"
            description="契约 §四只允许读取 id / faq_no / status / deleted；问题与答案正文虽存在于表中，但需要由 DBA 扩权并回写对接说明后才能展示。"
          />

          <el-table v-loading="faqLoading" :data="faqList" size="small" style="width: 100%">
            <el-table-column align="center" prop="id" label="FAQ ID" min-width="200" show-overflow-tooltip />
            <el-table-column align="center" prop="faq_no" label="FAQ 编号" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.faq_no || '-' }}</template>
            </el-table-column>
            <el-table-column align="center" label="业务状态" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="row.status === 'Published' ? 'success' : 'info'">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="删除" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.deleted === 1 ? 'danger' : 'success'">
                  {{ row.deleted === 1 ? '已删除' : '未删除' }}
                </el-tag>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无 FAQ 主记录" :image-size="80" />
            </template>
          </el-table>

          <div class="gsi-pagination">
            <el-pagination
              :current-page="faqPagination.page_no"
              :page-size="faqPagination.page_size"
              :page-sizes="[10, 20, 50]"
              :total="faqTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              size="small"
              @current-change="handleFaqPageChange"
              @size-change="handleFaqSizeChange"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </SectionCard>

    <el-dialog v-model="rulesVisible" title="生效规则" width="820px" destroy-on-close>
      <div v-if="rulesRow" class="gsi-detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="生效记录ID">{{ rulesRow.id }}</el-descriptions-item>
          <el-descriptions-item label="FAQ ID">{{ rulesRow.faq_id }}</el-descriptions-item>
          <el-descriptions-item label="团队编码">{{ rulesRow.team }}</el-descriptions-item>
          <el-descriptions-item label="生效快照">{{ rulesRow.record_id }}</el-descriptions-item>
          <el-descriptions-item label="规则来源">
            {{ rulesSourceLabel(rulesRow.rules_source) }}
          </el-descriptions-item>
          <el-descriptions-item label="规则有效">{{ rulesRow.rules_valid ? '有效' : '无效' }}</el-descriptions-item>
          <el-descriptions-item label="不可见团队" :span="2">
            <template v-if="rulesRow.invisible_teams?.length">
              <el-tag
                v-for="team in rulesRow.invisible_teams"
                :key="team"
                size="small"
                type="danger"
                effect="plain"
                class="gsi-team-tag"
              >
                {{ team }}
              </el-tag>
            </template>
            <span v-else class="gsi-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="rulesRow.rules_error" label="无效原因" :span="2">
            {{ rulesRow.rules_error }}
          </el-descriptions-item>
        </el-descriptions>
        <JsonViewer
          :data="rulesRow.access_rules_json"
          title="access_rules_json（publication 上的生效规则原文；为空时回落 record.snapshot_json）"
          max-height="320px"
        />
      </div>
      <template #footer>
        <el-button size="small" @click="rulesVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="recordDetailVisible" title="FAQ 审批快照详情" width="860px" destroy-on-close>
      <div v-loading="recordDetailLoading" class="gsi-detail">
        <template v-if="recordDetail">
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="快照ID">{{ recordDetail.id }}</el-descriptions-item>
            <el-descriptions-item label="FAQ ID">{{ recordDetail.faq_id }}</el-descriptions-item>
            <el-descriptions-item label="阶段">{{ stageLabel(recordDetail.stage) }}</el-descriptions-item>
            <el-descriptions-item label="状态" :span="3">{{ recordDetail.status }}</el-descriptions-item>
          </el-descriptions>
          <JsonViewer :data="recordDetail.snapshot_json" title="snapshot_json（审批快照原文）" max-height="420px" />
        </template>
      </div>
      <template #footer>
        <el-button size="small" @click="recordDetailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

import { getGsiFaqList, getGsiFaqPublicationList, getGsiFaqRecordDetail, getGsiFaqRecordList } from '@/api'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiFaqPublicationView' })

const activeTab = ref('publication')

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params, fields) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  fields.forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  // deleted 是 0/1，不能用真值判断
  if (params.deleted === 0 || params.deleted === 1) result.deleted = params.deleted
  return result
}

// ── ① 发布生效关系 ──────────────────────────────────────────────────

const {
  loading: publicationLoading,
  list: publicationList,
  total: publicationTotal,
  pagination: publicationPagination,
  query: publicationQuery,
  load: loadPublications,
  search: searchPublications,
  reset: resetPublications,
  handlePageChange: handlePublicationPageChange,
  handleSizeChange: handlePublicationSizeChange,
} = useTable(
  (params) => getGsiFaqPublicationList(buildQueryParams(params, ['faq_id', 'team', 'record_id', 'index_status'])),
  { defaultQuery: { faq_id: '', team: '', record_id: '', index_status: '' } },
)

const invalidRulesCount = computed(() => publicationList.value.filter((row) => !row.rules_valid).length)
const invisibleTeamCount = computed(() =>
  publicationList.value.reduce((sum, row) => sum + (row.invisible_teams?.length || 0), 0),
)
const pendingCount = computed(() => publicationList.value.filter((row) => row.index_status === 'pending').length)

function rulesSourceLabel(source) {
  if (source === 'publication') return '发布关系'
  if (source === 'record') return '回落快照'
  return '无规则'
}

function indexTagType(status) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'pending') return 'warning'
  return 'info'
}

const rulesVisible = ref(false)
const rulesRow = ref(null)

function openRules(row) {
  rulesRow.value = row
  rulesVisible.value = true
}

// ── ② 审批快照 ──────────────────────────────────────────────────────

const {
  loading: recordLoading,
  list: recordList,
  total: recordTotal,
  pagination: recordPagination,
  query: recordQuery,
  search: searchRecords,
  reset: resetRecords,
  handlePageChange: handleRecordPageChange,
  handleSizeChange: handleRecordSizeChange,
} = useTable((params) => getGsiFaqRecordList(buildQueryParams(params, ['faq_id', 'stage', 'status'])), {
  defaultQuery: { faq_id: '', stage: '', status: '' },
})

function stageLabel(stage) {
  const labels = {
    version: 'version 保存内容',
    owner: 'owner 内容审',
    audience: 'audience 受众审',
    owner_final: 'owner_final 终审发布',
  }
  return labels[stage] || stage
}

function recordStatusTagType(status) {
  if (status === 'approved') return 'success'
  if (status === 'pending') return 'warning'
  if (status === 'returned' || status === 'cancelled') return 'danger'
  return 'info'
}

const recordDetailVisible = ref(false)
const recordDetailLoading = ref(false)
const recordDetail = ref(null)

async function openRecordDetail(row) {
  recordDetailVisible.value = true
  recordDetailLoading.value = true
  recordDetail.value = null
  try {
    recordDetail.value = await getGsiFaqRecordDetail(row.id)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    recordDetailLoading.value = false
  }
}

// ── ③ FAQ 主记录 ────────────────────────────────────────────────────

const {
  loading: faqLoading,
  list: faqList,
  total: faqTotal,
  pagination: faqPagination,
  query: faqQuery,
  search: searchFaqs,
  reset: resetFaqs,
  handlePageChange: handleFaqPageChange,
  handleSizeChange: handleFaqSizeChange,
} = useTable((params) => getGsiFaqList(buildQueryParams(params, ['faq_no', 'status'])), {
  defaultQuery: { faq_no: '', status: '', deleted: '' },
})

async function refreshAll() {
  await Promise.all([loadPublications(), searchRecords(), searchFaqs()])
}
</script>

<style scoped lang="scss">
.gsi-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.gsi-tabs {
  padding: 8px 26px 0;

  :deep(.el-tabs__header) {
    margin-bottom: 0;
  }
}

.gsi-toolbar {
  padding: 16px 0 4px;
}

.gsi-pagination {
  @include flex($justify: flex-end);
  padding: 12px 0 20px;
}

.gsi-code {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-color-primary);
}

.gsi-muted {
  font-size: 12px;
  color: var(--app-text-muted);
}

.gsi-team-tag {
  margin: 2px 4px 2px 0;
}

.gsi-detail {
  @include flex($direction: column, $gap: 16px);
}
</style>
