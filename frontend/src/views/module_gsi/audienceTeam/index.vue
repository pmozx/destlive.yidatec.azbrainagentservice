<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">受众团队</h2>
        <p class="page-header__desc">
          只读查看 GSI 受众团队表（sbp_sys_audience_team）。授权判定只认团队编码 group_code：知识文档与 FAQ
          的生效关系里，team 列存的都是 group_code；audience_team 是历史兼容标识，仅作对照。
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
      <StatCard label="团队总数" :value="formatNumber(total)" icon="UserFilled" tip="按当前查询条件统计" />
      <StatCard label="本页启用" :value="formatNumber(enabledCount)" icon="CircleCheck" tip="status=1 且未删除的团队" />
      <StatCard label="本页停用" :value="formatNumber(disabledCount)" icon="CircleClose" tip="status=0 的团队" />
      <StatCard label="本页已删除" :value="formatNumber(deletedCount)" icon="Delete" tip="deleted=1 的团队（历史行）" />
    </div>

    <SectionCard flush>
      <div class="gsi-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="团队编码">
            <el-input v-model="query.group_code" placeholder="group_code 精确匹配" clearable style="width: 180px" />
          </el-form-item>
          <el-form-item label="团队名称">
            <el-input v-model="query.group_name" placeholder="模糊匹配" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="兼容标识">
            <el-input v-model="query.audience_team" placeholder="audience_team 精确" clearable style="width: 170px" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 100px">
              <el-option label="启用" value="1" />
              <el-option label="停用" value="0" />
            </el-select>
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
        <el-table-column align="center" prop="id" label="团队ID" width="85" />
        <el-table-column align="center" prop="group_code" label="团队编码 group_code" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="gsi-code">{{ row.group_code }}</span>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="group_name" label="团队名称" min-width="150" show-overflow-tooltip />
        <el-table-column align="center" prop="audience_team" label="兼容标识 audience_team" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.audience_team || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.status === '1' ? 'success' : 'info'">
              {{ row.status === '1' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="删除" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.deleted === 1 ? 'danger' : 'success'">
              {{ row.deleted === 1 ? '已删除' : '未删除' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无受众团队数据" :image-size="80" />
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

    <el-dialog v-model="detailVisible" title="受众团队详情" width="640px" destroy-on-close>
      <div v-loading="detailLoading" class="gsi-detail">
        <el-descriptions v-if="detail" :column="1" border size="small">
          <el-descriptions-item label="团队ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="团队编码 group_code">{{ detail.group_code }}</el-descriptions-item>
          <el-descriptions-item label="团队名称">{{ detail.group_name }}</el-descriptions-item>
          <el-descriptions-item label="兼容标识 audience_team">{{ detail.audience_team || '-' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ detail.status === '1' ? '启用' : '停用' }}</el-descriptions-item>
          <el-descriptions-item label="逻辑删除">{{ detail.deleted === 1 ? '已删除' : '未删除' }}</el-descriptions-item>
        </el-descriptions>
        <JsonViewer :data="detail" title="原始响应" max-height="240px" />
      </div>
      <template #footer>
        <el-button size="small" @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

import { getGsiAudienceTeamDetail, getGsiAudienceTeamList } from '@/api'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiAudienceTeamView' })

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['group_code', 'group_name', 'audience_team', 'status'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  // deleted 是 0/1，不能用真值判断
  if (params.deleted === 0 || params.deleted === 1) result.deleted = params.deleted
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiAudienceTeamList(buildQueryParams(params)),
  { defaultQuery: { group_code: '', group_name: '', audience_team: '', status: '', deleted: '' } },
)

const enabledCount = computed(() => list.value.filter((row) => row.status === '1' && row.deleted === 0).length)
const disabledCount = computed(() => list.value.filter((row) => row.status !== '1').length)
const deletedCount = computed(() => list.value.filter((row) => row.deleted === 1).length)

// ── 详情 ────────────────────────────────────────────────────────────

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getGsiAudienceTeamDetail(row.id)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
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

.gsi-code {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-color-primary);
}

.gsi-detail {
  @include flex($direction: column, $gap: 16px);
}
</style>
