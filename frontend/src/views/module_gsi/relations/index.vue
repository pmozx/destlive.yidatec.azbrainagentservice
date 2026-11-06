<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">用户与组织关系</h2>
        <p class="page-header__desc">
          一页打通 GSI 的用户、组织与成员关系：左侧默认按受众团队下钻，也可切到组织树（默认含所有下级），中间以
          用户为主体分页，行内直接给出组织归属数与受众团队数；点「详情」查看该用户档案、全部组织归属（含层级
          路径）与全部受众团队。组织只回答「人在哪个部门」，知识可见性只由受众团队的 group_code 决定 —— 团队数
          为 0 即等于「无知识授权」。
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="treeLoading || teamLoading || loading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="gsi-cards">
      <StatCard label="用户" :value="formatNumber(total)" icon="User" tip="当前筛选条件下的用户数（按用户去重分页）" />
      <StatCard
        label="组织节点"
        :value="formatNumber(treeNodeCount)"
        icon="OfficeBuilding"
        tip="当前分类筛选下树内节点总数"
      />
      <StatCard label="受众团队" :value="formatNumber(teamTotal)" icon="UserFilled" tip="受众团队总数（可翻页，点行下钻成员）" />
      <StatCard label="当前范围" :value="scopeLabel" icon="Pointer" tip="选中组织/团队后即按该范围下钻用户" />
    </div>

    <SectionCard flush>
      <div class="gsi-split">
        <aside class="gsi-split__side">
          <div class="gsi-split__head">
            <span class="gsi-split__title">关系维度</span>
            <el-button link type="primary" size="small" :disabled="!hasScope" @click="clearScope">
              看全部
            </el-button>
          </div>

          <el-radio-group v-model="scopeTab" size="small" @change="handleScopeTabChange">
            <el-radio-button value="team">受众团队</el-radio-button>
            <el-radio-button value="org">组织树</el-radio-button>
          </el-radio-group>

          <template v-if="scopeTab === 'org'">
            <div class="gsi-split__filters">
              <el-radio-group v-model="treeCategory" size="small" @change="loadTree">
                <el-radio-button value="">全部</el-radio-button>
                <el-radio-button value="admin">行政</el-radio-button>
                <el-radio-button value="business">业务</el-radio-button>
              </el-radio-group>
              <el-input v-model="treeKeyword" placeholder="按名称过滤组织" clearable size="small" />
              <div class="gsi-split__switches">
                <el-checkbox v-model="includeDisabled" size="small" @change="loadTree">含停用</el-checkbox>
                <el-checkbox v-model="includeDeleted" size="small" @change="loadTree">含已删除</el-checkbox>
                <el-checkbox v-model="query.include_sub" size="small" @change="handleIncludeSubChange">含下级</el-checkbox>
              </div>
            </div>

            <div v-loading="treeLoading" class="gsi-split__tree">
              <el-tree
                ref="treeRef"
                :data="treeData"
                node-key="id"
                :props="treeProps"
                :filter-node-method="filterTreeNode"
                highlight-current
                :expand-on-click-node="false"
                @node-click="handleNodeClick"
              >
                <template #default="{ data }">
                  <span class="gsi-tree-node">
                    <span class="gsi-tree-node__name">{{ data.name }}</span>
                    <el-tag size="small" effect="plain" :type="data.status === '1' ? 'success' : 'info'">
                      {{ data.status === '1' ? '启用' : '停用' }}
                    </el-tag>
                    <el-tag v-if="data.deleted === 1" size="small" type="danger" effect="plain">已删除</el-tag>
                  </span>
                </template>
              </el-tree>
              <el-empty
                v-if="!treeLoading && !treeData.length"
                description="没有符合条件的组织节点"
                :image-size="60"
              />
            </div>
          </template>

          <template v-else>
            <!-- 查询条件暂隐藏：团队总量有限，翻页 + 点行下钻已够用 -->
            <div v-loading="teamLoading" class="gsi-team-table-wrap">
              <el-table
                ref="teamTableRef"
                :data="teamList"
                size="small"
                row-key="id"
                highlight-current-row
                max-height="520"
                class="gsi-team-table"
                @row-click="selectTeam"
              >
                <el-table-column align="center" prop="id" label="团队ID" width="54" />
                <el-table-column label="团队编码" min-width="86" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="gsi-team-table__code">{{ row.group_code || '-' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="团队名称" min-width="86" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="gsi-team-table__name">{{ row.group_name || '-' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="负责人" width="60" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.leader_name || '-' }}</template>
                </el-table-column>
                <el-table-column align="center" label="状态" width="56">
                  <template #default="{ row }">
                    <el-tag size="small" effect="light" :type="row.status === '1' ? 'success' : 'info'">
                      {{ row.status === '1' ? '启用' : '停用' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" label="团队有效" width="62">
                  <template #default="{ row }">
                    <span class="gsi-team-table__valid" :class="{ 'is-invalid': row.deleted === 1 }">
                      {{ row.deleted === 1 ? '失效' : '有效' }}
                    </span>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty description="暂无受众团队" :image-size="56" />
                </template>
              </el-table>
            </div>

            <div class="gsi-split__pager">
              <el-pagination
                :current-page="teamPager.page_no"
                :page-size="teamPager.page_size"
                :total="teamTotal"
                layout="total, prev, pager, next"
                small
                background
                @current-change="handleTeamPageChange"
              />
            </div>
          </template>

          <p class="gsi-split__hint">
            团队列表只做浏览与下钻：搜索条件暂隐藏，接口仍支持 group_code / group_name / status / deleted。
            受众团队的 group_code 才是授权匹配依据，audience_team 列只是历史兼容标识。
          </p>
        </aside>

        <div class="gsi-split__main">
          <div class="gsi-scope">
            <template v-if="scopeTab === 'org' && selectedOrg">
              <span class="gsi-scope__name">{{ selectedOrg.name }}</span>
              <el-tag size="small" effect="plain">{{ selectedOrg.code || `ID ${selectedOrg.id}` }}</el-tag>
              <el-tag size="small" effect="plain" :type="selectedOrg.category === 'admin' ? 'warning' : 'primary'">
                {{ categoryLabel(selectedOrg.category) }}
              </el-tag>
              <el-tag size="small" effect="light" :type="selectedOrg.status === '1' ? 'success' : 'info'">
                {{ selectedOrg.status === '1' ? '启用' : '停用' }}
              </el-tag>
              <el-tag v-if="query.include_sub" size="small" type="success" effect="plain">
                含下级 {{ formatNumber(subtreeCount) }} 个节点
              </el-tag>
            </template>
            <template v-else-if="scopeTab === 'team' && selectedTeam">
              <span class="gsi-scope__name">{{ selectedTeam.group_name }}</span>
              <el-tag size="small" effect="plain">{{ selectedTeam.group_code }}</el-tag>
              <el-tag size="small" effect="light" :type="selectedTeam.status === '1' ? 'success' : 'info'">
                {{ selectedTeam.status === '1' ? '启用' : '停用' }}
              </el-tag>
              <el-tag v-if="selectedTeam.deleted === 1" size="small" type="danger" effect="plain">已删除</el-tag>
            </template>
            <span v-else class="gsi-scope__placeholder">
              未限定范围，当前展示全部用户；每行只给归属数量，逐条关系在「详情」里展开。
            </span>
          </div>

          <div class="gsi-toolbar">
            <el-form :inline="true" :model="query" size="small" @submit.prevent>
              <el-form-item label="账号">
                <el-input v-model="query.username" placeholder="模糊" clearable style="width: 130px" />
              </el-form-item>
              <el-form-item label="姓名">
                <el-input v-model="query.cn_name" placeholder="模糊" clearable style="width: 110px" />
              </el-form-item>
              <el-form-item label="职位">
                <el-input v-model="query.position_name" placeholder="模糊" clearable style="width: 130px" />
              </el-form-item>
              <el-form-item label="在职">
                <el-select v-model="query.status" placeholder="全部" clearable style="width: 100px">
                  <el-option label="在职" value="1" />
                  <el-option label="离职" value="0" />
                </el-select>
              </el-form-item>
              <el-form-item label="允许登录">
                <el-select v-model="query.allow_login" placeholder="全部" clearable style="width: 100px">
                  <el-option label="允许" value="1" />
                  <el-option label="禁止" value="0" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="loading" @click="search">查询</el-button>
                <el-button @click="handleReset">重置</el-button>
              </el-form-item>
              <el-form-item label="按用户查看">
                <el-input
                  v-model="lookupUserId"
                  placeholder="用户ID"
                  clearable
                  style="width: 100px"
                  @keyup.enter="openLookup"
                />
                <el-button style="margin-left: 8px" @click="openLookup">详情</el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
            <el-table-column align="center" prop="id" label="用户ID" width="78" />
            <el-table-column label="用户名" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.username || '-' }}</template>
            </el-table-column>
            <el-table-column label="中文名" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.cn_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="英文名" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.en_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="职位" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.position_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="职级" width="94" show-overflow-tooltip>
              <template #default="{ row }">{{ row.rank_name || (row.rank_id ? `#${row.rank_id}` : '-') }}</template>
            </el-table-column>
            <el-table-column align="center" label="在职" width="78">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="row.status === '1' ? 'success' : 'info'">
                  {{ row.status === '1' ? '在职' : '离职' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="允许登录" width="88">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.allow_login === '1' ? 'success' : 'info'">
                  {{ row.allow_login === '1' ? '允许' : '禁止' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="删除状态" width="88">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.deleted === 1 ? 'danger' : 'info'">
                  {{ row.deleted === 1 ? '已删除' : '正常' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="组织" width="72">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.org_count ? 'primary' : 'info'">
                  {{ row.org_count ?? '-' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="受众团队" width="108">
              <template #default="{ row }">
                <el-tag v-if="row.team_count" size="small" effect="plain" type="success">
                  {{ row.team_count }} 个
                </el-tag>
                <el-tag v-else size="small" effect="plain" type="danger">无授权</el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="操作" width="72" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail(row.id)">详情</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无用户（或筛选条件过窄）" :image-size="80" />
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
        </div>
      </div>
    </SectionCard>

    <GsiUserDetailDialog v-model="detailVisible" :user-id="detailUserId" title="用户详情 · 组织与团队归属" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { getGsiAudienceTeamList, getGsiOrgTree, getGsiUserList } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

import GsiUserDetailDialog from '../components/UserDetailDialog.vue'

defineOptions({ name: 'GsiRelationView' })

const treeProps = { label: 'name', children: 'children' }

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['username', 'cn_name', 'position_name', 'status', 'allow_login', 'org_id', 'team_id'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  // include_sub 只对组织生效：没选组织时不传，省掉后端一次整表展开
  if (params.org_id !== '' && params.include_sub) result.include_sub = true
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiUserList(buildQueryParams(params)),
  {
    defaultQuery: {
      username: '',
      cn_name: '',
      position_name: '',
      status: '',
      allow_login: '',
      org_id: '',
      team_id: '',
      include_sub: true,
    },
  },
)

// ── 关系维度：组织树 / 受众团队 ──────────────────────────────────────

// 受众团队才是知识可见性的判定依据，默认落在这一维度
const scopeTab = ref('team')
const selectedOrg = ref(null)
const selectedTeam = ref(null)

const hasScope = computed(() => Boolean(selectedOrg.value || selectedTeam.value))

const scopeLabel = computed(() => {
  if (scopeTab.value === 'org' && selectedOrg.value) return selectedOrg.value.name
  if (scopeTab.value === 'team' && selectedTeam.value) return selectedTeam.value.group_name
  return '全部'
})

function categoryLabel(category) {
  if (category === 'admin') return '行政'
  if (category === 'business') return '业务'
  return category || '-'
}

/** 树是递归结构，节点数只能自己数一遍 */
function walkTree(nodes, visit) {
  nodes.forEach((node) => {
    visit(node)
    if (node.children?.length) walkTree(node.children, visit)
  })
}

function findNode(nodes, id) {
  for (const node of nodes) {
    if (node.id === id) return node
    if (node.children?.length) {
      const hit = findNode(node.children, id)
      if (hit) return hit
    }
  }
  return null
}

// ── 组织树 ──────────────────────────────────────────────────────────

const treeRef = ref()
const treeLoading = ref(false)
const treeData = ref([])
const treeCategory = ref('')
const treeKeyword = ref('')
const includeDisabled = ref(true)
const includeDeleted = ref(false)

const treeNodeCount = computed(() => {
  let count = 0
  walkTree(treeData.value, () => {
    count += 1
  })
  return count
})

/** 当前选中节点下的子孙数：与后端「含下级」展开口径保持一致（不含自身） */
const subtreeCount = computed(() => {
  const root = selectedOrg.value ? findNode(treeData.value, selectedOrg.value.id) : null
  let count = 0
  walkTree(root?.children ?? [], () => {
    count += 1
  })
  return count
})

function filterTreeNode(value, data) {
  const keyword = String(value ?? '').trim().toLowerCase()
  if (!keyword) return true
  return [data.name, data.code].some((text) => String(text ?? '').toLowerCase().includes(keyword))
}

// 关键字只过滤已加载的树，不重新请求
watch(treeKeyword, (value) => {
  treeRef.value?.filter(value)
})

async function loadTree() {
  treeLoading.value = true
  try {
    // category 为空不能传：后端把它当精确筛选条件，传空串会返回空树
    const params = {
      include_disabled: includeDisabled.value,
      include_deleted: includeDeleted.value,
    }
    if (treeCategory.value) params.category = treeCategory.value
    treeData.value = (await getGsiOrgTree(params)) || []
    // 重新加载后选中的节点可能已不在树内，回读一次保持高亮与筛选一致
    if (selectedOrg.value) {
      const picked = findNode(treeData.value, selectedOrg.value.id)
      if (picked) {
        treeRef.value?.setCurrentKey(picked.id)
        selectedOrg.value = picked
      } else {
        clearScope()
      }
    }
  } catch {
    // 请求拦截器已统一提示错误（组织节点超过 5000 时后端返回 413）
    treeData.value = []
  } finally {
    treeLoading.value = false
  }
}

// ── 受众团队清单 ────────────────────────────────────────────────────

const teamTableRef = ref()
const teamLoading = ref(false)
const teamList = ref([])
const teamTotal = ref(0)
const teamPager = reactive({ page_no: 1, page_size: 50 })

/** 团队列表先只做分页浏览：查询条件隐藏，等团队量涨上来再放回来 */
async function loadTeams() {
  teamLoading.value = true
  try {
    const result = await getGsiAudienceTeamList({
      page_no: teamPager.page_no,
      page_size: teamPager.page_size,
    })
    teamList.value = result?.items ?? []
    teamTotal.value = result?.total ?? 0
    syncSelectedTeam()
  } catch {
    // 请求拦截器已统一提示错误
    teamList.value = []
    teamTotal.value = 0
  } finally {
    teamLoading.value = false
  }
}

/** 刷新后把选中团队换成最新行，范围条上的负责人/状态才不会停留在旧值 */
function syncSelectedTeam() {
  if (!selectedTeam.value) return
  const hit = teamList.value.find((team) => team.id === selectedTeam.value.id)
  if (hit) {
    selectedTeam.value = hit
    teamTableRef.value?.setCurrentRow(hit)
  }
}

function handleTeamPageChange(page) {
  teamPager.page_no = page
  loadTeams()
}

// ── 范围选择 ────────────────────────────────────────────────────────

function handleNodeClick(data) {
  selectedOrg.value = data
  query.org_id = String(data.id)
  // 组织与团队是平行维度：两个条件同时生效会让「这个人为什么出现」说不清
  selectedTeam.value = null
  query.team_id = ''
  search()
}

function selectTeam(team) {
  if (selectedTeam.value?.id === team.id) return
  selectedTeam.value = team
  teamTableRef.value?.setCurrentRow(team)
  query.team_id = String(team.id)
  selectedOrg.value = null
  treeRef.value?.setCurrentKey(null)
  query.org_id = ''
  search()
}

function clearScope() {
  selectedOrg.value = null
  selectedTeam.value = null
  treeRef.value?.setCurrentKey(null)
  teamTableRef.value?.setCurrentRow(null)
  query.org_id = ''
  query.team_id = ''
  search()
}

function handleScopeTabChange() {
  // 切维度同时清掉另一个维度的筛选，否则会出现「在团队页却仍按组织过滤」
  clearScope()
}

function handleIncludeSubChange() {
  // 没选组织时该开关没有意义，不必多打一次请求
  if (selectedOrg.value) search()
}

function handleReset() {
  // reset 会把 org_id / team_id 一并置空并重新查询，这里只同步清掉左侧选中态
  selectedOrg.value = null
  selectedTeam.value = null
  treeRef.value?.setCurrentKey(null)
  teamTableRef.value?.setCurrentRow(null)
  reset()
}

async function refreshAll() {
  await Promise.all([loadTree(), loadTeams(), load()])
}

// ── 用户详情 ────────────────────────────────────────────────────────

const detailVisible = ref(false)
const detailUserId = ref('')
const lookupUserId = ref('')

function openDetail(userId) {
  detailUserId.value = userId
  detailVisible.value = true
}

/** 按用户直接查：该用户可能不在当前筛选结果里，不能只依赖表格行 */
function openLookup() {
  const userId = String(lookupUserId.value ?? '').trim()
  if (!userId) {
    ElMessage.warning('请先输入用户ID')
    return
  }
  openDetail(userId)
}

onMounted(() => {
  loadTree()
  loadTeams()
})
</script>

<style scoped lang="scss">
.gsi-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: var(--app-gap);
}

.gsi-split {
  @include flex($align: stretch);

  &__side {
    @include flex($direction: column, $gap: 10px);
    // 受众团队已改为表格（6 列），侧栏按表格内容宽固定，切维度不跳宽
    width: 460px;
    flex: 0 0 460px;
    padding: 16px 12px 16px 26px;
    border-right: 1px solid var(--el-border-color-lighter);
  }

  &__main {
    flex: 1;
    min-width: 0;
    padding-bottom: 4px;
  }

  &__head {
    @include flex($justify: space-between, $gap: 8px);
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__filters {
    @include flex($direction: column, $align: stretch, $gap: 8px);
  }

  &__switches {
    @include flex($gap: 4px);
    flex-wrap: wrap;
  }

  &__tree {
    min-height: 220px;
    max-height: 520px;
    overflow: auto;
  }

  &__pager {
    @include flex($justify: center);
  }

  &__hint {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--app-text-muted);
  }
}

.gsi-scope {
  @include flex($align: center, $gap: 8px);
  flex-wrap: wrap;
  margin: 16px 26px 0;
  padding: 8px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background-color: rgba(120, 140, 175, 0.04);
  font-size: 12px;
  color: var(--app-text-secondary);

  &__name {
    font-size: 14px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__placeholder {
    font-size: 12px;
    color: var(--app-text-muted);
  }
}

.gsi-tree-node {
  @include flex($gap: 6px);
  min-width: 0;
  font-size: 13px;

  &__name {
    @include ellipsis;
  }
}

.gsi-team-table-wrap {
  min-height: 220px;
}

.gsi-team-table {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;

  // 6 列塞在侧栏里：表头浅色底 + 12px 字号，行内不再堆第二行文字
  :deep(.el-table) {
    --el-table-header-bg-color: rgba(120, 140, 175, 0.07);
    --el-table-header-text-color: var(--app-text-secondary);
    --el-table-row-hover-bg-color: rgba(120, 140, 175, 0.08);
    font-size: 12px;
  }

  :deep(th.el-table__cell) {
    font-weight: 600;
  }

  :deep(.el-table__body tr) {
    cursor: pointer;
  }

  // 选中行用整行浅底，默认描边在小表格里太重
  :deep(.el-table__body tr.current-row > td.el-table__cell) {
    background-color: var(--el-color-primary-light-9);
  }

  &__code {
    font-family: var(--el-font-family-mono, monospace);
    color: var(--el-color-primary);
  }

  &__name {
    font-weight: 500;
    color: var(--app-text-primary);
  }

  &__valid {
    color: var(--el-color-success);

    &::before {
      content: '';
      display: inline-block;
      width: 6px;
      height: 6px;
      margin-right: 4px;
      border-radius: 50%;
      background-color: currentColor;
      vertical-align: middle;
    }

    &.is-invalid {
      color: var(--el-color-danger);
    }
  }
}

.gsi-toolbar {
  padding: 12px 26px 0;
}

.gsi-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}
</style>
