# -*- coding: utf-8 -*-
"""生成「知识平台开发计划与工时评估 v2」Excel。

口径与 05-知识平台开发计划与工时评估-v2.md 完全同源：
21 个 /v1/* 接口 → 32.0 人日；跨切面与联调交付 18.5 人日；合计 50.5 人日 / 32 任务 / 12 周。
不含甘特图；不含实现状态列。

用法： python scripts/gen_kb_iface_xlsx.py
"""

from datetime import date
from pathlib import Path

from openpyxl import Workbook

import plan_xlsx_common as C

OUT = Path(__file__).resolve().parent.parent / "06-知识平台开发计划与工时评估-v2.xlsx"

START = date(2026, 11, 2)  # 首个工作日（工期起算日 2026-11-01 为周日）
END = date(2027, 1, 22)
HOLIDAYS = {date(2027, 1, 1)}  # 元旦

# ------------------------------------------------------------------ 阶段
PHASES = {
    "P1": ("接入层与审计地基", "D9E2F3"),
    "P2": ("解析", "E2EFDA"),
    "P3": ("知识写入", "FFF2CC"),
    "P4": ("检索", "FCE4D6"),
    "P5": ("会话与问答", "E4DFEC"),
    "P6": ("重复治理", "DDEBF7"),
    "P7": ("联调与测试", "F2F2F2"),
    "P8": ("文档与交付", "EDEDED"),
}

# ------------------------------------------------------------------ 接口（21 行）
# (编号, 手册编号, 方法, 路径, 分组, 人日, {任务: 人日}, 周次, 里程碑)
INTERFACES = [
    ("#01", "01", "POST", "/v1/parse/jobs", "解析", 1.5, {"T2.1": 1.5}, "W3", "M2"),
    ("#02", "02", "GET", "/v1/parse/jobs/{job_id}", "解析", 5.0,
     {"T2.2": 2.0, "T2.3": 2.0, "T2.4": 1.0}, "W3-W4", "M2"),
    ("03.1", "03.1", "POST", "/v1/knowledge/entries", "知识写入", 2.0,
     {"T3.2": 1.0, "T3.3": 1.0}, "W5-W6", "M3"),
    ("03.2", "03.2", "POST", "/v1/knowledge/entries/{publication_id}/query", "知识写入", 2.0,
     {"T3.1": 2.0}, "W4", "M3"),
    ("03.3", "03.3", "PUT", "/v1/knowledge/entries/{publication_id}", "知识写入", 2.0,
     {"T3.2": 1.0, "T3.3": 1.0}, "W5-W6", "M3"),
    ("03.4", "03.4", "DELETE", "/v1/knowledge/entries/{publication_id}", "知识写入", 0.5,
     {"T3.2": 0.5}, "W5", "M3"),
    ("#07", "04", "POST", "/v1/knowledge/search", "检索", 5.0,
     {"T4.1": 0.5, "T4.2": 2.0, "T4.3": 2.5}, "W5-W7", "M4"),
    ("#08", "05", "POST", "/v1/chat/sessions", "会话管理", 0.75,
     {"T5.1": 0.25, "T5.2": 0.25, "T5.6": 0.25}, "W6-W8", "M5"),
    ("#09", "06", "POST", "/v1/chat/sessions/query", "会话管理", 0.75,
     {"T5.1": 0.25, "T5.2": 0.25, "T5.6": 0.25}, "W6-W8", "M5"),
    ("#10", "07", "POST", "/v1/chat/sessions/{session_id}/query", "会话管理", 1.5,
     {"T5.1": 0.25, "T5.2": 0.25, "T5.3": 1.0}, "W6-W7", "M5"),
    ("#11", "08", "PATCH", "/v1/chat/sessions/{session_id}", "会话管理", 0.5,
     {"T5.2": 0.25, "T5.6": 0.25}, "W6-W8", "M5"),
    ("#12", "09", "DELETE", "/v1/chat/sessions/{session_id}", "会话管理", 0.5,
     {"T5.2": 0.25, "T5.6": 0.25}, "W6-W8", "M5"),
    ("#13", "11", "DELETE", "/v1/chat/sessions/{session_id}/messages", "对话历史", 1.0,
     {"T5.1": 0.25, "T5.2": 0.25, "T5.3": 0.5}, "W6-W7", "M5"),
    ("#14", "12", "POST", "/v1/chat/completions", "对话生成", 3.0,
     {"T5.4": 2.0, "T5.5": 1.0}, "W8", "M5"),
    ("#15", "13", "POST", "/v1/chat/requests/{target_request_id}/query", "对话生成", 1.0,
     {"T5.5": 1.0}, "W8", "M5"),
    ("#16", "D01", "POST", "/v1/knowledge/duplicates/check", "重复治理", 1.5,
     {"T6.1": 1.5}, "W9", "M6"),
    ("#17", "D02", "POST", "/v1/knowledge/duplicates/groups/query", "重复治理", 0.75,
     {"T6.2": 0.75}, "W9", "M6"),
    ("#18", "D03", "POST", "/v1/knowledge/duplicates/groups/{group_id}/query", "重复治理", 0.75,
     {"T6.2": 0.75}, "W9", "M6"),
    ("#19", "D04", "POST", "/v1/knowledge/duplicates/merge-preview", "重复治理", 1.0,
     {"T6.3": 1.0}, "W10", "M6"),
    ("#20", "D05", "POST", "/v1/knowledge/entries/{publication_id}/deactivate", "重复治理", 0.5,
     {"T6.3": 0.5}, "W10", "M6"),
    ("#21", "D06", "POST", "/v1/knowledge/duplicates/chunk-check", "重复治理", 0.5,
     {"T6.4": 0.5}, "W10", "M6"),
]

