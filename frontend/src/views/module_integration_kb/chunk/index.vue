<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识分片</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/chunk：分片是向量化与检索的最小单位，由解析流水线写入（按文档幂等覆盖）；
          这里只做查询与人工清理，删除后需重新解析对应文档才能补齐
        </p>
      </div>
    </div>

    <SectionCard flush>
      <div class="chunk-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="空间">
            <el-select v-model="query.space_id" placeholder="全部空间" clearable style="width: 160px">
              <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="文档ID">
            <el-input v-model="query.doc_id" placeholder="精确匹配" clearable style="width: 130px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="分片内容">
            <el-input
              v-model="query.content"
              placeholder="模糊匹配正文"
              clearable
              style="width: 200px"
              @keyup.enter="search"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>

        <div class="chunk-actions">
          <el-button size="small" type="danger" plain :disabled="!selectedIds.length" @click="removeMany">
            删除选中<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
          </el-button>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="list"
        size="small"
        row-key="id"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="id" label="分片ID" width="90" align="center" />
        <el-table-column prop="doc_id" label="文档ID" width="90" align="center" />
        <el-table-column prop="space_id" label="空间ID" width="90" align="center" />
        <el-table-column prop="chunk_index" label="序号" width="70" align="center" />
        <el-table-column label="页码" width="70" align="center">
          <template #default="{ row }">{{ row.page_no ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="section_title" label="标题链" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.section_title || '-' }}</template>
        </el-table-column>
        <el-table-column prop="content" label="分片正文" min-width="320" show-overflow-tooltip>
          <template #default="{ row }">{{ row.content }}</template>
        </el-table-column>
        <el-table-column label="字符数" width="90" align="center">
          <template #default="{ row }">{{ row.token_count || '-' }}</template>
        </el-table-column>
        <el-table-column label="详情" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有命中的分片：可在「知识文档」里对文档提交解析" :image-size="80" />
        </template>
      </el-table>

      <div class="chunk-pagination">
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

    <el-drawer v-model="detailVisible" title="分片详情" size="52%" destroy-on-close>
      <div v-if="detail" class="chunk-detail">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="分片ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="文档ID">{{ detail.doc_id }}</el-descriptions-item>
          <el-descriptions-item label="空间ID">{{ detail.space_id }}</el-descriptions-item>
          <el-descriptions-item label="序号">{{ detail.chunk_index }}</el-descriptions-item>
          <el-descriptions-item label="页码">{{ detail.page_no ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="字符数">{{ detail.token_count || '-' }}</el-descriptions-item>
          <el-descriptions-item label="标题链" :span="2">{{ detail.section_title || '-' }}</el-descriptions-item>
        </el-descriptions>

        <p class="chunk-detail__label">分片正文</p>
        <pre class="chunk-detail__content">{{ detail.content }}</pre>

        <template v-if="detail.meta && Object.keys(detail.meta).length">
          <p class="chunk-detail__label">解析器附加信息</p>
          <pre class="chunk-detail__content">{{ prettyJson(detail.meta) }}</pre>
        </template>
      </div>
      <template #footer>
        <el-button size="small" @click="detailVisible = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { deleteChunks, getChunkList, getSpaceOptions } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { prettyJson } from '@/utils/format'

defineOptions({ name: 'IntegrationKbChunkView' })

const spaces = ref([])

/** 空值不下发；doc_id/space_id 走精确匹配，非数字输入直接忽略（后端 ge=1 会拒） */
function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.space_id) result.space_id = params.space_id
  if (params.doc_id) result.doc_id = params.doc_id
  if (params.content) result.content = params.content
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getChunkList(buildParams(params)),
  { defaultQuery: { space_id: '', doc_id: '', content: '' } },
)

const selectedIds = ref([])

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

async function removeMany() {
  if (!selectedIds.value.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedIds.value.length} 条分片吗？删除后这些内容立即失去检索能力，需重新解析文档才能补回。`,
      '删除分片确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteChunks(selectedIds.value)
    ElMessage.success('删除成功')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}

// ── 详情 ───────────────────────────────────────────────────────────────

const detailVisible = ref(false)
const detail = ref(null)

function openDetail(row) {
  detail.value = row
  detailVisible.value = true
}

onMounted(async () => {
  try {
    spaces.value = (await getSpaceOptions()) ?? []
  } catch {
    spaces.value = []
  }
})
</script>

<style scoped lang="scss">
.chunk-toolbar {
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 20px 26px 4px;
}

.chunk-actions {
  @include flex($justify: flex-end);
  padding-bottom: 8px;
}

.chunk-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.chunk-detail {
  &__label {
    margin: 16px 0 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__content {
    max-height: 40vh;
    margin: 0;
    padding: 12px 14px;
    overflow: auto;
    font-family: var(--app-font-mono, monospace);
    font-size: 12px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
    background-color: var(--el-fill-color-light);
    border-radius: 10px;
  }
}
</style>
