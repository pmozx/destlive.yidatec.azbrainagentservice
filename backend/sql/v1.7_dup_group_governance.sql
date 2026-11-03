-- ============================================================================
-- 表结构变更：重复治理（#16–#20 · P6）
-- 版本：v1.7 · 日期：2026-11-03
-- 背景：P6 把重复治理从「按页面各自检测」收敛为「统一检测 + 治理物化」：
--       1) 检测结果必须三步（查询 / 详情 / 预览）看同一份快照 → 新增 sbp_ai_kb_dup_result；
--       2) 治理结论要能跨多次检测保持（人工已合并/已忽略不复发），且分组标识必须
--          与「类型 + 排序后的成员身份」一致 → sbp_ai_kb_dup_group 补 tenant_id / group_key
--          等列，并用 (tenant_id, group_key) 唯一索引兜住重复插行。
--
-- 何时执行：
--   1. 新库：无需手工执行。启动期 app.modules.kb.bootstrap 已按模型建表，列与索引齐全；
--   2. 存量库（dup_group 已有旧行）：必须执行本脚本——旧行的 group_key 是空串，
--      直接建唯一索引会撞约束，所以先 ADD COLUMN（带默认值）再回填再建索引；
--   3. 本脚本可重复执行（ADD COLUMN IF NOT EXISTS / 索引 IF NOT EXISTS / 约束先查 pg_constraint）。
-- ============================================================================

-- ── 1. sbp_ai_kb_dup_group：补治理字段 ──────────────────────────────────────
-- server_default 不是「业务默认值」，而是给存量行一个可回填的落点；
-- 应用侧每次物化都会显式写入这些列，不会依赖默认值。

ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS tenant_id      varchar(64)  NOT NULL DEFAULT '';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS group_key      varchar(64)  NOT NULL DEFAULT '';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS kind           varchar(20)  NOT NULL DEFAULT 'doc_similar';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS title          varchar(255) NOT NULL DEFAULT '';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS score_method   varchar(50)  NOT NULL DEFAULT '';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS reason         varchar(500) NOT NULL DEFAULT '';
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS suggested_keep_id varchar(64) NULL;
ALTER TABLE sbp_ai_kb_dup_group ADD COLUMN IF NOT EXISTS result_id      varchar(64)  NULL;

COMMENT ON COLUMN sbp_ai_kb_dup_group.tenant_id         IS '租户 ID（分组只对本租户可见）';
COMMENT ON COLUMN sbp_ai_kb_dup_group.group_key         IS '契约 group_id：类型 + 排序后成员身份的稳定摘要';
COMMENT ON COLUMN sbp_ai_kb_dup_group.kind              IS '治理类型：doc_exact/doc_similar/chunk/faq_ai/faq_manual';
COMMENT ON COLUMN sbp_ai_kb_dup_group.title             IS '分组标题（建议主项标题）';
COMMENT ON COLUMN sbp_ai_kb_dup_group.score_method      IS '得分方法版本（score_method）';
COMMENT ON COLUMN sbp_ai_kb_dup_group.reason            IS '判断依据（人工可读）';
COMMENT ON COLUMN sbp_ai_kb_dup_group.suggested_keep_id IS '建议保留项的 hit_id';
COMMENT ON COLUMN sbp_ai_kb_dup_group.result_id         IS '最近一次产出该分组的检测结果 result_id';

-- 旧行回填：旧检测没有 group_key（那时 uuid 就是分组标识），用 legacy-<id> 占位。
-- 这些行不会被新检测刷新（group_key 对不上），保留它们是为了不丢人工已处理的历史结论；
-- 如需让旧结论重新参与治理，由人工按旧 members 重新发起一次检测，新分组会另起一行。
UPDATE sbp_ai_kb_dup_group SET group_key = 'legacy-' || id::text WHERE group_key = '';

-- tenant_id 无法从库内推导（旧检测未落租户），保留 ''：
-- 空租户只影响「按租户筛选」不可见，不影响唯一索引；如需收敛由运维按 scope 清单人工归属。

