<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识发布关系</h2>
        <p class="page-header__desc">
          只读查看知识按受众团队生效关系（sbp_km_doc_publication）。team 列存的是团队编码 group_code，
          本页联表补齐团队名称与兼容标识；team_matched=false 表示该编码在受众团队表里没命中——
          这类行在检索侧授权判定会直接落空，是排障重点，不是展示瑕疵。
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
      <StatCard label="生效关系" :value="formatNumber(total)" icon="Link" tip="按当前查询条件统计" />
      <StatCard label="本页未命中团队" :value="formatNumber(unmatchedCount)" icon="QuestionFilled" tip="team 编码在受众团队表中不存在" />
      <StatCard label="本页团队失效" :value="formatNumber(invalidTeamCount)" icon="WarningFilled" tip="命中但团队停用或已删除" />
      <StatCard label="本页索引待同步" :value="formatNumber(pendingCount)" icon="RefreshRight" tip="index_status=pending" />
    </div>

    <el-alert
      v-if="unmatchedCount > 0"
      type="warning"
      :closable="false"
      show-icon
      :title="`本页有 ${unmatchedCount} 条生效关系的 team 编码在受众团队表中未命中`"
      description="历史数据可能写入的是团队名称或兼容标识而不是 group_code；授权只认 group_code，这些行会让对应知识对该团队不可见。"
    />

    <SectionCard flush>
      <div class="gsi-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="知识ID">
            <el-input v-model="query.doc_id" placeholder="doc_id 精确" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="团队编码">
            <el-input v-model="query.team" placeholder="team 精确" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="团队名称">
            <el-input v-model="query.team_name" placeholder="模糊" clearable style="width: 140px" />
          </el-form-item>
          <el-form-item label="快照ID">
            <el-input v-model="query.record_id" placeholder="record_id 精确" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="索引状态">
            <el-select v-model="query.index_status" placeholder="全部" clearable style="width: 120px">
              <el-option label="pending" value="pending" />
              <el-option label="succeeded" value="succeeded" />
              <el-option label="failed" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="id" label="生效记录ID" min-width="160" show-overflow-tooltip />
        <el-table-column align="center" prop="doc_id" label="知识ID doc_id" min-width="150" show-overflow-tooltip />
        <el-table-column align="center" prop="team" label="团队编码 team" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="gsi-code">{{ row.team }}</span>
          </template>
        </el-table-column>
        <el-table-column align="center" label="团队名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.team_name || '（未命中）' }}</template>
        </el-table-column>
        <el-table-column align="center" prop="team_ident" label="兼容标识" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.team_ident || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="团队状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.team_status === '1' ? 'success' : 'info'">
              {{ row.team_status === '1' ? '启用' : row.team_status === '0' ? '停用' : '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="编码命中" width="95">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.team_matched ? 'success' : 'danger'">
              {{ row.team_matched ? '命中' : '未命中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="授权有效" width="95">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.team_valid ? 'success' : 'danger'">
              {{ row.team_valid ? '有效' : '无效' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="record_id" label="生效快照 record_id" min-width="160" show-overflow-tooltip />
        <el-table-column align="center" label="索引状态" width="105">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="indexTagType(row.index_status)">{{ row.index_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="更新时间" width="165" fixed="right">
          <template #default="{ row }">{{ row.updated_at || '-' }}</template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无生效关系数据（或查询条件过窄）" :image-size="80" />
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
  </div>
</template>

<script setup>
import { computed } from 'vue'

import { getGsiDocPublicationPageWithTeam } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiDocPublicationView' })

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['doc_id', 'team', 'team_name', 'record_id', 'index_status'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiDocPublicationPageWithTeam(buildQueryParams(params)),
  { defaultQuery: { doc_id: '', team: '', team_name: '', record_id: '', index_status: '' } },
)

const unmatchedCount = computed(() => list.value.filter((row) => !row.team_matched).length)
const invalidTeamCount = computed(() => list.value.filter((row) => row.team_matched && !row.team_valid).length)
const pendingCount = computed(() => list.value.filter((row) => row.index_status === 'pending').length)

function indexTagType(status) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'pending') return 'warning'
  return 'info'
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

.gsi-code {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-color-primary);
}
</style>
