/**
 * 会话记录页的单轮指标投影。
 *
 * 指标与会话消息是**两个事实源**：消息是审计证据（正文 / 引用 / 依赖），指标是观测数据
 * （耗时 / token / 证据条数 / 失败码）。这里按 `assistant_message_id` 对齐，不去改消息接口
 * ——失败轮（断连 / 超时 / 生成失败）本来就没有助手消息，只能作为独立行插回对话流。
 */

/** 时间串 → 毫秒：后端输出 `2026-11-06 10:18:06`，直接 `new Date()` 在部分浏览器解析不稳 */
function timeOf(text) {
  const stamp = Date.parse(String(text || '').replace(/-/g, '/'))
  return Number.isNaN(stamp) ? 0 : stamp
}

/** 失败轮占位行：与消息行同形状，`_kind` 决定角色列与正文列怎么画 */
function toFailedRow(metric) {
  return {
    _kind: 'failed',
    _metric: metric,
    uuid: `metric-${metric.id}`,
    role: 'failed',
    content: '',
    citations: [],
    knowledge_deps: [],
    status: 'failed',
    created_time: metric.created_time,
  }
}

/**
 * 当前页消息 + 本会话指标 → 对话流。
 *
 * 失败轮只插入**当前页时间窗**内的那几条：跨页拼接要把分页交给前端，等于把两套分页合成
 * 一套，代价远大于收益；跨页的失败轮由抽屉顶部的「失败轮」计数兜底。
 */
export function mergeTurnRows(messages, metrics) {
  const byMessage = new Map()
  const failed = []
  ;(metrics || []).forEach((metric) => {
    if (metric.assistant_message_id) byMessage.set(metric.assistant_message_id, metric)
    else failed.push(metric)
  })

  const rows = (messages || []).map((message) => ({
    ...message,
    _kind: 'message',
    _metric: byMessage.get(message.uuid) || null,
  }))
  if (!rows.length) return failed.map(toFailedRow)

  const stamps = rows.map((row) => timeOf(row.created_time))
  const start = Math.min(...stamps)
  const end = Math.max(...stamps)
  failed
    .filter((metric) => {
      const stamp = timeOf(metric.created_time)
      return stamp >= start && stamp <= end
    })
    .forEach((metric) => rows.push(toFailedRow(metric)))

  return rows.sort((left, right) => timeOf(left.created_time) - timeOf(right.created_time))
}

/** 单轮指标 → 抽屉里的键值对（顺序即排查顺序：先看结论，再看耗时与用量） */
export function metricFields(metric) {
  if (!metric) return []
  return [
    { label: '模式', value: metric.mode === 'stream' ? '流式' : '非流式' },
    { label: '模型', value: metric.model || '-' },
    { label: '证据条数', value: `${metric.evidence_count ?? 0} 条` },
    { label: '最高相关度', value: metric.top_score === null || metric.top_score === undefined ? '-' : Number(metric.top_score).toFixed(3) },
    { label: '拒答', value: metric.no_evidence ? '是（证据不足）' : '否' },
    { label: '收尾原因', value: metric.finish_reason || '-' },
    { label: '失败码', value: metric.error_code || '-' },
    { label: '越界引用', value: `${metric.invalid_citations ?? 0} 个` },
    { label: '整轮耗时', value: `${metric.total_ms ?? 0} ms` },
    { label: '检索 / 生成', value: `${metric.retrieve_ms ?? 0} / ${metric.generate_ms ?? 0} ms` },
    {
      label: '首字 / 落库',
      value: `${metric.first_token_ms === null || metric.first_token_ms === undefined ? '-' : `${metric.first_token_ms} ms`} / ${metric.persist_ms ?? 0} ms`,
    },
    { label: 'token 入 / 出', value: `${metric.tokens_in ?? 0} / ${metric.tokens_out ?? 0}` },
    { label: '提示词指纹', value: metric.prompt_hash || '-' },
    { label: 'trace', value: metric.trace_id || '未开启追踪' },
  ]
}
