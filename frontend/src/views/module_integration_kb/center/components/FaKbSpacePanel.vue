<!-- 知识中心 · 空间卡片面板：可见空间网格 + 新增/编辑/启停/删除（接口对齐知识空间页） -->
<template>
  <div class="kb-space-panel">
    <div class="kb-panel-head">
      <div class="kb-panel-heading">
        <h3 class="kb-panel-title">知识空间</h3>
        <span class="kb-panel-hint">点选卡片切换下方面板的维护范围</span>
      </div>
      <el-button size="small" type="primary" plain @click="openDialog()">
        <el-icon><Plus /></el-icon>
        <span>新增空间</span>
      </el-button>
    </div>

    <div v-loading="loading" class="kb-space-grid">
      <div
        v-for="space in spaces"
        :key="space.id"
        class="kb-space-card"
        :class="{ 'is-active': space.id === modelValue, 'is-off': space.status === 1 }"
        @click="selectSpace(space.id)"
      >
        <div class="kb-space-card__head">
          <span class="kb-space-card__ico" :style="iconStyle(space)">{{ spaceInitial(space) }}</span>
          <span class="kb-space-card__name" :title="space.name">{{ space.name }}</span>
          <el-tag :type="securityMeta(space.security_level).type" size="small" effect="plain">
            {{ securityMeta(space.security_level).label }}
          </el-tag>
        </div>

        <div class="kb-space-card__desc" :title="space.description || space.code">
          {{ space.code }}<template v-if="space.description"> · {{ space.description }}</template>
        </div>

        <div class="kb-space-card__meta">
          <el-tag :type="space.status === 0 ? 'success' : 'info'" size="small" effect="light">
            {{ space.status === 0 ? '启用' : '停用' }}
          </el-tag>
          <span class="kb-space-card__count">{{ space.doc_count ?? 0 }} 份文档</span>
        </div>

        <div class="kb-space-card__actions" @click.stop>
          <el-button link type="primary" size="small" @click="openDialog(space)">编辑</el-button>
          <el-button
            link
            :type="space.status === 0 ? 'warning' : 'success'"
            size="small"
            @click="toggleStatus(space)"
          >
            {{ space.status === 0 ? '停用' : '启用' }}
          </el-button>
          <el-button link type="danger" size="small" @click="remove(space)">删除</el-button>
        </div>
      </div>

      <el-empty
        v-if="!loading && !spaces.length"
        description="还没有知识空间：点右上角「新增空间」开始"
        :image-size="64"
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="560px" destroy-on-close>
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
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createSpace, deleteSpace, updateSpace } from '@/api'

defineOptions({ name: 'FaKbSpacePanel' })

const props = defineProps({
  /** 当前选中的空间 ID，由知识中心页统一持有（undefined/null = 未选） */
  modelValue: { type: Number, default: null },
  /** 全量空间清单（含停用）：页面拉取后下发，本组件只负责维护动作 */
  spaces: { type: Array, default: () => [] },
  /** 空间清单加载态，由页面透传，避免卡片闪烁 */
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'refresh'])

/** 密级：与后端 SpaceCreateSchema.security_level 对齐 */
const SECURITY_META = {
  1: { label: '公开', type: 'success' },
  2: { label: '内部', type: 'warning' },
  3: { label: '机密', type: 'danger' },
}

function securityMeta(value) {
  return SECURITY_META[value] ?? { label: '未设置', type: 'info' }
}

/** 卡片图标位取名称首字：没有图标素材时也能一眼区分空间 */
function spaceInitial(space) {
  return (space.name || '').trim().charAt(0).toUpperCase() || '知'
}

/**
 * 图标着色：优先空间自定义色，缺省回退品牌主色。
 * 浅底由 color-mix 派生，避免为每个空间再维护一套背景色。
 */
