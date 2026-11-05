-- ============================================================================
-- 新增列脚本：解析产物记录「已写入知识平台」的去向
-- 版本：v1.9 · 日期：2026-11-05
-- 背景：解析任务详情页新增「写入知识平台」入口——把解析产物里已批准的切片集合
--       （含人工修订）直接落成一版正式知识（契约 03.1 等价）。产物表需要记住自己
--       写成了哪个发布单元、什么时候写的，否则：
--         1. 同一份产物会被重复写成两个发布单元（双击/刷新/多人操作）；
--         2. 页面无法展示「已写入」状态，只能去发布单元列表里靠标题猜。
--       写入不回改内容列（chunks/pages/content_hash 保持解析时快照），所以这两列
--       只做溯源与去重判断，可空、无默认值。
--
-- 何时执行：
--   1. 新库：无需执行，建表时即含这两列；
--   2. 存量库：无需手工执行，启动期
--      app.modules.kb.parse_result.crud.ensure_parse_result_schema 幂等自动补列；
--   3. 手工执行也安全：两条都是 ADD COLUMN IF NOT EXISTS，可重复运行。
-- ============================================================================

ALTER TABLE sbp_ai_kb_parse_result
    ADD COLUMN IF NOT EXISTS publication_id VARCHAR(64);

ALTER TABLE sbp_ai_kb_parse_result
    ADD COLUMN IF NOT EXISTS published_time TIMESTAMPTZ;

COMMENT ON COLUMN sbp_ai_kb_parse_result.publication_id IS '写入知识平台后的发布单元 ID（未写入为空）';
COMMENT ON COLUMN sbp_ai_kb_parse_result.published_time IS '最近一次写入知识平台的时间';

-- 便于「某发布单元由哪次解析生成」的反查（如停用/删除时回溯来源）
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_parse_result_publication_id
    ON sbp_ai_kb_parse_result (publication_id)
    WHERE publication_id IS NOT NULL;

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行；回滚后已写入的溯源信息会丢失，
-- 重新写入同一份产物会再生成一个发布单元）
-- ============================================================================
-- DROP INDEX IF EXISTS ix_sbp_ai_kb_parse_result_publication_id;
-- ALTER TABLE sbp_ai_kb_parse_result DROP COLUMN IF EXISTS published_time;
-- ALTER TABLE sbp_ai_kb_parse_result DROP COLUMN IF EXISTS publication_id;