# ------------------------------------------------------------------ 任务（32 项）
# (编号, 阶段, 名称, 描述, 交付物, 人日, 开始, 结束, 优先级, 覆盖接口)
TASKS_RAW = [
    ("T1.0", "P1", "数据模型与建表基线", "23 张 sbp_ai_ 表（system 6 / kb 9 / voice 8）建模；命名与共库约定；bootstrap 幂等建表",
     "23 张表 DDL + 启动期 bootstrap", 2.5, "11-02", "11-03", "P0", "全部 21"),
    ("T1.1", "P1", "HMAC 五头签名与防重放", "签名原文七行、±5 分钟窗口、nonce 原子登记、expires_at ≤120s",
     "签名校验中间件 + 正反用例", 1.5, "11-04", "11-05", "P0", "全部 21"),
    ("T1.2", "P1", "统一错误体与错误码", "{error:{code,message,retryable}} 与 9 类状态码映射；PARSE_FAILED 仅任务态",
     "错误码映射表 + 契约用例", 1.0, "11-06", "11-06", "P0", "全部 21"),
    ("T1.3", "P1", "幂等与请求台账", "job_id / request_id / event_id 三域台账；同 ID 异正文 409；删除留墓碑",
     "幂等台账表 + 用例", 1.5, "11-09", "11-10", "P0", "写接口"),
    ("T1.4", "P1", "审计扩展", "api_log 增 auth_mode / client_id / request_id / security_check(JSONB)；被拒留痕",
     "审计扩展迁移 + 留痕用例", 2.5, "11-11", "11-13", "P0", "全部 21"),
    ("T1.5", "P1", "21 路由骨架与契约冒烟", "独立 /v1 router（不挂 Bearer、纳入审计）；21 条路由可签名打通",
     "路由骨架 + 契约用例框架", 1.0, "11-16", "11-16", "P0", "全部 21"),
    ("T2.1", "P2", "#01 解析任务受理", "202 受理、file_url 拉取、10MB 与后缀校验、FAQ 必填项",
     "受理接口 + 校验用例", 1.5, "11-17", "11-18", "P0", "#01"),
    ("T2.2", "P2", "#02 执行器与状态机", "四态状态机 + progress；异步执行与失败语义",
     "执行器 + 状态机用例", 2.0, "11-19", "11-20", "P0", "#02"),
    ("T2.3", "P2", "#02 产物结构与失败语义", "chunks / pages / warnings / suggested 组装；PARSE_FAILED 回执",
     "产物组装 + 结构用例", 2.0, "11-23", "11-24", "P0", "#02"),
    ("T2.4", "P2", "#02 页图渲染与有效期", "PDF 页图渲染、对外基址投影、签名有效期与过期 404",
     "页图渲染 + 过期用例", 1.0, "11-25", "11-25", "P1", "#02"),
    ("T3.1", "P3", "版本模型与 03.2 基线读取", "publication / revision 模型；基线快照；游标分页 limit 1-500",
     "版本表 + 读取接口", 2.0, "11-26", "11-27", "P0", "03.2"),
    ("T3.2", "P3", "写入三路径 03.1/03.3/03.4", "首发写入、全量替换与 base_record_id 校验、单切片删除与墓碑",
     "三个写接口 + 409 用例", 2.5, "12-03", "12-04", "P0", "03.1/03.3/03.4"),
    ("T3.3", "P3", "同步向量化与原子切换", "bge-m3 同步 embedding、pgvector upsert、原子版本切换",
     "向量化链路 + 一致性用例", 2.0, "12-07", "12-08", "P0", "03.1/03.3"),
    ("T4.1", "P4", "#07 keyword 检索", "全文/trigram 命中、snippet 与 score 对齐",
     "keyword 通道 + 用例", 0.5, "11-30", "11-30", "P0", "#07"),
    ("T4.2", "P4", "#07 向量召回与精排", "bge-m3 召回 + RRF 融合 + bge-reranker-v2-m3 精排；limit 1-100",
     "语义通道 + 排序用例", 2.0, "12-01", "12-02", "P0", "#07"),
    ("T4.3", "P4", "#07 实时权限下推", "只读 8 张 GSI 共享表并 SQL 下推；禁止先全库后过滤；不可用 503",
     "授权下推 + 越权 0 命中用例", 2.5, "12-14", "12-16", "P0", "#07"),
    ("T5.1", "P5", "会话与消息模型", "tenant_id + user_id 归属；session_id 本侧分配；消息与上下文结构",
     "会话表 + 模型说明", 1.0, "12-09", "12-09", "P0", "#08/#09/#10/#13"),
    ("T5.2", "P5", "会话六接口", "创建/列表/详情/改标题/删除/清空；DELETE 带正文；归属校验 404",
     "六个会话接口 + 用例", 1.5, "12-10", "12-11", "P0", "#08-#13"),
    ("T5.3", "P5", "历史分页与可见性", "游标分页；逐条按最新知识授权判定可见性；清空后不恢复",
     "分页与可见性用例", 1.5, "12-17", "12-18", "P0", "#10/#13"),
    ("T5.4", "P5", "#14 生成主链路", "授权→检索→合规历史→prompt→ChatGPT 5.6→原子保存；非流式",
     "生成接口 + 主链路用例", 2.0, "12-21", "12-22", "P0", "#14"),
    ("T5.5", "P5", "引用投影与越权/#15", "citations 与 source_snapshots；越权 403；#15 结果查询与 RESULT_EXPIRED",
     "引用投影 + 越权与超时用例", 2.0, "12-23", "12-24", "P0", "#14/#15"),
    ("T5.6", "P5", "会话与问答契约自测", "#08-#15 契约用例回归；错误码与边界",
     "会话问答契约测试集", 1.0, "12-25", "12-25", "P0", "#08-#13"),
    ("T6.1", "P6", "#16 保存前查重", "scope 内召回与相似度；排除同 resource 旧版本；失败显式报错",
     "查重接口 + 失败语义用例", 1.5, "12-28", "12-29", "P0", "#16"),
    ("T6.2", "P6", "#17/#18 分组与详情", "分组物化 + 30 分钟只读快照 + result_id 翻页；组内成员与证据",
     "分组与详情接口 + 用例", 1.5, "12-30", "12-31", "P1", "#17/#18"),
    ("T6.3", "P6", "#19/#20 预览与停用", "合并预览草稿态不落正式库；停用复用 03.3 revision 校验",
     "预览与停用接口 + 用例", 1.5, "01-04", "01-05", "P1", "#19/#20"),
    ("T6.4", "P6", "#21 单篇切片查重（D06）", "按 source_seq/target_seq 定位切片对；返回正文、相似度与重复占比；只读",
     "切片查重接口 + 用例", 0.5, "01-06", "01-06", "P1", "#21"),
    ("T7.1", "P7", "GSI Mock 联调", "Mock 网关全量走查 21 接口；联调环境与签名联调脚本",
     "Mock 联调记录 + 问题清单", 1.5, "01-07", "01-08", "P0", "全部 21"),
    ("T7.2", "P7", "GSI 真实后端联调", "真实后端全量联调；共享表授权切换；DELETE body 透传验证",
     "真实联调记录 + 遗留问题", 2.5, "01-11", "01-13", "P0", "全部 21"),
    ("T7.3", "P7", "语义对齐与疑点闭环", "错误码、超时、重试、签名语义逐条对齐；契约疑点闭环",
     "语义对齐清单（关闭率 100%）", 1.0, "01-14", "01-14", "P0", "全部 21"),
    ("T7.4", "P7", "并发与安全验证、性能回归", "并发/幂等/重放/越权/审计留痕用例；检索与生成耗时回归",
     "验证报告 + 性能基线", 1.0, "01-15", "01-15", "P0", "全部 21"),
    ("T8.1", "P8", "实现说明与错误码映射", "接口实现说明；错误码映射与排查指引",
     "实现说明文档", 0.5, "01-18", "01-18", "P1", "全部 21"),
    ("T8.2", "P8", "部署检查单与验收移交", "部署、共享表授权、密钥轮换、反代透传检查单；验收移交",
     "上线检查单 + 验收移交单", 2.0, "01-19", "01-20", "P0", "全部 21"),
]

