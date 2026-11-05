<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">团队成员标识</h2>
        <p class="page-header__desc">
          只读查看受众团队成员关系（sbp_sys_audience_team_member）。关联表本身只存 team_id 与 user_id 两个数字，
          本页联表把团队编码、名称、兼容标识与用户在职状态放在同一行，避免只看到裸 ID；
          上方「按用户反查」用于确认某个用户到底落在哪些团队——这是检索无授权时最先要看的一步。
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
      <StatCard label="成员关系" :value="formatNumber(total)" icon="User" tip="按当前查询条件统计" />
      <StatCard label="本页团队有效" :value="formatNumber(validTeamCount)" icon="CircleCheck" tip="团队 status=1 且未删除" />
      <StatCard label="本页团队失效" :value="formatNumber(list.length - validTeamCount)" icon="WarningFilled" tip="团队停用、删除或未命中" />
      <StatCard label="本页用户有效" :value="formatNumber(validUserCount)" icon="Avatar" tip="用户存在、在职且未删除" />
    </div>

    <SectionCard title="按用户反查团队标识" subtitle="对应契约 §四 userTeams：空集合与「有团队但全部停用」是两种不同结论">
      <div class="gsi-lookup">
        <el-input
          v-model="lookupUserId"
          placeholder="输入用户ID，例如 1024"
          clearable
          style="width: 220px"
          @keyup.enter="lookup"
        />
        <el-button type="primary" size="small" :loading="lookupLoading" @click="lookup">反查</el-button>
        <span class="gsi-lookup__hint">team_count 含停用/已删除；valid_team_count 才是真正能命中的授权范围</span>
      </div>

      <div v-if="lookupResult" class="gsi-lookup__result">
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="用户ID">{{ lookupResult.user_id }}</el-descriptions-item>
          <el-descriptions-item label="用户存在">{{ lookupResult.user_exists ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="在职状态">{{ statusText(lookupResult.user_status) }}</el-descriptions-item>
          <el-descriptions-item label="允许登录">{{ loginText(lookupResult.allow_login) }}</el-descriptions-item>
        </el-descriptions>

        <div class="gsi-lookup__tags">
          <el-tag size="small" type="info" effect="plain">团队总数 {{ lookupResult.team_count }}</el-tag>
          <el-tag size="small" :type="lookupResult.valid_team_count ? 'success' : 'danger'" effect="plain">
            有效团队 {{ lookupResult.valid_team_count }}
          </el-tag>
        </div>

        <el-table v-if="lookupResult.teams.length" :data="lookupResult.teams" size="small" style="width: 100%">
          <el-table-column align="center" prop="team_id" label="团队ID" width="85" />
          <el-table-column align="center" prop="group_code" label="团队编码 group_code" min-width="180" show-overflow-tooltip />
          <el-table-column align="center" prop="group_name" label="团队名称" min-width="150" show-overflow-tooltip />
          <el-table-column align="center" prop="audience_team" label="兼容标识" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.audience_team || '-' }}</template>
          </el-table-column>
          <el-table-column align="center" label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="light" :type="row.team_status === '1' ? 'success' : 'info'">
                {{ row.team_status === '1' ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column align="center" label="有效" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.team_valid ? 'success' : 'danger'">
                {{ row.team_valid ? '有效' : '无效' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-alert
          v-else
          type="warning"
          :closable="false"
          show-icon
          title="该用户没有任何受众团队"
          description="检索侧等价于「无知识授权」，不是「全库可见」；若确实应有权限，请核对成员关系是否漏配。"
        />
      </div>
    </SectionCard>

    <SectionCard flush>
      <div class="gsi-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="团队ID">
            <el-input v-model="query.team_id" placeholder="精确" clearable style="width: 100px" />
          </el-form-item>
          <el-form-item label="用户ID">
            <el-input v-model="query.user_id" placeholder="精确" clearable style="width: 100px" />
          </el-form-item>
          <el-form-item label="团队编码">
            <el-input v-model="query.group_code" placeholder="精确" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="团队名称">
            <el-input v-model="query.group_name" placeholder="模糊" clearable style="width: 140px" />
          </el-form-item>
          <el-form-item label="兼容标识">
            <el-input v-model="query.audience_team" placeholder="精确" clearable style="width: 160px" />
          </el-form-item>
          <el-form-item label="团队状态">
            <el-select v-model="query.team_status" placeholder="全部" clearable style="width: 100px">
              <el-option label="启用" value="1" />
              <el-option label="停用" value="0" />
            </el-select>
          </el-form-item>
          <el-form-item label="在职状态">
            <el-select v-model="query.user_status" placeholder="全部" clearable style="width: 100px">
              <el-option label="在职" value="1" />
              <el-option label="离职" value="0" />
            </el-select>
          </el-form-item>
          <el-form-item label="仅有效团队">
            <el-switch v-model="query.valid_only" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="team_id" label="团队ID" width="82" />
        <el-table-column align="center" prop="group_code" label="团队编码 group_code" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.group_code" size="small" effect="plain">{{ row.group_code }}</el-tag>
            <el-tag v-else size="small" type="danger" effect="plain">团队缺失</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="group_name" label="团队名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.group_name || '（未命中受众团队表）' }}</template>
        </el-table-column>
        <el-table-column align="center" prop="audience_team" label="兼容标识 audience_team" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ row.audience_team || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="团队状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.team_status === '1' ? 'success' : 'info'">
              {{ row.team_status === '1' ? '启用' : row.team_status === '0' ? '停用' : '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="团队有效" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.team_valid ? 'success' : 'danger'">
              {{ row.team_valid ? '有效' : '无效' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="user_id" label="用户ID" width="82" />
        <el-table-column align="center" label="用户存在" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.user_exists ? 'success' : 'danger'">
              {{ row.user_exists ? '存在' : '缺失' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="在职状态" width="90">
          <template #default="{ row }">{{ statusText(row.user_status) }}</template>
        </el-table-column>
        <el-table-column align="center" label="允许登录" width="90">
          <template #default="{ row }">{{ loginText(row.allow_login) }}</template>
        </el-table-column>
        <el-table-column align="center" label="用户删除" width="90">
          <template #default="{ row }">{{ row.user_deleted === 1 ? '已删除' : row.user_deleted === 0 ? '未删除' : '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="用户有效" width="90" fixed="right">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.user_valid ? 'success' : 'danger'">
              {{ row.user_valid ? '有效' : '无效' }}
            </el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无成员关系（或查询条件过窄）" :image-size="80" />
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
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getGsiTeamMemberPageWithTeam, getGsiUserTeams } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiTeamMemberView' })

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['team_id', 'user_id', 'group_code', 'group_name', 'audience_team', 'team_status', 'user_status'].forEach(
    (key) => {
      if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
    },
  )
  if (params.valid_only) result.valid_only = true
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiTeamMemberPageWithTeam(buildQueryParams(params)),
  {
    defaultQuery: {
      team_id: '',
      user_id: '',
      group_code: '',
      group_name: '',
      audience_team: '',
      team_status: '',
      user_status: '',
      valid_only: false,
    },
  },
)

const validTeamCount = computed(() => list.value.filter((row) => row.team_valid).length)
const validUserCount = computed(() => list.value.filter((row) => row.user_valid).length)

function statusText(status) {
  if (status === '1') return '在职'
  if (status === '0') return '离职'
  return '-'
}

function loginText(allowLogin) {
  if (allowLogin === '1') return '允许'
  if (allowLogin === '0') return '禁止'
  return '-'
}

// ── 按用户反查 ──────────────────────────────────────────────────────

const lookupUserId = ref('')
const lookupLoading = ref(false)
const lookupResult = ref(null)

async function lookup() {
  const userId = String(lookupUserId.value ?? '').trim()
  if (!userId) {
    ElMessage.warning('请先输入用户ID')
    return
  }
  lookupLoading.value = true
  try {
    lookupResult.value = await getGsiUserTeams(userId)
  } catch {
    // 请求拦截器已统一提示错误
    lookupResult.value = null
  } finally {
    lookupLoading.value = false
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

.gsi-lookup {
  @include flex($gap: 10px);
  flex-wrap: wrap;

  &__hint {
    font-size: 12px;
    color: var(--app-text-muted);
  }

  &__result {
    @include flex($direction: column, $gap: 12px);
    margin-top: 16px;
  }

  &__tags {
    @include flex($gap: 8px);
  }
}
</style>