-- 校验约束：kind / status 是治理口径的枚举，写错值必须让库拒绝，而不是留下一行查不出来的分组
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_sbp_ai_kb_dup_group_kind') THEN
        ALTER TABLE sbp_ai_kb_dup_group
            ADD CONSTRAINT ck_sbp_ai_kb_dup_group_kind
            CHECK (kind IN ('doc_exact', 'doc_similar', 'chunk', 'faq_ai', 'faq_manual'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_sbp_ai_kb_dup_group_status') THEN
        ALTER TABLE sbp_ai_kb_dup_group
            ADD CONSTRAINT ck_sbp_ai_kb_dup_group_status
            CHECK (status IN ('active', 'merged', 'dismissed'));
    END IF;
END $$;

-- 唯一索引：重复检测每次都会跑，同一租户同一条重复只能有一行治理记录
CREATE UNIQUE INDEX IF NOT EXISTS uq_sbp_ai_kb_dup_group_tenant_key
    ON sbp_ai_kb_dup_group (tenant_id, group_key);
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_group_kind_status
    ON sbp_ai_kb_dup_group (kind, status);
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_group_status_created
    ON sbp_ai_kb_dup_group (status, created_time);

COMMENT ON TABLE sbp_ai_kb_dup_group IS '重复分组（#16–#20 治理物化）';

-- ── 2. sbp_ai_kb_dup_result：30 分钟只读检测结果快照 ────────────────────────
-- 为什么单独建表：翻页/详情/预览必须看到同一份检测结果，重算会因来源随时变化而
-- 出现「第一页有、第二页没了」；快照自包含 groups，读取时只按 scope_digest 校验
-- 「还是不是同一次治理」，不依赖当时的库状态。

CREATE TABLE IF NOT EXISTS sbp_ai_kb_dup_result (
    id             serial       PRIMARY KEY,
    uuid           varchar(64)  NOT NULL,
    created_time   timestamptz  NOT NULL DEFAULT now(),
    updated_time   timestamptz  NOT NULL DEFAULT now(),
    tenant_id      varchar(64)  NOT NULL,
    user_id        varchar(64)  NOT NULL,
    score_profile  varchar(50)  NOT NULL,
    kinds          jsonb        NOT NULL DEFAULT '[]'::jsonb,
    scope          jsonb,
    scope_digest   varchar(64)  NOT NULL,
    content_digest varchar(64)  NOT NULL,
    groups         jsonb        NOT NULL DEFAULT '[]'::jsonb,
    coverage       jsonb,
    total_groups   int4         NOT NULL DEFAULT 0,
    expires_at     timestamptz  NOT NULL,
    CONSTRAINT ck_sbp_ai_kb_dup_result_total_non_negative CHECK (total_groups >= 0)
);

COMMENT ON TABLE  sbp_ai_kb_dup_result                IS '重复检测只读结果快照（绑定租户/用户/范围摘要，保留 30 分钟）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.uuid           IS '契约 result_id（前缀 dup-result-）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.tenant_id      IS '租户 ID（结果只对本租户可见）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.user_id        IS '发起检测的用户主键（字符串）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.score_profile  IS '检测策略标识，如 gsi-duplicate-v1';
COMMENT ON COLUMN sbp_ai_kb_dup_result.kinds          IS '本次治理类型（五类枚举）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.scope          IS '授权范围快照（含 scope_id）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.scope_digest   IS '范围+内容+策略摘要（翻页校验，不含 scope_id 与时间戳）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.content_digest IS '内容摘要（审计与排障）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.groups         IS '全部分组（契约投影）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.coverage       IS '覆盖度（requested/evaluated）';
COMMENT ON COLUMN sbp_ai_kb_dup_result.total_groups   IS '该结果的全部分组数';
COMMENT ON COLUMN sbp_ai_kb_dup_result.expires_at     IS '结果到期时间（创建后 30 分钟）';

CREATE UNIQUE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_result_uuid
    ON sbp_ai_kb_dup_result (uuid);
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_result_created_time
    ON sbp_ai_kb_dup_result (created_time);
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_result_tenant_created
    ON sbp_ai_kb_dup_result (tenant_id, created_time);
CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_dup_result_expires
    ON sbp_ai_kb_dup_result (expires_at);

-- 过期清理（30 分钟）由运维或定时任务执行，应用侧读取时也会按 expires_at 拒绝：
-- DELETE FROM sbp_ai_kb_dup_result WHERE expires_at < now();

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行；先回滚应用再回滚库）
-- ============================================================================
-- DROP TABLE IF EXISTS sbp_ai_kb_dup_result;
-- DROP INDEX IF EXISTS uq_sbp_ai_kb_dup_group_tenant_key;
-- DROP INDEX IF EXISTS ix_sbp_ai_kb_dup_group_kind_status;
-- ALTER TABLE sbp_ai_kb_dup_group DROP CONSTRAINT IF EXISTS ck_sbp_ai_kb_dup_group_kind;
-- ALTER TABLE sbp_ai_kb_dup_group
--     DROP COLUMN IF EXISTS result_id,
--     DROP COLUMN IF EXISTS suggested_keep_id,
--     DROP COLUMN IF EXISTS reason,
--     DROP COLUMN IF EXISTS score_method,
--     DROP COLUMN IF EXISTS title,
--     DROP COLUMN IF EXISTS kind,
--     DROP COLUMN IF EXISTS group_key,
--     DROP COLUMN IF EXISTS tenant_id;