MILESTONES = [
    ("M1", "接入与审计地基", "2026-11-20", "签名/防重放/幂等/错误码可用；21 路由可签名打通；审计扩展生效"),
    ("M2", "解析闭环", "2026-11-27", "#01→#02 全链路；产物结构对齐手册；failed 走 200 + error_code"),
    ("M3", "知识写入闭环", "2026-12-11", "03.1-03.4：409 防覆盖、revision 单调、基线冲突 409、原子切换"),
    ("M4", "检索与权限闭环", "2026-12-18", "#07 两模式可用；8 张共享表实时下推；越权 0 命中；不可用 503"),
    ("M5", "会话与问答闭环", "2026-12-25", "#08-#15 全通；非流式生成 + 引用 + 越权 403；超时结果可查询"),
    ("M6", "重复治理闭环", "2027-01-06", "#16-#21（含 D06 切片查重）；预览不落正式库；停用后召回为空"),
    ("M7", "联调通过", "2027-01-15", "21 接口与真实后端全量联调通过；语义对齐；契约疑点闭环"),
    ("M8", "交付验收", "2027-01-20", "并发/幂等/重放/越权/审计用例通过；部署检查单与实现说明齐备"),
]

MS_BY_WEEK = {"M1": "W3", "M2": "W4", "M3": "W6", "M4": "W7", "M5": "W8", "M6": "W10", "M7": "W11", "M8": "W12"}

