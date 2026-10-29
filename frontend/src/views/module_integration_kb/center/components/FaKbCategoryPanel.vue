<!-- 知识中心 · 分类树面板：当前空间的两级分类树 + 新增/编辑/删除，选中项联动右侧文档列表 -->
<template>
  <div class="kb-category-panel">
    <div class="kb-panel-head">
      <div class="kb-panel-heading">
        <h3 class="kb-panel-title">知识分类</h3>
        <span class="kb-panel-hint">{{ spaceName }}</span>
      </div>
      <div class="kb-panel-tools">
        <el-button size="small" circle :loading="loading" title="刷新分类树" @click="loadTree">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button size="small" type="primary" plain :disabled="!spaceId" @click="openDialog(null, null)">
          <el-icon><Plus /></el-icon>
          <span>新增分类</span>
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="kb-category-body">
      <el-empty v-if="!spaceId" description="请先在上方选择知识空间" :image-size="60" />
      <template v-else>
        <div
          class="kb-tree-node kb-tree-node--root"
          :class="{ 'is-active': categoryId == null }"
          @click="selectCategory(null)"
        >
          <el-icon><FolderOpened /></el-icon>
          <span class="kb-tree-node__label">全部文档</span>
          <span class="kb-tree-node__count">{{ spaceDocCount ?? '—' }}</span>
        </div>

        <el-tree
          ref="treeRef"
          class="kb-category-tree"
          :data="tree"
          node-key="id"
          highlight-current
          default-expand-all
          :expand-on-click-node="false"
          :props="{ label: 'name', children: 'children' }"
          @node-click="onNodeClick"
        >
          <template #default="{ data }">
            <div class="kb-tree-node" :class="{ 'is-active': data.id === categoryId }">
              <el-icon><Folder /></el-icon>
              <span class="kb-tree-node__label" :title="data.name">{{ data.name }}</span>
              <span class="kb-tree-node__count" title="直属文档数">{{ data.doc_count ?? 0 }}</span>
              <span class="kb-tree-node__actions" @click.stop>
                <el-button
                  v-if="!data.parent_id"
                  link
                  type="primary"
                  size="small"
                  title="新增子分类"
                  @click="openDialog(null, data)"
                >
                  <el-icon><Plus /></el-icon>
                </el-button>
                <el-button link type="primary" size="small" title="编辑" @click="openDialog(data, null)">
                  <el-icon><EditPen /></el-icon>
                </el-button>
                <el-button link type="danger" size="small" title="删除" @click="remove(data)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </span>
            </div>
          </template>
        </el-tree>

        <el-empty v-if="!loading && !tree.length" description="该空间还没有分类" :image-size="60" />
      </template>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="520px" destroy-on-close>
      <el-form :model="form" label-width="88px" size="small" @submit.prevent>
        <el-form-item label="所属空间">
          <el-tag size="small" effect="plain">{{ spaceName }}</el-tag>
        </el-form-item>
        <el-form-item v-if="parentName" label="上级分类">
          <el-tag size="small" effect="plain">{{ parentName }}</el-tag>
          <span class="form-hint">分类固定两级，子分类只能挂在已有一级分类下</span>
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createCategory, deleteCategory, getCategoryTree, updateCategory } from '@/api'

defineOptions({ name: 'FaKbCategoryPanel' })

