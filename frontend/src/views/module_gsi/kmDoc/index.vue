<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识文档</h2>
        <p class="page-header__desc">
          只读查看 GSI 知识文档元数据（sbp_km_doc）与送审快照（sbp_km_doc_record）。文档只给标题、状态、类型、
          归属团队与有效期，正文不在契约授权范围内；点「查看快照」即按 doc_id 下钻该文档的审批快照，
          快照详情里的 snapshot_json 是整篇不可变快照，按 JSON 展开查看。
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="gsi-cards">
      <StatCard label="知识文档" :value="formatNumber(total)" icon="Document" tip="按当前查询条件统计" />
      <StatCard label="本页已发布" :value="formatNumber(publishedCount)" icon="CircleCheck" tip="status=Published" />
      <StatCard label="本页归属团队" :value="formatNumber(teamCount)" icon="UserFilled" tip="本页出现的不同 team 取值数" />
      <StatCard label="本页已删除" :value="formatNumber(deletedCount)" icon="Delete" tip="deleted=1 的历史行" />
    </div>

    <SectionCard flush>
      <div class="gsi-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="业务状态">
            <el-input v-model="query.status" placeholder="Draft / Published" clearable style="width: 150px" />
          </el-form-item>
          <el-form-item label="知识类型">
            <el-input v-model="query.doc_type" placeholder="Document / FAQ" clearable style="width: 140px" />
          </el-form-item>
          <el-form-item label="业务域">
            <el-input v-model="query.domain" placeholder="精确" clearable style="width: 120px" />
          </el-form-item>
          <el-form-item label="归属团队">
            <el-input v-model="query.team" placeholder="team 精确" clearable style="width: 150px" />
          </el-form-item>
          <el-form-item label="删除">
            <el-select v-model="query.deleted" placeholder="全部" clearable style="width: 100px">
              <el-option label="未删除" :value="0" />
              <el-option label="已删除" :value="1" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="id" label="知识ID" min-width="160" show-overflow-tooltip />
        <el-table-column align="center" prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" label="业务状态" width="105">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.status === 'Published' ? 'success' : 'info'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="doc_type" label="知识类型" width="105" />
        <el-table-column align="center" prop="domain" label="业务域" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.domain || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" prop="team" label="归属团队" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.team || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" prop="knowledge_no" label="知识编号" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.knowledge_no || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" prop="expire_at" label="有效期至" width="115">
          <template #default="{ row }">{{ row.expire_at || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="删除" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.deleted === 1 ? 'danger' : 'success'">
              {{ row.deleted === 1 ? '已删除' : '未删除' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRecords(row)">查看快照</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无知识文档（或查询条件过窄）" :image-size="80" />
        </template>
      </el-table>

      <div class="gsi-pagination">
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

    <SectionCard
      :title="recordsTitle"
      subtitle="阶段 save/owner/audience 对应保存、内容审、受众审；列表不含大字段，详情才读 snapshot_json"
      flush
    >
      <div class="gsi-toolbar">
        <el-form :inline="true" :model="recordQuery" size="small" @submit.prevent>
          <el-form-item label="知识ID">
            <el-input v-model="recordQuery.doc_id" placeholder="不填则看全部" clearable style="width: 170px" />
          </el-form-item>
          <el-form-item label="阶段">
            <el-select v-model="recordQuery.stage" placeholder="全部" clearable style="width: 110px">
              <el-option label="save" value="save" />
              <el-option label="owner" value="owner" />
              <el-option label="audience" value="audience" />
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
            <el-button @click="clearRecords">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="recordLoading" :data="recordList" size="small" style="width: 100%">
        <el-table-column align="center" prop="id" label="快照ID" min-width="160" show-overflow-tooltip />
        <el-table-column align="center" prop="doc_id" label="知识ID" min-width="160" show-overflow-tooltip />
        <el-table-column align="center" label="阶段" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.stage }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="recordStatusTagType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRecordDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="该文档还没有送审快照" :image-size="70" />
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
    </SectionCard>

    <el-dialog v-model="recordDetailVisible" title="送审快照详情" width="860px" destroy-on-close>
      <div v-loading="recordDetailLoading" class="gsi-detail">
        <template v-if="recordDetail">
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="快照ID">{{ recordDetail.id }}</el-descriptions-item>
            <el-descriptions-item label="知识ID">{{ recordDetail.doc_id }}</el-descriptions-item>
            <el-descriptions-item label="阶段">{{ recordDetail.stage }}</el-descriptions-item>
            <el-descriptions-item label="状态" :span="3">{{ recordDetail.status }}</el-descriptions-item>
          </el-descriptions>
          <JsonViewer :data="recordDetail.snapshot_json" title="snapshot_json（整篇不可变快照）" max-height="420px" />
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

import { getGsiKmDocList, getGsiKmDocRecordDetail, getGsiKmDocRecordList } from '@/api'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiKmDocView' })

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['title', 'status', 'doc_type', 'domain', 'team', 'knowledge_no'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  // deleted 是 0/1，不能用真值判断
  if (params.deleted === 0 || params.deleted === 1) result.deleted = params.deleted
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiKmDocList(buildQueryParams(params)),
  { defaultQuery: { title: '', status: '', doc_type: '', domain: '', team: '', knowledge_no: '', deleted: '' } },
)

const publishedCount = computed(() => list.value.filter((row) => row.status === 'Published').length)
const deletedCount = computed(() => list.value.filter((row) => row.deleted === 1).length)
const teamCount = computed(() => new Set(list.value.map((row) => row.team).filter(Boolean)).size)

// ── 送审快照 ────────────────────────────────────────────────────────

const selectedDoc = ref(null)

const recordsTitle = computed(() =>
  selectedDoc.value ? `送审快照 · ${selectedDoc.value.title}（${selectedDoc.value.id}）` : '送审快照',
)

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
} = useTable((params) => getGsiKmDocRecordList(params), {
  defaultQuery: { doc_id: '', stage: '', status: '' },
  immediate: false,
})

function openRecords(row) {
  selectedDoc.value = row
  recordQuery.doc_id = row.id
  searchRecords()
}

/** 重置要连标题一起清掉，否则标题还写着某篇文档、列表已经是全量了 */
function clearRecords() {
  selectedDoc.value = null
  resetRecords()
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
    recordDetail.value = await getGsiKmDocRecordDetail(row.id)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    recordDetailLoading.value = false
  }
}
</script>

<style scoped lang="scss">
.gsi-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.gsi-toolbar {
  padding: 20px 26px 4px;
}

.gsi-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.gsi-detail {
  @include flex($direction: column, $gap: 16px);
}
</style>