WEEK_FOCUS = {
    "W1": "表建模与建表基线、签名与防重放、错误码",
    "W2": "幂等台账、审计扩展（本周末前冻结 60s 与切片规模口径）",
    "W3": "路由骨架与契约冒烟、#01 受理、#02 状态机",
    "W4": "#02 产物与页图、03.2 版本模型与基线读取",
    "W5": "#07 keyword 与语义通道、03.1/03.3/03.4 写入三路径",
    "W6": "向量化与原子切换、会话模型与六接口",
    "W7": "#07 实时权限下推（共享表授权最晚本周）、历史分页与可见性",
    "W8": "#14 生成主链路与引用投影、#15 结果查询、会话问答契约自测",
    "W9": "#16 查重、#17/#18 分组与详情（元旦 01-01 休，本周 4 个工作日）",
    "W10": "#19/#20 预览与停用、#21 切片查重（D06）、Mock 联调启动",
    "W11": "真实后端联调、语义对齐与疑点闭环、并发与安全验证",
    "W12": "实现说明、部署检查单与验收移交（留返工缓冲）",
}

RECON = [
    ("P0 POC 验证", 6.0, 0.0, "移出范围（链路验证并入 T1.5 与 T7.1）"),
    ("P1 接入层与审计地基", 10.5, 10.0, "不进接口，整包计入跨切面"),
    ("P2 解析", 7.0, 6.5, "#01 1.5 + #02 5.0"),
    ("P3 知识写入", 7.5, 6.5, "03.1 2.0 + 03.2 2.0 + 03.3 2.0 + 03.4 0.5"),
    ("P4 检索", 6.0, 5.0, "#07 5.0"),
    ("P5 会话与问答", 10.5, 9.0, "#08-#13 共 5.0 + #14 3.0 + #15 1.0"),
    ("P6 重复治理", 5.0, 5.0, "#16 1.5 + #17 0.75 + #18 0.75 + #19 1.0 + #20 0.5 + #21 0.5（含 D06）"),
    ("P7 联调与测试", 8.0, 6.0, "不进接口，整包计入跨切面"),
    ("P8 文档与交付", 3.0, 2.5, "不进接口，整包计入跨切面"),
]

