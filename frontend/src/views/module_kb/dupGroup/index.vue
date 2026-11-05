<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">重复治理</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/dup-groups：发起重复检测（#17）、保存前查重（#16）、生成合并预览（#19），查看五类治理分组
          （文档完全重复 / 文档相近 / 切片重复 / AI 问答 / 人工问答），并标记「合并 / 忽略」治理结论
        </p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" size="small" @click="openDetect">
          <el-icon><Aim /></el-icon>
          <span>发起检测</span>
        </el-button>
        <el-button size="small" @click="openCheck">
          <el-icon><Search /></el-icon>
          <span>保存前查重</span>
        </el-button>
        <el-button size="small" :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="kb-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="治理类型">
            <el-select v-model="query.kind" placeholder="全部" clearable style="width: 150px">
              <el-option v-for="item in kindOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="资源类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="治理状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="租户">
            <el-input v-model="query.tenant_id" placeholder="gsi-default" clearable style="width: 140px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" label="治理类型" width="120">
          <template #default="{ row }">
            <el-tag :type="kindTagType(row.kind)" size="small" effect="light">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="title" label="分组标题" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="group_key" label="分组标识 group_key" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" label="资源类型" width="95">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="组内最低相似度" width="125">
          <template #default="{ row }">{{ similarityText(row.similarity) }}</template>
        </el-table-column>
        <el-table-column align="center" prop="member_count" label="成员数" width="85" />
        <el-table-column align="center" label="治理状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="result_id" label="最近结果 result_id" min-width="185" show-overflow-tooltip />
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <template v-if="row.status === 'active'">
              <el-button link type="success" size="small" @click="mark(row, 'merged')">标记合并</el-button>
              <el-button link type="warning" size="small" @click="mark(row, 'dismissed')">忽略</el-button>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无重复分组（由重复检测任务物化）" :image-size="80" />
        </template>
      </el-table>

      <div class="kb-pagination">
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

    <!-- #17 发起检测：管理端没有 GSI 签发 scope，范围改由发布单元反查 -->
    <el-dialog v-model="detectVisible" title="发起重复检测" width="640px" destroy-on-close>
      <el-form :model="detectForm" label-width="100px" size="small" @submit.prevent>
        <el-form-item label="租户 ID">
          <el-input v-model="detectForm.tenant_id" clearable maxlength="64" placeholder="留空按范围推导" />
        </el-form-item>
        <el-form-item label="治理范围">
          <el-select
            v-model="detectForm.publication_ids"
            multiple
            filterable
            clearable
            collapse-tags
            collapse-tags-tooltip
            :loading="publicationLoading"
            placeholder="留空表示全部生效发布单元"
            style="width: 100%"
          >
            <el-option v-for="item in publicationOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <div class="kb-form__hint">后端按发布单元当前生效快照反查记录 ID 与内容摘要</div>
        </el-form-item>
        <el-form-item label="治理类型">
          <el-checkbox-group v-model="detectForm.kinds">
            <el-checkbox v-for="item in kindOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="返回条数">
          <el-input-number v-model="detectForm.limit" :min="1" :max="100" controls-position="right" />
          <span class="kb-form__hint">仅限制本次响应条数，全部分组都会写库</span>
        </el-form-item>
      </el-form>

      <template v-if="detectResult">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          :title="`检测完成：范围 ${detectResult.scope_size} 条，产出 ${detectResult.total_groups} 个分组`"
          :description="`result_id ${detectResult.result_id}（快照 30 分钟内可用于合并预览）`"
        />
        <el-table v-if="(detectResult.items || []).length" :data="detectResult.items" size="small" class="kb-dialog__table">
          <el-table-column align="center" label="治理类型" width="110">
            <template #default="{ row }">
              <el-tag :type="kindTagType(row.kind)" size="small" effect="light">{{ kindLabel(row.kind) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="分组标题" min-width="170" show-overflow-tooltip />
          <el-table-column align="center" label="相似度" width="90">
            <template #default="{ row }">{{ similarityText(row.similarity) }}</template>
          </el-table-column>
          <el-table-column align="center" label="成员数" width="80">
            <template #default="{ row }">{{ (row.items || []).length }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="该范围未发现重复" :image-size="60" />
      </template>

      <template #footer>
        <el-button size="small" @click="detectVisible = false">关闭</el-button>
        <el-button size="small" type="primary" :loading="detecting" @click="submitDetect">
          {{ detectResult ? '重新检测' : '开始检测' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- #16 保存前查重：候选内容只在本次比较里使用，不落任何正式表；
         D06 单篇切片查重走「载入发布单元切片」——按正式切片的 chunk_id 逐条比对 -->
    <el-dialog v-model="checkVisible" title="保存前查重" width="680px" destroy-on-close>
      <el-form :model="checkForm" label-width="100px" size="small" @submit.prevent>
        <el-form-item label="资源类型">
          <el-radio-group v-model="checkForm.resource_type">
            <el-radio v-for="item in resourceTypeOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="checkForm.resource_id" maxlength="64" placeholder="业务资源 ID（草稿可自拟）" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="checkForm.title" maxlength="200" show-word-limit placeholder="拟保存的标题" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="checkForm.summary" type="textarea" :rows="2" placeholder="可留空" />
        </el-form-item>
        <el-form-item label="候选切片">
          <div class="kb-check__bar">
            <el-select
              v-model="checkFromPublication"
              filterable
              clearable
              :loading="publicationLoading"
              placeholder="选择发布单元，载入其已批准切片（D06 单篇查重）"
              class="kb-check__select"
            >
              <el-option v-for="item in publicationOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-button size="small" :loading="checkLoadingSlices" :disabled="!checkFromPublication" @click="loadCheckChunks">
              载入切片
            </el-button>
            <el-button v-if="checkChunks.length" size="small" @click="clearCheckChunks">改用整篇</el-button>
          </div>
          <div class="kb-form__hint">
            <template v-if="checkChunks.length">
              已载入 {{ checkChunks.length }} 个正式切片：按切片逐条比对，命中会给出 chunk_id
            </template>
            <template v-else>不载入切片时按整篇正文比对，只产出文档级命中</template>
          </div>
        </el-form-item>
        <el-form-item label="正文">
          <el-input
            v-model="checkForm.content"
            type="textarea"
            :rows="5"
            :placeholder="checkChunks.length ? '已按切片正文自动拼接，可继续编辑' : '拟保存的正文，用于与范围内容比对'"
          />
        </el-form-item>
        <el-form-item label="治理范围">
          <el-select
            v-model="checkForm.publication_ids"
            multiple
            filterable
            clearable
            collapse-tags
            collapse-tags-tooltip
            :loading="publicationLoading"
            placeholder="留空表示全部生效发布单元"
            style="width: 100%"
          >
            <el-option v-for="item in publicationOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="治理类型">
          <el-checkbox-group v-model="checkForm.kinds">
            <el-checkbox v-for="item in kindOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>

      <template v-if="checkResult">
        <el-alert
          :type="checkResult.is_duplicate ? 'warning' : 'success'"
          :closable="false"
          show-icon
          :title="`${checkResult.is_duplicate ? '命中重复' : '未命中重复'}：最高相似度 ${similarityText(checkResult.similarity)}，范围 ${checkResult.scope_size} 条`"
          description="查重只报命中，是否保存由调用方决定"
        />
        <el-table v-if="(checkResult.matches || []).length" :data="checkResult.matches" size="small" class="kb-dialog__table">
          <el-table-column prop="hit_id" label="hit_id" min-width="140" show-overflow-tooltip />
          <el-table-column label="标题" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">{{ row.title || '-' }}</template>
          </el-table-column>
          <el-table-column label="定位" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">{{ hitLocate(row) }}</template>
          </el-table-column>
          <el-table-column align="center" label="相似度" width="90">
            <template #default="{ row }">{{ similarityText(row.similarity) }}</template>
          </el-table-column>
          <el-table-column label="判断依据" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.reason || '-' }}</template>
          </el-table-column>
        </el-table>
      </template>

      <template #footer>
        <el-button size="small" @click="checkVisible = false">关闭</el-button>
        <el-button size="small" type="primary" :loading="checking" @click="submitCheck">开始查重</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="重复分组详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="治理类型">
              <el-tag :type="kindTagType(detail.kind)" size="small" effect="light">{{ kindLabel(detail.kind) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="分组标题">{{ detail.title || '-' }}</el-descriptions-item>
            <el-descriptions-item label="分组标识 group_key" :span="2">{{ detail.group_key || '-' }}</el-descriptions-item>
            <el-descriptions-item label="资源类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="治理状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="组内最低相似度">{{ similarityText(detail.similarity) }}</el-descriptions-item>
            <el-descriptions-item label="成员数">{{ detail.member_count }}</el-descriptions-item>
            <el-descriptions-item label="得分方法">{{ detail.score_method || '-' }}</el-descriptions-item>
            <el-descriptions-item label="租户">{{ detail.tenant_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="建议保留项">{{ detail.suggested_keep_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="最近结果 result_id">{{ detail.result_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="判断依据" :span="2">{{ detail.reason || '-' }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_time) }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="成员列表" name="members">
              <el-table v-if="memberRows.length" :data="memberRows" size="small" style="width: 100%">
                <el-table-column align="center" label="命中类型" width="95">
                  <template #default="{ row }">{{ hitKindLabel(row.hit_kind) }}</template>
                </el-table-column>
                <el-table-column prop="hit_id" label="hit_id" min-width="150" show-overflow-tooltip />
                <el-table-column label="标题" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div>{{ row.title || '-' }}</div>
                    <div v-if="row.subtitle" class="kb-member__subtitle">{{ row.subtitle }}</div>
                  </template>
                </el-table-column>
                <el-table-column prop="resource" label="资源" min-width="170" show-overflow-tooltip />
                <el-table-column prop="locate" label="定位" min-width="160" show-overflow-tooltip />
                <el-table-column align="center" label="相似度" width="90">
                  <template #default="{ row }">{{ similarityText(row.similarity) }}</template>
                </el-table-column>
                <el-table-column label="判断依据" min-width="170" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.reason || '-' }}</template>
                </el-table-column>
                <el-table-column label="内容摘要" min-width="220" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.snippet || '-' }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="该分组暂无成员" :image-size="70" />
                </template>
              </el-table>
              <el-empty v-else description="该分组暂无成员" :image-size="70" />
            </el-tab-pane>

            <el-tab-pane label="检测范围" name="scope">
              <JsonViewer :data="detail.scope" title="scope（检测范围）" placeholder="未记录检测范围" />
            </el-tab-pane>

            <el-tab-pane label="合并预览" name="mergedPreview">
              <JsonViewer
                :data="detail.merged_preview"
                title="merged_preview（草稿态，不落正式库）"
                placeholder="未生成合并预览"
                max-height="460px"
              />
            </el-tab-pane>
          </el-tabs>

          <div class="kb-detail__actions">
            <el-button size="small" @click="openPreview">生成合并预览</el-button>
            <template v-if="detail.status === 'active'">
              <el-button type="success" size="small" :loading="marking" @click="mark(detail, 'merged')">标记合并</el-button>
              <el-button type="warning" size="small" :loading="marking" @click="mark(detail, 'dismissed')">忽略该分组</el-button>
            </template>
          </div>
        </template>
        <el-empty v-else description="未获取到重复分组详情" :image-size="80" />
      </div>
    </el-drawer>

    <!-- #19 合并预览：范围与 result_id 取自分组行，产物只写回 merged_preview -->
    <el-dialog v-model="previewVisible" title="生成合并预览" width="720px" destroy-on-close>
      <el-form label-width="100px" size="small" @submit.prevent>
        <el-form-item label="保留主项">
          <el-select v-model="previewForm.keep_hit_id" filterable style="width: 100%" @change="handleKeepChange">
            <el-option
              v-for="item in previewMembers"
              :key="item.hit_id"
              :value="item.hit_id"
              :label="`${item.title || item.hit_id}（${item.hit_id}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="成员处置">
          <el-table :data="previewMembers" size="small" style="width: 100%">
            <el-table-column prop="hit_id" label="hit_id" min-width="150" show-overflow-tooltip />
            <el-table-column label="标题" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.title || '-' }}</template>
            </el-table-column>
            <el-table-column align="center" label="处置动作" width="130">
              <template #default="{ row }">
                <el-select
                  v-model="previewActions[row.hit_id]"
                  size="small"
                  :disabled="row.hit_id === previewForm.keep_hit_id"
                  style="width: 110px"
                >
                  <el-option v-for="item in actionOptionsFor(row)" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </template>
            </el-table-column>
          </el-table>
        </el-form-item>
        <el-form-item label="合并提示词">
          <el-input v-model="previewForm.prompt_text" type="textarea" :rows="2" maxlength="4000" placeholder="可留空" />
        </el-form-item>
      </el-form>

      <template v-if="previewResult">
        <el-descriptions :column="1" border size="small" class="kb-dialog__table">
          <el-descriptions-item label="合并后标题">{{ previewResult.title || '-' }}</el-descriptions-item>
          <el-descriptions-item label="摘要">{{ previewResult.summary || '-' }}</el-descriptions-item>
          <el-descriptions-item label="参与成员">{{ (previewResult.source_hit_ids || []).join('、') || '-' }}</el-descriptions-item>
          <el-descriptions-item label="预览摘要">{{ previewResult.preview_hash }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="previewAnswerHtml" class="kb-preview__answer" v-html="previewAnswerHtml"></div>
        <el-alert
          v-for="(item, index) in previewResult.warnings || []"
          :key="`warning-${index}`"
          class="kb-dialog__alert"
          type="warning"
          :closable="false"
          show-icon
          :title="item"
        />
        <el-alert
          v-for="(item, index) in previewResult.conflicts || []"
          :key="`conflict-${index}`"
          class="kb-dialog__alert"
          type="error"
          :closable="false"
          show-icon
          :title="`${item.field}：${item.description}`"
        />
      </template>

      <template #footer>
        <el-button size="small" @click="previewVisible = false">关闭</el-button>
        <el-button size="small" type="primary" :loading="previewing" @click="submitPreview">生成预览</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import DOMPurify from 'dompurify'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  checkDupGroups,
  detectDupGroups,
  getDupGroupDetail,
  getDupGroupList,
  previewDupGroup,
  updateDupGroupStatus,
} from '@/api/module_kb/dupGroup'
import { getPublicationBaseline, getPublicationDetail, getPublicationList } from '@/api/module_kb/publication'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbDupGroupView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const kindOptions = [
  { value: 'doc_exact', label: '文档完全重复' },
  { value: 'doc_similar', label: '文档相近' },
  { value: 'chunk', label: '切片重复' },
  { value: 'faq_ai', label: 'AI 问答重复' },
  { value: 'faq_manual', label: '人工问答重复' },
]
const statusOptions = [
  { value: 'active', label: '待处理' },
  { value: 'merged', label: '已合并' },
  { value: 'dismissed', label: '已忽略' },
]
const STATUS_TAG = { active: 'warning', merged: 'success', dismissed: 'info' }
// 类型标签色只用于快速区分五类，不代表严重程度（红≠错误）
const KIND_TAG = { doc_exact: 'danger', doc_similar: 'warning', chunk: 'info', faq_ai: 'primary', faq_manual: 'success' }
const HIT_KIND_LABELS = { doc: '整篇文档', faq: '问答', chunk: '单切片' }

function kindLabel(kind) {
  return kindOptions.find((item) => item.value === kind)?.label ?? (kind || '-')
}
function kindTagType(kind) {
  return KIND_TAG[kind] || 'info'
}
function hitKindLabel(hitKind) {
  return HIT_KIND_LABELS[hitKind] || (hitKind || '-')
}
function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}
function similarityText(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `${(Number(value) * 100).toFixed(1)}%`
}
/** 命中/成员定位：正式切片给 chunk_id，草稿切片给 seq，都没有就是整篇命中 */
function hitLocate(item) {
  if (item.chunk_id) return `正式切片 ${item.chunk_id}`
  if (item.source_seq) return `草稿切片 seq=${item.source_seq}`
  return '整篇'
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['kind', 'resource_type', 'status', 'tenant_id'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getDupGroupList(buildQueryParams(params)),
  {
    defaultQuery: { kind: '', resource_type: '', status: '', tenant_id: '' },
  },
)

// ── 详情 ──────────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('members')
const marking = ref(false)

/** 成员是契约 items[]：把嵌套的 resource 拍平成「资源 / 定位」两列，便于在表格里核对 */
const memberRows = computed(() =>
  (detail.value?.members || []).map((item) => {
    const resource = item.resource || {}
    return {
      hit_id: item.hit_id,
      hit_kind: item.hit_kind,
      title: item.title,
      subtitle: item.subtitle,
      resource: resource.resource_id ? `${resource.resource_type}:${resource.resource_id}` : '-',
      locate: `${hitLocate(item)}${resource.record_id ? ` · ${resource.record_id}` : ''}`,
      similarity: item.similarity,
      reason: item.reason,
      snippet: item.snippet,
    }
  }),
)

async function openDetail(row) {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'members'
  await fetchDetail(row.id)
}

async function fetchDetail(groupId) {
  try {
    detail.value = await getDupGroupDetail(groupId)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}

// ── 标记处理 ──────────────────────────────────────────────────────
async function mark(row, status) {
  const action = status === 'merged' ? '标记合并' : '忽略'
  try {
    await ElMessageBox.confirm(
      `确定将分组「${row.uuid}」${action}吗？该操作只改变治理状态，不改写知识内容。`,
      `${action}确认`,
      { type: 'warning' },
    )
  } catch {
    return
  }
  marking.value = true
  try {
    await updateDupGroupStatus(row.id, status)
    ElMessage.success(`${action}成功`)
    load()
    if (detailVisible.value && detail.value?.id === row.id) {
      detailLoading.value = true
      await fetchDetail(row.id)
    }
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    marking.value = false
  }
}

// ── 主动治理：#17 检测 / #16 保存前查重 / #19 合并预览 ─────────────────────

/** 范围选择器与两个对话框共用：发布单元最多取一页（100 条），列表只用于「限定比谁」 */
const publicationOptions = ref([])
const publicationLoading = ref(false)
let publicationsLoaded = false

async function ensurePublicationOptions() {
  if (publicationsLoaded) return
  publicationLoading.value = true
  try {
    const page = await getPublicationList({ page_no: 1, page_size: 100, status: 'active' })
    publicationOptions.value = (page?.items || []).map((item) => ({
      value: item.publication_id,
      label: `${item.title || item.publication_id}（${item.publication_id}）`,
    }))
    publicationsLoaded = true
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    publicationLoading.value = false
  }
}

/** 范围字段的空值不下发：后端对「留空」的语义是全部生效发布单元 */
function buildScopePayload(form) {
  const payload = { kinds: [...form.kinds] }
  if (form.tenant_id) payload.tenant_id = form.tenant_id
  if (form.publication_ids.length) payload.publication_ids = [...form.publication_ids]
  return payload
}

const ALL_KINDS = kindOptions.map((item) => item.value)

// ── 发起检测 ──────────────────────────────────────────────────────
const detectVisible = ref(false)
const detecting = ref(false)
const detectResult = ref(null)
const detectForm = reactive({ tenant_id: '', publication_ids: [], kinds: [...ALL_KINDS], limit: 100 })

function openDetect() {
  detectResult.value = null
  detectVisible.value = true
  ensurePublicationOptions()
}

async function submitDetect() {
  if (!detectForm.kinds.length) {
    ElMessage.warning('至少选择一种治理类型')
    return
  }
  detecting.value = true
  detectResult.value = null
  try {
    detectResult.value = await detectDupGroups({ ...buildScopePayload(detectForm), limit: detectForm.limit })
    ElMessage.success('重复检测完成')
    // 新分组按创建时间倒序，回到第一页才看得到
    search()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detecting.value = false
  }
}

// ── 保存前查重 ────────────────────────────────────────────────────
const checkVisible = ref(false)
const checking = ref(false)
const checkResult = ref(null)
const checkForm = reactive({
  resource_type: 'document',
  resource_id: '',
  title: '',
  summary: '',
  content: '',
  tenant_id: '',
  publication_ids: [],
  kinds: [...ALL_KINDS],
})

/** D06：从发布单元载入正式切片后按切片比对；为空表示按整篇正文比对 */
const checkFromPublication = ref('')
const checkLoadingSlices = ref(false)
const checkChunks = ref([])

function openCheck() {
  checkResult.value = null
  checkFromPublication.value = ''
  checkChunks.value = []
  checkVisible.value = true
  ensurePublicationOptions()
}

/** 载入已批准快照的切片：归属字段一并回填，否则候选与库里那份内容对不上会误报不重复 */
async function loadCheckChunks() {
  if (!checkFromPublication.value) return
  checkLoadingSlices.value = true
  try {
    const detail = await getPublicationDetail(checkFromPublication.value)
    const parts = []
    let cursor = ''
    do {
      const page = await getPublicationBaseline(checkFromPublication.value, {
        cursor: cursor || undefined,
        limit: 200,
      })
      parts.push(...page.chunks)
      cursor = page.next_cursor || ''
    } while (cursor)
    if (!parts.length) {
      ElMessage.warning('该发布单元还没有已批准切片，请先写入知识正文')
      return
    }
    checkChunks.value = parts.map((chunk) => ({
      seq: chunk.seq,
      content: chunk.content,
      chunk_id: chunk.chunk_id,
      title: chunk.heading || null,
    }))
    checkForm.resource_type = detail.resource_type === 'faq' ? 'faq' : 'document'
    checkForm.resource_id = detail.resource_id || detail.publication_id
    checkForm.title = detail.title || ''
    checkForm.summary = detail.summary || ''
    checkForm.content = parts
      .map((chunk) => [chunk.heading ? `## ${chunk.heading}` : '', chunk.content].filter(Boolean).join('\n\n'))
      .join('\n\n')
    ElMessage.success(`已载入 ${parts.length} 个切片`)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    checkLoadingSlices.value = false
  }
}

/** 退回整篇比对：保留正文，避免用户还要重敲一遍 */
function clearCheckChunks() {
  checkChunks.value = []
}

async function submitCheck() {
  if (!checkForm.resource_id.trim()) {
    ElMessage.warning('请填写资源 ID')
    return
  }
  if (!checkForm.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  if (!checkForm.content.trim() && !checkChunks.value.length) {
    ElMessage.warning('请填写正文，或从发布单元载入切片')
    return
  }
  if (!checkForm.kinds.length) {
    ElMessage.warning('至少选择一种治理类型')
    return
  }
  checking.value = true
  checkResult.value = null
  try {
    checkResult.value = await checkDupGroups({
      ...buildScopePayload(checkForm),
      candidate: {
        resource_type: checkForm.resource_type,
        resource_id: checkForm.resource_id.trim(),
        title: checkForm.title.trim(),
        summary: checkForm.summary || null,
        content: checkForm.content,
        chunks: checkChunks.value,
      },
    })
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    checking.value = false
  }
}

// ── 合并预览 ──────────────────────────────────────────────────────
const FAQ_KINDS = ['faq_ai', 'faq_manual']
const previewVisible = ref(false)
const previewing = ref(false)
const previewResult = ref(null)
const previewForm = reactive({ keep_hit_id: '', prompt_text: '' })
/** hit_id → 处置动作；契约要求每个成员恰好一个动作、主项动作必须为 keep */
const previewActions = reactive({})

const previewMembers = computed(() =>
  (detail.value?.members || []).map((item) => ({ hit_id: item.hit_id, title: item.title })),
)

const previewAnswerHtml = computed(() => {
  const html = previewResult.value?.answer_html
  // 答案正文由知识内容拼装，仍按不可信 HTML 处理，消毒后再渲染
  return html ? DOMPurify.sanitize(html) : ''
})

/** 主项只能保留一个，非主项不允许选 keep；merge_answer 仅问答类分组可用 */
function actionOptionsFor(row) {
  if (row.hit_id === previewForm.keep_hit_id) return [{ value: 'keep', label: '保留' }]
  const options = [
    { value: 'drop', label: '丢弃' },
    { value: 'discard', label: '下线' },
  ]
  if (FAQ_KINDS.includes(detail.value?.kind)) options.push({ value: 'merge_answer', label: '合并答案' })
  return options
}

function openPreview() {
  const members = previewMembers.value
  if (!members.length) {
    ElMessage.warning('该分组没有成员，无法生成合并预览')
    return
  }
  previewResult.value = null
  previewForm.prompt_text = ''
  const suggested = detail.value?.suggested_keep_id
  previewForm.keep_hit_id = members.some((item) => item.hit_id === suggested) ? suggested : members[0].hit_id
  Object.keys(previewActions).forEach((hitId) => delete previewActions[hitId])
  members.forEach((item) => {
    previewActions[item.hit_id] = item.hit_id === previewForm.keep_hit_id ? 'keep' : 'drop'
  })
  previewVisible.value = true
}

/** 换主项时把旧主项降级为丢弃，保证始终恰好一个 keep */
function handleKeepChange(keepHitId) {
  previewMembers.value.forEach((item) => {
    if (item.hit_id === keepHitId) previewActions[item.hit_id] = 'keep'
    else if (previewActions[item.hit_id] === 'keep') previewActions[item.hit_id] = 'drop'
  })
}

async function submitPreview() {
  const groupId = detail.value?.id
  if (!groupId) return
  const actions = previewMembers.value.map((item) => ({
    target_hit_id: item.hit_id,
    action: previewActions[item.hit_id] || 'drop',
  }))
  previewing.value = true
  try {
    previewResult.value = await previewDupGroup(groupId, {
      keep_hit_id: previewForm.keep_hit_id,
      actions,
      prompt_text: previewForm.prompt_text || '',
    })
    ElMessage.success('合并预览已生成')
    // 预览会写回分组行 merged_preview，重新拉详情让抽屉与弹窗同步
    detailLoading.value = true
    await fetchDetail(groupId)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    previewing.value = false
  }
}
</script>

<style scoped lang="scss">
.kb-toolbar {
  padding: 20px 26px 4px;
}

.kb-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.kb-member__subtitle {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-form__hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-check__bar {
  @include flex($justify: flex-start);
  gap: 8px;
  width: 100%;
}

.kb-check__select {
  flex: 1;
  min-width: 0;
}

.kb-dialog__table {
  margin-top: 12px;
}

.kb-dialog__alert {
  margin-top: 8px;
}

.kb-preview__answer {
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid var(--app-border-color);
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text-primary);
  background: var(--app-bg);

  :deep(p) {
    margin: 0 0 8px;
  }

  :deep(p:last-child) {
    margin-bottom: 0;
  }
}

.kb-detail__tabs {
  margin-top: 16px;
}

.kb-detail__actions {
  @include flex($justify: flex-end);
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--app-border-color);
}
</style>
