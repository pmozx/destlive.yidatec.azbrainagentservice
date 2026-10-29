<template>
  <div class="markdown-view" v-html="renderedHtml"></div>
</template>

<script setup>
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

defineOptions({ name: 'MarkdownView' })

const props = defineProps({
  /** Markdown 源文本（解析产物 full.md，图片引用已由服务端重写为读取接口地址） */
  content: { type: String, default: '' },
})

// html: true 是必需的：MinerU 产物用原始 HTML 表达复杂表格与版式
const md = new MarkdownIt({ html: true, linkify: true, breaks: true })

/** 外链一律新窗口打开：预览面板里跳走就再也回不到当前文档 */
const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

/** 图片懒加载：一篇文档几十张图时，不必首屏一次性全量拉取 */
const defaultImage =
  md.renderer.rules.image ?? ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.image = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('loading', 'lazy')
  tokens[idx].attrSet('decoding', 'async')
  return defaultImage(tokens, idx, options, env, self)
}

const renderedHtml = computed(() => {
  if (!props.content) return ''
  // 产物由第三方解析服务生成，属于不可信内容：一律消毒后再 v-html，杜绝脚本与事件属性执行
  const clean = DOMPurify.sanitize(md.render(props.content), { RETURN_DOM_FRAGMENT: true })
  // MinerU 用原始 HTML 表达复杂表格，markdown-it 的 table 规则管不到它们：
  // 统一在 DOM 上给每张表套滚动容器，避免宽表把弹窗撑破
  for (const table of clean.querySelectorAll('table')) {
    if (table.parentElement?.classList.contains('md-table-wrap')) continue
    const wrap = document.createElement('div')
    wrap.className = 'md-table-wrap'
    table.replaceWith(wrap)
    wrap.appendChild(table)
  }
  const holder = document.createElement('div')
  holder.appendChild(clean)
  return holder.innerHTML
})
</script>

<style scoped lang="scss">
/* 全局样式没有 Markdown 语义排版，这里显式还原标题、列表、表格等观感 */
.markdown-view {
  font-size: 14px;
  line-height: 1.75;
  color: var(--app-text-primary);
  overflow-wrap: break-word;

  // 首尾元素去掉多余外边距，避免容器上下出现空白带
  > :deep(:first-child) {
    margin-top: 0;
  }

  > :deep(:last-child) {
    margin-bottom: 0;
  }

  :deep(h1),
  :deep(h2),
  :deep(h3),
  :deep(h4),
  :deep(h5),
  :deep(h6) {
    margin: 18px 0 8px;
    font-weight: 600;
    line-height: 1.4;
  }

  :deep(h1) {
    padding-bottom: 6px;
    font-size: 20px;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  :deep(h2) {
    padding-bottom: 4px;
    font-size: 17px;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  :deep(h3) {
    font-size: 15px;
  }

  :deep(h4),
  :deep(h5),
  :deep(h6) {
    font-size: 14px;
  }

  :deep(p) {
    margin: 8px 0;
  }

  :deep(strong) {
    font-weight: 600;
  }

  :deep(ul),
  :deep(ol) {
    padding-left: 22px;
    margin: 8px 0;
  }

  :deep(ul) {
    list-style: disc;
  }

  :deep(ol) {
    list-style: decimal;
  }

  :deep(li) {
    margin: 3px 0;

    > p {
      margin: 3px 0;
    }
  }

  :deep(a) {
    color: var(--el-color-primary);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  :deep(blockquote) {
    padding: 6px 14px;
    margin: 10px 0;
    color: var(--app-text-secondary);
    background-color: var(--el-fill-color-light);
    border-left: 3px solid var(--el-color-primary);
    border-radius: 0 8px 8px 0;

    > :first-child {
      margin-top: 0;
    }

    > :last-child {
      margin-bottom: 0;
    }
  }

  :deep(pre) {
    padding: 10px 14px;
    margin: 10px 0;
    overflow-x: auto;
    line-height: 1.6;
    background-color: var(--el-fill-color-light);
    border-radius: 8px;

    code {
      padding: 0;
      font-size: 13px;
      color: inherit;
      background: transparent;
    }
  }

  :deep(code) {
    padding: 2px 5px;
    font-family: var(--app-font-mono, monospace);
    font-size: 13px;
    background-color: var(--el-fill-color-light);
    border-radius: 4px;
  }

  :deep(.md-table-wrap) {
    margin: 10px 0;
    overflow-x: auto;
  }

  :deep(table) {
    width: max-content;
    max-width: 100%;
    border-collapse: collapse;

    th,
    td {
      padding: 6px 10px;
      text-align: left;
      border: 1px solid var(--el-border-color-light);
    }

    th {
      font-weight: 600;
      background-color: var(--el-fill-color-light);
    }
  }

  :deep(img) {
    max-width: 100%;
    height: auto;
    margin: 10px 0;
    vertical-align: middle;
    border-radius: 8px;
  }

  :deep(hr) {
    margin: 16px 0;
    border: none;
    border-top: 1px solid var(--el-border-color-lighter);
  }
}
</style>