COMPRESS = [  # 与基线逐项差异（展示为「差异明细」）
    ("P0 POC 验证（T0.1-T0.4）", "6.0", "—", "-6.0", "本次不设 POC：链路验证并入 T1.5 契约冒烟与 T7.1 Mock 联调；M0 门禁取消"),
    ("T1.1 签名与防重放", "2.0", "1.5", "-0.5", "无 POC 冒烟版到生产版的二次改造，一次按生产标准实现"),
    ("#02 解析结果", "5.5", "5.0", "-0.5", "产物结构与页图投影一次组装、一次用例"),
    ("03.2 读取基线", "2.5", "2.0", "-0.5", "版本模型与 03.3 共用一次建模"),
    ("03.4 删除集合", "1.0", "0.5", "-0.5", "复用 03.3 的版本指针与幂等墓碑"),
    ("#07 检索", "6.0", "5.0", "-1.0", "keyword 复用授权下推与响应投影（-0.5）；精排窗口按尾段预算反推免双轮调参（-0.5）"),
    ("#10 会话详情", "2.0", "1.5", "-0.5", "历史分页与可见性判定同一条读取链路"),
    ("#12 删除会话", "0.75", "0.5", "-0.25", "与 #13 共用状态位与迟到丢弃判定"),
    ("#13 清空历史", "1.25", "1.0", "-0.25", "同上"),
    ("#14 生成回答", "3.5", "3.0", "-0.5", "检索与授权直接复用 #07 链路"),
    ("#17/#18 分组与详情", "2.0", "1.5", "-0.5", "共用同一结果快照投影"),
    ("P7 联调与测试", "8.0", "6.0", "-2.0", "Mock 与真实联调串接（Mock 仅 1.5）；对齐、验证、性能回归各收一档"),
    ("P8 文档与交付", "3.0", "2.5", "-0.5", "实现说明与错误码映射并入部署检查单"),
    ("D06 单篇切片查重（新增纳入）", "—", "0.5", "+0.5", "复用 #16 相似度内核，增量只在切片对定位与占比统计"),
]

PRECONDITIONS = [
    ("共享密钥与 client_id 固定值", "全部 21", "W3（M1 前）", "全部接口 401，联调无法启动"),
    ("8 张共享表只读授权（含 sbp_km_* 三张）", "#07、#14、#16-#21", "W7（T4.3）", "越权 0 命中无法证明，M4 顺延"),
    ("ChatGPT 5.6 端点与密钥", "#14（主）、#02、#19", "W8（T5.4）", "生成链路只能跑兼容 mock，真实耗时不可知"),
    ("索引切片上限与 60s 超时口径", "#01、#02、03.1、03.3、#14", "W2 末", "同步索引需分批或转异步，触发契约变更"),
    ("GSI 联调窗口（W10 Mock / W11 真实）", "全部 21", "W9 前锁定", "M7/M8 顺延（本期无二次窗口）"),
    ("网关透传 DELETE 的 JSON 正文", "03.4、#12、#13", "上线前", "网关丢 body 导致 400，治理与会话清理不可用"),
]

RISKS = [
    ("R1", "关键路径无并行与替补；缓冲已收至 8.5 人日", "全部 21", "高", "任务 ≤3 人日；用例先行；W10/W12 缓冲专用于联调与验收"),
    ("R2", "8 张共享表授权延迟", "#07、#14、#16-#21", "高", "W1 提交申请；开发期用导出样本替代；不可用按 503 降级"),
    ("R3", "LLM 端点未就绪", "#14、#02、#19", "中", "端点配置化 + 兼容 mock；W8 前冻结端点清单"),
    ("R4", "同步索引超 60s", "03.1、03.3", "中", "W2 末实测冻结切片上限；必要时分批提交并协商契约"),
    ("R5", "非流式整答超 60s", "#14、#15", "中", "请求台账 + #15 结果查询必做；明确同 ID 重试语义"),
    ("R6", "审计中间件与既有配置耦合（白名单、跳过路径）", "全部 21", "中", "T1.4 显式覆盖 /v1；T7.4 审计留痕用例把关"),
    ("R7", "/v1 前缀与 Bearer 体系冲突（签名原文路径不符）", "全部 21", "高", "独立 router 不挂 Bearer；禁止反代改写路径"),
    ("R8", "无 POC 前置，契约疑点在联调期集中爆发", "全部 21", "中", "W2 末前发出疑点清单；T7.3 留 1.0 人日专用于闭环"),
    ("R9", "范围外追加（前端 / 管理端 / 语音质检）", "全部 21", "中", "一律走变更登记，不占用 8.5 人日缓冲"),
]

