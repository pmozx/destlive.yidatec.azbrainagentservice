<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识空间</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/space：空间是知识组织的第一层（空间 → 分类 → 文档 → 分片），
          问答按「启用空间」圈定检索范围；停用空间不再参与检索，删除会级联清理其分类、文档、分片与对象存储原件
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" @click="openDialog()">
          <el-icon><Plus /></el-icon>
          <span>新建空间</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="space-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="空间名称">
            <el-input v-model="query.name" placeholder="模糊匹配" clearable style="width: 170px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="空间编码">
            <el-input v-model="query.code" placeholder="模糊匹配" clearable style="width: 170px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 120px">
              <el-option label="启用" :value="0" />
              <el-option label="停用" :value="1" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column prop="name" label="空间名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="code" label="空间编码" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="space-code">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="空间说明" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column label="文档数" width="90" align="center">
          <template #default="{ row }">{{ row.doc_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="密级" width="90">
          <template #default="{ row }">
            <el-tag :type="SECURITY_META[row.security_level]?.type || 'info'" size="small" effect="plain">
              {{ SECURITY_META[row.security_level]?.label || '公开' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="sort" label="排序" width="80" align="center" />
        <el-table-column label="是否参与检索" width="130" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === 0"
              size="small"
              :loading="switchingId === row.id"
              @change="(value) => toggleStatus(row, value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有知识空间：先建一个，再在「知识文档」里上传材料" :image-size="80" />
        </template>
      </el-table>

      <div class="space-pagination">
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

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? `编辑空间 #${editingId}` : '新建知识空间'"
      width="540px"
      destroy-on-close
    >
      <el-form :model="form" label-width="92px" size="small" @submit.prevent>
        <el-form-item label="空间名称" required>
          <el-input v-model="form.name" placeholder="如：产品手册库" maxlength="128" />
        </el-form-item>
        <el-form-item label="空间编码" required>
          <el-input
            v-model="form.code"
            :disabled="Boolean(editingId)"
            placeholder="如 kb-demo（字母/数字/下划线/中划线）"
            maxlength="64"
          />
          <p class="form-hint">编码参与对象存储目录与空间唯一性判定，创建后不可修改</p>
        </el-form-item>
        <el-form-item label="空间说明">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="512" show-word-limit />
        </el-form-item>
        <el-form-item label="密级">
          <el-radio-group v-model="form.security_level">
            <el-radio v-for="(meta, value) in SECURITY_META" :key="value" :value="Number(value)">
              {{ meta.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" :max="9999" size="small" controls-position="right" />
          <span class="form-hint">升序，数值小的排在前面</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio :value="0">启用（参与检索）</el-radio>
            <el-radio :value="1">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createSpace, deleteSpace, getSpaceList, updateSpace } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'IntegrationKbSpaceView' })

/** 密级：与后端 SpaceCreateSchema.security_level 对齐 */
const SECURITY_META = {
  1: { label: '公开', type: 'success' },
  2: { label: '内部', type: 'warning' },
  3: { label: '机密', type: 'danger' },
}

/** 空值不下发；status 可能是 0（启用），不能用真值判断 */
function buildParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.name) result.name = params.name
  if (params.code) result.code = params.code
  if (params.status !== '' && params.status !== null && params.status !== undefined) result.status = params.status
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getSpaceList(buildParams(params)),
  { defaultQuery: { name: '', code: '', status: '' } },
)

// ── 新增 / 编辑 ────────────────────────────────────────────────────────

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const form = reactive({
  name: '',
  code: '',
  description: '',
  security_level: 1,
  sort: 0,
  status: 0,
})

function openDialog(row) {
  editingId.value = row?.id ?? null
  Object.assign(form, {
    name: row?.name ?? '',
    code: row?.code ?? '',
    description: row?.description ?? '',
    security_level: row?.security_level ?? 1,
    sort: row?.sort ?? 0,
    status: row?.status ?? 0,
  })
  dialogVisible.value = true
}

async function submit() {
  if (!form.name.trim() || !form.code.trim()) {
    ElMessage.warning('空间名称与空间编码不能为空')
    return
  }
  if (!/^[\w-]+$/.test(form.code)) {
    ElMessage.warning('空间编码只能包含字母、数字、下划线和中划线')
    return
  }

  submitting.value = true
  try {
    if (editingId.value) {
      await updateSpace(editingId.value, {
        name: form.name,
        description: form.description,
        security_level: form.security_level,
        sort: form.sort,
        status: form.status,
      })
      ElMessage.success('修改成功')
    } else {
      await createSpace({
        name: form.name,
        code: form.code,
        description: form.description,
        security_level: form.security_level,
        sort: form.sort,
        status: form.status,
      })
      ElMessage.success('新建成功')
    }
    dialogVisible.value = false
    await load()
  } catch {
    // 请求拦截器已统一提示
  } finally {
    submitting.value = false
  }
}

// ── 启停 / 删除 ────────────────────────────────────────────────────────

const switchingId = ref(null)

/** 启停即「是否参与检索」：停用后该空间的知识不再被问答召回 */
async function toggleStatus(row, enabled) {
  switchingId.value = row.id
  try {
    await updateSpace(row.id, { status: enabled ? 0 : 1 })
    row.status = enabled ? 0 : 1
    ElMessage.success(enabled ? '已启用（参与检索）' : '已停用（不再参与检索）')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    switchingId.value = null
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除空间「${row.name}」吗？会连带删除其 ${row.doc_count ?? 0} 份文档、对应分片与对象存储原件，不可撤销。`,
      '删除空间确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteSpace([row.id])
    ElMessage.success('删除成功')
    await load()
  } catch {
    // 请求拦截器已统一提示
  }
}
</script>

<style scoped lang="scss">
.space-toolbar {
  padding: 20px 26px 4px;
}

.space-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.space-code {
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--app-text-secondary);
}

.form-hint {
  margin: 4px 0 0 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
