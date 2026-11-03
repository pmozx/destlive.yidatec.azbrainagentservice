<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识检索</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/retrieval：以指定用户身份走 #07 双通道检索（关键词字面命中 / 自然语言向量召回 + RRF 融合 + 精排），
          并回显当前生效的检索参数；参数由环境变量维护，页面只读
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" :loading="configLoading" @click="loadConfig">
          <el-icon><Refresh /></el-icon>
          <span>刷新配置</span>
        </el-button>
      </div>
    </div>

    <SectionCard title="检索通道" subtitle="授权先于召回：先按用户算可见发布单元，再做字面/语义召回">
      <template #extra>
        <el-tag :type="config.embedding_ready ? 'success' : 'info'" size="small" effect="light">
          向量化 {{ config.embedding_ready ? '已接入' : '未接入' }}
        </el-tag>
        <el-tag :type="config.rerank_ready ? 'success' : 'warning'" size="small" effect="light">
          精排 {{ config.rerank_ready ? '已接入' : '未接入（回落融合序）' }}
        </el-tag>
      </template>
      <el-descriptions v-loading="configLoading" :column="4" border size="small">
        <el-descriptions-item label="向量化模型">{{ config.embedding_model || '-' }}</el-descriptions-item>
        <el-descriptions-item label="向量维度">{{ config.vector_dim ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="关键词候选上限">{{ config.keyword_candidate_max ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="向量候选上限">{{ config.vector_candidate_max ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="向量召回窗口">{{ config.vector_top_k ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="相似度下限">{{ config.vector_min_score ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="精排窗口">{{ config.rerank_window ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="精排正文上限">
          {{ config.rerank_doc_chars ? `${config.rerank_doc_chars} 字符` : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="精排超时">
          {{ config.rerank_timeout ? `${config.rerank_timeout} 秒` : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="精排下限">{{ config.rerank_min_score ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="精排相对闸门">{{ config.rerank_relative_ratio ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="精排分口径">
          {{ config.rerank_score_is_logit ? 'logit（sigmoid 归一）' : '0–1 概率' }}
        </el-descriptions-item>
        <el-descriptions-item label="RRF k">{{ config.rrf_k ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="摘要长度">{{ config.snippet_length ? `${config.snippet_length} 字符` : '-' }}</el-descriptions-item>
        <el-descriptions-item label="精排模型" :span="2">{{ config.rerank_model || '-' }}</el-descriptions-item>
      </el-descriptions>
    </SectionCard>

    <SectionCard flush>
      <div class="retrieval-toolbar">
        <el-form :inline="true" :model="form" size="small" @submit.prevent>
          <el-form-item label="通道">
            <el-radio-group v-model="form.mode">
              <el-radio-button value="keyword">关键词</el-radio-button>
              <el-radio-button value="natural_language" :disabled="!config.embedding_ready">自然语言</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="查询">
            <el-input
              v-model="form.query"
              placeholder="如 vpn 申请 / 出差报销标准"
              clearable
              style="width: 300px"
              @keyup.enter="runSearch"
            />
          </el-form-item>
          <el-form-item label="租户">
            <el-input v-model="form.tenant_id" placeholder="tenant_id" clearable style="width: 130px" />
          </el-form-item>
          <el-form-item label="用户">
            <el-input v-model="form.user_id" placeholder="数字用户主键" clearable style="width: 130px" @keyup.enter="runSearch" />
          </el-form-item>
          <el-form-item label="条数">
            <el-select v-model="form.limit" style="width: 88px">
              <el-option v-for="item in limitOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="searching" @click="runSearch">检索</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <div v-if="result" class="retrieval-summary">
        <el-tag size="small" effect="plain">{{ modeLabel(result.mode) }}</el-tag>
        <span>命中 <b>{{ result.returned_count }}</b> 条</span>
        <span>耗时 <b>{{ result.elapsed_ms }}</b> ms</span>
        <span>授权发布单元 <b>{{ result.authorized_publication_count }}</b> 个</span>
        <span class="retrieval-summary__query">查询：{{ result.query }}</span>
      </div>

      <el-alert
        v-if="result && result.no_evidence"
        :title="noEvidenceTitle"
        :type="result.authorized_publication_count ? 'info' : 'warning'"
        :closable="false"
        show-icon
        class="retrieval-alert"
      />
      <el-alert
        v-else-if="!result"
        title="尚未执行检索：填好通道、查询词与用户后点「检索」"
        type="info"
        :closable="false"
        show-icon
        class="retrieval-alert"
      />

      <el-table v-loading="searching" :data="result?.hits || []" size="small" style="width: 100%">
        <el-table-column align="center" label="#" type="index" width="56" />
        <el-table-column align="center" label="相关度" width="160">
          <template #default="{ row }">
            <el-progress :percentage="Math.round((row.score || 0) * 100)" :stroke-width="6" />
          </template>
        </el-table-column>
        <el-table-column align="center" prop="title" label="标题" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="publication_id" label="发布单元" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="chunk_id" label="切片" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="snippet" label="命中摘要" min-width="320" show-overflow-tooltip />
        <el-table-column align="center" label="页码" width="96">
          <template #default="{ row }">{{ pageText(row) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="emptyText" :image-size="80" />
        </template>
      </el-table>
    </SectionCard>

    <el-drawer v-model="detailVisible" title="命中详情" size="640px" destroy-on-close>
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="标题">{{ detail.title }}</el-descriptions-item>
          <el-descriptions-item label="相关度">{{ (detail.score * 100).toFixed(1) }}%</el-descriptions-item>
          <el-descriptions-item label="发布单元">{{ detail.publication_id }}</el-descriptions-item>
          <el-descriptions-item label="切片 ID">{{ detail.chunk_id }}</el-descriptions-item>
          <el-descriptions-item label="记录 / 版本">
            {{ detail.record_id }} · v{{ detail.revision }}（version_id={{ detail.version_id }}）
          </el-descriptions-item>
          <el-descriptions-item label="知识编号">{{ detail.knowledge_no || '-' }}</el-descriptions-item>
          <el-descriptions-item label="资源">
            {{ detail.resource_type }} / {{ detail.resource_id || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="页码">{{ pageText(detail) }}</el-descriptions-item>
          <el-descriptions-item label="命中摘要">{{ detail.snippet }}</el-descriptions-item>
        </el-descriptions>
        <div class="retrieval-detail__block">
          <JsonViewer title="hits[] 原始字段" :data="detail" max-height="320px" />
        </div>
      </template>
      <el-empty v-else description="未获取到命中详情" :image-size="80" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getRetrievalConfig, searchKnowledge } from '@/api/module_kb/retrieval'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'

defineOptions({ name: 'KbRetrievalView' })

const MODE_LABELS = { keyword: '关键词通道', natural_language: '自然语言通道' }
const limitOptions = [5, 10, 20, 50]

function modeLabel(mode) {
  return MODE_LABELS[mode] || mode || '-'
}

function pageText(row) {
  if (row.page_from && row.page_to && row.page_from !== row.page_to) return `${row.page_from}-${row.page_to}`
  return row.page_from || row.page_to || '-'
}

// ── 通道配置（只读） ─────────────────────────────────────────────
const configLoading = ref(false)
const config = ref({})

/** 配置拉不到时按「未接入」展示：宁可提示不可用，也不假装精排/向量化已就绪 */
async function loadConfig() {
  configLoading.value = true
  try {
    config.value = (await getRetrievalConfig()) || {}
  } catch {
    config.value = {}
  } finally {
    configLoading.value = false
  }
}

// ── 检索 ────────────────────────────────────────────────────────
const form = reactive({ mode: 'keyword', query: '', tenant_id: 'gsi-default', user_id: '', limit: 10 })
const searching = ref(false)
const result = ref(null)

const noEvidenceTitle = computed(() => {
  const authCount = result.value?.authorized_publication_count ?? 0
  if (!authCount) {
    return '该用户没有可检索的发布单元：先确认用户 ID 是否正确、是否属于受众团队，以及发布单元是否已生效'
  }
  return `授权范围 ${authCount} 个发布单元内没有命中：换关键词或改用自然语言通道再试`
})

const emptyText = computed(() => {
  if (!result.value) return '尚未执行检索'
  if (result.value.no_evidence) return '本次检索没有召回任何切片'
  return '暂无命中'
})

async function runSearch() {
  if (!form.query.trim()) {
    ElMessage.warning('请填写查询内容')
    return
  }
  if (!form.user_id.trim()) {
    ElMessage.warning('请填写用户 ID：检索要先按用户算授权范围')
    return
  }
  searching.value = true
  result.value = null
  try {
    result.value = await searchKnowledge({
      query: form.query.trim(),
      mode: form.mode,
      tenant_id: form.tenant_id.trim() || undefined,
      user_id: form.user_id.trim(),
      limit: form.limit,
    })
  } catch {
    // 请求拦截器已统一提示错误（授权/向量化不可用时后端回 503）
  } finally {
    searching.value = false
  }
}

function reset() {
  form.mode = 'keyword'
  form.query = ''
  form.user_id = ''
  form.limit = 10
  result.value = null
}

// ── 命中详情 ────────────────────────────────────────────────────
const detailVisible = ref(false)
const detail = ref(null)

function openDetail(row) {
  detail.value = row
  detailVisible.value = true
}

onMounted(loadConfig)
</script>

<style scoped lang="scss">
.retrieval-toolbar {
  padding: 20px 26px 4px;
}

.retrieval-summary {
  @include flex($align: center, $gap: 14px);
  flex-wrap: wrap;
  padding: 10px 26px 0;
  font-size: 12px;
  color: var(--app-text-secondary);

  b {
    color: var(--app-text-primary);
  }

  &__query {
    @include ellipsis;
    max-width: 420px;
    color: var(--app-text-muted);
  }
}

.retrieval-alert {
  margin: 12px 26px 0;
  width: auto;
}

.retrieval-detail__block {
  margin-top: 14px;
}
</style>