LEGENDS = [
    ("工时口径", "接口 32.0（21 个）+ 跨切面与联调交付 18.5 = 50.5 人日；接口人日是对任务人日的拆分，排期以任务为单位"),
    ("排期口径", "2026-11-01 起算（周日，首个工作日 11-02），12 个自然周 / 59 个工作日；元旦 2027-01-01 不计；按周归集，元旦周 4 个工作日"),
    ("产能占用", "50.5 / 59 = 85.6%，缓冲 8.5 人日（W10 1.5 + W12 2.5 + 流程内空隙约 4.5）"),
    ("与基线关系", "02/03/04 的 63.5 人日 / 36 任务 / 17 周口径未改动；本表为 v2 口径，差额 -13.0 见 05 工作表"),
    ("范围边界", "不含 POC、不含前端页面与交互、不含管理端 /api/v1/kb/* 维护接口、不含语音质检"),
    ("状态列", "本表不设置实现状态列，只描述计划工时、任务归属、周次与里程碑"),
    ("表数量", "23 张 sbp_ai_ 表：system 6 / kb 9 / voice 8（按代码内实际建表清单统计）"),
    ("编号双轨", "契约手册编号（#01-#13 + D01-D06）与计划/代码编号（#NN）并存；联调以「方法 + 路径」为唯一键"),
    ("跨切面编号", "统一采用 02 口径 C1 签名 / C2 防重放 / C3 幂等 / C4 错误体 / C5 权限下推 / C6 审计 / C7 共库"),
    ("生成方式", "python scripts/gen_kb_iface_xlsx.py（脚本内置断言：32 任务 / 50.5 人日 / 接口 32.0 / 工作日不重叠）"),
]


# ------------------------------------------------------------------ 构建与校验
def build():
    tasks = C.build_tasks([(t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8]) for t in TASKS_RAW])
    days = C.workdays(START, END, HOLIDAYS)
    C.validate(tasks, days, START, expect_count=32, expect_days=50.5)

    # 图表与文档同源校验
    assert len(INTERFACES) == 21, f"接口数应为 21，实际 {len(INTERFACES)}"
    iface_total = round(sum(i[5] for i in INTERFACES), 2)
    assert abs(iface_total - 32.0) < 1e-9, f"接口合计应为 32.0，实际 {iface_total}"

    by_task = {}
    for row in INTERFACES:
        for tid, hrs in row[6].items():
            by_task[tid] = round(by_task.get(tid, 0.0) + hrs, 4)
    days_of = {t["id"]: t["days"] for t in tasks}
    for tid, hrs in by_task.items():
        assert tid in days_of, f"{tid} 在接口拆分中出现但任务表不存在"
        assert abs(hrs - days_of[tid]) < 1e-9, f"{tid} 接口拆分 {hrs} ≠ 任务人日 {days_of[tid]}"

    rest = round(sum(d for tid, d in days_of.items() if tid not in by_task), 2)
    assert abs(rest - 18.5) < 1e-9, f"跨切面与联调应为 18.5，实际 {rest}"

    for key in MS_BY_WEEK:
        assert any(m[0] == key for m in MILESTONES), f"{key} 未在里程碑表登记"
    return tasks, days