function iconStyle(space) {
  const color = (space.color || '').trim()
  if (!color) return {}
  return { color, backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)` }
}

function selectSpace(id) {
  if (id === props.modelValue) return
  emit('update:modelValue', id)
}

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

const dialogTitle = computed(() => (editingId.value ? `编辑空间 #${editingId.value}` : '新增知识空间'))

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
      const created = await createSpace({
        name: form.name,
        code: form.code,
        description: form.description,
        security_level: form.security_level,
        sort: form.sort,
        status: form.status,
      })
      ElMessage.success('新建成功')
      // 新建后直接切到新空间，省掉用户在卡片里再点一次
      if (created?.id) emit('update:modelValue', created.id)
    }
    dialogVisible.value = false
    emit('refresh')
  } catch {
    // 请求拦截器已统一提示
  } finally {
    submitting.value = false
  }
}

// ── 启停 / 删除 ────────────────────────────────────────────────────────

/** 启停即「是否参与检索」：停用后该空间的知识不再被问答召回，数据保留 */
async function toggleStatus(space) {
  const nextStatus = space.status === 0 ? 1 : 0
  if (nextStatus === 1) {
    try {
      await ElMessageBox.confirm(
        `停用「${space.name}」后，该空间下的知识不再参与问答检索，数据仍会保留，可随时重新启用。`,
        '停用空间确认',
        { type: 'warning', confirmButtonText: '确定停用', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  try {
    await updateSpace(space.id, { status: nextStatus })
    ElMessage.success(nextStatus === 0 ? '已启用（参与检索）' : '已停用（不再参与检索）')
    emit('refresh')
  } catch {
    // 请求拦截器已统一提示
  }
}

async function remove(space) {
  try {
    await ElMessageBox.confirm(
      `确定删除空间「${space.name}」吗？会连带删除其 ${space.doc_count ?? 0} 份文档、对应分片与对象存储原件，不可撤销。`,
      '删除空间确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteSpace([space.id])
    // 删掉的正是当前选中项：先清空选中，避免下方分类/文档面板带着孤儿 ID 请求
    if (props.modelValue === space.id) emit('update:modelValue', null)
    ElMessage.success('删除成功')
    emit('refresh')
  } catch {
    // 请求拦截器已统一提示
  }
}
</script>

<style scoped lang="scss">
.kb-panel-head {
  @include flex($justify: space-between, $gap: 12px);
  padding: 18px 26px 12px;

  .kb-panel-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  .kb-panel-hint {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.kb-panel-heading {
  @include flex($align: baseline, $gap: 10px);
  min-width: 0;
}

.kb-space-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  min-height: 120px;
  padding: 0 26px 20px;
}

.kb-space-card {
  @include flex($direction: column, $gap: 8px);
  padding: 14px 16px;
  cursor: pointer;
  border: 1px solid var(--app-border-color);
  border-radius: var(--app-radius);
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    background-color 0.2s ease;

  &:hover {
    border-color: color-mix(in srgb, var(--el-color-primary) 40%, transparent);
    box-shadow: var(--app-shadow-card);
  }

  &.is-active {
    background-color: color-mix(in srgb, var(--el-color-primary) 4%, transparent);
    border-color: var(--el-color-primary);
  }

  // 停用空间弱化展示：仍是维护对象，只是不再参与检索
  &.is-off .kb-space-card__name {
    color: var(--app-text-secondary);
  }
}

.kb-space-card__head {
  @include flex($gap: 8px);

  .kb-space-card__ico {
    @include flex-center;
    flex-shrink: 0;
    width: 30px;
    height: 30px;
    font-size: 14px;
    font-weight: 600;
    color: var(--el-color-primary);
    background-color: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
    border-radius: 8px;
  }

  .kb-space-card__name {
    flex: 1;
    font-size: 14px;
    font-weight: 600;
    color: var(--app-text-primary);
    @include ellipsis;
  }
}

.kb-space-card__desc {
  font-size: 12px;
  color: var(--app-text-secondary);
  @include ellipsis;
}

.kb-space-card__meta {
  @include flex($justify: space-between, $gap: 8px);

  .kb-space-card__count {
    font-size: 12px;
    color: var(--app-text-regular);
  }
}

.kb-space-card__actions {
  @include flex($gap: 2px);
  padding-top: 6px;
  border-top: 1px dashed var(--app-border-color);
}

.form-hint {
  margin: 4px 0 0 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
