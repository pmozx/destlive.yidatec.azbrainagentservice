<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识文档</h2>
        <p class="page-header__desc">
          只读查看 GSI 知识文档元数据（sbp_km_doc）与送审快照（sbp_km_doc_record）。点「详情」开抽屉：上半是文档档案，
          下半是送审快照明细（送审人、所属团队、审批人、送审时间），逐条详情里按 JSON 展开 snapshot_json；业务状态为 Published 时，同一抽屉里直接给出该
          文档的知识发布关系（团队编码/名称、编码是否命中、授权是否有效、生效快照与索引状态）——
          发布关系不再单独维护。
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
        <el-table-column align="center" label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
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

    <el-drawer v-model="docDrawerVisible" :title="drawerTitle" size="72%" destroy-on-close>
      <div v-loading="docDetailLoading" class="gsi-doc-detail">
        <template v-if="currentDoc">
          <div class="gsi-doc-detail__head">
            <div class="gsi-doc-detail__title">{{ currentDoc.title || '（无标题）' }}</div>
            <div class="gsi-doc-detail__tags">
              <el-tag size="small" effect="light" :type="currentDoc.status === 'Published' ? 'success' : 'info'">
                {{ currentDoc.status }}
              </el-tag>
              <el-tag size="small" effect="plain">{{ currentDoc.doc_type }}</el-tag>
              <el-tag v-if="currentDoc.domain" size="small" effect="plain">{{ currentDoc.domain }}</el-tag>
              <el-tag v-if="currentDoc.team" size="small" effect="plain">归属 {{ currentDoc.team }}</el-tag>
              <el-tag v-if="currentDoc.deleted === 1" size="small" type="danger" effect="plain">已删除</el-tag>
            </div>
          </div>

          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="知识ID">{{ currentDoc.id }}</el-descriptions-item>
            <el-descriptions-item label="知识编号">{{ currentDoc.knowledge_no || '-' }}</el-descriptions-item>
            <el-descriptions-item label="有效期至">{{ currentDoc.expire_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="业务状态">{{ currentDoc.status }}</el-descriptions-item>
            <el-descriptions-item label="知识类型">{{ currentDoc.doc_type }}</el-descriptions-item>
            <el-descriptions-item label="归属团队">{{ currentDoc.team || '-' }}</el-descriptions-item>
          </el-descriptions>

          <div class="gsi-doc-detail__section">
            <div class="gsi-doc-detail__section-head">
              <span class="gsi-doc-detail__section-title">
                <el-icon><Files /></el-icon>
                <span>送审快照明细</span>
              </span>
              <span class="gsi-doc-detail__section-meta">
                共 {{ formatNumber(recordTotal) }} 条 · 阶段 save / owner / audience ·
                created_by 即送审人，team 是受众团队编码（内容审为空），created_at 即送审时间（save 阶段为保存时间）
              </span>
            </div>
            <el-table :data="records" size="small" style="width: 100%" empty-text="该文档还没有送审快照">
              <el-table-column align="center" prop="id" label="快照ID" min-width="170" show-overflow-tooltip />
              <el-table-column align="center" label="阶段" width="96">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ row.stage }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column align="center" label="状态" width="96">
                <template #default="{ row }">
                  <el-tag size="small" effect="light" :type="recordStatusTagType(row.status)">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column align="center" label="送审人" min-width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ row.created_by || '-' }}</template>
              </el-table-column>
              <el-table-column align="center" label="所属团队" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.team" class="gsi-code">{{ row.team }}</span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column align="center" label="审批人" min-width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ row.assignee || '-' }}</template>
              </el-table-column>
              <el-table-column align="center" label="送审时间" width="160">
                <template #default="{ row }">{{ row.created_at || '-' }}</template>
              </el-table-column>
              <el-table-column align="center" label="操作" width="84">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openRecordDetail(row)">详情</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <div class="gsi-doc-detail__section">
            <div class="gsi-doc-detail__section-head">
              <span class="gsi-doc-detail__section-title">
                <el-icon><Link /></el-icon>
                <span>知识发布</span>
              </span>
              <span class="gsi-doc-detail__section-meta">
                <template v-if="isPublished">
                  共 {{ formatNumber(publicationTotal) }} 条生效关系 · team 存的是团队编码 group_code
                </template>
                <template v-else>业务状态不是 Published，尚未产生生效关系</template>
              </span>
            </div>

            <template v-if="isPublished">
              <el-alert
                v-if="unmatchedTeams > 0"
                type="warning"
                :closable="false"
                show-icon
                :title="`有 ${unmatchedTeams} 条生效关系的 team 编码在受众团队表中未命中`"
                description="历史数据可能写入的是团队名称或兼容标识而不是 group_code；授权只认 group_code，这些行会让该知识对该团队不可见。"
              />
              <el-table
                :data="publications"
                size="small"
                style="width: 100%"
                empty-text="已发布但未查到生效关系（发布链路可能尚未落库）"
              >
                <el-table-column align="center" prop="team" label="团队编码" min-width="130" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="gsi-code">{{ row.team }}</span>
                  </template>
                </el-table-column>
                <el-table-column align="center" label="团队名称" min-width="120" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.team_name || '（未命中）' }}</template>
                </el-table-column>
                <el-table-column align="center" label="兼容标识" min-width="120" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.team_ident || '-' }}</template>
                </el-table-column>
                <el-table-column align="center" label="团队状态" width="90">
                  <template #default="{ row }">
                    <el-tag size="small" effect="light" :type="row.team_status === '1' ? 'success' : 'info'">
                      {{ row.team_status === '1' ? '启用' : row.team_status === '0' ? '停用' : '-' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" label="授权有效" width="92">
                  <template #default="{ row }">
                    <el-tag size="small" effect="plain" :type="row.team_valid ? 'success' : 'danger'">
                      {{ row.team_valid ? '有效' : '无效' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" label="生效快照" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">
                    <el-button link type="primary" size="small" @click="openRecordDetail({ id: row.record_id })">
                      {{ row.record_id }}
                    </el-button>
                  </template>
                </el-table-column>
                <el-table-column align="center" label="索引状态" width="105">
                  <template #default="{ row }">
                    <el-tag size="small" effect="light" :type="indexTagType(row.index_status)">
                      {{ row.index_status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" prop="updated_at" label="更新时间" width="165">
                  <template #default="{ row }">{{ row.updated_at || '-' }}</template>
                </el-table-column>
              </el-table>
            </template>
            <el-empty v-else description="未发布文档没有生效关系，发布后会自动出现在这里" :image-size="70" />
          </div>
        </template>
      </div>
      <template #footer>
        <el-button size="small" @click="docDrawerVisible = false">关闭</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="recordDrawerVisible" title="送审快照详情" size="720px" append-to-body destroy-on-close>
      <div v-loading="recordDetailLoading" class="gsi-doc-detail">
        <template v-if="recordDetail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="快照ID">{{ recordDetail.id }}</el-descriptions-item>
            <el-descriptions-item label="知识ID">{{ recordDetail.doc_id }}</el-descriptions-item>
            <el-descriptions-item label="阶段">{{ recordDetail.stage }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ recordDetail.status }}</el-descriptions-item>
            <el-descriptions-item label="送审人">{{ recordDetail.created_by || '-' }}</el-descriptions-item>
            <el-descriptions-item label="所属团队">{{ recordDetail.team || '-' }}</el-descriptions-item>
            <el-descriptions-item label="审批人">{{ recordDetail.assignee || '-' }}</el-descriptions-item>
            <el-descriptions-item label="送审时间">{{ recordDetail.created_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ recordDetail.updated_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="审批意见" :span="2">{{ recordDetail.comment || '-' }}</el-descriptions-item>
          </el-descriptions>
          <JsonViewer :data="recordDetail.snapshot_json" title="snapshot_json（整篇不可变快照）" max-height="460px" />
        </template>
      </div>
      <template #footer>
        <el-button size="small" @click="recordDrawerVisible = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

import {
  getGsiDocPublicationPageWithTeam,
  getGsiKmDocList,
  getGsiKmDocRecordDetail,
  getGsiKmDocRecordList,
} from '@/api'
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

// ── 文档详情抽屉：快照明细 + 发布关系 ────────────────────────────────

/** 单文档的快照/发布关系都是个位数，抽屉里一次拉够，不再叠一层分页 */
const DETAIL_PAGE_SIZE = 100

const docDrawerVisible = ref(false)
const docDetailLoading = ref(false)
const currentDoc = ref(null)
const records = ref([])
const recordTotal = ref(0)
const publications = ref([])
const publicationTotal = ref(0)

const drawerTitle = computed(() => (currentDoc.value ? `知识详情 · ${currentDoc.value.title}` : '知识详情'))
const isPublished = computed(() => currentDoc.value?.status === 'Published')
const unmatchedTeams = computed(() => publications.value.filter((item) => !item.team_matched).length)

async function openDetail(row) {
  currentDoc.value = row
  docDrawerVisible.value = true
  await loadDetail(row)
}

async function loadDetail(doc) {
  docDetailLoading.value = true
  records.value = []
  recordTotal.value = 0
  publications.value = []
  publicationTotal.value = 0
  try {
    const [recordResult, publicationResult] = await Promise.all([
      getGsiKmDocRecordList({ doc_id: doc.id, page_no: 1, page_size: DETAIL_PAGE_SIZE }),
      // 未发布文档不会有生效关系，省掉这一次请求
      doc.status === 'Published'
        ? getGsiDocPublicationPageWithTeam({ doc_id: doc.id, page_no: 1, page_size: DETAIL_PAGE_SIZE })
        : Promise.resolve(null),
    ])
    records.value = recordResult?.items ?? []
    recordTotal.value = recordResult?.total ?? 0
    publications.value = publicationResult?.items ?? []
    publicationTotal.value = publicationResult?.total ?? 0
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    docDetailLoading.value = false
  }
}

function recordStatusTagType(status) {
  if (status === 'approved') return 'success'
  if (status === 'pending') return 'warning'
  if (status === 'returned' || status === 'cancelled') return 'danger'
  return 'info'
}

function indexTagType(status) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'pending') return 'warning'
  return 'info'
}

// ── 快照详情（二级抽屉）────────────────────────────────────────────

const recordDrawerVisible = ref(false)
const recordDetailLoading = ref(false)
const recordDetail = ref(null)

/** 发布关系行的生效快照也能直接点开：传 `{ id: record_id }` 即可，详情接口只按主键查 */
async function openRecordDetail(row) {
  recordDrawerVisible.value = true
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

.gsi-doc-detail {
  @include flex($direction: column, $gap: 18px);

  &__head {
    @include flex($direction: column, $gap: 8px);
  }

  &__title {
    font-size: 16px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__tags {
    @include flex($gap: 6px);
    flex-wrap: wrap;
  }

  &__section {
    @include flex($direction: column, $gap: 8px);
  }

  &__section-head {
    @include flex($justify: space-between, $align: center, $gap: 8px);
    flex-wrap: wrap;
  }

  &__section-title {
    @include flex($align: center, $gap: 6px);
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__section-meta {
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.gsi-code {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-color-primary);
}
</style>