def sheet_interfaces(wb):
    headers = ["接口编号", "手册编号", "方法", "路径", "分组", "人日", "任务拆分", "周次", "里程碑"]
    rows = []
    for no, hb, method, path, group, hrs, alloc, weeks, ms in INTERFACES:
        split = "\n".join(f"{tid} {h:.2f}".rstrip("0").rstrip(".") for tid, h in alloc.items())
        rows.append([no, hb, method, path, group, hrs, split, weeks, ms])
    ws = C.sheet_table(
        wb, "02 接口清单", "接口维度清单（21 接口 / 32.0 人日）",
        headers, [10, 9, 8, 52, 12, 7, 26, 9, 8], rows, center_cols={1, 2, 3, 5, 6, 8, 9},
        note="接口人日是对任务人日的拆分，同一任务可支撑多个接口（如 T5.2 支撑 #08-#13）；排期以任务为单位，不存在按接口排期。",
        total=["合计", "21 接口", "—", "19 个路径（03.3/03.4、#11/#12 各共用一条）", "—", 32.0, "", "", ""],
    )
    ws.freeze_panes = "A3"
    return ws


def sheet_tasks(wb, tasks):
    headers = ["编号", "阶段", "任务名称", "任务描述", "交付物", "人日", "优先级", "开始", "结束", "覆盖接口"]
    widths = [8, 20, 26, 56, 30, 6.5, 7, 11, 11, 20]
    ws = wb.create_sheet("03 任务清单")
    C.put_title(ws, "任务清单（32 项 / 50.5 人日）", len(headers))
    C.put_header(ws, 2, headers, widths)
    center = {1, 6, 7, 8, 9}
    row = 3
    for t in sorted(tasks, key=lambda x: (x["start"], x["id"])):
        vals = [t["id"], f"{t['phase']} {PHASES[t['phase']][0]}", t["name"], t["desc"], t["deliver"],
                t["days"], t["prio"], t["start"].strftime("%Y-%m-%d"), t["end"].strftime("%Y-%m-%d"),
                next(r[9] for r in TASKS_RAW if r[0] == t["id"])]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = C.F_BODY
            c.border = C.BORDER
            c.alignment = C.CENTER if idx in center else C.LEFT
        row += 1
    C.put_total(ws, row, len(headers), ["合计", "", "32 项任务", "", "", 50.5, "", "", "", ""], merge_to=5)
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:J{row - 1}"
    return ws


def sheet_recon(wb):
    ws = wb.create_sheet("05 工时对账与差异")
    C.put_title(ws, "工时对账与差异明细（63.5 → 50.5 人日，-13.0）", 6)
    C.put_header(ws, 2, ["工作包", "基线人日", "本次人日", "归集方式", "", ""], [26, 12, 12, 78, 3, 3])
    ws.merge_cells(start_row=2, start_column=4, end_row=2, end_column=6)
    row = 3
    for name, base, now, how in RECON:
        vals = [name, base, now, how]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = C.F_BODY
            c.border = C.BORDER
            c.alignment = C.CENTER if idx in (2, 3) else C.LEFT
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)
        for col in (5, 6):
            ws.cell(row=row, column=col).border = C.BORDER
        row += 1
    C.put_total(ws, row, 6, ["合计", 63.5, 50.5, "接口 32.0 + 跨切面与联调交付 18.5；32 项任务", "", ""])
    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)

    row += 2
    c = ws.cell(row=row, column=1, value="差异明细（逐项依据）")
    c.font = C.F_BOLD
    row += 1
    C.put_header(ws, row, ["差异项", "基线", "本次", "差额", "依据", ""])
    ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
    row += 1
    start = row
    for item, before, after, delta, why in COMPRESS:
        vals = [item, before, after, delta, why]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = C.F_BODY
            c.border = C.BORDER
            c.alignment = C.CENTER if idx in (2, 3, 4) else C.LEFT
            if idx == 4:
                c.font = C.Font(name=C.FONT_NAME, size=9, bold=True, color=C.C_WARN)
        ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
        ws.cell(row=row, column=6).border = C.BORDER
        row += 1
    ws.freeze_panes = f"A{start}"
    C.put_note(ws, row + 1, 6,
               "工时归集不改交付面：21 个接口全量、C1-C7 全量、23 张表全量；差异来自不设 POC、按边际成本归集接口、流程合并。影响见 07 口径与假设。")
    return ws


