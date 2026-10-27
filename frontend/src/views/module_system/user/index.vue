<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">用户管理</h2>
        <p class="page-header__desc">对接后端 /system/users 接口，支持分页查询、新增、编辑、详情与删除</p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新增用户</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="user-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="用户名">
            <el-input v-model="query.username" placeholder="模糊匹配" clearable @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input v-model="query.mobile" placeholder="精确匹配" clearable @keyup.enter="search" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="120" show-overflow-tooltip />
        <el-table-column prop="name" label="姓名" width="110" show-overflow-tooltip />
        <el-table-column prop="mobile" label="手机号" width="130" />
        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
        <el-table-column label="性别" width="80">
          <template #default="{ row }">{{ GenderLabel[row.gender] || '-' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeUser(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无数据，确认后端数据库连接是否正常" :image-size="80" />
        </template>
      </el-table>

      <div class="user-pagination">
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
      v-model="formVisible"
      :title="formMode === 'create' ? '新增用户' : '编辑用户'"
      width="480px"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="78px" size="small">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="登录用户名" maxlength="32" />
        </el-form-item>
        <el-form-item v-if="formMode === 'create'" label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="真实姓名" maxlength="32" />
        </el-form-item>
        <el-form-item label="手机号" prop="mobile">
          <el-input v-model="form.mobile" placeholder="11 位手机号" maxlength="11" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="name@example.com" />
        </el-form-item>
        <el-form-item label="性别" prop="gender">
          <el-radio-group v-model="form.gender">
            <el-radio v-for="option in genderOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="formVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="用户详情" width="460px">
      <div v-loading="detailLoading">
        <el-descriptions v-if="detail" :column="1" border size="small">
          <el-descriptions-item label="ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="用户名">{{ detail.username || '-' }}</el-descriptions-item>
          <el-descriptions-item label="姓名">{{ detail.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="手机号">{{ detail.mobile || '-' }}</el-descriptions-item>
          <el-descriptions-item label="邮箱">{{ detail.email || '-' }}</el-descriptions-item>
          <el-descriptions-item label="性别">{{ GenderLabel[detail.gender] || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_time) }}</el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="未获取到用户信息" :image-size="80" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createUser, deleteUser, getUserDetail, getUserList, updateUser } from '@/api/module_system/user'
import SectionCard from '@/components/common/SectionCard.vue'
import { GenderEnum, GenderLabel } from '@/enums'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'SystemUserView' })

/** 组装查询参数，空字符串不下发，避免后端正则校验失败 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.username) result.username = params.username
  if (params.mobile) result.mobile = params.mobile
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } =
  useTable((params) => getUserList(buildQueryParams(params)), {
    defaultQuery: { username: '', mobile: '' },
  })

const genderOptions = Object.entries(GenderLabel).map(([value, label]) => ({ value, label }))

const formVisible = ref(false)
const formMode = ref('create')
const submitting = ref(false)
const formRef = ref()
const editingId = ref(null)
const form = reactive({
  username: '',
  password: '',
  name: '',
  mobile: '',
  email: '',
  gender: GenderEnum.UNKNOWN,
})

const formRules = computed(() => ({
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  ...(formMode.value === 'create'
    ? {
        password: [
          { required: true, message: '请输入密码', trigger: 'blur' },
          { min: 6, message: '密码至少 6 位', trigger: 'blur' },
        ],
      }
    : {}),
  mobile: [{ pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
}))

function resetForm() {
  Object.assign(form, {
    username: '',
    password: '',
    name: '',
    mobile: '',
    email: '',
    gender: GenderEnum.UNKNOWN,
  })
  editingId.value = null
  formRef.value?.clearValidate()
}

function openCreate() {
  formMode.value = 'create'
  resetForm()
  formVisible.value = true
}

function openEdit(row) {
  formMode.value = 'edit'
  resetForm()
  Object.assign(form, {
    username: row.username || '',
    name: row.name || '',
    mobile: row.mobile || '',
    email: row.email || '',
    gender: row.gender || GenderEnum.UNKNOWN,
  })
  editingId.value = row.id
  formVisible.value = true
}

/** 剔除空字段，避免后端对空字符串执行手机号 / 邮箱校验 */
function toPayload(source) {
  return Object.fromEntries(
    Object.entries(source).filter(([, value]) => value !== '' && value !== null && value !== undefined),
  )
}

async function submitForm() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    if (formMode.value === 'create') {
      await createUser(toPayload(form))
    } else {
      // 后端 UserUpdateSchema 不接收密码字段
      const { password, ...payload } = form
      await updateUser(editingId.value, toPayload(payload))
    }
    ElMessage.success(formMode.value === 'create' ? '用户创建成功' : '用户修改成功')
    formVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    submitting.value = false
  }
}

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getUserDetail(row.id)
  } catch {
    detail.value = row
  } finally {
    detailLoading.value = false
  }
}

async function removeUser(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username || row.name || row.id}」吗？该操作不可撤销。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteUser([row.id])
    ElMessage.success('删除成功')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}
</script>

<style scoped lang="scss">
.user-toolbar {
  padding: 20px 26px 4px;
}

.user-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}
</style>
