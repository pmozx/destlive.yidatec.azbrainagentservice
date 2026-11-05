<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">组织与成员</h2>
        <p class="page-header__desc">
          只读查看 GSI 组织架构（sbp_sys_org）与组织成员（sbp_sys_org_member）。左侧按行政 / 业务分类展开层级树，
          点击节点即下钻成员列表；右侧联表带出组织名称、编码与用户在职状态。组织只回答「人在哪个部门」，
          知识可见性由受众团队决定，不能反过来用组织推授权。
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="treeLoading || loading" @click="refreshAll">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="gsi-cards">
      <StatCard label="组织节点" :value="formatNumber(treeNodeCount)" icon="OfficeBuilding" tip="当前筛选条件下树内节点总数" />
      <StatCard label="成员记录" :value="formatNumber(total)" icon="User" tip="按当前成员查询条件统计的总数" />
      <StatCard label="当前组织" :value="selectedOrgName" icon="Pointer" tip="点击左侧节点即可下钻成员" />
      <StatCard
        label="行政 / 业务"
        :value="`${formatNumber(categoryCount.admin)} / ${formatNumber(categoryCount.business)}`"
        icon="Share"
        tip="按 category 分类的节点数"
      />
    </div>

    <SectionCard flush>
      <div class="gsi-split">
        <aside class="gsi-split__side">
          <div class="gsi-split__head">
            <span class="gsi-split__title">组织树</span>
            <el-button link type="primary" size="small" :disabled="!selectedOrg" @click="clearOrgFilter">
              看全部
            </el-button>
          </div>

          <div class="gsi-split__filters">
            <el-radio-group v-model="treeCategory" size="small" @change="loadTree">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="admin">行政</el-radio-button>
              <el-radio-button value="business">业务</el-radio-button>
            </el-radio-group>
            <div class="gsi-split__switches">
              <el-checkbox v-model="includeDisabled" size="small" @change="loadTree">含停用</el-checkbox>
              <el-checkbox v-model="includeDeleted" size="small" @change="loadTree">含已删除</el-checkbox>
            </div>
          </div>

          <div v-loading="treeLoading" class="gsi-split__tree">
            <el-tree
              ref="treeRef"
              :data="treeData"
              node-key="id"
              :props="treeProps"
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

          <p class="gsi-split__hint">
            行政树与业务树共用一张表，不能合并成一棵：跨树拼接会得到「一个人挂两个部门」的错觉。
          </p>
        </aside>

        <div class="gsi-split__main">
          <div class="gsi-toolbar">
            <el-form :inline="true" :model="query" size="small" @submit.prevent>
              <el-form-item label="组织ID">
                <el-input v-model="query.org_id" placeholder="点击左侧节点" clearable style="width: 130px" />
              </el-form-item>
              <el-form-item label="用户ID">
                <el-input v-model="query.user_id" placeholder="按用户查" clearable style="width: 120px" />
              </el-form-item>
              <el-form-item label="组织分类">
                <el-select v-model="query.org_category" placeholder="全部" clearable style="width: 108px">
                  <el-option label="行政" value="admin" />
                  <el-option label="业务" value="business" />
                </el-select>
              </el-form-item>
              <el-form-item label="在职状态">
                <el-select v-model="query.user_status" placeholder="全部" clearable style="width: 108px">
                  <el-option label="在职" value="1" />
                  <el-option label="离职" value="0" />
                </el-select>
              </el-form-item>
              <el-form-item label="仅有效组织">
                <el-switch v-model="query.valid_only" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="loading" @click="search">查询</el-button>
                <el-button @click="handleReset">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
            <el-table-column align="center" prop="org_id" label="组织ID" width="80" />
            <el-table-column align="center" prop="org_name" label="组织名称" min-width="150" show-overflow-tooltip />
            <el-table-column align="center" prop="org_code" label="组织编码" min-width="130" show-overflow-tooltip />
            <el-table-column align="center" prop="org_type" label="节点类型" width="100" />
            <el-table-column align="center" label="组织分类" width="95">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.org_category === 'admin' ? 'warning' : 'primary'">
                  {{ categoryLabel(row.org_category) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="组织状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="row.org_status === '1' ? 'success' : 'info'">
                  {{ row.org_status === '1' ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="组织有效" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.org_valid ? 'success' : 'danger'">
                  {{ row.org_valid ? '有效' : '无效' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" prop="user_id" label="用户ID" width="80" />
            <el-table-column align="center" label="用户存在" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.user_exists ? 'success' : 'danger'">
                  {{ row.user_exists ? '存在' : '缺失' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="在职状态" width="90">
              <template #default="{ row }">
                <span v-if="row.user_status === null || row.user_status === undefined">-</span>
                <el-tag v-else size="small" effect="light" :type="row.user_status === '1' ? 'success' : 'info'">
                  {{ row.user_status === '1' ? '在职' : '离职' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column align="center" label="允许登录" width="90">
              <template #default="{ row }">{{ row.allow_login === '1' ? '允许' : row.allow_login === '0' ? '禁止' : '-' }}</template>
            </el-table-column>
            <el-table-column align="center" label="用户有效" width="90" fixed="right">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.user_valid ? 'success' : 'danger'">
                  {{ row.user_valid ? '有效' : '无效' }}
                </el-tag>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无组织成员数据（或查询条件过窄）" :image-size="80" />
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
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

import { getGsiOrgMemberPageWithOrg, getGsiOrgTree } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import StatCard from '@/components/common/StatCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatNumber } from '@/utils/format'

defineOptions({ name: 'GsiOrgView' })

const treeProps = { label: 'name', children: 'children' }

/** 只送有值的条件：空串会变成 `field=` 被后端当成「等于空字符串」筛掉全部数据 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['org_id', 'user_id', 'org_category', 'org_type', 'user_status'].forEach((key) => {
    if (params[key] !== '' && params[key] !== null && params[key] !== undefined) result[key] = params[key]
  })
  if (params.valid_only) result.valid_only = true
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getGsiOrgMemberPageWithOrg(buildQueryParams(params)),
  {
    defaultQuery: { org_id: '', user_id: '', org_category: '', org_type: '', user_status: '', valid_only: false },
  },
)

// ── 组织树 ──────────────────────────────────────────────────────────

const treeRef = ref()
const treeLoading = ref(false)
const treeData = ref([])
const treeCategory = ref('')
const includeDisabled = ref(true)
const includeDeleted = ref(false)
const selectedOrg = ref(null)

const selectedOrgName = computed(() => (selectedOrg.value ? `${selectedOrg.value.name}（${selectedOrg.value.id}）` : '全部'))

/** 树是递归结构，节点数与分类分布只能自己数一遍 */
function walkTree(nodes, visit) {
  nodes.forEach((node) => {
    visit(node)
    if (node.children?.length) walkTree(node.children, visit)
  })
}

const treeNodeCount = computed(() => {
  let count = 0
  walkTree(treeData.value, () => {
    count += 1
  })
  return count
})

const categoryCount = computed(() => {
  const result = { admin: 0, business: 0 }
  walkTree(treeData.value, (node) => {
    if (node.category === 'admin') result.admin += 1
    if (node.category === 'business') result.business += 1
  })
  return result
})

function categoryLabel(category) {
  if (category === 'admin') return '行政'
  if (category === 'business') return '业务'
  return category || '-'
}

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
        clearOrgFilter()
      }
    }
  } catch {
    // 请求拦截器已统一提示错误（超过 5000 节点时后端返回 413）
    treeData.value = []
  } finally {
    treeLoading.value = false
  }
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

function handleNodeClick(data) {
  selectedOrg.value = data
  query.org_id = String(data.id)
  search()
}

function clearOrgFilter() {
  selectedOrg.value = null
  treeRef.value?.setCurrentKey(null)
  query.org_id = ''
  search()
}

function handleReset() {
  // reset 会把 org_id 一并置空并重新查询，这里只需同步清掉树上的高亮，避免多打一次请求
  selectedOrg.value = null
  treeRef.value?.setCurrentKey(null)
  reset()
}

async function refreshAll() {
  await Promise.all([loadTree(), load()])
}

onMounted(loadTree)
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
    width: 320px;
    flex: 0 0 320px;
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
    @include flex($direction: column, $align: flex-start, $gap: 8px);
  }

  &__switches {
    @include flex($gap: 4px);
  }

  &__tree {
    min-height: 220px;
    max-height: 560px;
    overflow: auto;
  }

  &__hint {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
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

.gsi-toolbar {
  padding: 16px 26px 0;
}

.gsi-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}
</style>
