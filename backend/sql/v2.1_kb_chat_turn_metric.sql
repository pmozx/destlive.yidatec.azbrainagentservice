-- ============================================================================
-- 新增表脚本：AI 问答「单轮指标」台账
-- 版本：v2.1 · 日期：2026-11-06
-- 背景：AI 问答此前只有日志。想知道「这周答得比以前差在哪、钱花在哪、慢在哪」，
--       只能翻日志拼，且无法按模型/模式/提示词分组对比。本次新增：
--         sbp_ai_kb_chat_turn_metric：一轮问答一行，只写不改。
--       它是可 SQL 聚合的质量与成本底账，与 LangSmith trace 用 trace_id 对齐：
--         trace 答「这一轮发生了什么」，本表答「一段时间内质量/成本怎么变」。
--
-- 设计约束：
--   1. 只做聚合，不参与业务读写，因此可以自由加列而不动 #10/#15 的消息投影；
--   2. 写入失败必须降级：ChatTurnMeter.save 捕获异常只告警，
--      指标是观测数据，不能反过来把一次正常问答搞失败；
--   3. user_hash 存用户标识的哈希，不存账号原值；未开启追踪时 trace_id 为空。
--
-- 何时执行：
--   1. 新库：无需执行，启动期建表即包含本脚本内容；
--   2. 存量库：也无需手工执行——建表由
--      app.modules.kb.bootstrap.ensure_tables 完成（新表按缺失创建）；
--   3. 手工执行同样安全：全部是 IF NOT EXISTS。
-- ============================================================================

CREATE TABLE IF NOT EXISTS sbp_ai_kb_chat_turn_metric (
    id                      BIGSERIAL       PRIMARY KEY,
    trace_id                VARCHAR(64),
    session_id              VARCHAR(64)     NOT NULL,
    assistant_message_id    VARCHAR(64),
    tenant_id               VARCHAR(64)     NOT NULL,
    user_hash               VARCHAR(32)     NOT NULL,
    mode                    VARCHAR(16)     NOT NULL,
    model                   VARCHAR(100)    NOT NULL DEFAULT '',
    prompt_hash             VARCHAR(16),
    evidence_count          INTEGER         NOT NULL DEFAULT 0,
    top_score               DOUBLE PRECISION,
    tokens_in               INTEGER         NOT NULL DEFAULT 0,
    tokens_out              INTEGER         NOT NULL DEFAULT 0,
    retrieve_ms             INTEGER         NOT NULL DEFAULT 0,
    first_token_ms          INTEGER,
    generate_ms             INTEGER         NOT NULL DEFAULT 0,
    persist_ms              INTEGER         NOT NULL DEFAULT 0,
    total_ms                INTEGER         NOT NULL DEFAULT 0,
    no_evidence             BOOLEAN         NOT NULL DEFAULT false,
    finish_reason           VARCHAR(32),
    invalid_citations       INTEGER         NOT NULL DEFAULT 0,
    error_code              VARCHAR(64),
    created_time            TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE sbp_ai_kb_chat_turn_metric IS 'AI 问答单轮指标（质量/成本/耗时；写入失败不影响问答）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.trace_id IS 'LangSmith trace id（未开启追踪为空）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.session_id IS 'chat_session.uuid';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.assistant_message_id IS '助手消息 uuid（落库成功才有）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.tenant_id IS '租户';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.user_hash IS '用户标识的哈希（不存账号原值）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.mode IS 'sync/stream';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.model IS '本轮使用的模型名快照';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.prompt_hash IS '业务提示词指纹（只存哈希，换提示词后按它分组对比）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.evidence_count IS '进入上下文的证据条数';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.top_score IS '最高相关度（排序质量）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.tokens_in IS '输入 token（非流式门面不返回用量，记 0）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.tokens_out IS '输出 token';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.retrieve_ms IS '检索+回查耗时（毫秒）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.first_token_ms IS '首字耗时（毫秒，仅流式）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.generate_ms IS '生成耗时（毫秒）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.persist_ms IS '落库耗时（毫秒）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.total_ms IS '整轮耗时（毫秒）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.no_evidence IS '是否因无证据拒答';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.finish_reason IS 'stop/no_evidence（失败轮看 error_code）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.invalid_citations IS '被摘除的越界引用编号个数';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.error_code IS '失败码（成功轮为空）';
COMMENT ON COLUMN sbp_ai_kb_chat_turn_metric.created_time IS '创建时间';

-- 趋势图：按模式 + 时间聚合（sync/stream 的成本结构不同，混在一起看没有意义）
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_chat_turn_metric_mode_created
    ON sbp_ai_kb_chat_turn_metric (mode, created_time);

-- 单会话回看：某个用户投诉「这轮答得不对」时按会话串起来
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_chat_turn_metric_session
    ON sbp_ai_kb_chat_turn_metric (session_id);

-- 保留期清理：按时间删历史（指标表只增不减，需要定期裁剪）
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_chat_turn_metric_created_time
    ON sbp_ai_kb_chat_turn_metric (created_time);

-- ============================================================================
-- 常用查询（留作运维参考，不随脚本执行）
-- ============================================================================
-- 近 7 天按模式看质量与成本：
--   SELECT mode, count(*) AS turns,
--          round(avg(evidence_count), 2) AS avg_evidence,
--          round(avg(top_score), 4)      AS avg_top_score,
--          sum(no_evidence)              AS no_evidence_turns,
--          round(avg(tokens_out), 1)     AS avg_tokens_out,
--          round(avg(first_token_ms), 0) AS avg_first_token_ms,
--          round(avg(total_ms), 0)       AS avg_total_ms,
--          count(error_code)             AS failed_turns
--     FROM sbp_ai_kb_chat_turn_metric
--    WHERE created_time >= now() - interval '7 days'
--    GROUP BY mode;
-- 换提示词前后的效果对比：
--   SELECT prompt_hash, count(*) AS turns, round(avg(top_score), 4) AS avg_top_score
--     FROM sbp_ai_kb_chat_turn_metric
--    GROUP BY prompt_hash ORDER BY turns DESC;

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行；回滚后历史质量与成本底账全部丢失）
-- ============================================================================
-- DROP TABLE IF EXISTS sbp_ai_kb_chat_turn_metric;
