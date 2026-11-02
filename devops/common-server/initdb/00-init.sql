-- ==============================================================================
-- PostgreSQL 首次初始化脚本（仅当数据目录为空时由镜像自动执行一次）
--
--   pgvector 扩展需要超级用户创建，放在 initdb 里最稳；数据卷已存在时本脚本
--   不会重跑，此时由应用启动阶段按 VECTOR_AUTO_CREATE 幂等补建（见
--   app/core/table_bootstrap.py 的 ensure_pgvector_extension，lifespan 首步执行）。
--   两条路径都执行 CREATE EXTENSION IF NOT EXISTS，重复执行无副作用。
-- ==============================================================================
CREATE EXTENSION IF NOT EXISTS vector;
