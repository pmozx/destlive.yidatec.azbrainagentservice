<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">AI 对话</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/chat/ask/stream：以指定用户身份走「授权 → 自然语言检索 → 生成」，
          回答按 SSE 逐字流式回显（首字前先给检索/生成进度，收尾帧回填落库结果与引用）。这里产生的会话与消息会同步出现在「会话记录」页
        </p>
      </div>
      <div class="page-header__actions">
        <el-tag :type="channel.embedding_ready ? 'success' : 'warning'" size="small" effect="light">
          向量化 {{ channel.embedding_ready ? '已接入' : '未接入（问答依赖它，会报 503）' }}
        </el-tag>
        <el-button size="small" @click="newConversation">
          <el-icon><Plus /></el-icon>
          <span>新建对话</span>
        </el-button>
      </div>
    </div>

    <div class="ai-chat">
      <aside class="ai-chat__side">
        <div class="ai-chat__identity">
          <span class="ai-chat__identity-label">用户 ID</span>
          <el-input
            v-model="form.user_id"
            size="small"
            placeholder="数字用户主键（如 1）"
            clearable
            @change="onIdentityChange"
          />
        </div>
        <p class="ai-chat__hint">会话归属与检索授权都按这个用户算：非数字主键会得到空授权（0 命中）</p>

        <div v-loading="sessionLoading" class="ai-chat__sessions">
          <button
            v-for="item in sessions"
            :key="item.session_id"
            type="button"
            class="ai-chat__session"
            :class="{ 'is-active': item.session_id === activeSessionId }"
            @click="selectSession(item)"
          >
            <span class="ai-chat__session-title">{{ item.title || item.session_id }}</span>
            <span class="ai-chat__session-meta">
              {{ item.message_count ?? 0 }} 条 · {{ formatDateTime(item.last_active_at) }}
            </span>
          </button>
          <el-empty v-if="!sessions.length" description="该用户还没有会话" :image-size="60" />
        </div>
      </aside>

      <section class="ai-chat__main">
        <header class="ai-chat__bar">
          <span class="ai-chat__bar-title">{{ activeSession?.title || '新对话' }}</span>
          <div class="ai-chat__bar-meta">
            <el-tag v-if="activeSessionId" size="small" effect="plain">{{ activeSessionId }}</el-tag>
            <el-button v-if="activeSessionId" link type="primary" size="small" :disabled="sending" @click="reloadMessages">重新加载</el-button>
          </div>
        </header>

        <div ref="messageBox" class="ai-chat__messages">
          <el-empty
            v-if="!messages.length"
            :description="emptyText"
            :image-size="80"
          />
          <div v-for="(item, index) in messages" :key="item.key || index" class="bubble" :class="`bubble--${item.role}`">
            <div class="bubble__role">{{ item.role === 'user' ? '你' : 'AI' }}</div>
            <div class="bubble__body">
              <MarkdownView v-if="item.role === 'assistant' && item.content" :content="item.content" />
              <p v-else-if="item.role === 'assistant'" class="bubble__text is-typing">{{ stageText(item) }}</p>
              <p v-else class="bubble__text">{{ item.content }}</p>
            </div>
            <div v-if="item.status === 'failed'" class="bubble__failed">
              <span>{{ item.error?.message || '本次提问失败，未落库' }}</span>
              <el-button v-if="item.error?.retryable !== false" link type="primary" size="small" @click="retryTurn(item)">
                重试
              </el-button>
            </div>
            <div v-else-if="item.status === 'stopped'" class="bubble__failed">
              <span>已停止（本轮未落库）</span>
              <el-button link type="primary" size="small" @click="retryTurn(item)">重新提问</el-button>
            </div>
            <div v-else-if="item.role === 'assistant' && item.status === 'done'" class="bubble__meta">
              <el-tag v-if="isNoEvidence(item)" type="warning" size="small" effect="light">证据不足</el-tag>
              <span v-if="item.finish_reason">finish_reason={{ item.finish_reason }}</span>
              <span v-if="item.elapsed_ms != null">耗时 {{ item.elapsed_ms }} ms</span>
            </div>
            <el-collapse v-if="(item.citations || []).length" class="bubble__citations">
              <el-collapse-item :title="`引用 ${item.citations.length} 条`" :name="`cite-${index}`">
                <div v-for="citation in item.citations" :key="citation.citation_id" class="cite">
                  <p class="cite__head">
                    [{{ citation.index }}] {{ citation.resource_type }} · {{ citation.publication_id }}
                  </p>
                  <p class="cite__quote">{{ citation.quote }}</p>
                  <p class="cite__meta">
                    chunk {{ citation.chunk_id }} · record {{ citation.record_id }} · v{{ citation.revision }}
                  </p>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>

        <div class="ai-chat__composer">
          <el-collapse v-model="advancedOpen" class="ai-chat__advanced">
            <el-collapse-item title="高级设置（提示词 / 租户 / token 上限）" name="advanced">
              <el-form label-width="88px" size="small" @submit.prevent>
                <el-form-item label="系统提示词">
                  <el-input
                    v-model="advanced.prompt_text"
                    type="textarea"
                    :rows="3"
                    maxlength="4000"
                    placeholder="留空使用后端默认提示词；这里只写业务边界，引用格式与「没证据就拒答」由后端追加"
                  />
                </el-form-item>
                <el-form-item label="租户 ID">
                  <el-input v-model="advanced.tenant_id" placeholder="gsi-default" />
                </el-form-item>
                <el-form-item label="token 上限">
                  <el-input-number v-model="advanced.max_output_tokens" :min="1" :max="8192" :step="128" controls-position="right" />
                </el-form-item>
              </el-form>
            </el-collapse-item>
          </el-collapse>

          <el-input
            v-model="draft"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            resize="none"
            placeholder="提问：例如 差旅报销的住宿标准是多少？（Enter 发送，Shift+Enter 换行）"
            @keydown="onKeydown"
          />
          <div class="ai-chat__composer-actions">
            <span class="ai-chat__composer-hint">回答只依据检索到的切片，证据不足时明确拒答</span>
            <div class="ai-chat__composer-buttons">
              <el-button v-if="sending" plain @click="stopStreaming">停止</el-button>
              <el-button v-else type="primary" :disabled="!draft.trim()" @click="send">发送</el-button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { askKnowledgeChatStream } from '@/api/module_kb/aiChat'
