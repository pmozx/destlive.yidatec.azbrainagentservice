<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">知识分类</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-kb/category：分类挂在空间下，固定两级（一级 + 子级），文档可选归入任一级；
          删除一级分类时子级自动上提、其下文档归为「未分类」，不会连带删除文档
        </p>
      </div>
      <div class="page-header__actions">
        <el-select v-model="spaceId" size="small" placeholder="选择空间" style="width: 180px" @change="loadTree">
          <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button size="small" :disabled="!spaceId" @click="openDialog(null, null)">
          <el-icon><Plus /></el-icon>
          <span>新建一级分类</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush :subtitle="spaceId ? '' : '请先在右上角选择空间'">
      <template #title>
        <h3 class="category-title">分类树</h3>
      </template>
      <template #extra>
        <el-input
          v-model="keyword"
          size="small"
          placeholder="按名称筛选"
          clearable
          style="width: 180px"
          @keyup.enter="loadTree"
          @clear="loadTree"
        />
        <el-button size="small" :loading="loading" @click="loadTree">刷新</el-button>
      </template>

      <div class="category-table">
        <el-table
          v-loading="loading"
          :data="tree"
          size="small"
          row-key="id"
          default-expand-all
          :tree-props="{ children: 'children' }"
          style="width: 100%"
        >
          <el-table-column prop="name" label="分类名称" min-width="220" show-overflow-tooltip />
          <el-table-column prop="description" label="分类说明" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">{{ row.description || '-' }}</template>
          </el-table-column>
          <el-table-column label="直属文档" width="100" align="center">
            <template #default="{ row }">{{ row.doc_count ?? 0 }}</template>
          </el-table-column>
          <el-table-column prop="sort" label="排序" width="80" align="center" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 0 ? 'success' : 'info'" size="small" effect="light">
                {{ row.status === 0 ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button v-if="!row.parent_id" link type="primary" size="small" @click="openDialog(null, row)">
                加子分类
              </el-button>
              <el-button link type="primary" size="small" @click="openDialog(row, null)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="spaceId ? '该空间还没有分类' : '请先选择空间'" :image-size="80" />
          </template>
        </el-table>
      </div>
    </SectionCard>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="520px" destroy-on-close>
      <el-form :model="form" label-width="88px" size="small" @submit.prevent>
        <el-form-item label="所属空间">
          <el-tag size="small" effect="plain">{{ spaceName }}</el-tag>
        </el-form-item>
        <el-form-item v-if="form.parent_id" label="上级分类">
          <el-tag size="small" effect="plain">{{ parentName }}</el-tag>
        </el-form-item>
        <el-form-item label="分类名称" required>
          <el-input v-model="form.name" placeholder="如：常见问题" maxlength="128" />
        </el-form-item>
        <el-form-item label="分类说明">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="512" show-word-limit />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" :max="9999" size="small" controls-position="right" />
          <span class="form-hint">升序，数值小的排在前面</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio :value="0">启用</el-radio>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createCategory, deleteCategory, getCategoryTree, getSpaceOptions, updateCategory } from '@/api'
import SectionCard from '@/components/common/SectionCard.vue'

defineOptions({ name: 'IntegrationKbCategoryView' })

const spaces = ref([])
const spaceId = ref(null)
const keyword = ref('')
const tree = ref([])
const loading = ref(false)

const spaceName = computed(() => spaces.value.find((item) => item.id === spaceId.value)?.name ?? '-')

async function fetchSpaces() {
  try {
    spaces.value = (await getSpaceOptions()) ?? []
    if (!spaceId.value && spaces.value.length) {
      spaceId.value = spaces.value[0].id
      await loadTree()
    }
  } catch {
    spaces.value = []
  }
}

async function loadTree() {
  keyword.value = keyword.value.trim()
  if (!spaceId.value) {
    tree.value = []
    return
  }
  loading.value = true
  try {
    // 有筛选词时按名称 like 查询，服务端仍返回两级树（父节点命中会把子级一并带上）
    const params = { space_id: spaceId.value }
    if (keyword.value) params.name = keyword.value
    tree.value = (await getCategoryTree(params)) ?? []
  } catch {
    tree.value = []
  } finally {
    loading.value = false
  }
}

// ── 新增 / 编辑 ────────────────────────────────────────────────────────

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const parentName = ref('')
const form = reactive({ parent_id: null, name: '', description: '', sort: 0, status: 0 })

const dialogTitle = computed(() => {
  if (editingId.value) return `编辑分类 #${editingId.value}`
  return form.parent_id ? '新建子分类' : '新建一级分类'
})

/**
 * @param {object|null} row 编辑时的目标分类
 * @param {object|null} parent 新建子分类时的父分类
 */
function openDialog(row, parent) {
  if (!spaceId.value) {
    ElMessage.warning('请先选择空间')
    return
  }
  editingId.value = row?.id ?? null
  parentName.value = parent?.name ?? ''
  Object.assign(form, {
    parent_id: row?.parent_id ?? parent?.id ?? null,
    name: row?.name ?? '',
    description: row?.description ?? '',
    sort: row?.sort ?? 0,
    status: row?.status ?? 0,
  })
  dialogVisible.value = true
}

async function submit() {
  if (!form.name.trim()) {
    ElMessage.warning('分类名称不能为空')
    return
  }

  submitting.value = true
  try {
    if (editingId.value) {
      await updateCategory(editingId.value, {
        name: form.name,
        description: form.description,
        sort: form.sort,
        status: form.status,
        parent_id: form.parent_id ?? undefined,
      })
      ElMessage.success('修改成功')
    } else {
      await createCategory({
        space_id: spaceId.value,
        parent_id: form.parent_id ?? undefined,
        name: form.name,
        description: form.description,
        sort: form.sort,
        status: form.status,
      })
      ElMessage.success('新建成功')
    }
    dialogVisible.value = false
    await loadTree()
  } catch {
    // 请求拦截器已统一提示
  } finally {
    submitting.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      row.parent_id
        ? `确定删除分类「${row.name}」吗？其下 ${row.doc_count ?? 0} 份文档会归为未分类。`
        : `确定删除一级分类「${row.name}」吗？其子分类会上提为一级分类，其下 ${row.doc_count ?? 0} 份文档会归为未分类。`,
      '删除分类确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteCategory([row.id])
    ElMessage.success('删除成功')
    await loadTree()
  } catch {
    // 请求拦截器已统一提示
  }
}

onMounted(fetchSpaces)
</script>

<style scoped lang="scss">
.category-table {
  padding: 8px 26px 20px;
}

.category-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