def sheet_risk(wb):
    ws = wb.create_sheet("06 风险与前置")
    C.put_title(ws, "前置条件与风险登记（按接口归集）", 5)
    C.put_header(ws, 2, ["前置条件", "受影响接口", "最晚需要", "逾期后果", ""], [40, 24, 14, 66, 3])
    ws.merge_cells(start_row=2, start_column=4, end_row=2, end_column=5)
    row = 3
    for cond, ifaces, when, impact in PRECONDITIONS:
        for idx, val in enumerate([cond, ifaces, when, impact], start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = C.F_BODY
            c.border = C.BORDER
            c.alignment = C.CENTER if idx == 3 else C.LEFT
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
        ws.cell(row=row, column=5).border = C.BORDER
        row += 1

    row += 2
    c = ws.cell(row=row, column=1, value="风险登记（R1-R9）")
    c.font = C.F_BOLD
    row += 1
    C.put_header(ws, row, ["编号", "风险", "命中接口", "概率", "应对"])
    row += 1
    for rid, desc, ifaces, prob, action in RISKS:
        for idx, val in enumerate([rid, desc, ifaces, prob, action], start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = C.F_BODY
            c.border = C.BORDER
            c.alignment = C.CENTER if idx in (1, 4) else C.LEFT
        row += 1
    C.put_note(ws, row + 1, 5, "R1 为结构性风险：缓冲 8.5 人日与关键路径无替补同时成立时，任何 2 天以上意外都会侵入尾周。")
    return ws


def sheet_legends(wb, tasks, days):
    headers = ["项", "说明"]
    rows = [[k, v] for k, v in LEGENDS]
    total = sum(t["days"] for t in tasks)
    rows.append(["校验结果", f"任务 {len(tasks)} 项 / 人日 {total:.1f} / 工作日 {len(days)} 天 / "
                            f"占用 {total / len(days) * 100:.1f}% / 接口 {len(INTERFACES)} 个 32.0 人日 —— 全部断言通过"])
    ws = C.sheet_table(wb, "07 口径与假设", "口径、假设与自校验", headers, [20, 130],
                       rows, center_cols={1},
                       note="本表由脚本生成，如与 05-知识平台开发计划与工时评估-v2.md 不一致，以 markdown 为准并重跑脚本。")
    return ws


def main():
    tasks, days = build()
    wb = Workbook()
    wb.remove(wb.active)

    info = [
        ("项目名称", "服务大脑 AI 项目 · 知识平台（接口维度）"),
        ("文档版本", "v2.0   编制 2026-11-04   编制人 孟星旭"),
        ("接口范围", "21 个 /v1/* 接口 / 19 个路径（#01-#13 + D01-D06；D06 本期新增纳入，计划编号 #21）"),
        ("工时口径", "接口 32.0 + 跨切面与联调交付 18.5 = 50.5 人日（基线 63.5，-13.0）"),
        ("任务规模", "32 项任务（P1-P8 八个工作包；P0 POC 已移出本次范围）"),
        ("排期", "2026-11-01 起算（周日，首个工作日 11-02）~ 2027-01-22，12 个自然周 / 59 个工作日（元旦不计，不含春节）"),
        ("产能占用", "85.6%，缓冲 8.5 人日（W10 1.5 + W12 2.5 + 流程内空隙约 4.5）"),
        ("数据库", "23 张 sbp_ai_ 表（system 6 / kb 9 / voice 8），service_brain public schema 共库"),
        ("技术栈", "FastAPI + PostgreSQL/pgvector(HNSW) + bge-m3(1024) + bge-reranker-v2-m3 + ChatGPT 5.6"),
        ("不含范围", "POC 验证 / 前端页面与交互 / 管理端维护接口 / 语音质检 / 契约复核闭环"),
        ("配套文档", "05-知识平台开发计划与工时评估-v2.md（本文口径来源）；02/03/04 仍为 63.5 人日基线"),
    ]
    C.sheet_info(wb, "知识平台开发计划与工时评估 v2（v3 契约 · 21 接口）", info, MILESTONES,
                 "用途：对外报价、对内排期与验收追溯。本表不含甘特图，周粒度与任务粒度见 03/04 工作表；不设置实现状态列。",
                 widths=(16, 40, 14, 78))

    sheet_interfaces(wb)
    sheet_tasks(wb, tasks)
    C.sheet_weeks(wb, days, START, tasks, WEEK_FOCUS, MS_BY_WEEK,
                  "周度负载（W1-W12）")
    sheet_recon(wb)
    sheet_risk(wb)
    sheet_legends(wb, tasks, days)

    wb.save(OUT)
    print(f"OK -> {OUT}")
    print(f"task={len(tasks)} days={sum(t['days'] for t in tasks):.1f} iface={len(INTERFACES)}")


if __name__ == "__main__":
    main()