const props = defineProps({
  /** 当前空间；为空时不加载分类树 */
  spaceId: { type: Number, default: null },
  /** 当前选中的分类（null = 全部文档） */
  categoryId: { type: Number, default: null },
  /** 当前空间文档总数，用于根节点「全部文档」计数（页面的空间聚合值） */
  spaceDocCount: { type: Number, default: null },
  /** 空间清单：仅用于在面板标题处回显空间名称 */
  spaces: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:categoryId', 'change'])

const spaceName = computed(
  () => props.spaces.find((item) => item.id === props.spaceId)?.name ?? '未选择空间',
)

// ── 分类树 ─────────────────────────────────────────────────────────────

const treeRef = ref(null)
const tree = ref([])
const loading = ref(false)

async function loadTree() {
  if (!props.spaceId) {
    tree.value = []
    return
  }
  loading.value = true
  try {
    tree.value = (await getCategoryTree({ space_id: props.spaceId })) ?? []
  } catch {
    tree.value = []
  } finally {
    loading.value = false
  }
}

/** null（全部文档）时要同时清掉树的高亮，否则会残留上一次的选中行 */
function selectCategory(categoryId) {
  if (categoryId == null) treeRef.value?.setCurrentKey(null)
  emit('update:categoryId', categoryId)
}

function onNodeClick(data) {
  selectCategory(data.id)
}

// 切空间：分类属于空间，跨空间的选中一定是脏值
watch(
  () => props.spaceId,
  async () => {
    selectCategory(null)
    await loadTree()
  },
)

// ── 新增 / 编辑 ────────────────────────────────────────────────────────

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const parentName = ref('')
const form = reactive({ parent_id: null, name: '', description: '', sort: 0, status: 0 })

const dialogTitle = computed(() => {
  if (editingId.value) return '编辑分类'
  return form.parent_id ? '新增子分类' : '新增一级分类'
})

/**
 * @param {object|null} row 编辑时的目标分类
 * @param {object|null} parent 新增子分类时的父分类
 */
function openDialog(row, parent) {
  if (!props.spaceId) {
    ElMessage.warning('请先选择知识空间')
    return
  }
  editingId.value = row?.id ?? null
  parentName.value = row ? parentLabelOf(row) : (parent?.name ?? '')
  Object.assign(form, {
    parent_id: row?.parent_id ?? parent?.id ?? null,
    name: row?.name ?? '',
    description: row?.description ?? '',
    sort: row?.sort ?? 0,
    status: row?.status ?? 0,
  })
  dialogVisible.value = true
}

/** 编辑时回显上级分类名称：树里找父节点，找不到就保持空（一级分类） */
function parentLabelOf(row) {
  if (!row.parent_id) return ''
  return tree.value.find((node) => node.id === row.parent_id)?.name ?? ''
}

async function submit() {
  if (!form.name.trim()) {
    ElMessage.warning('分类名称不能为空')
    return
  }

  submitting.value = true
  try {
    if (editingId.value) {
      // 编辑不传 parent_id：改层级容易绕过两级约束，需要挪动时删了重建
      await updateCategory(editingId.value, {
        name: form.name,
        description: form.description,
        sort: form.sort,
        status: form.status,
      })
      ElMessage.success('修改成功')
    } else {
      await createCategory({
        space_id: props.spaceId,
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
    emit('change')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    submitting.value = false
  }
}

// ── 删除 ───────────────────────────────────────────────────────────────

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
    if (props.categoryId === row.id) selectCategory(null)
    ElMessage.success('删除成功')
    await loadTree()
    emit('change')
  } catch {
    // 请求拦截器已统一提示
  }
}

defineExpose({
  /** 重新加载分类树（分类数据在父级之外发生变化时调用） */
  reloadTree: loadTree,
})

onMounted(loadTree)
</script>

<style scoped lang="scss">
.kb-category-panel {
  @include flex($direction: column);
  height: 100%;
  min-height: 0;
}

.kb-panel-head {
  @include flex($justify: space-between, $gap: 10px);
  padding: 18px 18px 12px;
  border-bottom: 1px solid var(--app-border-color);

  .kb-panel-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  .kb-panel-hint {
    font-size: 12px;
    color: var(--app-text-secondary);
    @include ellipsis;
  }

  .kb-panel-tools {
    @include flex($gap: 6px);
    flex-shrink: 0;
  }
}

.kb-panel-heading {
  @include flex($direction: column, $align: flex-start, $gap: 2px);
  min-width: 0;
}

.kb-category-body {
  flex: 1;
  min-height: 260px;
  padding: 8px 12px 16px;
  overflow: auto;
}

.kb-tree-node {
  @include flex($gap: 6px);
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border-radius: var(--app-radius-sm);
  transition: background-color 0.2s ease;

  .kb-tree-node__label {
    flex: 1;
    font-size: 13px;
    color: var(--app-text-regular);
    @include ellipsis;
  }

  .kb-tree-node__count {
    flex-shrink: 0;
    min-width: 18px;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-align: right;
  }

  .kb-tree-node__actions {
    display: none;
    align-items: center;

    :deep(.el-button + .el-button) {
      margin-left: 2px;
    }
  }

  &:hover .kb-tree-node__actions {
    display: inline-flex;
  }

  &.is-active {
    background-color: color-mix(in srgb, var(--el-color-primary) 8%, transparent);

    .kb-tree-node__label {
      font-weight: 600;
      color: var(--el-color-primary);
    }
  }
}

.kb-tree-node--root {
  margin-bottom: 4px;
  cursor: pointer;

  .kb-tree-node__label {
    font-weight: 600;
  }
}

.kb-category-tree {
  --el-tree-node-content-height: 30px;

  background: transparent;

  :deep(.el-tree-node__content) {
    border-radius: var(--app-radius-sm);
  }
}

.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