import { getChatMessageList, getChatSessionList } from '@/api/module_kb/chatRecord'
import { getRetrievalConfig } from '@/api/module_kb/retrieval'
import MarkdownView from '@/components/common/MarkdownView.vue'
import { formatDateTime } from '@/utils/format'
import { isAbortError } from '@/utils/sse'

defineOptions({ name: 'KbAiChatView' })

/** 用户 ID 记在本地：调试同一个账号时不必每次重填 */
const USER_ID_KEY = 'kb.aiChat.userId'

const form = reactive({ user_id: localStorage.getItem(USER_ID_KEY) || '1' })
const advanced = reactive({ prompt_text: '', tenant_id: 'gsi-default', max_output_tokens: undefined })
const advancedOpen = ref([])
const channel = ref({})
const draft = ref('')
const sending = ref(false)
const sessionLoading = ref(false)
const sessions = ref([])
const activeSessionId = ref('')
const messages = ref([])
const messageBox = ref(null)

/** 增量帧合并间隔：每片都重渲染 Markdown 会把主线程打满（规范 S32） */
const RENDER_INTERVAL = 80
/** 超过 3× 心跳间隔没有收到任何帧就判连接已死（规范 S21；后端 ping 间隔 15s） */
const IDLE_LIMIT = 45000

/**
 * 当前这一轮的流状态（同一时刻只允许一条流）。
 * 不用 reactive：这些都是传输过程量，只改 `reply` 上的字段触发渲染。
 */
const stream = {
  reply: null,
  userKey: '',
  text: '',
  timer: null,
  idleTimer: null,
  controller: null,
  settled: false,
  finished: false,
  stoppedByUser: false,
  timedOut: false,
}

const activeSession = computed(() => sessions.value.find((item) => item.session_id === activeSessionId.value) || null)
const emptyText = computed(() =>
  activeSessionId.value ? '该会话没有可用消息（可能已清空）' : '直接提问即可开始新对话，会话会在首轮提问后创建',
)

/** 助手消息没有引用即当时的「证据不足」：与后端 no_evidence 同一判定（不另存列） */
function isNoEvidence(item) {
  return item.no_evidence === true || (item.role === 'assistant' && !(item.citations || []).length)
}

function scrollToBottom() {
  nextTick(() => {
    const box = messageBox.value
    if (box) box.scrollTop = box.scrollHeight
  })
}

async function loadChannel() {
  try {
    channel.value = (await getRetrievalConfig()) || {}
  } catch {
    channel.value = {}
  }
}

