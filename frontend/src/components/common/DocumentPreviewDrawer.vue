<!-- 文档预览抽屉：原件 / 解析产物双视图（知识文档、知识中心、问答引用共用） -->
<template>
  <el-drawer v-model="visible" :title="drawerTitle" size="62%" destroy-on-close>
    <div v-loading="loading" class="doc-preview">
      <div class="doc-preview__head">
        <el-radio-group v-model="activeView" size="small">
          <el-radio-button value="source">原件</el-radio-button>
          <el-radio-button value="parsed">解析产物</el-radio-button>
        </el-radio-group>
        <span class="doc-preview__file">{{ fileHint }}</span>
      </div>

      <div class="doc-preview__body">
        <template v-if="detail">
          <!-- 原件：pdf / 纯文本走 iframe，图片走 <img>（浏览器原生缩放） -->
          <template v-if="activeView === 'source'">
            <iframe
              v-if="detail.source_type === 'pdf' || detail.source_type === 'text'"
              class="doc-preview__frame"
              :src="sourceUrl"
              :title="`${detail.file_name || '文档'}原件预览`"
            />
            <img
              v-else-if="detail.source_type === 'image'"
              class="doc-preview__image"
              :src="sourceUrl"
              :alt="detail.title"
            />
            <el-alert
              v-else
              type="info"
              :closable="false"
              show-icon
              :title="detail.source_message || '该类型暂不支持在线预览原件'"
            />
          </template>

          <!-- 解析产物：full.md（内嵌图片引用已由服务端重写为读取接口地址） -->
          <template v-else>
            <MarkdownView v-if="detail.parsed_type === 'markdown'" :content="detail.markdown || ''" />
            <el-alert
              v-else
              type="info"
              :closable="false"
              show-icon
              :title="detail.parsed_message || '暂无解析产物'"
            />
          </template>
        </template>
        <el-empty v-else-if="!loading" description="暂无预览内容" :image-size="80" />
      </div>
    </div>

    <template #footer>
      <el-button size="small" :disabled="!docId" @click="downloadSource">下载原件</el-button>
      <el-button size="small" @click="visible = false">关闭</el-button>
    </template>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

import { documentSourceUrl, getDocumentPreview } from '@/api'
import MarkdownView from '@/components/common/MarkdownView.vue'

defineOptions({ name: 'DocumentPreviewDrawer' })

const props = defineProps({
  /** 抽屉开关（v-model） */
  modelValue: { type: Boolean, default: false },
  /** 文档ID：变化即重新拉取预览信息 */
  docId: { type: [Number, String], default: null },
  /** 标题兜底（行标题 / 引用卡片标题，接口失败时仍可显示） */
  title: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const loading = ref(false)
const detail = ref(null)
/** 当前视图：默认由后端 preview_type 决定（PDF/图片/文本看原件，其余看解析产物） */
const activeView = ref('source')

const drawerTitle = computed(() => detail.value?.title || props.title || '文档预览')
const sourceUrl = computed(() => (props.docId ? documentSourceUrl(props.docId) : ''))
const fileHint = computed(() => detail.value?.file_name || '')

async function load() {
  if (!props.docId) return
  loading.value = true
  detail.value = null
  try {
    // quiet：失败原因在抽屉内提示，不再额外弹一行全局错误
    detail.value = await getDocumentPreview(props.docId)
    activeView.value = detail.value.preview_type === 'markdown' ? 'parsed' : 'source'
  } catch (error) {
    const message = error?.message || '预览加载失败'
    // 两侧都不可用时仍保留抽屉结构：用户可切 tab 看到各自的原因
    detail.value = {
      title: props.title,
      source_type: 'unsupported',
      source_message: message,
      parsed_type: 'unsupported',
      parsed_message: message,
    }
    activeView.value = 'source'
  } finally {
    loading.value = false
  }
}

/** 下载原件：走 source 接口的附件形态，不占内存也不阻塞抽屉 */
function downloadSource() {
  if (!props.docId) return
  const link = document.createElement('a')
  link.href = documentSourceUrl(props.docId, true)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

watch(
  () => [props.modelValue, props.docId],
  ([isOpen]) => {
    if (isOpen) void load()
  },
  { immediate: true },
)
</script>

<style scoped lang="scss">
.doc-preview {
  display: flex;
  flex-direction: column;
  height: 100%;

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }

  &__file {
    overflow: hidden;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__body {
    flex: 1;
    overflow: auto;
  }

  &__frame {
    width: 100%;
    height: 70vh;
    border: none;
  }

  &__image {
    display: block;
    max-width: 100%;
    max-height: 70vh;
    margin: 0 auto;
    border-radius: 8px;
  }
}
</style>
