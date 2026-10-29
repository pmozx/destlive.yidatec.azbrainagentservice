<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">AI 问答</h2>
        <p class="page-header__desc">
          对接 /api/v1/integration-chat：基于知识库的检索增强问答，走 POST + SSE 流式返回
          「思考阶段 → 逐字正文 → 引用来源 → 追问建议」；回答只用知识库内容，无依据时明确拒答而不是编造
        </p>
      </div>
    </div>

    <div class="chat-layout">
      <SectionCard class="chat-side" flush title="问答会话" :subtitle="`共 ${sessions.length} 个`">
        <template #extra>
          <el-button size="small" type="primary" @click="startNew">
            <el-icon><Plus /></el-icon>
            <span>新对话</span>
          </el-button>
        </template>

        <div v-loading="sessionLoading" class="chat-side__list">
          <div
            v-for="item in sessions"
            :key="item.id"
            class="session-item"
            :class="{ 'is-active': item.id === activeSessionId }"
            role="button"
            tabindex="0"
            @click="selectSession(item)"
            @keyup.enter="selectSession(item)"
          >
            <div class="session-item__head">
              <span class="session-item__title" :title="item.title">{{ item.title || '未命名会话' }}</span>
              <span class="session-item__actions">
                <el-button link size="small" @click.stop="renameSession(item)">改</el-button>
                <el-button link type="danger" size="small" @click.stop="removeSession(item)">删</el-button>
              </span>
            </div>
            <p class="session-item__meta">
              {{ item.scope_type === 2 ? `指定空间 ${(item.space_ids || []).length} 个` : '全部空间' }} ·
              {{ item.message_count || 0 }} 条消息
            </p>
          </div>
          <el-empty v-if="!sessions.length" description="还没有会话：直接提问即可创建" :image-size="70" />
        </div>
      </SectionCard>

      <SectionCard class="chat-main" flush>
        <template #title>
          <div class="chat-scope">
            <span class="chat-scope__label">问答范围</span>
            <el-radio-group v-model="scopeType" size="small" @change="onScopeTypeChange">
              <el-radio-button :value="1">全部空间</el-radio-button>
              <el-radio-button :value="2">指定空间</el-radio-button>
            </el-radio-group>
            <el-select
              v-if="scopeType === 2"
              v-model="spaceIds"
              multiple
              collapse-tags
              collapse-tags-tooltip
              size="small"
              placeholder="选择知识空间"
              style="width: 240px"
            >
              <el-option v-for="item in spaces" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </div>
        </template>
        <template #extra>
          <el-button size="small" :disabled="streaming || !messages.length" @click="startNew">清空当前对话</el-button>
        </template>

        <div ref="scrollRef" class="chat-messages">
          <div v-if="!messages.length" class="chat-welcome">
            <el-icon class="chat-welcome__icon"><ChatDotRound /></el-icon>
            <p class="chat-welcome__title">向知识库提问</p>
            <p class="chat-welcome__desc">
              当前范围：{{ scopeLabel }}。回答会附带引用来源，点击引用可查看原文片段与文档预览。
            </p>
            <div class="chat-welcome__samples">
              <el-button v-for="sample in SAMPLES" :key="sample" size="small" round @click="send(sample)">
                {{ sample }}
              </el-button>
            </div>
          </div>

          <div v-for="msg in messages" :key="msg.key" class="chat-message" :class="`is-${msg.role}`">
            <div class="chat-message__avatar">
              {{ msg.role === 'user' ? '我' : 'AI' }}
            </div>
            <div class="chat-message__body">
              <!-- 思考阶段：只在流式过程中展示，历史回显不重放 -->
              <div v-if="msg.reasoning?.length" class="chat-reasoning">
                <el-icon class="is-loading" v-if="msg.streaming"><Loading /></el-icon>
                <el-icon v-else><MagicStick /></el-icon>
                <span>{{ msg.reasoning.join(' · ') }}</span>
              </div>

              <template v-if="msg.role === 'user'">
                <div class="chat-bubble">{{ msg.content }}</div>
              </template>

              <template v-else>
                <el-alert
                  v-if="msg.error"
                  class="chat-alert"
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="msg.error"
                />
                <div v-if="msg.content" class="chat-answer">
                  <MarkdownView :content="msg.content" />
                </div>
                <div v-else-if="msg.streaming" class="chat-typing">正在检索与生成…</div>

                <div v-if="msg.cites?.length" class="chat-cites">
                  <span class="chat-cites__label">引用来源</span>
                  <button
                    v-for="(cite, index) in msg.cites"
                    :key="cite.chunkId"
                    class="chat-cite"
                    type="button"
                    @click="openCite(cite)"
                  >
                    <span class="chat-cite__index">{{ index + 1 }}</span>
                    <span class="chat-cite__doc">{{ cite.doc }}</span>
                    <span v-if="cite.page" class="chat-cite__page">P{{ cite.page }}</span>
                  </button>
                </div>

                <div v-if="msg.followUps?.length" class="chat-followups">
                  <span class="chat-followups__label">接着问</span>
                  <el-button v-for="item in msg.followUps" :key="item" size="small" round @click="send(item)">
                    {{ item }}
                  </el-button>
                </div>

                <div v-if="msg.disclaimer" class="chat-disclaimer">{{ msg.disclaimer }}</div>

                <div v-if="!msg.streaming" class="chat-message__footer">
                  <el-button
                    link
                    size="small"
                    :type="msg.feedback === 1 ? 'primary' : ''"
                    :disabled="!msg.messageId"
                    @click="feedback(msg, 1)"
                  >
                    <el-icon><Select /></el-icon>
                    <span>有用</span>
                  </el-button>
                  <el-button
                    link
                    size="small"
                    :type="msg.feedback === -1 ? 'danger' : ''"
                    :disabled="!msg.messageId"
                    @click="feedback(msg, -1)"
                  >
                    <el-icon><CloseBold /></el-icon>
                    <span>无用</span>
                  </el-button>
                  <span v-if="msg.notFound" class="chat-tag">未命中知识库</span>
                </div>
              </template>
            </div>
          </div>
        </div>

        <div class="chat-editor">
          <el-input
            v-model="question"
            type="textarea"
            :rows="2"
            resize="none"
            maxlength="2000"
            show-word-limit
            placeholder="输入问题，Enter 发送，Shift + Enter 换行"
            :disabled="streaming"
            @keydown.enter.exact.prevent="send()"
          />
          <div class="chat-editor__actions">
            <span class="chat-editor__hint">回答严格基于知识库，未命中会明确告知</span>
            <el-button v-if="streaming" size="small" @click="stopStream">
              <el-icon><VideoPause /></el-icon>
              <span>停止生成</span>
            </el-button>
            <el-button v-else size="small" type="primary" :disabled="!question.trim()" @click="send()">
              <el-icon><Promotion /></el-icon>
              <span>发送</span>
            </el-button>
          </div>
        </div>
      </SectionCard>
    </div>

    <!-- 引用原文预览：与知识中心一致的原件 / 解析产物双视图 -->
    <DocumentPreviewDrawer v-model="citeVisible" :doc-id="activeCite?.docId" :title="activeCite?.doc" />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  CHAT_STREAM_URL,
  createSession,
  deleteSessions,
  getChatScope,
  getSessionDetail,
  getSessionList,
  setMessageFeedback,
  updateSession,
} from '@/api'
import DocumentPreviewDrawer from '@/components/common/DocumentPreviewDrawer.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { streamPost } from '@/utils/sse'