// ── 会话列表 ──────────────────────────────────────────────────────
async function loadSessions() {
  if (!form.user_id.trim()) {
    sessions.value = []
    return
  }
  sessionLoading.value = true
  try {
    const data = await getChatSessionList({ page_no: 1, page_size: 50, user_id: form.user_id.trim() })
    // 已删除的会话不再出现在 AI 对话的左侧列表里（管理端「会话记录」仍可查到）
    sessions.value = (data.items || []).filter((item) => item.status !== 'deleted')
    if (activeSessionId.value && !sessions.value.some((item) => item.session_id === activeSessionId.value)) {
      activeSessionId.value = ''
    }
  } catch {
    sessions.value = []
  } finally {
    sessionLoading.value = false
  }
}

async function onIdentityChange() {
  localStorage.setItem(USER_ID_KEY, form.user_id.trim())
  activeSessionId.value = ''
  messages.value = []
  sessions.value = []
  await loadSessions()
}

function selectSession(item) {
  if (sending.value || item.session_id === activeSessionId.value) return
  activeSessionId.value = item.session_id
  reloadMessages()
}

function newConversation() {
  if (sending.value) return
  activeSessionId.value = ''
  messages.value = []
  draft.value = ''
}

async function reloadMessages() {
  if (!activeSessionId.value || sending.value) return
  try {
    const data = await getChatMessageList({ page_no: 1, page_size: 100, session_id: activeSessionId.value })
    messages.value = (data.items || [])
      .filter((item) => item.status !== 'deleted')
      .map((item) => ({
        key: item.uuid,
        role: item.role,
        content: item.content,
        citations: item.citations || [],
        status: 'done',
      }))
    if ((data.total || 0) > messages.value.length) {
      ElMessage.info('该会话消息较多，仅显示最早 100 条')
    }
    scrollToBottom()
  } catch {
    messages.value = []
  }
}

// ── 提问 ─────────────────────────────────────────────────────────
function onKeydown(event) {
  // 输入法组合中的回车是在确认候选词，不能当发送
  if (event.isComposing || event.shiftKey || event.key !== 'Enter') return
  event.preventDefault()
  send()
}

/** 占位气泡的进度文案：meta 到达前不显示「空回答」，靠 stage 帧给反馈（规范 S33） */
function stageText(item) {
  if (item.stage === 'generating') return '正在生成回答…'
  if (item.stage === 'retrieving') return '正在检索知识…'
  return '正在连接…'
}

/** 节流渲染：合并 RENDER_INTERVAL 内的增量帧（规范 S32） */
function paintStream() {
  stream.timer = null
  if (!stream.reply) return
  stream.reply.content = stream.text
  scrollToBottom()
}

function schedulePaint() {
  if (stream.timer) return
  stream.timer = setTimeout(paintStream, RENDER_INTERVAL)
}

/** 无帧看门狗：超时即主动 abort，不让用户对着一个永不动的气泡等（规范 S21） */
function touchIdle() {
  if (stream.idleTimer) clearTimeout(stream.idleTimer)
  stream.idleTimer = setTimeout(() => {
    stream.timedOut = true
    stream.controller?.abort()
  }, IDLE_LIMIT)
}

function clearTimers() {
  if (stream.timer) clearTimeout(stream.timer)
  if (stream.idleTimer) clearTimeout(stream.idleTimer)
  stream.timer = null
  stream.idleTimer = null
}

/** 失败（error 帧 / 首帧前的 4xx、5xx）：服务端一条都没写，撤掉半截正文 */
function failReply(message, retryable = true) {
  clearTimers()
  stream.settled = true
  const reply = stream.reply
  if (reply) {
    reply.content = ''
    reply.status = 'failed'
    reply.error = { message, retryable }
  }
  ElMessage.error(message)
}

/** 中断（用户点停止 / 连接意外结束）：保留已经流出来的正文，并说明本轮未落库 */
function markStopped() {
  clearTimers()
  stream.settled = true
  const reply = stream.reply
  if (!reply || reply.status !== 'streaming') return
  reply.content = stream.text
  if (stream.stoppedByUser) {
    reply.status = 'stopped'
    return
  }
  reply.status = 'failed'
  reply.error = {
    message: stream.timedOut ? '长时间没有收到服务端的任何帧，连接已超时' : '连接意外中断，本轮未落库',
    retryable: true,
  }
}

