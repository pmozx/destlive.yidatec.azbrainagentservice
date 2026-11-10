<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">模型配置</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/system/model-config 接口：一处维护对话 / 嵌入 / 重排序三类模型的连接方式（供应商 / 网关 / 密钥）
          与默认调优参数；问答生成、向量化、精排统一按「模型标识」取配置，本地部署填内网地址即可，改了即时生效
        </p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新增模型</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="model-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="模型类型">
            <el-select v-model="query.model_type" placeholder="全部" clearable style="width: 130px">
              <el-option v-for="item in MODEL_TYPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="展示名称">
            <el-input v-model="query.model_config_name" placeholder="模糊匹配" clearable style="width: 150px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="模型标识">
            <el-input v-model="query.model_config_key" placeholder="模糊匹配" clearable style="width: 150px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="供应商">
            <el-input v-model="query.model_provider" placeholder="精确匹配" clearable style="width: 130px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.enabled" placeholder="全部" clearable style="width: 110px">
              <el-option label="启用" :value="true" />
              <el-option label="停用" :value="false" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
            <el-button type="danger" plain :disabled="!selectedIds.length" @click="removeSelected">
              删除选中<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table
        v-loading="loading"
        :data="list"
        size="small"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="sort" label="排序" width="76" />
        <el-table-column label="类型" width="106">
          <template #default="{ row }">
            <el-tag :type="modelTypeTag(row.model_type)" size="small" effect="light">
              {{ modelTypeLabel(row.model_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model_config_name" label="展示名称" min-width="140" show-overflow-tooltip />
        <el-table-column label="模型标识" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="model-key">{{ row.model_config_key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="模型名称" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ row.model_name || '（同标识）' }}</template>
        </el-table-column>
        <el-table-column prop="model_provider" label="供应商" width="110" show-overflow-tooltip />
        <el-table-column label="网关地址" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.base_url || '沿用环境变量' }}</template>
        </el-table-column>
        <el-table-column label="密钥" width="150">
          <template #default="{ row }">
            <span v-if="row.has_api_key" class="model-mask">{{ row.api_key_masked }}</span>
            <span v-else class="model-muted">沿用环境变量</span>
          </template>
        </el-table-column>
        <el-table-column label="默认参数" width="110">
          <template #default="{ row }">
            <el-tooltip :disabled="!configCount(row)" placement="top" effect="dark">
              <template #content>
                <pre class="model-config-tip">{{ prettyJson(row.default_config_json) }}</pre>
              </template>
              <el-tag size="small" effect="plain" type="info">
                {{ configCount(row) ? `${configCount(row)} 项` : '未配置' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="86">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="light">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="默认" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="warning" size="small" effect="light">默认</el-tag>
            <span v-else class="model-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="success" size="small" @click="runTest(row)">测试</el-button>
            <el-button
              link
              type="danger"
              size="small"
              :disabled="row.is_default"
              @click="removeRows([row.id], row.model_config_name || row.model_config_key)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            description="还没有模型配置：先选类型，再填供应商、网关与模型名称即可测试连通性"
            :image-size="80"
          />
        </template>
      </el-table>

      <div class="model-pagination">
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

    <el-dialog v-model="formVisible" :title="formTitle" width="780px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px" size="small" @submit.prevent>
        <el-form-item label="模型类型" prop="model_type">
          <el-select v-model="form.model_type" style="width: 100%" @change="handleTypeChange">
            <el-option v-for="item in MODEL_TYPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <p class="model-form__tip">切换类型会同步刷新默认参数模板；「默认模型」在每种类型内各自唯一</p>
        </el-form-item>
        <el-form-item label="展示名称" prop="model_config_name">
          <el-input
            v-model="form.model_config_name"
            placeholder="给人看的名字，如「问答-快模型」"
            maxlength="128"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="模型标识" prop="model_config_key">
          <el-input v-model="form.model_config_key" placeholder="系统内调用键，如 kb-answer；业务配置引用它" maxlength="128" />
        </el-form-item>
        <el-form-item label="模型名称">
          <el-input v-model="form.model_name" placeholder="真正传给厂商的 model 参数；留空则用模型标识" maxlength="256" />
        </el-form-item>
        <el-form-item label="供应商">
          <el-select
            v-model="form.model_provider"
            :placeholder="providerPlaceholder"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
          >
            <el-option v-for="item in providerOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <p class="model-form__tip">{{ providerTip }}</p>
        </el-form-item>
        <el-form-item label="网关地址">
          <el-input v-model="form.base_url" :placeholder="baseUrlPlaceholder" maxlength="512" />
          <p class="model-form__tip">留空沿用同类环境变量；本地部署填内网地址即可（不读系统代理，直连局域网）</p>
        </el-form-item>
        <el-form-item label="密钥">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="apiKeyPlaceholder"
            maxlength="1024"
          />
          <p class="model-form__tip">密钥只以掩码回显；编辑时留空表示不修改，本地部署的私有化服务通常不需要密钥。</p>
        </el-form-item>
        <el-form-item label="默认调优参数" prop="default_config_json">
          <el-input
            v-model="form.default_config_json"
            type="textarea"
            :rows="8"
            class="model-form__json"
            :placeholder="configPlaceholder"
          />
          <p class="model-form__tip">
            {{ configTip }}
            <el-button link type="primary" size="small" @click="fillDefaultConfig">
              恢复为{{ modelTypeLabel(form.model_type) }}默认模板
            </el-button>
          </p>
        </el-form-item>
        <el-form-item label="模型描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="适用场景、上下文长度、成本档次" maxlength="512" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" :max="9999" controls-position="right" />
          <span class="model-form__tip model-form__tip--inline">数字小的排在前面</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
          <span class="model-form__tip model-form__tip--inline">停用后不再参与取配置，记录保留</span>
        </el-form-item>
        <el-form-item label="默认模型">
          <el-switch v-model="form.is_default" :disabled="!form.enabled" />
          <span class="model-form__tip model-form__tip--inline">
            该类型没有指定模型时用它；每种类型只有一条默认（切换会自动清掉同类型的其它默认行）
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="formVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="testVisible" title="连通性测试" width="560px">
      <div v-loading="testing" class="model-test">
        <template v-if="testResult">
          <el-result
            :icon="testResult.ok ? 'success' : 'error'"
            :title="testResult.ok ? '连通正常' : '连通失败'"
            :sub-title="`${testResult.model || '-'} · ${testResult.latency_ms} ms`"
          />
          <div v-if="testResult.reply" class="model-test__block">
            <p class="model-test__label">{{ testReplyLabel }}</p>
            <p class="model-test__value">{{ testResult.reply }}</p>
          </div>
          <div v-if="testResult.error" class="model-test__block">
            <p class="model-test__label">失败原因</p>
            <p class="model-test__value model-test__value--error">{{ testResult.error }}</p>
          </div>
        </template>
        <el-empty v-else description="正在按该模型的配置发起一次最小请求…" :image-size="70" />
      </div>
      <template #footer>
        <el-button size="small" @click="testVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createModelConfig,
  deleteModelConfig,
  getModelConfigDetail,
  getModelConfigList,
  testModelConfig,
  updateModelConfig,
} from '@/api/module_system/modelConfig'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { prettyJson } from '@/utils/format'

defineOptions({ name: 'SystemModelConfigView' })

/** 三类模型：类型决定连接协议、可调参数与默认模板（与服务端 model.MODEL_TYPE_* 对齐） */
const MODEL_TYPE_OPTIONS = [
  { value: 'chat', label: '对话模型' },
  { value: 'embedding', label: '嵌入模型' },
  { value: 'rerank', label: '重排序模型' },
]

const MODEL_TYPE_TAGS = { chat: 'primary', embedding: 'success', rerank: 'warning' }

/** 默认参数模板：与服务端 schema.DEFAULT_CONFIG_BY_TYPE 一致（页面预填，服务端兜底） */
const DEFAULT_CONFIG_BY_TYPE = {
  chat: { temperature: 0.2, max_tokens: 1500, timeout: 60, max_retries: 2 },
  embedding: { timeout: 60, batch_size: 16, concurrency: 3, max_retries: 2 },
  rerank: { timeout: 30 },
}

/** 各类型可填的键：这里只做提示，真正的白名单在服务端按类型校验 */
const CONFIG_KEYS_BY_TYPE = {
  chat: 'temperature / max_tokens / top_p / timeout / max_retries / extra_body 等',
  embedding: 'timeout / batch_size / concurrency / max_retries',
  rerank: 'timeout',
}

/** 供应商候选：只是输入提示，实际值原样透传（本地部署填 local / ollama / vllm 均可） */
const PROVIDER_OPTIONS = {
  chat: ['openai', 'azure_openai', 'anthropic', 'google_genai', 'dashscope', 'ollama', 'local', 'vllm'],
  embedding: ['openai', 'ollama', 'local', 'vllm', 'tei'],
  rerank: ['local', 'tei', 'xinference', 'ollama'],
}

/** 网关地址占位：本地部署的关键是填内网地址，密钥可以留空 */
const BASE_URL_PLACEHOLDER = {
  chat: '如 https://gateway.example.com/v1；本地 vLLM 填 http://10.0.0.8:8000/v1',
  embedding: '如 http://10.0.0.9:8080/v1（TEI / vLLM 的 OpenAI 兼容入口，供应商选 openai）',
  rerank: '如 http://10.0.0.10:9997/v1（精排服务的 Cohere 同构入口）',
}

function modelTypeLabel(value) {
  return MODEL_TYPE_OPTIONS.find((item) => item.value === value)?.label || value || '-'
}

function modelTypeTag(value) {
  return MODEL_TYPE_TAGS[value] || 'info'
}

function defaultConfigText(modelType) {
  return JSON.stringify(DEFAULT_CONFIG_BY_TYPE[modelType] || {}, null, 2)
}

/** 组装查询参数：空值不下发，enabled 为空时不按状态过滤 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  if (params.model_type) result.model_type = params.model_type
  if (params.model_config_name) result.model_config_name = params.model_config_name
  if (params.model_config_key) result.model_config_key = params.model_config_key
  if (params.model_provider) result.model_provider = params.model_provider
  if (params.enabled === true || params.enabled === false) result.enabled = params.enabled
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getModelConfigList(buildQueryParams(params)),
  { defaultQuery: { model_type: '', model_config_name: '', model_config_key: '', model_provider: '', enabled: '' } },
)

const selectedIds = ref([])

function handleSelectionChange(rows) {
  selectedIds.value = rows.map((row) => row.id)
}

function configCount(row) {
  return Object.keys(row.default_config_json || {}).length
}

// ── 新增 / 编辑 ────────────────────────────────────────────────────────

function emptyForm() {
  return {
    id: null,
    model_type: 'chat',
    model_config_name: '',
    model_config_key: '',
    model_name: '',
    model_provider: 'openai',
    base_url: '',
    api_key: '',
    // 表单里用字符串编辑：直接绑对象会失去「写了半个 JSON 也能保存」之外的报错提示
    default_config_json: defaultConfigText('chat'),
    description: '',
    enabled: true,
    is_default: false,
    sort: 0,
  }
}

const formVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)
const form = reactive(emptyForm())
const editingId = computed(() => form.id)

/** 切换类型：只有当前 JSON 还是某类默认模板（或为空）时才刷新，避免盖掉手填参数 */
function handleTypeChange(nextType) {
  const current = (form.default_config_json || '').trim()
  const untouched =
    !current ||
    current === '{}' ||
    Object.values(DEFAULT_CONFIG_BY_TYPE).some((item) => current === JSON.stringify(item, null, 2))
  if (untouched) form.default_config_json = defaultConfigText(nextType)
}

function fillDefaultConfig() {
  form.default_config_json = defaultConfigText(form.model_type)
}

const providerOptions = computed(() => PROVIDER_OPTIONS[form.model_type] || PROVIDER_OPTIONS.chat)
const providerPlaceholder = computed(() => {
  if (form.model_type === 'embedding') return 'OpenAI 兼容服务填 openai；Ollama 填 ollama；本地 vLLM / TEI 填 local'
  if (form.model_type === 'rerank') return 'OpenAI 兼容服务填 openai；Ollama 填 ollama'
  return 'OpenAI 兼容网关统一填 openai；本地部署填 local 或 ollama'
})
const providerTip = computed(() => {
  if (form.model_type === 'rerank') {
    return '精排服务只需 Cohere 同构报文：自建 TEI / Xinference 填 local 即可，密钥可留空'
  }
  if (form.model_type === 'embedding') {
    return '决定初始化用哪个集成：OpenAI 兼容（含 vLLM / TEI）填 openai，Ollama 填 ollama，本地部署也可填 local（归一到 openai）'
  }
  return '决定「用哪种协议说话」：OpenAI 兼容（含 vLLM / TEI）填 openai，Ollama 填 ollama，不校验时可填 local'
})
const baseUrlPlaceholder = computed(() => BASE_URL_PLACEHOLDER[form.model_type] || BASE_URL_PLACEHOLDER.chat)
const configPlaceholder = computed(() => defaultConfigText(form.model_type))
const configTip = computed(
  () =>
    `模型级默认值，仅作兜底：代码里的用途场景与单次调用会逐键覆盖它。${
      form.model_type === 'rerank' ? '重排序只认' : '可用键：'
    } ${CONFIG_KEYS_BY_TYPE[form.model_type] || ''}；连接类参数（base_url / api_key / model / model_provider）请用上面的字段。`,
)

const API_KEY_ENV = { chat: 'LLM_API_KEY', embedding: 'EMBEDDING_API_KEY', rerank: 'RERANK_API_KEY' }

const formTitle = computed(() =>
  editingId.value ? `编辑模型 · ${form.model_config_name || form.model_config_key}` : '新增模型',
)
const apiKeyPlaceholder = computed(() => {
  if (editingId.value) return '留空表示不修改；要换密钥时粘贴新值'
  return `留空则沿用环境变量 ${API_KEY_ENV[form.model_type] || 'LLM_API_KEY'}；本地部署通常不需要密钥`
})

/** JSON 校验：既要能解析，也要是对象——数组或标量写进去，后端读取时会被整块丢弃 */
function validateConfigJson(rule, value, callback) {
  const text = (value || '').trim()
  if (!text) return callback()
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch {
    return callback(new Error('JSON 格式不正确，请检查引号、逗号与括号'))
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return callback(new Error('必须是 JSON 对象，例如 {"temperature": 0.3}'))
  }
  callback()
}

const rules = {
  model_type: [{ required: true, message: '请选择模型类型', trigger: 'change' }],
  model_config_name: [{ required: true, message: '请填写展示名称', trigger: 'blur' }],
  model_config_key: [{ required: true, message: '请填写模型标识', trigger: 'blur' }],
  default_config_json: [{ validator: validateConfigJson, trigger: 'blur' }],
}

function resetForm(values = {}) {
  Object.assign(form, emptyForm(), values)
}

function openCreate() {
  resetForm()
  formVisible.value = true
}

async function openEdit(row) {
  resetForm({
    id: row.id,
    model_type: row.model_type || 'chat',
    model_config_name: row.model_config_name || '',
    model_config_key: row.model_config_key || '',
    model_name: row.model_name || '',
    model_provider: row.model_provider || 'openai',
    base_url: row.base_url || '',
    // 密钥只回显掩码：输入框留空，保存时后端才会沿用库里的原值
    api_key: '',
    default_config_json: JSON.stringify(row.default_config_json || {}, null, 2),
    description: row.description || '',
    enabled: Boolean(row.enabled),
    is_default: Boolean(row.is_default),
    sort: row.sort ?? 0,
  })
  formVisible.value = true
  // 列表出参已够用，但详情能拿到最新的掩码与参数：编辑前再拉一次，避免用过期行覆盖别人的修改
  try {
    const detail = await getModelConfigDetail(row.id)
    if (detail && detail.id === form.id) {
      resetForm({
        id: detail.id,
        model_type: detail.model_type || 'chat',
        model_config_name: detail.model_config_name || '',
        model_config_key: detail.model_config_key || '',
        model_name: detail.model_name || '',
        model_provider: detail.model_provider || 'openai',
        base_url: detail.base_url || '',
        api_key: '',
        default_config_json: JSON.stringify(detail.default_config_json || {}, null, 2),
        description: detail.description || '',
        enabled: Boolean(detail.enabled),
        is_default: Boolean(detail.is_default),
        sort: detail.sort ?? 0,
      })
    }
  } catch {
    // 详情失败就沿用列表行数据，拦截器已提示
  }
}

async function submitForm() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  const payload = {
    model_type: form.model_type,
    model_config_name: form.model_config_name.trim(),
    model_config_key: form.model_config_key.trim(),
    model_name: form.model_name.trim(),
    model_provider: form.model_provider.trim(),
    base_url: form.base_url.trim(),
    default_config_json: form.default_config_json.trim() ? JSON.parse(form.default_config_json) : {},
    description: form.description.trim(),
    enabled: form.enabled,
    is_default: form.is_default,
    sort: Number(form.sort) || 0,
  }
  // 编辑时密钥留空 = 沿用原值：不传这个字段，后端就不会覆盖
  if (form.api_key) payload.api_key = form.api_key.trim()
  if (!editingId.value) payload.api_key = form.api_key.trim()

  submitting.value = true
  try {
    if (editingId.value) {
      await updateModelConfig(editingId.value, payload)
      ElMessage.success('模型配置已更新')
    } else {
      await createModelConfig(payload)
      ElMessage.success('模型配置已创建')
    }
    formVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    submitting.value = false
  }
}

// ── 删除 ──────────────────────────────────────────────────────────────

async function removeRows(ids, tip) {
  try {
    await ElMessageBox.confirm(`确定删除模型「${tip}」吗？该操作不可撤销。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  try {
    await deleteModelConfig(ids)
    ElMessage.success('删除成功')
    load()
  } catch {
    // 请求拦截器已统一提示错误
  }
}

function removeSelected() {
  if (!selectedIds.value.length) return
  removeRows([...selectedIds.value], `选中的 ${selectedIds.value.length} 条配置`)
}

// ── 连通性测试 ────────────────────────────────────────────────────────

const testVisible = ref(false)
const testing = ref(false)
const testResult = ref(null)
/** 本次测试的类型：三类模型的探测方式不同，结果标签也跟着变 */
const testType = ref('chat')

const testReplyLabel = computed(() => {
  if (testType.value === 'embedding') return '向量维度'
  if (testType.value === 'rerank') return '排序结果'
  return '模型回复（截断）'
})

async function runTest(row) {
  testResult.value = null
  testType.value = row.model_type || 'chat'
  testVisible.value = true
  testing.value = true
  try {
    testResult.value = await testModelConfig(row.id)
  } catch (error) {
    // 网络层失败（超时 / 网关 502）也要给出可读结论，不能停在「正在测试…」
    testResult.value = {
      ok: false,
      model: row.model_config_key,
      latency_ms: 0,
      reply: '',
      error: error.message,
    }
  } finally {
    testing.value = false
  }
}
</script>

<style scoped lang="scss">
.model-toolbar {
  padding: 20px 26px 4px;
}

.model-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.model-key {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-color-primary);
}

.model-mask {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}

.model-muted {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.model-config-tip {
  margin: 0;
  max-width: 360px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.model-form__tip {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-secondary);

  &--inline {
    margin: 0 0 0 10px;
  }
}

.model-form__json {
  :deep(.el-textarea__inner) {
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 12px;
  }
}

.model-test {
  min-height: 160px;

  &__block {
    margin-top: 8px;
  }

  &__label {
    margin: 0 0 4px;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__value {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    word-break: break-word;

    &--error {
      color: var(--el-color-danger);
    }
  }
}
</style>