defineOptions({ name: 'IntegrationChatView' })

/** 空库引导问题：覆盖「是什么 / 怎么办 / 流程类」三类常见问法 */
const SAMPLES = ['这个系统的核心能力有哪些？', '文档解析支持哪些格式？', '如何把新知识加入检索范围？']

// ── 问答范围 ───────────────────────────────────────────────────────────

const spaces = ref([])
const scopeType = ref(1)
const spaceIds = ref([])

const scopeLabel = computed(() => {
  if (scopeType.value === 1) return '全部启用空间'
  const names = spaces.value.filter((item) => spaceIds.value.includes(item.id)).map((item) => item.name)
  return names.length ? names.join('、') : '尚未选择空间'
})

async function loadScope() {
  try {
    spaces.value = (await getChatScope()) ?? []
    // 空间被删/停用后残留的选中项要清掉，否则会静默缩小检索范围
    const valid = new Set(spaces.value.map((item) => item.id))
    spaceIds.value = spaceIds.value.filter((id) => valid.has(id))
  } catch {
    spaces.value = []
  }
}

function onScopeTypeChange() {
  if (scopeType.value === 1) spaceIds.value = []
}

// ── 会话列表 ───────────────────────────────────────────────────────────

const sessions = ref([])
const sessionLoading = ref(false)
const activeSessionId = ref(null)