/** 一帧的语义只在这里解释，页面不关心传输细节（规范 S11 / S31） */
function onFrame({ event, data }) {
  touchIdle()
  const reply = stream.reply
  if (!reply) return

  if (event === 'meta') {
    activeSessionId.value = data.session_id
  } else if (event === 'stage') {
    reply.stage = data.phase
  } else if (event === 'delta') {
    stream.text += data.text || ''
    schedulePaint()
  } else if (event === 'citations') {
    reply.citations = data.citations || []
  } else if (event === 'done') {
    // done.answer 是落库终稿：流期间的半截正文一律以它覆盖（规范 S14 / S31）
    clearTimers()
    stream.settled = true
    stream.finished = true
    stream.text = data.answer
    reply.content = data.answer
    reply.key = data.assistant_message_id
    reply.citations = reply.citations || []
    reply.no_evidence = data.no_evidence
    reply.finish_reason = data.finish_reason
    reply.elapsed_ms = data.elapsed_ms
    reply.stage = ''
    reply.status = 'done'
    const pending = messages.value.find((item) => item.key === stream.userKey)
    if (pending) pending.key = data.user_message_id
    activeSessionId.value = data.session_id
  } else if (event === 'error') {
    failReply(data.message || '本次提问失败，未落库', data.retryable !== false)
  }
}

async function send() {
  const question = draft.value.trim()
  if (!question || sending.value) return
  if (!form.user_id.trim()) {
    ElMessage.warning('请先填写用户 ID：检索授权与会话归属都按它算')
    return
  }

  const stamp = Date.now()
  const userKey = `temp-user-${stamp}`
  // 用户消息与助手占位气泡一起入列：占位气泡本身就是「本轮正在进行」的状态（规范 S33）
  messages.value.push({ key: userKey, role: 'user', content: question, status: 'done' })
  messages.value.push({
    key: `temp-reply-${stamp}`,
    role: 'assistant',
    content: '',
    citations: [],
    status: 'streaming',
    stage: '',
    question,
  })
  Object.assign(stream, {
    reply: messages.value[messages.value.length - 1],
    userKey,
    text: '',
    timer: null,
    idleTimer: null,
    controller: new AbortController(),
    settled: false,
    finished: false,
    stoppedByUser: false,
    timedOut: false,
  })
  draft.value = ''
  sending.value = true
  scrollToBottom()
  touchIdle()

  try {
    await askKnowledgeChatStream(
      {
        session_id: activeSessionId.value || undefined,
        user_id: form.user_id.trim(),
        tenant_id: advanced.tenant_id.trim() || undefined,
        question,
        prompt_text: advanced.prompt_text.trim() || undefined,
        max_output_tokens: advanced.max_output_tokens || undefined,
      },
      { signal: stream.controller.signal, onEvent: onFrame },
    )
    // 收完既没有 done 也没有 error：按中断处理，不能把「没答完」显示成「答完了」
    if (!stream.settled) markStopped()
  } catch (error) {
    if (isAbortError(error)) {
      markStopped()
    } else {
      // 首帧前的拒绝（400/404/409/422）重问也没用，其余按可重试处理
      const retryable = ![400, 404, 409, 422].includes(error.status)
      failReply(error.message || '流式请求失败，请稍后重试', retryable)
    }
  } finally {
    const finished = stream.finished
    clearTimers()
    stream.controller = null
    stream.reply = null
    sending.value = false
    scrollToBottom()
    // 首轮提问后标题由问题派生、活跃时间也变了：刷新左侧列表（不打断当前会话）
    if (finished) loadSessions()
  }
}

/** 「停止」：主动断流，服务端断连即停止消费与落库（规范 S24） */
function stopStreaming() {
  stream.stoppedByUser = true
  stream.controller?.abort()
}

/** 重试：这一轮没落库，连同用户消息一起撤掉重问，避免同一问题在页面上出现两次 */
function retryTurn(item) {
  if (sending.value) return
  const index = messages.value.indexOf(item)
  if (index < 0) return
  let start = index
  for (let i = index - 1; i >= 0; i -= 1) {
    if (messages.value[i].role === 'user') {
      start = i
      break
    }
  }
  messages.value.splice(start, index - start + 1)
  draft.value = item.question || ''
  send()
}

onBeforeUnmount(() => {
  stream.stoppedByUser = true
  stream.controller?.abort()
  clearTimers()
})

onMounted(async () => {
  await loadChannel()
  await loadSessions()
})
</script>

<style scoped lang="scss">
.ai-chat {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: var(--app-gap);
}

