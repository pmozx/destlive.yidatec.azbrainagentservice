<template>
  <el-dialog v-model="visible" :title="title" width="860px" destroy-on-close>
    <div v-loading="loading" class="user-detail">
      <el-alert
        v-if="loaded && !profile"
        type="warning"
        :closable="false"
        show-icon
        title="该用户不存在于 sbp_sys_user"
        description="成员关系行仍然保留。请核对 user_id 是否已被物理删除，或关系表存在脏数据。"
      />

      <template v-if="profile">
        <div class="user-detail__section">基本信息</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="用户ID">{{ profile.id }}</el-descriptions-item>
          <el-descriptions-item label="登录账号">{{ profile.username || '-' }}</el-descriptions-item>
          <el-descriptions-item label="中文名">{{ profile.cn_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="英文名">{{ profile.en_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="职位">{{ profile.position_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="职级">
            {{ profile.rank_name || (profile.rank_id ? `#${profile.rank_id}` : '-') }}
          </el-descriptions-item>
          <el-descriptions-item label="在职状态">{{ profile.status === '1' ? '在职' : '离职' }}</el-descriptions-item>
          <el-descriptions-item label="允许登录">{{ profile.allow_login === '1' ? '允许' : '禁止' }}</el-descriptions-item>
          <el-descriptions-item label="逻辑删除">{{ profile.deleted === 1 ? '已删除' : '未删除' }}</el-descriptions-item>
          <el-descriptions-item label="用户有效">{{ validText }}</el-descriptions-item>
        </el-descriptions>
      </template>

      <div class="user-detail__section">
        所属组织（{{ orgs?.org_count ?? 0 }}）
        <span class="user-detail__hint">组织只回答「人在哪个部门」，不代表知识授权</span>
      </div>
      <el-table :data="orgs?.orgs ?? []" size="small" style="width: 100%" max-height="220">
        <el-table-column align="center" prop="org_id" label="组织ID" width="80" />
        <el-table-column label="组织名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.org_name || '（组织缺失）' }}</template>
        </el-table-column>
        <el-table-column label="组织编码" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.org_code || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="分类" width="85">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.org_category === 'admin' ? 'warning' : 'primary'">
              {{ categoryLabel(row.org_category) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.org_status === '1' ? 'success' : 'info'">
              {{ row.org_status === '1' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="组织有效" width="85">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.org_valid ? 'success' : 'danger'">
              {{ row.org_valid ? '有效' : '无效' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="org_path" label="层级路径" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.org_path || '-' }}</template>
        </el-table-column>
        <template #empty>
          <el-empty description="该用户没有任何组织归属" :image-size="60" />
        </template>
      </el-table>

      <div class="user-detail__section">
        所属受众团队（{{ teams?.team_count ?? 0 }}）
        <span class="user-detail__hint">空集合 = 无知识授权，不是全库可见</span>
      </div>
      <el-table :data="teams?.teams ?? []" size="small" style="width: 100%" max-height="220">
        <el-table-column align="center" prop="team_id" label="团队ID" width="80" />
        <el-table-column prop="group_code" label="团队编码 group_code" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.group_code || '-' }}</template>
        </el-table-column>
        <el-table-column prop="group_name" label="团队名称" min-width="130" show-overflow-tooltip />
        <el-table-column label="负责人" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.leader_name || '-' }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.team_status === '1' ? 'success' : 'info'">
              {{ row.team_status === '1' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="团队有效" width="85">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.team_valid ? 'success' : 'danger'">
              {{ row.team_valid ? '有效' : '无效' }}
            </el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="该用户没有任何受众团队" :image-size="60" />
        </template>
      </el-table>
    </div>
    <template #footer>
      <el-button size="small" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

import { getGsiUserDetail, getGsiUserOrgs, getGsiUserTeams } from '@/api'

defineOptions({ name: 'GsiUserDetailDialog' })

const props = defineProps({
  userId: { type: [Number, String], default: '' },
  title: { type: String, default: '用户详情' },
})

const visible = defineModel({ type: Boolean, default: false })

const loading = ref(false)
const loaded = ref(false)
const profile = ref(null)
const orgs = ref(null)
const teams = ref(null)

const validText = computed(() => {
  if (!profile.value) return '-'
  return profile.value.status === '1' && profile.value.deleted === 0 ? '有效' : '无效'
})

function categoryLabel(category) {
  if (category === 'admin') return '行政'
  if (category === 'business') return '业务'
  return category || '-'
}

watch(visible, (open) => {
  if (open) load()
})

/** 三个请求互不依赖：用户档案可能 404（关系残留），不能连带阻断组织/团队两块 */
async function load() {
  const userId = String(props.userId ?? '').trim()
  if (!userId) return
  loading.value = true
  loaded.value = false
  profile.value = null
  orgs.value = null
  teams.value = null
  try {
    const [profileResult, orgsResult, teamsResult] = await Promise.allSettled([
      // 用户缺失由弹窗内的 warning 说明，避免拦截器再弹一次错误
      getGsiUserDetail(userId, { quiet: true }),
      getGsiUserOrgs(userId),
      getGsiUserTeams(userId),
    ])
    if (profileResult.status === 'fulfilled') profile.value = profileResult.value
    if (orgsResult.status === 'fulfilled') orgs.value = orgsResult.value
    if (teamsResult.status === 'fulfilled') teams.value = teamsResult.value
    loaded.value = true
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.user-detail {
  @include flex($direction: column, $gap: 10px);
  min-height: 160px;

  &__section {
    @include flex($align: baseline, $gap: 10px);
    margin-top: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__hint {
    font-size: 12px;
    font-weight: 400;
    color: var(--app-text-muted);
  }
}
</style>
