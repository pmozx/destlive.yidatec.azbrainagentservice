-- ============================================================================
-- 索引迁移脚本：关键词检索（#07 keyword · T4.1）的中文文本索引
-- 版本：v1.6 · 日期：2026-11-03
-- 背景：本项目不引入外部全文检索引擎，关键词通道落在 PostgreSQL：
--       PostgreSQL 内置 tsvector 的 simple 解析器把整句当一个 token，中文基本无法命中，
--       因此改用 pg_trgm 的字符三元组（gin_trgm_ops 支撑 ILIKE '%关键词%'）。
--       同理不做 zhparser / pg_jieba：需要自建镜像编译，当前不值得。
--
-- 何时执行：
--   1. 新库 / 应用账号是表 owner：无需手工执行，启动期
--      app.core.table_bootstrap.ensure_pg_trgm_extension + ensure_keyword_index 幂等补建；
--   2. 存量库且希望零锁表：关闭 SEARCH_INDEX_AUTO_CREATE 后，用本脚本人工执行
--      （CONCURRENTLY 不能在事务块里跑，psql 直接执行，不要包 BEGIN/COMMIT）；
--   3. 托管库无建扩展权限：由 DBA 先执行 CREATE EXTENSION，再执行第 2 步。
-- ============================================================================

-- 1. 扩展：gin_trgm_ops 操作符类由 pg_trgm 提供（postgresql-contrib 自带）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. 分片正文的中文三元组索引
--    覆盖：ILIKE '%关键词%'、similarity(content, '关键词')
--    限制：关键词含 ≥3 个连续字符才能利用该索引；2 字中文词退化为顺序扫描（当前量级可接受）
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sbp_ai_kb_chunk_content_trgm
    ON sbp_ai_kb_chunk USING gin (content gin_trgm_ops);

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行）
-- ============================================================================
-- DROP INDEX CONCURRENTLY IF EXISTS ix_sbp_ai_kb_chunk_content_trgm;
-- DROP EXTENSION IF EXISTS pg_trgm;   -- 其它对象若已依赖该扩展，须一并评估
