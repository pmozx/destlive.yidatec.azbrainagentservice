<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">发布单元</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/publication：正文写入（03.1）/ 修订（03.3）由服务端切分入索引，可查看版本快照与切片、读取 03.2 基线、按 03.4 删快照，按 #20 停用归档
        </p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" size="small" @click="openEntryWrite(null)">
          <el-icon><EditPen /></el-icon>
          <span>写入知识正文</span>
        </el-button>
        <el-button size="small" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新增空壳</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="kb-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="发布单元">
            <el-input v-model="query.publication_id" placeholder="模糊匹配" clearable style="width: 160px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="query.title" placeholder="模糊匹配" clearable style="width: 160px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="分类">
            <el-input v-model="query.category" placeholder="精确匹配" clearable style="width: 120px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="维护人">
            <el-input v-model="query.owner" placeholder="模糊匹配" clearable style="width: 120px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" prop="publication_id" label="发布单元" min-width="170" show-overflow-tooltip />
        <el-table-column align="center" prop="category" label="分类" width="110" show-overflow-tooltip />
        <el-table-column align="center" label="类型" width="90">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="标签" min-width="150">
          <template #default="{ row }">
            <el-tag v-for="tag in row.tags || []" :key="tag" size="small" effect="plain" class="kb-tag">{{ tag }}</el-tag>
            <span v-if="!(row.tags || []).length" class="kb-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column align="center" label="当前版本" width="100">
          <template #default="{ row }">v{{ row.current_revision ?? 0 }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" prop="owner" label="维护人" width="110" show-overflow-tooltip />
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button
              v-if="row.status !== 'deleted'"
              link
              type="primary"
              size="small"
              @click="openEntryWrite(row)"
            >
              {{ row.current_record_id ? '修订正文' : '写正文' }}
            </el-button>
            <el-button v-if="row.status !== 'deleted'" link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button
              v-if="row.status === 'active'"
              link
              type="warning"
              size="small"
              @click="changeStatus(row, 'inactive')"
            >
              下线
            </el-button>
            <el-button v-else-if="row.status === 'inactive'" link type="success" size="small" @click="changeStatus(row, 'active')">
              上线
            </el-button>
            <el-button
              v-if="row.status === 'active' && row.current_record_id"
              link
              type="warning"
              size="small"
              @click="deactivate(row)"
            >
              停用归档
            </el-button>
            <el-button v-if="row.status !== 'deleted'" link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无发布单元，点击右上角新增" :image-size="80" />
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

    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新增发布单元' : '编辑发布单元'" width="640px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" size="small" @submit.prevent>
        <el-form-item label="发布单元" prop="publication_id">
          <el-input v-model="form.publication_id" :disabled="dialogMode === 'edit'" placeholder="全局唯一的发布单元 ID" />
        </el-form-item>
        <el-form-item label="知识类型" prop="resource_type">
          <el-select v-model="form.resource_type" style="width: 100%">
            <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="请输入标题" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" maxlength="64" placeholder="如：it / hr / 产品" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入后回车创建标签"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="维护人">
          <el-input v-model="form.owner" maxlength="64" placeholder="负责人" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="form.summary" type="textarea" :rows="3" placeholder="内容摘要" />
        </el-form-item>
        <el-form-item label="租户 ID">
          <el-input v-model="form.tenant_id" maxlength="64" placeholder="多租户归属，可留空" />
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="form.resource_id" maxlength="64" placeholder="原始资源 ID，可留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="entryVisible"
      :title="entryMode === 'revise' ? '修订知识正文（03.3）' : '写入知识正文（03.1）'"
      width="860px"
      top="5vh"
      destroy-on-close
    >
      <el-form ref="entryFormRef" :model="entryForm" :rules="entryRules" label-width="96px" size="small" @submit.prevent>
        <el-form-item label="发布单元" prop="publication_id">
          <el-input
            v-model="entryForm.publication_id"
            :disabled="entryMode === 'revise'"
            placeholder="留空由服务端生成（kb-xxxxxxxx）；填写已存在的空壳则直接补录"
          />
        </el-form-item>
        <el-form-item label="知识类型">
          <el-select v-model="entryForm.resource_type" :disabled="entryMode === 'revise' || !!entryForm.publication_id" style="width: 100%">
            <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="entryMode === 'create'" label="租户 ID">
          <el-input
            v-model="entryForm.tenant_id"
            maxlength="64"
            placeholder="检索按租户硬过滤，留空则该知识不会被任何租户召回"
          />
        </el-form-item>
        <el-form-item v-if="entryMode === 'create'" label="资源 ID">
          <el-input v-model="entryForm.resource_id" maxlength="64" placeholder="GSI 知识主记录 ID，可留空" />
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="entryForm.title" maxlength="200" show-word-limit placeholder="请输入标题" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="entryForm.category" maxlength="64" placeholder="如：it / hr / 产品" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="entryForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入后回车创建标签"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="维护人">
          <el-input v-model="entryForm.owner" maxlength="64" placeholder="负责人" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="entryForm.summary" type="textarea" :rows="2" placeholder="内容摘要" />
        </el-form-item>
        <el-form-item label="正文" prop="content">
          <div class="kb-entry__bar">
            <el-button
              v-if="entryMode === 'revise'"
              size="small"
              :loading="entryLoadingContent"
              @click="loadEntryContent"
            >
              载入当前正文
            </el-button>
            <span class="kb-muted">服务端按 PARSE_CHUNK_SIZE / PARSE_CHUNK_OVERLAP 切分，与解析链路同一口径</span>
          </div>
          <el-input
            v-model="entryForm.content"
            type="textarea"
            :rows="14"
            resize="vertical"
            placeholder="支持 Markdown：小标题会写入切片标题路径"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="entryVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="entrySubmitting" @click="submitEntry">
          {{ entryMode === 'revise' ? '提交修订' : '写入并入库' }}
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="发布单元详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="标题" :span="2">{{ detail.title }}</el-descriptions-item>
            <el-descriptions-item label="发布单元">{{ detail.publication_id }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="知识类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="分类">{{ detail.category || '-' }}</el-descriptions-item>
            <el-descriptions-item label="维护人">{{ detail.owner || '-' }}</el-descriptions-item>
            <el-descriptions-item label="租户 ID">{{ detail.tenant_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="资源 ID">{{ detail.resource_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前记录">{{ detail.current_record_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前版本">v{{ detail.current_revision ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="版本数">{{ detail.version_count }}</el-descriptions-item>
            <el-descriptions-item label="切片数">{{ detail.chunk_count }}</el-descriptions-item>
            <el-descriptions-item label="同步时间">{{ formatDateTime(detail.last_synced_at) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="标签" :span="2">
              <el-tag v-for="tag in detail.tags || []" :key="tag" size="small" effect="plain" class="kb-tag">{{ tag }}</el-tag>
              <span v-if="!(detail.tags || []).length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="摘要" :span="2">{{ detail.summary || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="版本快照（03.3 写入）" name="versions">
              <el-table v-loading="versions.loading.value" :data="versions.list.value" size="small" style="width: 100%">
                <el-table-column align="center" prop="revision" label="版本号" width="90">
                  <template #default="{ row }">v{{ row.revision }}</template>
                </el-table-column>
                <el-table-column align="center" prop="record_id" label="记录 ID" min-width="140" show-overflow-tooltip />
                <el-table-column align="center" prop="version_id" label="契约版本" min-width="110" show-overflow-tooltip />
                <el-table-column align="center" prop="chunk_count" label="切片数" width="80" />
                <el-table-column align="center" label="可检索" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.searchable ? 'success' : 'info'" size="small" effect="light">
                      {{ row.searchable ? '是' : '否' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column align="center" prop="status" label="状态" width="90" />
                <el-table-column align="center" label="创建时间" width="160">
                  <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
                </el-table-column>
                <el-table-column align="center" label="操作" width="170" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" size="small" @click="viewBaseline(row.record_id)">读基线</el-button>
                    <el-button
                      v-if="row.status === 'online' && row.searchable"
                      link
                      type="danger"
                      size="small"
                      @click="removeSnapshot(row)"
                    >
                      删除快照
                    </el-button>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty description="该发布单元还没有版本快照（等待契约写入）" :image-size="70" />
                </template>
              </el-table>
              <div class="kb-pagination">
                <el-pagination
                  :current-page="versions.pagination.page_no"
                  :page-size="versions.pagination.page_size"
                  :total="versions.total.value"
                  layout="total, prev, pager, next"
                  background
                  size="small"
                  @current-change="versions.handlePageChange"
                />
              </div>
            </el-tab-pane>

            <el-tab-pane label="知识切片（03.1/03.3 写入）" name="chunks">
              <el-table v-loading="chunks.loading.value" :data="chunks.list.value" size="small" style="width: 100%">
                <el-table-column align="center" prop="seq" label="序号" width="70" />
                <el-table-column align="center" prop="heading" label="标题路径" min-width="160" show-overflow-tooltip />
                <el-table-column align="center" prop="content" label="正文" min-width="280" show-overflow-tooltip />
                <el-table-column align="center" prop="source_type" label="来源" width="90" />
                <el-table-column align="center" label="页范围" width="100">
                  <template #default="{ row }">
                    {{ row.page_from || row.page_to ? `${row.page_from ?? '-'} ~ ${row.page_to ?? '-'}` : '-' }}
                  </template>
                </el-table-column>
                <el-table-column align="center" prop="status" label="状态" width="90" />
                <template #empty>
                  <el-empty description="该发布单元还没有切片（等待契约写入）" :image-size="70" />
                </template>
              </el-table>
              <div class="kb-pagination">
                <el-pagination
                  :current-page="chunks.pagination.page_no"
                  :page-size="chunks.pagination.page_size"
                  :total="chunks.total.value"
                  layout="total, prev, pager, next"
                  background
                  size="small"
                  @current-change="chunks.handlePageChange"
                />
              </div>
            </el-tab-pane>
            <el-tab-pane label="已批准基线（03.2）" name="baseline">
              <div class="kb-baseline__bar">
                <el-input
                  v-model="baseline.recordId"
                  size="small"
                  placeholder="快照 record_id，留空读当前生效快照"
                  clearable
                  style="width: 320px"
                  @keyup.enter="loadBaseline(true)"
                />
                <el-button size="small" type="primary" :loading="baseline.loading" @click="loadBaseline(true)">
                  读取基线
                </el-button>
                <span class="kb-muted">只读已批准快照；删除操作请到「版本快照」页签</span>
              </div>

              <el-descriptions v-if="baseline.data" :column="2" border size="small" class="kb-baseline__meta">
                <el-descriptions-item label="快照 ID">{{ baseline.data.record_id }}</el-descriptions-item>
                <el-descriptions-item label="索引修订号">v{{ baseline.data.revision }}</el-descriptions-item>
                <el-descriptions-item label="契约版本">{{ baseline.data.version_id || '-' }}</el-descriptions-item>
                <el-descriptions-item label="知识编号">{{ baseline.data.knowledge_no || '-' }}</el-descriptions-item>
                <el-descriptions-item label="可检索">
                  <el-tag :type="baseline.data.searchable ? 'success' : 'info'" size="small" effect="light">
                    {{ baseline.data.searchable ? '是' : '否' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="切片总数">{{ baseline.data.chunks.length }}（本页）</el-descriptions-item>
                <el-descriptions-item label="内容摘要" :span="2">
                  <span class="kb-baseline__hash">{{ baseline.data.content_hash }}</span>
                </el-descriptions-item>
              </el-descriptions>

              <el-table v-loading="baseline.loading" :data="baseline.data?.chunks || []" size="small" style="width: 100%">
                <el-table-column align="center" prop="chunk_id" label="切片 ID" min-width="160" show-overflow-tooltip />
                <el-table-column align="center" prop="seq" label="序号" width="70" />
                <el-table-column align="center" prop="heading" label="标题路径" min-width="150" show-overflow-tooltip />
                <el-table-column align="center" prop="content" label="正文" min-width="280" show-overflow-tooltip />
                <el-table-column align="center" label="页范围" width="100">
                  <template #default="{ row }">
                    {{ row.page_from || row.page_to ? `${row.page_from ?? '-'} ~ ${row.page_to ?? '-'}` : '-' }}
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty description="未读取到已批准快照" :image-size="70" />
                </template>
              </el-table>

              <div class="kb-pagination">
                <el-button size="small" :disabled="!baseline.cursorStack.length" @click="baselinePrev">上一页</el-button>
                <el-button size="small" :disabled="!baseline.data?.next_cursor" @click="baselineNext">下一页</el-button>
              </div>
            </el-tab-pane>

            <el-tab-pane label="来源与关联" name="source">
              <div v-loading="origin.loading" class="kb-origin">
                <el-alert
                  :title="origin.data?.hint || '发布单元与解析任务没有库级关联，这里只展示可核对得上的线索'"
                  type="info"
                  :closable="false"
                  show-icon
                />

                <el-descriptions v-if="origin.data" :column="2" border size="small" class="kb-origin__meta">
                  <el-descriptions-item label="写入渠道">
                    <el-tag :type="channelTagType(origin.data.write_channel)" size="small" effect="light">
                      {{ channelLabel(origin.data.write_channel) }}
                    </el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="资源 ID">{{ origin.data.resource_id || '-' }}</el-descriptions-item>
                  <el-descriptions-item label="租户 ID">{{ origin.data.tenant_id || '-' }}</el-descriptions-item>
                  <el-descriptions-item label="当前记录">{{ origin.data.current_record_id || '-' }}</el-descriptions-item>
                </el-descriptions>

                <el-table :data="origin.data?.parse_jobs || []" size="small" style="width: 100%" class="kb-origin__table">
                  <el-table-column align="center" prop="job_id" label="解析任务" min-width="170" show-overflow-tooltip />
                  <el-table-column align="center" prop="status" label="状态" width="100" />
                  <el-table-column align="center" label="进度" width="90">
                    <template #default="{ row }">{{ row.progress }}%</template>
                  </el-table-column>
                  <el-table-column align="center" prop="resource_type" label="类型" width="90" />
                  <el-table-column align="center" label="创建时间" width="165">
                    <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
                  </el-table-column>
                  <el-table-column align="center" prop="matched_by" label="匹配依据" width="150" show-overflow-tooltip />
                  <template #empty>
                    <el-empty
                      description="未匹配到解析任务：契约任务才有 doc_id，管理台上传的任务不参与匹配"
                      :image-size="70"
                    />
                  </template>
                </el-table>
              </div>
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="未获取到发布单元详情" :image-size="80" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createPublication,
  deactivatePublication,
  deletePublication,
  deletePublicationSnapshot,
  getPublicationBaseline,
  getPublicationChunks,
  getPublicationDetail,
  getPublicationList,
  getPublicationSource,
  getPublicationVersions,
  revisePublicationEntry,
  updatePublication,
  updatePublicationStatus,
  writePublicationEntry,
} from '@/api/module_kb/publication'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbPublicationView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const statusOptions = [
  { value: 'active', label: '生效' },
  { value: 'inactive', label: '停用' },
  { value: 'deleted', label: '已删除' },
]
const STATUS_TAG = { active: 'success', inactive: 'warning', deleted: 'info' }

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['publication_id', 'title', 'category', 'resource_type', 'status', 'owner'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getPublicationList(buildQueryParams(params)),
  {
    defaultQuery: {
      publication_id: '',
      title: '',
      category: '',
      resource_type: '',
      status: '',
      owner: '',
    },
  },
)

// ── 新增 / 编辑 ────────────────────────────────────────────────────
const formRef = ref()
const dialogVisible = ref(false)
const dialogMode = ref('create')
const submitting = ref(false)
const form = reactive({
  publication_id: '',
  resource_type: 'document',
  tenant_id: '',
  resource_id: '',
  title: '',
  category: '',
  summary: '',
  tags: [],
  owner: '',
})

const rules = {
  publication_id: [
    { required: true, message: '请输入发布单元 ID', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_.:-]+$/, message: '仅支持字母、数字与 _ . : -', trigger: 'blur' },
  ],
  resource_type: [{ required: true, message: '请选择知识类型', trigger: 'change' }],
  title: [
    { required: true, message: '请输入标题', trigger: 'blur' },
    { max: 200, message: '标题不能超过 200 个字符', trigger: 'blur' },
  ],
}

function resetForm() {
  Object.assign(form, {
    publication_id: '',
    resource_type: 'document',
    tenant_id: '',
    resource_id: '',
    title: '',
    category: '',
    summary: '',
    tags: [],
    owner: '',
  })
  formRef.value?.clearValidate?.()
}

function openCreate() {
  dialogMode.value = 'create'
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  Object.assign(form, {
    publication_id: row.publication_id,
    resource_type: row.resource_type || 'document',
    tenant_id: row.tenant_id || '',
    resource_id: row.resource_id || '',
    title: row.title || '',
    category: row.category || '',
    summary: row.summary || '',
    tags: [...(row.tags || [])],
    owner: row.owner || '',
  })
  dialogVisible.value = true
}

function buildPayload() {
  return {
    resource_type: form.resource_type,
    tenant_id: form.tenant_id?.trim() || null,
    resource_id: form.resource_id?.trim() || null,
    title: form.title.trim(),
    category: form.category?.trim() || null,
    summary: form.summary?.trim() || null,
    tags: form.tags,
    owner: form.owner?.trim() || null,
  }
}

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    if (dialogMode.value === 'create') {
      await createPublication({ ...buildPayload(), publication_id: form.publication_id.trim() })
      ElMessage.success('新增发布单元成功')
    } else {
      await updatePublication(form.publication_id, buildPayload())
      ElMessage.success('编辑发布单元成功')
    }
    dialogVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    submitting.value = false
  }
}

// ── 状态 / 删除 ────────────────────────────────────────────────────
async function changeStatus(row, status) {
  const action = status === 'active' ? '上线' : '下线'
  try {
    await ElMessageBox.confirm(
      status === 'active'
        ? `确定上线发布单元「${row.title}」吗？上线只切状态，切片仍为归档时要重新写入正文才能被召回。`
        : `确定下线发布单元「${row.title}」吗？下线只切状态、可随时上线；要归档切片请用「停用归档」。`,
      `${action}确认`,
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await updatePublicationStatus(row.publication_id, status)
    ElMessage.success(`${action}成功`)
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除发布单元「${row.title}」吗？将标记为已删除并立即不再被检索，版本快照与切片保留供追溯。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deletePublication(row.publication_id)
    ElMessage.success('已标记为删除，该发布单元不再被检索')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

// ── 详情（含版本 / 切片） ──────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('versions')
const currentPublicationId = ref('')

const versions = useTable(
  (params) => getPublicationVersions(currentPublicationId.value, { page_no: params.page_no, page_size: params.page_size }),
  { pageSize: 5, immediate: false },
)
const chunks = useTable(
  (params) => getPublicationChunks(currentPublicationId.value, { page_no: params.page_no, page_size: params.page_size }),
  { pageSize: 5, immediate: false },
)

// ── 03.2 已批准基线（游标分页） ───────────────────────────────────
const baseline = reactive({
  loading: false,
  data: null,
  recordId: '',
  currentCursor: '',
  cursorStack: [],
  limit: 20,
})

async function loadBaseline(reset = false) {
  if (!currentPublicationId.value) return
  if (reset) {
    baseline.currentCursor = ''
    baseline.cursorStack = []
  }
  baseline.loading = true
  try {
    baseline.data = await getPublicationBaseline(currentPublicationId.value, {
      record_id: baseline.recordId?.trim() || undefined,
      cursor: baseline.currentCursor || undefined,
      limit: baseline.limit,
    })
  } catch {
    baseline.data = null
  } finally {
    baseline.loading = false
  }
}

function baselineNext() {
  const next = baseline.data?.next_cursor
  if (!next) return
  baseline.cursorStack.push(baseline.currentCursor)
  baseline.currentCursor = next
  loadBaseline()
}

function baselinePrev() {
  if (!baseline.cursorStack.length) return
  baseline.currentCursor = baseline.cursorStack.pop() || ''
  loadBaseline()
}

/** 从版本快照行跳到基线页签并读该快照（03.2 支持历史 record_id）。 */
function viewBaseline(recordId) {
  activeTab.value = 'baseline'
  baseline.recordId = recordId
  loadBaseline(true)
}

// ── 03.1 / 03.3 正文写入与 #20 停用 ────────────────────────────────
const entryVisible = ref(false)
const entryMode = ref('create')
const entrySubmitting = ref(false)
const entryLoadingContent = ref(false)
const entryFormRef = ref()
const entryBaseRecordId = ref('')
const entryForm = reactive({
  publication_id: '',
  resource_type: 'document',
  tenant_id: '',
  resource_id: '',
  title: '',
  category: '',
  tags: [],
  owner: '',
  summary: '',
  content: '',
})

const entryRules = {
  publication_id: [
    {
      validator: (_rule, value, callback) => {
        if (value && !/^[A-Za-z0-9_.:-]+$/.test(value)) callback(new Error('仅支持字母、数字与 _ . : -'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  title: [
    { required: true, message: '请输入标题', trigger: 'blur' },
    { max: 200, message: '标题不能超过 200 个字符', trigger: 'blur' },
  ],
  content: [{ required: true, message: '请输入正文', trigger: 'blur' }],
}

/** 有快照 → 修订（03.3）；无快照（含空壳）→ 写入（03.1） */
function openEntryWrite(row) {
  entryMode.value = row?.current_record_id ? 'revise' : 'create'
  entryBaseRecordId.value = row?.current_record_id || ''
  Object.assign(entryForm, {
    publication_id: row?.publication_id || '',
    resource_type: row?.resource_type || 'document',
    tenant_id: row?.tenant_id || '',
    resource_id: row?.resource_id || '',
    title: row?.title || '',
    category: row?.category || '',
    tags: [...(row?.tags || [])],
    owner: row?.owner || '',
    summary: row?.summary || '',
    content: '',
  })
  entryFormRef.value?.clearValidate?.()
  entryVisible.value = true
}

/** 把当前快照的切片拼回可编辑正文：纠正只需改差异，不用重敲一遍 */
async function loadEntryContent() {
  if (!entryForm.publication_id) return
  entryLoadingContent.value = true
  try {
    const parts = []
    let cursor = ''
    do {
      const page = await getPublicationBaseline(entryForm.publication_id, {
        record_id: entryBaseRecordId.value || undefined,
        cursor: cursor || undefined,
        limit: 200,
      })
      parts.push(...page.chunks)
      cursor = page.next_cursor || ''
    } while (cursor)
    entryForm.content = parts
      .map((chunk) => [chunk.heading ? `## ${chunk.heading}` : '', chunk.content].filter(Boolean).join('\n\n'))
      .join('\n\n')
    ElMessage.success(`已载入 ${parts.length} 个切片，可直接编辑后提交`)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    entryLoadingContent.value = false
  }
}

function buildEntryPayload() {
  const payload = {
    title: entryForm.title.trim(),
    category: entryForm.category?.trim() || null,
    tags: entryForm.tags,
    owner: entryForm.owner?.trim() || null,
    summary: entryForm.summary?.trim() || null,
    content: entryForm.content,
  }
  if (entryMode.value === 'revise') {
    payload.base_record_id = entryBaseRecordId.value || null
  } else {
    payload.publication_id = entryForm.publication_id?.trim() || null
    payload.resource_type = entryForm.resource_type
    // 租户决定检索能否召回（SQL 侧硬过滤），资源 ID 承载 GSI 溯源，两者都随 03.1 落库
    payload.tenant_id = entryForm.tenant_id?.trim() || null
    payload.resource_id = entryForm.resource_id?.trim() || null
  }
  return payload
}

async function submitEntry() {
  try {
    await entryFormRef.value.validate()
  } catch {
    return
  }
  entrySubmitting.value = true
  try {
    const payload = buildEntryPayload()
    const result =
      entryMode.value === 'revise'
        ? await revisePublicationEntry(entryForm.publication_id, payload)
        : await writePublicationEntry(payload)
    ElMessage.success(`写入成功：v${result.revision}，${result.chunk_count} 个切片`)
    entryVisible.value = false
    load()
    if (detailVisible.value && detail.value?.publication_id === result.publication_id) {
      detail.value = await getPublicationDetail(result.publication_id)
      versions.load()
      chunks.load()
      baseline.data = null
    }
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    entrySubmitting.value = false
  }
}

/** #20 停用归档：切片与版本转 archived，三处开关同时失效，不能靠「上线」恢复 */
async function deactivate(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `确定停用「${row.title}」吗？停用会把当前快照的切片与版本归档，检索立即失效，且不能靠「上线」恢复（需重新写入正文）。`,
      '停用已生效知识（#20）',
      {
        type: 'warning',
        confirmButtonText: '确定停用',
        cancelButtonText: '取消',
        inputPlaceholder: '请输入停用原因（必填）',
        inputValidator: (value) => (value && value.trim() ? true : '停用原因必填'),
      },
    )
    reason = result.value.trim()
  } catch {
    return
  }
  try {
    await deactivatePublication(row.publication_id, { reason })
    ElMessage.success('已停用并归档，检索不再召回')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

// ── 03.4 删除单个快照 ─────────────────────────────────────────────
async function removeSnapshot(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `确定删除快照「${row.record_id}」的正文与向量吗？删除后不可恢复，也不能再被读取。`,
      '删除已批准切片集合（03.4）',
      {
        type: 'warning',
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        inputPlaceholder: '请输入删除原因（必填）',
        inputValidator: (value) => (value && value.trim() ? true : '删除原因必填'),
      },
    )
    reason = result.value.trim()
  } catch {
    return
  }
  try {
    const result = await deletePublicationSnapshot(currentPublicationId.value, { record_id: row.record_id, reason })
    ElMessage.success(`快照已删除，清除 ${result.deleted_chunk_count ?? 0} 个切片`)
    baseline.data = null
    baseline.cursorStack = []
    baseline.currentCursor = ''
    versions.load()
    chunks.load()
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

// ── 来源与关联（只读溯源：写入渠道 + 按 file_meta.doc_id 反查解析任务）───
const origin = reactive({
  loading: false,
  data: null,
})

/** 写入渠道中文名：按 record_id 前缀判定（无快照 / 管理台 / GSI 契约） */
function channelLabel(channel) {
  return { admin: '管理台写入', contract: 'GSI 契约写入', pending: '无快照' }[channel] || channel || '-'
}

function channelTagType(channel) {
  return { admin: 'warning', contract: 'success', pending: 'info' }[channel] || 'info'
}

async function loadOrigin() {
  if (!currentPublicationId.value) return
  origin.loading = true
  try {
    origin.data = await getPublicationSource(currentPublicationId.value)
  } catch {
    origin.data = null
  } finally {
    origin.loading = false
  }
}

watch(activeTab, (name) => {
  if (name === 'baseline' && baseline.data === null && detail.value?.current_record_id) {
    baseline.recordId = detail.value.current_record_id
    loadBaseline(true)
  }
  if (name === 'source' && origin.data === null) {
    loadOrigin()
  }
})

async function openDetail(row) {
  currentPublicationId.value = row.publication_id
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'versions'
  baseline.data = null
  baseline.recordId = ''
  baseline.cursorStack = []
  baseline.currentCursor = ''
  origin.data = null
  try {
    detail.value = await getPublicationDetail(row.publication_id)
    versions.load()
    chunks.load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
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

.kb-tag {
  margin: 0 4px 4px 0;
}

.kb-muted {
  color: var(--app-text-muted);
}

.kb-origin__meta {
  margin: 12px 0;
}

.kb-origin__table {
  margin-bottom: 8px;
}

.kb-detail__tabs {
  margin-top: 16px;
}

.kb-baseline__bar {
  @include flex($justify: flex-start, $gap: 10px);
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.kb-baseline__meta {
  margin-bottom: 12px;
}

.kb-baseline__hash {
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}

.kb-entry__bar {
  @include flex($justify: flex-start, $gap: 10px);
  flex-wrap: wrap;
  margin-bottom: 8px;
}
</style>