async function loadSessions() {
  sessionLoading.value = true
  try {
    const result = await getSessionList({ page_no: 1, page_size: 50 })
    sessions.value = result?.items ?? []
  } catch {
    sessions.value = []
  } finally {
    sessionLoading.value = false
  }
}

// ── 消息 ───────────────────────────────────────────────────────────────

const messages = ref([])
const question = ref('')
const streaming = ref(false)
const scrollRef = ref(null)

/** 历史消息 → 视图模型（历史不回放思考阶段，只还原结论与引用） */
function toView(item) {
  return reactive({
    key: `m-${item.id ?? Math.random().toString(36).slice(2)}`,
    messageId: item.id ?? 0,
    role: item.role === 'user' ? 'user' : 'assistant',
    content: item.content || '',
    cites: item.cites ?? [],
    followUps: item.follow_ups ?? [],
    disclaimer: item.disclaimer || '',
    notFound: Boolean(item.not_found),
    feedback: item.feedback ?? null,
    analysis: item.analysis ?? null,
    reasoning: [],
    streaming: false,
    error: '',
  })
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function selectSession(item) {
  if (streaming.value) stopStream()
  activeSessionId.value = item.id
  try {
    const detail = await getSessionDetail(item.id)
    messages.value = (detail?.messages ?? []).map(toView)
    // 回显时把会话里记录的范围带回来，便于继续追问同一范围
    if (detail?.session) {
      scopeType.value = detail.session.scope_type === 2 ? 2 : 1
      spaceIds.value = detail.session.space_ids ?? []
    }
    scrollToBottom()
  } catch {
    messages.value = []
  }
}

function startNew() {
  if (streaming.value) stopStream()
  activeSessionId.value = null
  messages.value = []
  question.value = ''
}

// ── 流式问答 ───────────────────────────────────────────────────────────

let currentStream = null
let activeReply = null

function send(text) {
  const content = (text ?? question.value).trim()
  if (!content || streaming.value) return
  if (scopeType.value === 2 && !spaceIds.value.length) {
    ElMessage.warning('请选择至少一个知识空间，或切回「全部空间」')
    return
  }

  // 无会话时先建会话：SSE 的 done 帧只带 messageId，发问前必须先把 session_id 拿到
  if (!activeSessionId.value) {
    void createSessionAndAsk(content)
    return
  }
  void ask(content)
}

async function createSessionAndAsk(content) {
  try {
    const created = await createSession({
      title: content.slice(0, 20),
      scope_type: scopeType.value,
      space_ids: scopeType.value === 2 ? [...spaceIds.value] : undefined,
    })
    activeSessionId.value = created?.id ?? null
    await loadSessions()
  } catch {
    return
  }
  void ask(content)
}

function ask(content) {
  question.value = ''

  const userMessage = reactive({
    key: `u-${Date.now()}`,
    messageId: 0,
    role: 'user',
    content,
    cites: [],
    followUps: [],
    disclaimer: '',
    notFound: false,
    feedback: null,
    reasoning: [],
    streaming: false,
    error: '',
  })
  const reply = reactive({
    key: `a-${Date.now()}`,
    messageId: 0,
    role: 'assistant',
    content: '',
    cites: [],
    followUps: [],
    disclaimer: '',
    notFound: false,
    feedback: null,
    analysis: null,
    reasoning: [],
    streaming: true,
    error: '',
  })

  messages.value.push(userMessage, reply)
  activeReply = reply
  streaming.value = true
  scrollToBottom()

  currentStream = streamPost(
    CHAT_STREAM_URL,
    {
      question: content,
      session_id: activeSessionId.value,
      scope_type: scopeType.value,
      space_ids: scopeType.value === 2 ? [...spaceIds.value] : undefined,
    },
    {
      onEvent: (name, data) => handleEvent(name, data, reply),
      onError: (message) => {
        reply.error = message
        reply.streaming = false
        streaming.value = false
        scrollToBottom()
      },
      onClose: () => {
        reply.streaming = false
        streaming.value = false
        currentStream = null
        activeReply = null
        // 会话标题/消息数在服务端更新，收流后刷新一次侧栏
        void loadSessions()
      },
    },
  )
}

/** 帧处理：与后端 pipeline 的帧序契约一一对应 */
function handleEvent(name, data, reply) {
  switch (name) {
    case 'reasoning': {
      if (data.optimized) reply.analysis = { ...(reply.analysis || {}), optimized: data.optimized }
      if (data.text && !reply.reasoning.includes(data.text)) reply.reasoning.push(data.text)
      break
    }
    case 'answer':
      reply.content += data.delta || ''
      break
    case 'sources':
      reply.cites = data.cites ?? []
      break
    case 'followups':
      reply.followUps = data.questions ?? []
      break
    case 'error':
      // notFound 属业务结果（后端已把拒答文案落库），其余是故障
      if (data.code === 'notFound') {
        reply.notFound = true
        reply.content = data.message || ''
      } else {
        reply.error = data.message || '问答失败，请稍后重试'
      }
      break
    case 'done':
      reply.messageId = data.messageId ?? 0
      reply.streaming = false
      streaming.value = false
      break
    default:
      break
  }
  scrollToBottom()
}

function stopStream() {
  currentStream?.abort()
  currentStream = null
  if (activeReply) {
    activeReply.streaming = false
    activeReply.error = '已停止生成（本条回答不会写入历史）'
  }
  activeReply = null
  streaming.value = false
}

async function feedback(message, value) {
  if (!message.messageId) {
    ElMessage.warning('本条回答未落库，无法提交反馈')
    return
  }
  try {
    await setMessageFeedback(message.messageId, value)
    message.feedback = value
    ElMessage.success(value === 1 ? '已标记为有用' : '已标记为无用，谢谢反馈')
  } catch {
    // 请求拦截器已统一提示
  }
}

// ── 会话重命名 / 删除 ──────────────────────────────────────────────────

async function renameSession(item) {
  let title = ''
  try {
    const result = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
      inputValue: item.title || '',
      inputValidator: (value) => (value && value.trim() ? true : '标题不能为空'),
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    title = result.value.trim()
  } catch {
    return
  }

  try {
    await updateSession(item.id, title)
    item.title = title
    ElMessage.success('重命名成功')
  } catch {
    // 请求拦截器已统一提示
  }
}

async function removeSession(item) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${item.title || '未命名会话'}」吗？会话内的 ${item.message_count || 0} 条消息会一并清理。`,
      '删除会话确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteSessions([item.id])
    ElMessage.success('删除成功')
    if (activeSessionId.value === item.id) startNew()
    await loadSessions()
  } catch {
    // 请求拦截器已统一提示
  }
}

// ── 引用预览 ───────────────────────────────────────────────────────────

const citeVisible = ref(false)
const activeCite = ref(null)

/** 点引用：把文档 ID 与标题交给公共预览抽屉（原件 / 解析产物在抽屉内切换） */
function openCite(cite) {
  activeCite.value = cite
  citeVisible.value = true
}

onMounted(async () => {
  await Promise.all([loadScope(), loadSessions()])
})

onUnmounted(() => {
  currentStream?.abort()
})
</script>

<style scoped lang="scss">
.chat-layout {
  display: grid;
  grid-template-columns: 272px minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
  min-height: 640px;
  height: calc(100vh - 240px);

  > * {
    min-width: 0;
  }
}

// ── 会话侧栏 ───────────────────────────────────────────────────────────

.chat-side {
  :deep(.section-card__body) {
    height: 100%;
    min-height: 0;
  }

  &__list {
    height: 100%;
    padding: 4px 10px 10px;
    overflow-y: auto;
  }
}

.session-item {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--app-border-color);
  border-radius: 12px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;

  &:first-child {
    margin-top: 0;
  }

  &.is-active {
    border-color: var(--el-color-primary);
    background-color: var(--el-color-primary-light-9);
  }

  &__head {
    @include flex($justify: space-between, $align: center, $gap: 8px);
  }

  &__title {
    @include ellipsis;
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__actions {
    flex-shrink: 0;
  }

  &__meta {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

// ── 对话主区 ───────────────────────────────────────────────────────────

.chat-main {
  @include flex($direction: column);

  :deep(.section-card__body) {
    @include flex($direction: column);
    flex: 1;
    min-height: 0;
    padding-bottom: 0;
  }
}

.chat-scope {
  @include flex($align: center, $gap: 10px);
  flex-wrap: wrap;

  &__label {
    font-size: 15px;
    font-weight: 600;
    color: var(--app-text-primary);
  }
}

.chat-messages {
  flex: 1;
  min-height: 0;
  padding-right: 6px;
  overflow-y: auto;
}

.chat-welcome {
  @include flex($direction: column, $align: center, $gap: 10px);
  padding: 70px 20px;

  &__icon {
    font-size: 44px;
    color: var(--el-color-primary);
  }

  &__title {
    font-size: 16px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__desc {
    max-width: 520px;
    font-size: 13px;
    line-height: 1.7;
    text-align: center;
    color: var(--app-text-secondary);
  }

  &__samples {
    @include flex($justify: center, $gap: 8px);
    flex-wrap: wrap;
  }
}

.chat-message {
  @include flex($align: flex-start, $gap: 10px);
  margin-bottom: 18px;

  &__avatar {
    @include flex($justify: center, $align: center);
    flex-shrink: 0;
    width: 30px;
    height: 30px;
    font-size: 12px;
    font-weight: 600;
    color: #fff;
    background-color: var(--app-text-secondary);
    border-radius: 50%;
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__footer {
    @include flex($align: center, $gap: 6px);
    margin-top: 8px;
  }

  &.is-user {
    .chat-message__avatar {
      background-color: var(--el-color-primary);
    }

    .chat-message__body {
      @include flex($direction: column, $align: flex-end);
    }
  }
}

.chat-bubble {
  display: inline-block;
  max-width: 82%;
  padding: 9px 14px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--app-text-primary);
  background-color: var(--el-color-primary-light-9);
  border-radius: 12px 2px 12px 12px;
}

.chat-reasoning {
  @include flex($align: center, $gap: 6px);
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.chat-answer {
  padding: 12px 16px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 2px 12px 12px 12px;
}

.chat-typing {
  font-size: 13px;
  color: var(--app-text-secondary);
}

.chat-alert {
  margin-bottom: 8px;
}

.chat-cites {
  @include flex($align: center, $gap: 6px);
  flex-wrap: wrap;
  margin-top: 10px;

  &__label {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.chat-cite {
  @include flex($align: center, $gap: 6px);
  padding: 3px 10px 3px 4px;
  font-size: 12px;
  color: var(--app-text-primary);
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 999px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;

  &:hover {
    border-color: var(--el-color-primary);
    background-color: var(--el-color-primary-light-9);
  }

  &__index {
    @include flex($justify: center, $align: center);
    width: 18px;
    height: 18px;
    font-size: 11px;
    color: #fff;
    background-color: var(--el-color-primary);
    border-radius: 50%;
  }

  &__doc {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__page {
    color: var(--app-text-secondary);
  }
}

.chat-followups {
  @include flex($align: center, $gap: 6px);
  flex-wrap: wrap;
  margin-top: 10px;

  &__label {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.chat-disclaimer {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-muted);
}

.chat-tag {
  font-size: 12px;
  color: var(--app-text-secondary);
}

// ── 输入区 ─────────────────────────────────────────────────────────────

.chat-editor {
  flex-shrink: 0;
  padding: 12px 0 16px;
  border-top: 1px solid var(--el-border-color-lighter);

  &__actions {
    @include flex($justify: flex-end, $align: center, $gap: 10px);
    margin-top: 8px;
  }

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

@media (max-width: 1100px) {
  .chat-layout {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
  }

  .chat-messages {
    max-height: 60vh;
  }
}
</style>