.ai-chat__side {
  @include panel;
  @include flex($direction: column, $gap: 10px);
  padding: 18px 16px;
  min-height: 560px;
  max-height: 720px;
}

.ai-chat__identity {
  @include flex($direction: column, $gap: 6px, $align: stretch);

  &-label {
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.ai-chat__hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-muted);
}

.ai-chat__sessions {
  @include flex($direction: column, $gap: 6px);
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 2px;
}

.ai-chat__session {
  @include flex($direction: column, $gap: 2px, $align: stretch);
  width: 100%;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
  background-color: transparent;
  border: 1px solid transparent;
  border-radius: var(--app-radius-sm);
  transition: background-color 0.16s ease;

  &:hover {
    background-color: var(--app-tint-bg);
  }

  &.is-active {
    background-color: var(--app-tint-bg);
    border-color: var(--app-border-strong);
  }

  &-title {
    @include ellipsis;
    font-size: 13px;
    color: var(--app-text-primary);
  }

  &-meta {
    @include ellipsis;
    font-size: 11px;
    color: var(--app-text-muted);
  }
}

.ai-chat__main {
  @include panel;
  @include flex($direction: column);
  min-height: 560px;
  max-height: 720px;
  overflow: hidden;
}

.ai-chat__bar {
  @include flex($justify: space-between, $gap: 12px);
  padding: 14px 22px;
  border-bottom: 1px solid var(--app-border-color);

  &-title {
    @include ellipsis;
    font-size: 14px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &-meta {
    @include flex($gap: 8px);
    flex-shrink: 0;
  }
}

.ai-chat__messages {
  flex: 1;
  min-height: 0;
  padding: 18px 22px;
  overflow-y: auto;
  background-color: var(--app-tint-bg);
}

.bubble {
  @include flex($direction: column, $gap: 6px, $align: flex-start);
  max-width: 86%;
  margin-bottom: 16px;

  &--user {
    margin-left: auto;
    align-items: flex-end;
  }

  &__role {
    font-size: 11px;
    color: var(--app-text-muted);
  }

  &__body {
    padding: 10px 14px;
    background-color: var(--app-card-bg);
    border-radius: var(--app-radius);
    box-shadow: var(--app-shadow-soft);
  }

  &__text {
    margin: 0;
    font-size: 14px;
    line-height: 1.7;
    color: var(--app-text-primary);
    white-space: pre-wrap;
    overflow-wrap: break-word;

    &.is-typing {
      color: var(--app-text-secondary);
    }
  }

  &__meta {
    @include flex($gap: 10px);
    flex-wrap: wrap;
    font-size: 11px;
    color: var(--app-text-muted);
  }

  &__failed {
    @include flex($gap: 8px);
    flex-wrap: wrap;
    font-size: 11px;
    color: var(--el-color-danger);
  }

  &__citations {
    width: 100%;
    border: none;
  }
}

.cite {
  padding: 6px 0;
  border-top: 1px dashed var(--app-border-color);

  &:first-child {
    border-top: none;
  }

  &__head {
    margin: 0 0 4px;
    font-size: 12px;
    font-weight: 600;
    color: var(--app-text-regular);
  }

  &__quote {
    margin: 0;
    font-size: 12px;
    line-height: 1.7;
    color: var(--app-text-primary);
    white-space: pre-wrap;
  }

  &__meta {
    margin: 4px 0 0;
    font-size: 11px;
    color: var(--app-text-muted);
    word-break: break-all;
  }
}

.ai-chat__composer {
  @include flex($direction: column, $gap: 10px);
  padding: 14px 22px 18px;
  border-top: 1px solid var(--app-border-color);
  background-color: var(--app-card-bg);

  &-actions {
    @include flex($justify: space-between, $gap: 12px);
  }

  &-hint {
    font-size: 12px;
    color: var(--app-text-muted);
  }

  &-buttons {
    @include flex($gap: 8px);
    flex-shrink: 0;
  }
}

.ai-chat__advanced {
  border: none;

  :deep(.el-collapse-item__header),
  :deep(.el-collapse-item__wrap) {
    background-color: transparent;
    border-bottom: none;
  }

  :deep(.el-collapse-item__header) {
    height: auto;
    padding: 4px 0;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

@media (max-width: 1180px) {
  .ai-chat {
    grid-template-columns: minmax(0, 1fr);
  }

  .ai-chat__side {
    min-height: 0;
    max-height: 240px;
  }
}
</style>
