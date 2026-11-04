-- ============================================================================
-- 列类型迁移脚本：审计日志的请求参数 / 响应报文改为 JSONB
-- 版本：v1.8 · 日期：2026-11-04
-- 背景：这两列原来存「序列化后的 JSON 字符串」（TEXT），页面只能当纯文本看，
--       也没法在库内按 JSON 字段取证。改为 JSONB 后详情页可直接展开阅读。
--
-- 何时执行：
--   1. 新库：无需执行，建表时即为 JSONB；
--   2. 存量库 / 表不大：无需执行，启动期
--      app.modules.system.bootstrap._ensure_json_payload_columns 幂等自动转换；
--   3. 存量库且表较大（ALTER ... TYPE 会取 ACCESS EXCLUSIVE 锁，阻塞审计写入）：
--      低峰期手工执行本脚本，执行前先停写或接受短暂阻塞。
--
-- 老值有两类：正常行的 JSON 字符串（转成对象），以及被截断 / 非 JSON 的历史串
-- （包成 {"_raw": "<原文>"}），后者仍然可读，不会因类型变更丢数据。
-- ============================================================================

-- 容错转换：非法 JSON 返回 NULL 而不是让整列转换失败
CREATE OR REPLACE FUNCTION pg_temp.try_jsonb(raw text) RETURNS jsonb
LANGUAGE plpgsql AS $$
BEGIN
    RETURN raw::jsonb;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

ALTER TABLE sbp_ai_system_api_log
    ALTER COLUMN request_payload TYPE JSONB
    USING CASE
              WHEN request_payload IS NULL THEN NULL
              ELSE COALESCE(pg_temp.try_jsonb(request_payload), jsonb_build_object('_raw', request_payload))
          END;

ALTER TABLE sbp_ai_system_api_log
    ALTER COLUMN response_json TYPE JSONB
    USING CASE
              WHEN response_json IS NULL THEN NULL
              ELSE COALESCE(pg_temp.try_jsonb(response_json), jsonb_build_object('_raw', response_json))
          END;

-- ============================================================================
-- 回滚脚本（如需回滚，取消注释执行；回滚后新写入的结构化 JSON 会以文本形式存放）
-- ============================================================================
-- ALTER TABLE sbp_ai_system_api_log ALTER COLUMN request_payload TYPE TEXT USING request_payload::text;
-- ALTER TABLE sbp_ai_system_api_log ALTER COLUMN response_json TYPE TEXT USING response_json::text;
