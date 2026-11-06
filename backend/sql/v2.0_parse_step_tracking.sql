-- ============================================================================
-- 新增表与列脚本：解析流水线「步骤台账」与 MinerU 产物归档
-- 版本：v2.0 · 日期：2026-11-05
-- 背景：解析任务原先只有粗粒度 status/progress，「卡在 35%」时页面既说不清是哪一步
--       出了问题，也没有任何中间态可复用——只能整份重来（MinerU 一次是分钟级成本）。
--       本次新增：
--         1. sbp_ai_kb_parse_step：一次执行、一步一行（含耗时、失败原因、续跑快照）；
--         2. sbp_ai_kb_parse_job.attempt：执行轮次，重跑 +1，写库时做乐观锁，防止
--            卡死的旧协程迟到的结果覆盖新一轮；
--         3. sbp_ai_kb_parse_result.artifacts：MinerU 产物（full.md / content_list.json /
--            images/*）在对象存储的键，供后续重提 FAQ、重新分片、按原图定位。
--
-- 何时执行：
--   1. 新库：无需执行，启动期建表即包含本脚本内容；
--   2. 存量库：也无需手工执行——建表由
--      app.modules.kb.bootstrap.ensure_tables 完成（新表按缺失创建），
--      两列由 parse_job.crud / parse_result.crud 在首次访问时幂等补齐；
--   3. 手工执行同样安全：全部是 IF NOT EXISTS。
-- ============================================================================

-- 解析流水线步骤台账
CREATE TABLE IF NOT EXISTS sbp_ai_kb_parse_step (
    id              BIGSERIAL       PRIMARY KEY,
    job_id          VARCHAR(64)     NOT NULL,
    attempt         INTEGER         NOT NULL DEFAULT 1,
    step            VARCHAR(32)     NOT NULL,
    status          VARCHAR(16)     NOT NULL,
    fatal           BOOLEAN         NOT NULL DEFAULT true,
    started_time    TIMESTAMPTZ     NOT NULL,
    finished_time   TIMESTAMPTZ,
    duration_ms     INTEGER,
    error_code      VARCHAR(64),
    error_message   TEXT,
    detail          JSONB           NOT NULL DEFAULT '{}'::jsonb,
    output          JSONB,
    CONSTRAINT ck_sbp_ai_kb_parse_step_status
        CHECK (status IN ('running', 'succeeded', 'failed', 'skipped'))
);

COMMENT ON TABLE sbp_ai_kb_parse_step IS '解析任务步骤台账（一次执行一步一行，含续跑快照）';
COMMENT ON COLUMN sbp_ai_kb_parse_step.job_id IS 'parse_job.uuid';
COMMENT ON COLUMN sbp_ai_kb_parse_step.attempt IS '第几次执行（1 起，重跑 +1）';
COMMENT ON COLUMN sbp_ai_kb_parse_step.step IS 'extract/artifacts/faq/chunk/pages/suggest/persist';
COMMENT ON COLUMN sbp_ai_kb_parse_step.status IS 'running/succeeded/failed/skipped';
COMMENT ON COLUMN sbp_ai_kb_parse_step.fatal IS '失败是否中断任务（false=降级继续，如页图/产物归档）';
COMMENT ON COLUMN sbp_ai_kb_parse_step.detail IS '轻量元数据（解析器/页数/条数/上传对象数）';
COMMENT ON COLUMN sbp_ai_kb_parse_step.output IS '续跑快照（extract：正文+页码；faq/chunk：切片），详情接口不返回';

-- 「某任务的全部执行轮次」按时间顺序读出（页面时间线）
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_parse_step_job_attempt
    ON sbp_ai_kb_parse_step (job_id, attempt, id);

-- 惰性看门狗：按状态 + 开始时间扫卡死的步骤
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_parse_step_status_started
    ON sbp_ai_kb_parse_step (status, started_time);

-- 执行轮次（乐观锁）
ALTER TABLE sbp_ai_kb_parse_job
    ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1;

COMMENT ON COLUMN sbp_ai_kb_parse_job.attempt IS '执行轮次（重跑 +1，写库时做乐观锁）';

-- MinerU 产物归档键
ALTER TABLE sbp_ai_kb_parse_result
    ADD COLUMN IF NOT EXISTS artifacts JSONB;

COMMENT ON COLUMN sbp_ai_kb_parse_result.artifacts IS
    'MinerU 产物在对象存储的键：markdown_key / content_list_key / image_keys / image_count';

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行；回滚后重跑任务的续跑快照与产物归档键会丢失）
-- ============================================================================
-- ALTER TABLE sbp_ai_kb_parse_result DROP COLUMN IF EXISTS artifacts;
-- ALTER TABLE sbp_ai_kb_parse_job DROP COLUMN IF EXISTS attempt;
-- DROP TABLE IF EXISTS sbp_ai_kb_parse_step;