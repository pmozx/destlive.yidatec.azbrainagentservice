<template>
  <div class="json-viewer">
    <div class="json-viewer__toolbar">
      <span class="json-viewer__title">{{ title }}</span>
      <el-button link type="primary" :disabled="!text" @click="copy">
        <el-icon><DocumentCopy /></el-icon>
        <span>复制</span>
      </el-button>
    </div>
    <pre class="json-viewer__body"><code>{{ text || placeholder }}</code></pre>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

import { prettyJson, prettyJsonString } from '@/utils/format'

defineOptions({ name: 'JsonViewer' })

const props = defineProps({
  data: { type: null, default: null },
  title: { type: String, default: '响应报文' },
  placeholder: { type: String, default: '暂无数据' },
  maxHeight: { type: String, default: '320px' },
})

const text = computed(() => {
  if (props.data === null || props.data === undefined) return ''
  // 历史审计行与部分契约场景存的是 JSON 字符串，能与对象一样展开才能读
  if (typeof props.data === 'string') return prettyJsonString(props.data)
  return prettyJson(props.data)
})

async function copy() {
  try {
    await navigator.clipboard.writeText(text.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('当前浏览器不支持自动复制，请手动选择内容')
  }
}
</script>

<style scoped lang="scss">
.json-viewer {
  @include flex($direction: column, $align: stretch);
  border: 1px solid var(--app-border-color);
  border-radius: 10px;
  overflow: hidden;
  background-color: rgba(120, 140, 175, 0.04);

  &__toolbar {
    @include flex($justify: space-between);
    padding: 6px 12px;
    border-bottom: 1px solid var(--app-border-color);
    background-color: rgba(120, 140, 175, 0.06);
  }

  &__title {
    font-size: 12px;
    font-weight: 600;
    color: var(--app-text-regular);
  }

  &__body {
    margin: 0;
    padding: 12px 14px;
    max-height: v-bind(maxHeight);
    overflow: auto;
    font-size: 12px;
    line-height: 1.7;
    color: var(--app-text-regular);
    white-space: pre-wrap;
    word-break: break-all;
  }
}
</style>
