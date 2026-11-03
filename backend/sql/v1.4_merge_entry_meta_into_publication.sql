-- ============================================================================
-- DDL 迁移脚本：合并 sbp_ai_kb_entry_meta 入 sbp_ai_kb_publication
-- 版本：v1.4 · 日期：2026-11-03
-- 背景：原伴随表 sbp_ai_kb_entry_meta 与 sbp_ai_kb_publication 是 1:1 关系，
--       为降低查询复杂度与维护成本，将管理端展示字段合并入发布单元主表。
-- ============================================================================

-- 1. 向 sbp_ai_kb_publication 添加管理端展示字段
ALTER TABLE sbp_ai_kb_publication
    ADD COLUMN IF NOT EXISTS title      VARCHAR(200) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS summary    TEXT,
    ADD COLUMN IF NOT EXISTS tags       JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS owner      VARCHAR(64);

-- 2. 添加分类索引（原 entry_meta 表的索引迁移过来）
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_publication_category
    ON sbp_ai_kb_publication (category);

-- 3. 数据迁移：将 entry_meta 的存量数据合并入 publication
--    注意：如果存量 entry_meta 有数据，需要先执行此迁移再删表
UPDATE sbp_ai_kb_publication AS pub
SET
    title    = COALESCE(meta.title, pub.publication_id),
    category = meta.category,
    summary  = meta.summary,
    tags     = COALESCE(meta.tags, '[]'::jsonb),
    owner    = meta.owner
FROM sbp_ai_kb_entry_meta AS meta
WHERE pub.publication_id = meta.publication_id
  AND (pub.title IS NULL OR pub.title = '');

-- 4. 删除原伴随表（确认数据迁移完成后执行）
-- DROP TABLE IF EXISTS sbp_ai_kb_entry_meta CASCADE;

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行）
-- ============================================================================
-- ALTER TABLE sbp_ai_kb_publication
--     DROP COLUMN IF EXISTS title,
--     DROP COLUMN IF EXISTS category,
--     DROP COLUMN IF EXISTS summary,
--     DROP COLUMN IF EXISTS tags,
--     DROP COLUMN IF EXISTS owner;
-- DROP INDEX IF EXISTS ix_sbp_ai_kb_publication_category;
