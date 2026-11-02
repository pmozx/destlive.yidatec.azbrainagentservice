# 知识平台与 AI 服务接口手册

> **GSI × AI 接口参考 · v2.1** | AI 团队对接版 | OpenAPI 3.0.3 | 2026-10-28

> AI 团队实现、GSI 后端调用的 20 个服务接口，涵盖文档解析、正式知识切片、检索、会话、回答与重复治理。使用 HMAC-SHA256 认证，共享 service_brain PostgreSQL 数据库，AI 在独立 schema 管理切片与 pgvector 向量。本文定义对接契约，不作为服务上线或验收证明。

---

> **阅读方式**：Swagger 风格的接口参考。展开接口 → 查看请求参数 → 查看响应参数 → 复制完整 JSON 示例。本手册由 HTML 版转换生成，内容与 OpenAPI JSON 同源；原 HTML 中的"下载 OpenAPI JSON"可导入 Swagger Editor / Apifox，本页面不发送业务请求。

> **字段阅读约定**：字段路径中的 `[]` 表示数组元素；"父级存在时必填"表示父对象提供后必须传入该字段；"条件必填"以字段说明中的触发条件为准。文档中的域名、ID、时间和摘要均为示例，不能直接用于生产请求。

## 目录

- [一、服务职责与接入约定](#一服务职责与接入约定)
- [二、知识修订：AI 读取，GSI 审批，批准后写回](#二知识修订ai-读取gsi-审批批准后写回)
- [三、提供给 AI 的指定共享表与权限](#三提供给-ai-的指定共享表与权限)
- [四、公共请求头与签名](#四公共请求头与签名)
- [五、接口总览](#五接口总览)
- [六、接口详情](#六接口详情)
  - [解析](#解析)
    - [01 提交文档 / FAQ 解析任务](#01-提交文档-faq-解析任务)
    - [02 查询解析进度与完整结果](#02-查询解析进度与完整结果)
  - [知识索引与检索](#知识索引与检索)
    - [03.1 新增已批准切片集合](#031-新增已批准切片集合)
    - [03.2 读取知识修订基线](#032-读取知识修订基线)
    - [03.3 修改已批准切片集合](#033-修改已批准切片集合)
    - [03.4 删除已批准切片集合](#034-删除已批准切片集合)
    - [04 关键词 / 自然语言检索](#04-关键词-自然语言检索)
  - [会话管理](#会话管理)
    - [05 创建新会话](#05-创建新会话)
    - [06 分页查询我的会话](#06-分页查询我的会话)
    - [07 查询会话详情（可分页返回历史）](#07-查询会话详情可分页返回历史)
    - [08 修改会话标题](#08-修改会话标题)
    - [09 删除会话](#09-删除会话)
  - [对话历史](#对话历史)
    - [11 清空会话历史](#11-清空会话历史)
  - [对话生成](#对话生成)
    - [12 发送问题并生成回答](#12-发送问题并生成回答)
    - [13 查询超时操作的结果](#13-查询超时操作的结果)
  - [重复治理](#重复治理)
    - [D01 保存前重复检查](#d01-保存前重复检查)
    - [D02 查询重复分组](#d02-查询重复分组)
    - [D03 查询分组详情与对比依据](#d03-查询分组详情与对比依据)
    - [D04 生成合并预览（不落正式库）](#d04-生成合并预览不落正式库)
    - [D05 停用已生效知识（通用接口）](#d05-停用已生效知识通用接口)
- [七、业务错误码与处理方式](#七业务错误码与处理方式)
- [八、重复治理 · AI 对接说明](#八重复治理--ai-对接说明)

---

## 一、服务职责与接入约定

本文面向 AI 服务开发与联调人员。全部 20 个接口由 AI 团队提供，GSI 后端签名调用；浏览器不直接调用 AI 服务。AI 返回裸 JSON，不增加 GSI 的 code / msg / data 包装。

AI 服务负责：文档解析、FAQ 提取、正式切片存储、Embedding 向量生成、pgvector 存储与检索、按权限检索知识、回答生成、用户会话与对话历史、重复检测及合并预览。

GSI 负责：可信用户身份、知识元数据、草稿、审批、发布范围与生效时间、治理处置、调用签名以及返回来源的权限复核。点赞点踩由 GSI 保存，AI 保持会话和消息标识稳定。

数据存储：双方共用 service_brain PostgreSQL 数据库。AI 使用独立数据库账号，在 ai_knowledge schema 管理正文、向量、会话、消息和幂等记录；pgvector 用于保存和检索 Embedding 模型生成的向量。GSI 业务表按下文指定字段只读开放。

接入地址：以 AI 服务 Base URL 拼接下方 /v1 路径。联调地址、生产地址、client_id 和签名密钥通过双方约定的配置渠道提供。文档中的域名、ID、时间和摘要为示例，不能直接用于生产请求。

参数阅读：字段路径中的 [] 表示数组元素；“父级存在时必填”表示父对象提供后必须传入该字段。接口卡片分别提供请求参数、响应参数、错误响应和可复制 JSON 示例。

一致性：知识 record_id、version_id、revision 标识批准快照和操作顺序；会话使用 request_id 幂等与服务端串行处理。正式写入返回最终完成回执，同请求 ID 的重试必须保持正文一致。

---

## 二、知识修订：AI 读取，GSI 审批，批准后写回

1. 进入修订页面，GSI 校验维护权限，通过 03.2 精确读取 publication_id + record_id 的 AI 切片，并读取全部分页，保存修订基线。
2. 新增、修改、删除切片先保存在 GSI 修订草稿，保留原文、修改后全文和变更说明。前端不能直接调用 AI 写接口。
3. 提交现有整单审批或切片修订审批。待审、驳回、退回、撤回时都不修改 AI 正式切片；重新打开页面优先恢复 GSI 草稿，不能被 AI 原文覆盖。
4. 相应审批最终通过后，GSI 后端构造完整目标切片集合。首次发布调用 03.1；修订调用 03.3；批准删除整个目标集合调用 03.4。删除单片通过 03.3 提交移除该片后的完整集合，不额外增加单片接口。
5. 计划发布需等到计划时点才对外生效；部分受众通过只更新已通过的发布单元。AI 不根据 metadata.audience 自动扩散发布范围。
6. AI 写入新快照并完成向量化后返回最终回执，GSI 再切换对应发布指针。AI 可同时保留旧、新快照，权限查询只认可 GSI 当前指向的快照；切换前继续使用旧内容。
7. 失败时保留旧生效快照，以原 request_id 和完全相同的正文重试。批准内容若改变必须重新审批并使用新请求 ID。基线冲突返回 409，不能静默覆盖。

读接口适用“知识维护权限”（如本人、归属团队、审批处理权限），回答接口适用“消费权限”（生效发布 + 受众团队 + 不可见排除）。二者不能混用。审批和发布状态仍由 GSI 管理；AI 保存检索内容、向量和快照，不写 GSI 审批业务表。

发布一致性：AI 写入成功、GSI 切换失败时，旧发布继续有效，新快照不参与回答；重试回执后继续切换。GSI 撤销发布后，即使 AI 暂时未删数据，也必须立即停止召回。DELETE 保留幂等墓碑，迟到 CREATE / UPDATE 不得复活已删除数据。空集合不能作为正常发布；删除最后一片需要走批准删除及撤销发布流程。

写入前提：GSI 在审批通过且满足生效条件后调用写入接口。AI 验证服务签名、资源归属与修订顺序，不接收审批信息、不查询 GSI 审批记录。审批单及执行事件由 GSI 内部通过 request_id 关联。

---

## 三、提供给 AI 的指定共享表与权限

数据库：`service_brain`。实际部署 schema / search_path 在开通账号时确认。AI 使用独立账号，读取所需字段；只在自有 `ai_knowledge` schema 写入切片、pgvector 向量、会话和消息。数据库账号与只读授权由数据库管理员配置。

| 表名 | 用途 | 指定字段 | 关联及注意事项 |
| --- | --- | --- | --- |
| sbp_sys_org | 组织架构 | id, parent_id, name, code, type, category, source_org_id, status, deleted | parent_id → id；category 区分 admin / business，source_org_id 为业务镜像关联行政组织。组织上下级不自动继承知识权限。 |
| sbp_sys_org_member | 组织成员 | org_id, user_id | org_id → sbp_sys_org.id；user_id → sbp_sys_user.id。仅提供组织归属，不代替受众团队成员关系。 |
| sbp_sys_audience_team | 受众团队 | id, group_code, group_name, audience_team, status, deleted | 仅 status=1 且 deleted=0 有效。当前 userTeams 同时兼容编码、名称及 audience_team；新写入使用 group_code。 |
| sbp_sys_audience_team_member | 受众团队成员 | team_id, user_id | team_id → sbp_sys_audience_team.id；以签名 user_id 查询，不使用姓名。 |
| sbp_sys_user | 用户有效性（限字段） | id, status, deleted, allow_login | 用于核对可信用户身份、账号状态；不授予 password_hash、邮箱、手机号等无关列。状态映射与平台登录规则统一，不能按示例猜测。 |
| sbp_km_doc_publication | 文档按团队生效关系 | id, doc_id, team, record_id, index_status, updated_at | doc_id → sbp_km_doc.id；record_id → sbp_km_doc_record.id；team 与用户受众团队标识匹配。index_status 不能单独作为授权依据。 |
| sbp_km_doc_record | 文档生效快照 | id, doc_id, stage, status, snapshot_json | 仅读取 publication.record_id 指向的生效快照作为回答来源；snapshot_json 的 invisibleTeams、expireAt 决定拒绝范围和有效期。AI 仅核对内容归属和当前生效指针，不核验审批过程。 |
| sbp_km_doc | （原文档损坏） | （原文档损坏） | （原文档损坏） |

> ⚠️ **转换说明（原文档损坏）**：原 HTML 文件在共享表清单处存在约 3KB 的二进制损坏，导致：
> 1. 上表最后一行 `sbp_km_doc`（文档主表）的用途、字段及关联说明完全丢失；其后可能存在的其他共享表行（如 FAQ 相关表）一并丢失，无法恢复；
> 2. 权限模型段落开头的部分内容损坏。损坏点之后、"读接口适用知识维护权限……"一段与第二节对应段落完全相同（完整版见第二节），故不再重复；
> 3. 下方"检索与权限模型"为损坏区域之后可读取内容的整理。

### 检索与权限模型（可读部分整理）

1. 行政 / 业务组织只用于组织上下文，不递归扩权。
2. 匹配各发布单元 team，读取其 record_id 指向的生效快照；拒绝逻辑删除、已归档或已下架资源。
3. 解析生效 JSON，invisibleTeams 命中任一用户团队即拒绝，排除优先；expireAt 按 Asia/Shanghai 日期判断，早于当天拒绝。FAQ 优先 access_rules_json，缺少 invisibleTeams 或 JSON 无效拒绝；不能使用编辑草稿推断权限。
4. 与 AI 本地索引的 resource_type / resource_id / publication_id / record_id / version_id / revision 对齐，并要求索引就绪。全文和向量检索都限定在这些候选内，不能把未授权内容交给模型。
5. 本轮答案及历史摘要的所有知识依赖都要可访问。撤权后的历史正文、引用、派生标题不能通过会话查询、幂等回放、13 结果查询或下一轮上下文泄漏。提交回答前 AI 再检查一次，GSI 展示前复核 source_snapshots。

权限表不可读取时返回 503 AUTHORIZATION_UNAVAILABLE，不能退化为全库检索；用户无权或无可用证据属于正常空结果。团队调整、停用、撤权必须在下一请求生效，不能长期缓存用户权限。保留当前 group_code / group_name / audience_team 的兼容匹配，但新数据使用唯一编码；部署前检查重名与冲突。

SQL 示例仅用于筛选候选，必须配合上述权限检查；所有业务查询使用参数绑定。


---

## 四、公共请求头与签名

所有接口均使用 HMAC-SHA256；不传 Bearer Token，不把密钥发送给 AI。JSON 正文使用 UTF-8，`Content-Type: application/json; charset=utf-8`。GET 没有正文。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| `X-AI-Signature-Version` | string | 必填 / 必返 | 固定 v1。 | `"v1"` |
| `X-AI-Client-Id` | string | 必填 / 必返 | 固定 gsi-service，AI 通过该标识选择共享密钥。 | `"gsi-service"` |
| `X-AI-Timestamp` | string | 必填 / 必返 | 13 位 Unix 毫秒，允许前后 5 分钟；示例时间不能直接用于在线调用。 | `"1800000000000"` |
| `X-AI-Nonce` | string | 必填 / 必返 | 每次 HTTP 请求新生成的 UUID；16–128 位字母、数字、下划线或连字符。 | `"11111111-2222-4333-8444-555555555555"` |
| `X-AI-Signature` | string | 必填 / 必返 | 原始正文签名，64 位小写十六进制 HMAC-SHA256；不是直接发送密钥。 | `"<运行时计算的签名>"` |

#### 签名原文：七行，用 LF 连接，末尾没有换行

```
v1
gsi-service
{X-AI-Timestamp}
{X-AI-Nonce}
{大写 HTTP 方法}
{原始路径及可选查询字符串}
{实际请求正文原始字节的 SHA-256 小写十六进制摘要}
```

`X-AI-Signature = HMAC-SHA256(共享密钥, 上述 UTF-8 原文)`，输出 64 位小写十六进制。无正文对空字节序列求 SHA-256。AI 必须在解析 JSON 前验签，不能排序字段或重序列化。

验签通过后原子登记 client_id + nonce，保留至 timestamp + 5 分钟之后。生产多实例及重启期间必须共享持久防重放记录。业务幂等 ID 重试保持不变，时间戳、nonce 和签名每次重新生成。

---

## 五、接口总览

全部 20 个接口由 AI 团队提供，GSI 后端签名调用；浏览器不直接调用 AI 服务。AI 返回裸 JSON，不增加 GSI 的 `code / msg / data` 包装。

| 编号 | 接口 | 方法 | 路径 | 分组 |
| --- | --- | --- | --- | --- |
| 01 | 提交文档 / FAQ 解析任务 | POST | `/v1/parse/jobs` | 解析 |
| 02 | 查询解析进度与完整结果 | GET | `/v1/parse/jobs/{job_id}` | 解析 |
| 03.1 | 新增已批准切片集合 | POST | `/v1/knowledge/entries` | 知识索引与检索 |
| 03.2 | 读取知识修订基线 | POST | `/v1/knowledge/entries/{publication_id}/query` | 知识索引与检索 |
| 03.3 | 修改已批准切片集合 | PUT | `/v1/knowledge/entries/{publication_id}` | 知识索引与检索 |
| 03.4 | 删除已批准切片集合 | DELETE | `/v1/knowledge/entries/{publication_id}` | 知识索引与检索 |
| 04 | 关键词 / 自然语言检索 | POST | `/v1/knowledge/search` | 知识索引与检索 |
| 05 | 创建新会话 | POST | `/v1/chat/sessions` | 会话管理 |
| 06 | 分页查询我的会话 | POST | `/v1/chat/sessions/query` | 会话管理 |
| 07 | 查询会话详情（可分页返回历史） | POST | `/v1/chat/sessions/{session_id}/query` | 会话管理 |
| 08 | 修改会话标题 | PATCH | `/v1/chat/sessions/{session_id}` | 会话管理 |
| 09 | 删除会话 | DELETE | `/v1/chat/sessions/{session_id}` | 会话管理 |
| 11 | 清空会话历史 | DELETE | `/v1/chat/sessions/{session_id}/messages` | 对话历史 |
| 12 | 发送问题并生成回答 | POST | `/v1/chat/completions` | 对话生成 |
| 13 | 查询超时操作的结果 | POST | `/v1/chat/requests/{target_request_id}/query` | 对话生成 |
| D01 | 保存前重复检查 | POST | `/v1/knowledge/duplicates/check` | 重复治理 |
| D02 | 查询重复分组 | POST | `/v1/knowledge/duplicates/groups/query` | 重复治理 |
| D03 | 查询分组详情与对比依据 | POST | `/v1/knowledge/duplicates/groups/{group_id}/query` | 重复治理 |
| D04 | 生成合并预览（不落正式库） | POST | `/v1/knowledge/duplicates/merge-preview` | 重复治理 |
| D05 | 停用已生效知识（通用接口） | POST | `/v1/knowledge/entries/{publication_id}/deactivate` | 重复治理 |

> 编号沿用原文档卡片编号（原文档无 10 号，非缺漏）。所有接口均需携带公共签名请求头（见第四节）。

---

## 六、接口详情

### 解析

#### 01 提交文档 / FAQ 解析任务

`POST /v1/parse/jobs`

录入知识或从文档解析 FAQ 时调用。AI 接受任务后返回 202，GSI 随后轮询任务结果；不需要取消接口。相同 job_id + 相同正文重试返回原任务。FAQ 由 prompt_text 指定提取问题与答案，不另设 FAQ 解析接口。 新增 metadata，涵盖新增页面字段。解析产物为待确认草稿，AI 不得自动写入正式检索库；提示词和模型仍传正文及模型键。FAQ 从文档解析同样携带已填写的公共元数据。 resource_type=document 时 prompt_text、model_key 可选；resource_type=faq 时两项必填。提供的值必须包含非空白字符；FAQ 缺失任一字段或传入空值返回 HTTP 400 INVALID_REQUEST。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

GSI 提供文件 URL 和实际提示词、模型标识；不是传 GSI 配置 ID。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| job_id | string | 必填 | GSI 预先生成的解析任务 ID，也是幂等标识。 | "parse-001" |
| doc_id | string | 必填 | GSI 文档 / 本次解析所属业务资源 ID；不是发布单元 ID。 | "doc-demo-001" |
| version_id | string | 必填 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| file_url | string | 必填 | AI 可直接读取的文件 URL，需保证有效期覆盖解析过程。 【格式：uri】 | "https://files.example.com/vpn.pdf" |
| file_name | string | 必填 | 原始文件名，带扩展名。 | "VPN操作手册.pdf" |
| prompt_text | string | 条件必填 | 条件必填：resource_type=document 时可省略；resource_type=faq 时必填。传入完整提示词正文，不传配置 ID；提供时不能为 null、空字符串或纯空白。 【最小长度 1；正则约束：\S】 | "提取文档正文并按主题切片。" |
| model_key | string | 条件必填 | 条件必填：resource_type=document 时可省略；resource_type=faq 时必填。传入双方约定的模型调用键，不传 GSI 配置记录 ID；提供时不能为 null、空字符串或纯空白。 【最小长度 1；正则约束：\S】 | "default" |
| options | object | 必填 | 解析选项。 |  |
| options.chunk | boolean | 必填 | 是否生成切片；当前 GSI 传 true。 | true |
| options.language | string | 必填 | 解析语言，与 metadata.language 保持一致；GSI 将页面值统一映射后传递。 | "中文" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| resource_type | string | 必填 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| file_id | string | 可选 | GSI 文件标识转字符串，供追踪，不替代 file_url。 | "108" |
| file_size | integer(int64) | 可选 | 文件大小，字节。 【格式：int64；最小值 0】 | 102400 |
| metadata | object | 必填 | 本次新增页面已保存的元数据快照。由 GSI 从保存结果构造；解析期间变更须新建 job_id。AI 仅返回建议，不覆盖人工选择，不据此发布或授权。 |  |
| metadata.title | string | 可选 | 知识标题。 | "VPN申请操作手册" |
| metadata.doc_type | string | 可选 | 文档类型，沿用页面枚举。 【枚举："Document" / "FAQ" / "Code" / "API" / "Test" / "Runbook" / "Handover"】 | "Document" |
| metadata.owner | string | 可选 | 负责人，沿用 GSI 页面保存值，不用姓名推断权限。 | "kali03" |
| metadata.author | string | 可选 | 实际维护人，沿用页面保存值。 | "kali03" |
| metadata.team | string | 可选 | 归属团队编码；与受众团队不是同一概念。 | "TEAM-IT" |
| metadata.language | string | 可选 | 页面选择的语言；与 options.language 保持一致。 | "中文" |
| metadata.trust | string | 可选 | 知识级别：A 正式知识、B 确认经验、C 参考经验。 【枚举："A" / "B" / "C"】 | "A" |
| metadata.summary | string | 可选 | 用户填写的摘要。 | "VPN申请与开通" |
| metadata.content | string | 可选 | 用户已填写的正文；未填写时省略。 | "VPN申请流程" |
| metadata.tags | array<string> | 可选 | 页面标签。 |  |
| metadata.expire_at | string | 可选 | 知识有效期，YYYY-MM-DD；未填写时省略。 【格式：date】 | "2027-12-31" |
| metadata.scheduled_publish_at | string | 可选 | 计划发布时间，ISO 8601，必须含时区；未填写时省略。 【格式：date-time】 | "2026-11-01T09:00:00+08:00" |
| metadata.invisible_teams | array<string> | 可选 | 不可见团队编码；解析上下文，最终消费权限读取已生效快照。 |  |
| metadata.audience | object | 可选 | 新增页面的受众维度；仅作为解析上下文，不能直接授权。实际权限以生效发布关系为准。 |  |
| metadata.audience.department | array<string> | 可选 | 部门；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.team | array<string> | 可选 | 受众团队；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.role | array<string> | 可选 | 角色；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.region | array<string> | 可选 | 地域；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.skill | array<string> | 可选 | 技能；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.scenario | array<string> | 可选 | 场景；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.cover_url | string | 可选 | 封面地址；不传 data URL，未填写时省略。 【格式：uri】 | "https://files.example.com/cover.png" |
| metadata.cover_name | string | 可选 | 封面文件名。 | "cover.png" |
| metadata.linked_doc_id | string | 可选 | 关联来源文档 ID，未关联时省略。 | "doc-source-001" |
| metadata.linked_section | string | 可选 | 关联章节。 | "VPN申请" |
| metadata.change_impact_level | string | 可选 | 修订影响级别；新增未填写时省略。 【枚举："minor" / "substantial"】 | "minor" |
| metadata.units | array<object> | 可选 | 新增页面已有知识单元；未生成时传 []。只作解析上下文，不视为正式切片。 |  |
| metadata.units[].unit_id | string | 可选 | 已有单元 ID，新建可省略。 | "unit-1" |
| metadata.units[].no | string | 必填 | 页面单元编号。 | "U001" |
| metadata.units[].title | string | 必填 | 单元标题。 | "申请步骤" |
| metadata.units[].kind | string | 必填 | 页面单元类型，按 KnowledgeUnit.kind 原值传递。 | "text" |
| metadata.units[].text | string | 必填 | 页面正文（可能是富文本 HTML），AI 先清洗再解析。 | "在服务台提交申请。" |

**请求示例 · document**（文档解析请求示例：省略可选提示词和模型调用键）

```json
{
  "job_id": "parse-001",
  "doc_id": "doc-demo-001",
  "version_id": "1",
  "file_url": "https://files.example.com/vpn.pdf",
  "file_name": "VPN操作手册.pdf",
  "options": {
    "chunk": true,
    "language": "中文"
  },
  "tenant_id": "gsi-default",
  "resource_type": "document",
  "file_id": "108",
  "file_size": 102400,
  "metadata": {
    "title": "VPN申请操作手册",
    "doc_type": "Document",
    "owner": "kali03",
    "author": "kali03",
    "team": "TEAM-IT",
    "language": "中文",
    "trust": "A",
    "summary": "VPN申请与开通",
    "content": "VPN申请流程",
    "tags": [
      "VPN"
    ],
    "expire_at": "2027-12-31",
    "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
    "invisible_teams": [
      "TEAM-EXCLUDED"
    ],
    "audience": {
      "department": [
        "IT"
      ],
      "team": [
        "TEAM-IT"
      ],
      "role": [
        "工程师"
      ],
      "region": [
        "中国"
      ],
      "skill": [
        "基础"
      ],
      "scenario": [
        "VPN申请"
      ]
    },
    "cover_url": "https://files.example.com/cover.png",
    "cover_name": "cover.png",
    "linked_doc_id": "doc-source-001",
    "linked_section": "VPN申请",
    "change_impact_level": "minor",
    "units": [
      {
        "unit_id": "unit-1",
        "no": "U001",
        "title": "申请步骤",
        "kind": "text",
        "text": "在服务台提交申请。"
      }
    ]
  }
}
```

**请求示例 · faq**（FAQ 从文档解析：提示词和模型调用键必填（模型键须替换为约定值））

```json
{
  "job_id": "parse-001",
  "doc_id": "doc-demo-001",
  "version_id": "1",
  "file_url": "https://files.example.com/vpn.pdf",
  "file_name": "VPN操作手册.pdf",
  "prompt_text": "从文档中提取常见问题及对应答案，答案必须有原文依据，不补造事实。",
  "model_key": "default",
  "options": {
    "chunk": true,
    "language": "中文"
  },
  "tenant_id": "gsi-default",
  "resource_type": "faq",
  "file_id": "108",
  "file_size": 102400,
  "metadata": {
    "title": "VPN申请操作手册",
    "doc_type": "FAQ",
    "owner": "kali03",
    "author": "kali03",
    "team": "TEAM-IT",
    "language": "中文",
    "trust": "A",
    "summary": "VPN申请与开通",
    "content": "VPN申请流程",
    "tags": [
      "VPN"
    ],
    "expire_at": "2027-12-31",
    "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
    "invisible_teams": [
      "TEAM-EXCLUDED"
    ],
    "audience": {
      "department": [
        "IT"
      ],
      "team": [
        "TEAM-IT"
      ],
      "role": [
        "工程师"
      ],
      "region": [
        "中国"
      ],
      "skill": [
        "基础"
      ],
      "scenario": [
        "VPN申请"
      ]
    },
    "cover_url": "https://files.example.com/cover.png",
    "cover_name": "cover.png",
    "linked_doc_id": "doc-source-001",
    "linked_section": "VPN申请",
    "change_impact_level": "minor",
    "units": [
      {
        "unit_id": "unit-1",
        "no": "U001",
        "title": "申请步骤",
        "kind": "text",
        "text": "在服务台提交申请。"
      }
    ]
  }
}
```

**响应**

**HTTP 202 · Accepted：请求已受理但处理尚未完成；通过任务查询接口获取结果。**

已受理，不代表解析完成。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| job_id | string | 必返 | 原样返回请求 job_id。 | "parse-001" |
| status | string | 必返 | 解析任务当前状态。 【枚举："queued" / "running" / "succeeded"】 | "queued" |
| message | string | 可选 | 可选受理说明。 | "任务已受理，请查询结果。" |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "job_id": "parse-001",
  "status": "queued",
  "message": "任务已受理，请查询结果。"
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"resource_type 为 faq 时，prompt_text 和 model_key 必填且不能仅包含空白字符。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 02 查询解析进度与完整结果

`GET /v1/parse/jobs/{job_id}`

GET 无请求正文，对空字节序列计算签名摘要。succeeded 时必须返回 result，failed 时必须返回 error_code 和 error_message；解析失败属于任务结果，HTTP 仍为 200。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| job_id | string | 必填 | GSI 创建解析任务时给的 job_id。 | "parse-001" |

**请求正文**：无（GET 请求）。

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

查询解析任务状态。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| job_id | string | 必返 | 任务 ID。 | "parse-001" |
| status | string | 必返 | queued=排队，running=执行，succeeded=成功，failed=失败。 【枚举："queued" / "running" / "succeeded" / "failed"】 | "succeeded" |
| progress | integer(int64) | 必返 | 进度百分比 0–100。 【格式：int64；最小值 0；最大值 100】 | 100 |
| result | object | 可选 | 仅 status=succeeded 时必返。文档与 FAQ 从文档解析共用此结构；FAQ 按所选提示词返回问题标题和答案正文。 |  |
| result.file_name | string | 可选 | 原始文件名。 | "VPN操作手册.pdf" |
| result.suggested_doc_type | string | 可选 | AI 建议的文档分类，仅作建议，不改变 GSI 业务状态。 | "SOP" |
| result.suggested | object | 可选 | 可选的 AI 建议元数据。 |  |
| result.suggested.title | string | 可选 | 建议标题，用户可修改。 | "VPN 申请操作手册" |
| result.suggested.summary | string | 可选 | 建议摘要。 | "VPN 申请与开通说明。" |
| result.suggested.tags | array<string> | 可选 | 建议标签。 |  |
| result.pages | array<object> | 必返 | 文档预览页面；没有可用页图时返回 []，不能伪造图像 URL。 |  |
| result.pages[].page_no | integer(int64) | 必返 | 页码，从 1 开始。 【格式：int64；最小值 1】 | 1 |
| result.pages[].width | integer(int64) | 必返 | 预览图宽度，像素。 【格式：int64；最小值 1】 | 600 |
| result.pages[].height | integer(int64) | 必返 | 预览图高度，像素。 【格式：int64；最小值 1】 | 800 |
| result.pages[].image_url | string | 必返 | GSI 可访问的页图 URL；有效期需覆盖编辑预览。 【格式：uri】 | "https://ai.example.com/previews/page-1.png" |
| result.chunks | array<object> | 必返 | 解析后的切片；不得包含 embedding 或其他向量字段。 |  |
| result.chunks[].seq | integer(int64) | 必返 | 切片顺序，从 1 开始，本次列表内唯一。 【格式：int64；最小值 1】 | 1 |
| result.chunks[].heading | string | 可选 | 切片小标题，可省略。 | "VPN 申请流程" |
| result.chunks[].content | string | 必返 | 完整切片正文，不能为空；向量由 AI 生成，不传向量数组。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| result.chunks[].page_from | integer(int64) | 可选 | 起始页，页码从 1 开始；无分页内容可省略或 null。 【格式：int64；最小值 1】 | 1 |
| result.chunks[].page_to | integer(int64) | 可选 | 结束页，可省略或 null；不小于起始页。 【格式：int64；最小值 1】 | 1 |
| result.chunks[].source_type | string | 可选 | 内容来源类型，例如 text；支持的类型由双方确认。 | "text" |
| result.warnings | array<string> | 可选 | 非致命解析警告，无警告返回 []。 |  |
| error_code | string | 可选 | status=failed 时必返机器错误码。 | "PARSE_FAILED" |
| error_message | string | 可选 | status=failed 时必返失败说明。 | "无法识别文档内容。" |

**响应示例 · success**（解析成功）

```json
{
  "job_id": "parse-001",
  "status": "succeeded",
  "progress": 100,
  "result": {
    "file_name": "VPN操作手册.pdf",
    "suggested_doc_type": "SOP",
    "suggested": {
      "title": "VPN 申请操作手册",
      "summary": "VPN 申请与开通说明。",
      "tags": [
        "VPN"
      ]
    },
    "pages": [
      {
        "page_no": 1,
        "width": 600,
        "height": 800,
        "image_url": "https://ai.example.com/previews/page-1.png"
      }
    ],
    "chunks": [
      {
        "seq": 1,
        "heading": "VPN 申请流程",
        "content": "在服务台提交 VPN 申请，由负责人审核后开通账号。",
        "page_from": 1,
        "page_to": 1,
        "source_type": "text"
      }
    ],
    "warnings": [
      "部分页面识别质量较低，请人工确认。"
    ]
  }
}
```

**响应示例 · running**（解析中）

```json
{
  "job_id": "parse-001",
  "status": "running",
  "progress": 50
}
```

**响应示例 · failed**（解析失败）

```json
{
  "job_id": "parse-001",
  "status": "failed",
  "progress": 100,
  "error_code": "PARSE_FAILED",
  "error_message": "无法解析文件。"
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

### 知识索引与检索

#### 03.1 新增已批准切片集合

`POST /v1/knowledge/entries`

审批通过前的增删改均保存在 GSI 草稿；驳回或撤回不调用本接口。知识管理权限由 GSI 校验，AI 验证服务签名和资源归属，不查询 GSI 审批记录。 首次发布使用；已有 publication_id 返回 409，不能隐式覆盖。AI 保存正文并完成向量化，成功回执后 GSI 才允许新发布指针对外生效。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| resource_type | string | 必填 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必填 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必填 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必填 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必填 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必填 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| knowledge_no | string | 必填 | 页面知识编号，仅展示与追踪。 | "KB-0000064" |
| metadata | object | 必填 | 本次新增页面已保存的元数据快照。由 GSI 从保存结果构造；解析期间变更须新建 job_id。AI 仅返回建议，不覆盖人工选择，不据此发布或授权。 |  |
| metadata.title | string | 可选 | 知识标题。 | "VPN申请操作手册" |
| metadata.doc_type | string | 可选 | 文档类型，沿用页面枚举。 【枚举："Document" / "FAQ" / "Code" / "API" / "Test" / "Runbook" / "Handover"】 | "Document" |
| metadata.owner | string | 可选 | 负责人，沿用 GSI 页面保存值，不用姓名推断权限。 | "kali03" |
| metadata.author | string | 可选 | 实际维护人，沿用页面保存值。 | "kali03" |
| metadata.team | string | 可选 | 归属团队编码；与受众团队不是同一概念。 | "TEAM-IT" |
| metadata.language | string | 可选 | 页面选择的语言；与 options.language 保持一致。 | "中文" |
| metadata.trust | string | 可选 | 知识级别：A 正式知识、B 确认经验、C 参考经验。 【枚举："A" / "B" / "C"】 | "A" |
| metadata.summary | string | 可选 | 用户填写的摘要。 | "VPN申请与开通" |
| metadata.content | string | 可选 | 用户已填写的正文；未填写时省略。 | "VPN申请流程" |
| metadata.tags | array<string> | 可选 | 页面标签。 |  |
| metadata.expire_at | string | 可选 | 知识有效期，YYYY-MM-DD；未填写时省略。 【格式：date】 | "2027-12-31" |
| metadata.scheduled_publish_at | string | 可选 | 计划发布时间，ISO 8601，必须含时区；未填写时省略。 【格式：date-time】 | "2026-11-01T09:00:00+08:00" |
| metadata.invisible_teams | array<string> | 可选 | 不可见团队编码；解析上下文，最终消费权限读取已生效快照。 |  |
| metadata.audience | object | 可选 | 新增页面的受众维度；仅作为解析上下文，不能直接授权。实际权限以生效发布关系为准。 |  |
| metadata.audience.department | array<string> | 可选 | 部门；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.team | array<string> | 可选 | 受众团队；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.role | array<string> | 可选 | 角色；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.region | array<string> | 可选 | 地域；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.skill | array<string> | 可选 | 技能；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.scenario | array<string> | 可选 | 场景；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.cover_url | string | 可选 | 封面地址；不传 data URL，未填写时省略。 【格式：uri】 | "https://files.example.com/cover.png" |
| metadata.cover_name | string | 可选 | 封面文件名。 | "cover.png" |
| metadata.linked_doc_id | string | 可选 | 关联来源文档 ID，未关联时省略。 | "doc-source-001" |
| metadata.linked_section | string | 可选 | 关联章节。 | "VPN申请" |
| metadata.change_impact_level | string | 可选 | 修订影响级别；新增未填写时省略。 【枚举："minor" / "substantial"】 | "minor" |
| metadata.units | array<object> | 可选 | 新增页面已有知识单元；未生成时传 []。只作解析上下文，不视为正式切片。 |  |
| metadata.units[].unit_id | string | 可选 | 已有单元 ID，新建可省略。 | "unit-1" |
| metadata.units[].no | string | 必填 | 页面单元编号。 | "U001" |
| metadata.units[].title | string | 必填 | 单元标题。 | "申请步骤" |
| metadata.units[].kind | string | 必填 | 页面单元类型，按 KnowledgeUnit.kind 原值传递。 | "text" |
| metadata.units[].text | string | 必填 | 页面正文（可能是富文本 HTML），AI 先清洗再解析。 | "在服务台提交申请。" |
| content_hash | string | 必填 | 完整切片集合的内容摘要，双方按固定序列化规则计算。 | "sha256:example-content-digest" |
| chunks | array<object> | 必填 | 该快照完整切片，审批内修改后整体替换；被移除的切片不能留在新集合中。 |  |
| chunks[].chunk_id | string | 必填 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| chunks[].seq | integer(int64) | 必填 | 切片顺序，从 1 开始，本次列表内唯一。 【格式：int64；最小值 1】 | 1 |
| chunks[].heading | string | 可选 | 切片小标题，可省略。 | "VPN 申请流程" |
| chunks[].content | string | 必填 | 完整切片正文，不能为空；向量由 AI 生成，不传向量数组。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| chunks[].page_from | integer(int64) | 可选 | 起始页，页码从 1 开始；无分页内容可省略或 null。 【格式：int64；最小值 1】 | 1 |
| chunks[].page_to | integer(int64) | 可选 | 结束页，可省略或 null；不小于起始页。 【格式：int64；最小值 1】 | 1 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "knowledge_no": "KB-0000064",
  "metadata": {
    "title": "VPN申请操作手册",
    "doc_type": "Document",
    "owner": "kali03",
    "author": "kali03",
    "team": "TEAM-IT",
    "language": "中文",
    "trust": "A",
    "summary": "VPN申请与开通",
    "content": "VPN申请流程",
    "tags": [
      "VPN"
    ],
    "expire_at": "2027-12-31",
    "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
    "invisible_teams": [
      "TEAM-EXCLUDED"
    ],
    "audience": {
      "department": [
        "IT"
      ],
      "team": [
        "TEAM-IT"
      ],
      "role": [
        "工程师"
      ],
      "region": [
        "中国"
      ],
      "skill": [
        "基础"
      ],
      "scenario": [
        "VPN申请"
      ]
    },
    "cover_url": "https://files.example.com/cover.png",
    "cover_name": "cover.png",
    "linked_doc_id": "doc-source-001",
    "linked_section": "VPN申请",
    "change_impact_level": "minor",
    "units": [
      {
        "unit_id": "unit-1",
        "no": "U001",
        "title": "申请步骤",
        "kind": "text",
        "text": "在服务台提交申请。"
      }
    ]
  },
  "content_hash": "sha256:example-content-digest",
  "chunks": [
    {
      "chunk_id": "record-demo-001:1",
      "seq": 1,
      "heading": "VPN 申请流程",
      "content": "在服务台提交 VPN 申请，由负责人审核后开通账号。",
      "page_from": 1,
      "page_to": 1
    }
  ]
}
```

**响应**

**HTTP 201 · Created：请求成功并创建了资源；响应体返回新资源标识。**

同步完成回执，不返回仅排队的 202。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| success | boolean | 必返 | 操作完成。 | true |
| content_hash | string | 必返 | 完整切片集合的内容摘要，双方按固定序列化规则计算。 | "sha256:example-content-digest" |
| accepted_chunk_count | integer(int64) | 必返 | 本次完整切片条数；删除为 0。 【格式：int64；最小值 0】 | 1 |
| searchable | boolean | 必返 | AI 本地索引是否就绪；不是用户访问许可。 | true |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "success": true,
  "content_hash": "sha256:example-content-digest",
  "accepted_chunk_count": 1,
  "searchable": true
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

```json
{"request_id":"req-20261029-002","error":{"code":"STALE_REVISION","message":"知识基线已发生变化，请读取最新内容后重新提交。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### 03.2 读取知识修订基线

`POST /v1/knowledge/entries/{publication_id}/query`

审批通过前的增删改均保存在 GSI 草稿；驳回或撤回不调用本接口。知识管理权限由 GSI 校验，AI 验证服务签名和资源归属，不查询 GSI 审批记录。 本接口不修改任何切片。AI 必须核对 publication_id、record_id 归属；只读已批准快照，支持按历史 record_id 精确读取。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| publication_id | string | 必填 | GSI 发布单元 ID；不是知识编号。 | "pub-demo-001" |

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| record_id | string | 必填 | 要读取的确切生效快照 ID；不得用其他团队的任意最新版本。 | "record-demo-001" |
| cursor | string | 可选 | 切片分页游标，首次省略。 | "cursor-from-previous-page" |
| limit | integer(int64) | 可选 | 每页切片数，默认 100，1–500。 【格式：int64；最小值 1；最大值 500；默认：100】 | 100 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "record_id": "record-demo-001",
  "limit": 100
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

修订页基线；GSI 保存为自己的草稿，逐页读取完整后才能提交修改。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| knowledge_no | string | 必返 | 页面知识编号，仅展示与追踪。 | "KB-0000064" |
| metadata | object | 必返 | 本次新增页面已保存的元数据快照。由 GSI 从保存结果构造；解析期间变更须新建 job_id。AI 仅返回建议，不覆盖人工选择，不据此发布或授权。 |  |
| metadata.title | string | 可选 | 知识标题。 | "VPN申请操作手册" |
| metadata.doc_type | string | 可选 | 文档类型，沿用页面枚举。 【枚举："Document" / "FAQ" / "Code" / "API" / "Test" / "Runbook" / "Handover"】 | "Document" |
| metadata.owner | string | 可选 | 负责人，沿用 GSI 页面保存值，不用姓名推断权限。 | "kali03" |
| metadata.author | string | 可选 | 实际维护人，沿用页面保存值。 | "kali03" |
| metadata.team | string | 可选 | 归属团队编码；与受众团队不是同一概念。 | "TEAM-IT" |
| metadata.language | string | 可选 | 页面选择的语言；与 options.language 保持一致。 | "中文" |
| metadata.trust | string | 可选 | 知识级别：A 正式知识、B 确认经验、C 参考经验。 【枚举："A" / "B" / "C"】 | "A" |
| metadata.summary | string | 可选 | 用户填写的摘要。 | "VPN申请与开通" |
| metadata.content | string | 可选 | 用户已填写的正文；未填写时省略。 | "VPN申请流程" |
| metadata.tags | array<string> | 可选 | 页面标签。 |  |
| metadata.expire_at | string | 可选 | 知识有效期，YYYY-MM-DD；未填写时省略。 【格式：date】 | "2027-12-31" |
| metadata.scheduled_publish_at | string | 可选 | 计划发布时间，ISO 8601，必须含时区；未填写时省略。 【格式：date-time】 | "2026-11-01T09:00:00+08:00" |
| metadata.invisible_teams | array<string> | 可选 | 不可见团队编码；解析上下文，最终消费权限读取已生效快照。 |  |
| metadata.audience | object | 可选 | 新增页面的受众维度；仅作为解析上下文，不能直接授权。实际权限以生效发布关系为准。 |  |
| metadata.audience.department | array<string> | 可选 | 部门；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.team | array<string> | 可选 | 受众团队；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.role | array<string> | 可选 | 角色；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.region | array<string> | 可选 | 地域；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.skill | array<string> | 可选 | 技能；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.scenario | array<string> | 可选 | 场景；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.cover_url | string | 可选 | 封面地址；不传 data URL，未填写时省略。 【格式：uri】 | "https://files.example.com/cover.png" |
| metadata.cover_name | string | 可选 | 封面文件名。 | "cover.png" |
| metadata.linked_doc_id | string | 可选 | 关联来源文档 ID，未关联时省略。 | "doc-source-001" |
| metadata.linked_section | string | 可选 | 关联章节。 | "VPN申请" |
| metadata.change_impact_level | string | 可选 | 修订影响级别；新增未填写时省略。 【枚举："minor" / "substantial"】 | "minor" |
| metadata.units | array<object> | 可选 | 新增页面已有知识单元；未生成时传 []。只作解析上下文，不视为正式切片。 |  |
| metadata.units[].unit_id | string | 可选 | 已有单元 ID，新建可省略。 | "unit-1" |
| metadata.units[].no | string | 必返 | 页面单元编号。 | "U001" |
| metadata.units[].title | string | 必返 | 单元标题。 | "申请步骤" |
| metadata.units[].kind | string | 必返 | 页面单元类型，按 KnowledgeUnit.kind 原值传递。 | "text" |
| metadata.units[].text | string | 必返 | 页面正文（可能是富文本 HTML），AI 先清洗再解析。 | "在服务台提交申请。" |
| content_hash | string | 必返 | 完整切片集合的内容摘要，双方按固定序列化规则计算。 | "sha256:example-content-digest" |
| chunks | array<object> | 必返 | 该快照完整切片，审批内修改后整体替换；被移除的切片不能留在新集合中。 |  |
| chunks[].chunk_id | string | 必返 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| chunks[].seq | integer(int64) | 必返 | 切片顺序，从 1 开始，本次列表内唯一。 【格式：int64；最小值 1】 | 1 |
| chunks[].heading | string | 可选 | 切片小标题，可省略。 | "VPN 申请流程" |
| chunks[].content | string | 必返 | 完整切片正文，不能为空；向量由 AI 生成，不传向量数组。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| chunks[].page_from | integer(int64) | 可选 | 起始页，页码从 1 开始；无分页内容可省略或 null。 【格式：int64；最小值 1】 | 1 |
| chunks[].page_to | integer(int64) | 可选 | 结束页，可省略或 null；不小于起始页。 【格式：int64；最小值 1】 | 1 |
| searchable | boolean | 必返 | AI 本地索引是否就绪；不是用户访问许可。 | true |
| next_cursor | string | 必返 | 下一页游标，结束为 null。 | null |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "knowledge_no": "KB-0000064",
  "metadata": {
    "title": "VPN申请操作手册",
    "doc_type": "Document",
    "owner": "kali03",
    "author": "kali03",
    "team": "TEAM-IT",
    "language": "中文",
    "trust": "A",
    "summary": "VPN申请与开通",
    "content": "VPN申请流程",
    "tags": [
      "VPN"
    ],
    "expire_at": "2027-12-31",
    "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
    "invisible_teams": [
      "TEAM-EXCLUDED"
    ],
    "audience": {
      "department": [
        "IT"
      ],
      "team": [
        "TEAM-IT"
      ],
      "role": [
        "工程师"
      ],
      "region": [
        "中国"
      ],
      "skill": [
        "基础"
      ],
      "scenario": [
        "VPN申请"
      ]
    },
    "cover_url": "https://files.example.com/cover.png",
    "cover_name": "cover.png",
    "linked_doc_id": "doc-source-001",
    "linked_section": "VPN申请",
    "change_impact_level": "minor",
    "units": [
      {
        "unit_id": "unit-1",
        "no": "U001",
        "title": "申请步骤",
        "kind": "text",
        "text": "在服务台提交申请。"
      }
    ]
  },
  "content_hash": "sha256:example-content-digest",
  "chunks": [
    {
      "chunk_id": "record-demo-001:1",
      "seq": 1,
      "heading": "VPN 申请流程",
      "content": "在服务台提交 VPN 申请，由负责人审核后开通账号。",
      "page_from": 1,
      "page_to": 1
    }
  ],
  "searchable": true,
  "next_cursor": null
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

```json
{"request_id":"req-20261029-002","error":{"code":"STALE_REVISION","message":"知识基线已发生变化，请读取最新内容后重新提交。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### 03.3 修改已批准切片集合

`PUT /v1/knowledge/entries/{publication_id}`

审批通过前的增删改均保存在 GSI 草稿；驳回或撤回不调用本接口。知识管理权限由 GSI 校验，AI 验证服务签名和资源归属，不查询 GSI 审批记录。 请求为审批通过后的全量切片（含增删改结果）。路径与正文 publication_id 必须一致；base_record_id 与当前切片基线不一致返回 409，不允许覆盖别人已发布的修订。保存新快照，保留旧快照直至 GSI 发布指针切换；每个发布单元独立处理。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| publication_id | string | 必填 | GSI 发布单元 ID；不是知识编号。 | "pub-demo-001" |

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| resource_type | string | 必填 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必填 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必填 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必填 | 该发布单元当前对应的不可变内容快照 ID。 | "record-approved-002" |
| version_id | string | 必填 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必填 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 102 |
| knowledge_no | string | 必填 | 页面知识编号，仅展示与追踪。 | "KB-0000064" |
| metadata | object | 必填 | 本次新增页面已保存的元数据快照。由 GSI 从保存结果构造；解析期间变更须新建 job_id。AI 仅返回建议，不覆盖人工选择，不据此发布或授权。 |  |
| metadata.title | string | 可选 | 知识标题。 | "VPN申请操作手册" |
| metadata.doc_type | string | 可选 | 文档类型，沿用页面枚举。 【枚举："Document" / "FAQ" / "Code" / "API" / "Test" / "Runbook" / "Handover"】 | "Document" |
| metadata.owner | string | 可选 | 负责人，沿用 GSI 页面保存值，不用姓名推断权限。 | "kali03" |
| metadata.author | string | 可选 | 实际维护人，沿用页面保存值。 | "kali03" |
| metadata.team | string | 可选 | 归属团队编码；与受众团队不是同一概念。 | "TEAM-IT" |
| metadata.language | string | 可选 | 页面选择的语言；与 options.language 保持一致。 | "中文" |
| metadata.trust | string | 可选 | 知识级别：A 正式知识、B 确认经验、C 参考经验。 【枚举："A" / "B" / "C"】 | "A" |
| metadata.summary | string | 可选 | 用户填写的摘要。 | "VPN申请与开通" |
| metadata.content | string | 可选 | 用户已填写的正文；未填写时省略。 | "VPN申请流程" |
| metadata.tags | array<string> | 可选 | 页面标签。 |  |
| metadata.expire_at | string | 可选 | 知识有效期，YYYY-MM-DD；未填写时省略。 【格式：date】 | "2027-12-31" |
| metadata.scheduled_publish_at | string | 可选 | 计划发布时间，ISO 8601，必须含时区；未填写时省略。 【格式：date-time】 | "2026-11-01T09:00:00+08:00" |
| metadata.invisible_teams | array<string> | 可选 | 不可见团队编码；解析上下文，最终消费权限读取已生效快照。 |  |
| metadata.audience | object | 可选 | 新增页面的受众维度；仅作为解析上下文，不能直接授权。实际权限以生效发布关系为准。 |  |
| metadata.audience.department | array<string> | 可选 | 部门；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.team | array<string> | 可选 | 受众团队；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.role | array<string> | 可选 | 角色；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.region | array<string> | 可选 | 地域；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.skill | array<string> | 可选 | 技能；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.audience.scenario | array<string> | 可选 | 场景；沿用页面值，team 由 GSI 规范为 group_code。 |  |
| metadata.cover_url | string | 可选 | 封面地址；不传 data URL，未填写时省略。 【格式：uri】 | "https://files.example.com/cover.png" |
| metadata.cover_name | string | 可选 | 封面文件名。 | "cover.png" |
| metadata.linked_doc_id | string | 可选 | 关联来源文档 ID，未关联时省略。 | "doc-source-001" |
| metadata.linked_section | string | 可选 | 关联章节。 | "VPN申请" |
| metadata.change_impact_level | string | 可选 | 修订影响级别；新增未填写时省略。 【枚举："minor" / "substantial"】 | "minor" |
| metadata.units | array<object> | 可选 | 新增页面已有知识单元；未生成时传 []。只作解析上下文，不视为正式切片。 |  |
| metadata.units[].unit_id | string | 可选 | 已有单元 ID，新建可省略。 | "unit-1" |
| metadata.units[].no | string | 必填 | 页面单元编号。 | "U001" |
| metadata.units[].title | string | 必填 | 单元标题。 | "申请步骤" |
| metadata.units[].kind | string | 必填 | 页面单元类型，按 KnowledgeUnit.kind 原值传递。 | "text" |
| metadata.units[].text | string | 必填 | 页面正文（可能是富文本 HTML），AI 先清洗再解析。 | "在服务台提交申请。" |
| content_hash | string | 必填 | 完整切片集合的内容摘要，双方按固定序列化规则计算。 | "sha256:example-content-digest" |
| chunks | array<object> | 必填 | 该快照完整切片，审批内修改后整体替换；被移除的切片不能留在新集合中。 |  |
| chunks[].chunk_id | string | 必填 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| chunks[].seq | integer(int64) | 必填 | 切片顺序，从 1 开始，本次列表内唯一。 【格式：int64；最小值 1】 | 1 |
| chunks[].heading | string | 可选 | 切片小标题，可省略。 | "VPN 申请流程" |
| chunks[].content | string | 必填 | 完整切片正文，不能为空；向量由 AI 生成，不传向量数组。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| chunks[].page_from | integer(int64) | 可选 | 起始页，页码从 1 开始；无分页内容可省略或 null。 【格式：int64；最小值 1】 | 1 |
| chunks[].page_to | integer(int64) | 可选 | 结束页，可省略或 null；不小于起始页。 【格式：int64；最小值 1】 | 1 |
| base_record_id | string | 必填 | 进入修订时的原快照 ID，用于知识并发校验；不是会话版本。 | "record-demo-001" |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-approved-002",
  "version_id": "1",
  "revision": 102,
  "knowledge_no": "KB-0000064",
  "metadata": {
    "title": "VPN申请操作手册",
    "doc_type": "Document",
    "owner": "kali03",
    "author": "kali03",
    "team": "TEAM-IT",
    "language": "中文",
    "trust": "A",
    "summary": "VPN申请与开通",
    "content": "VPN申请流程",
    "tags": [
      "VPN"
    ],
    "expire_at": "2027-12-31",
    "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
    "invisible_teams": [
      "TEAM-EXCLUDED"
    ],
    "audience": {
      "department": [
        "IT"
      ],
      "team": [
        "TEAM-IT"
      ],
      "role": [
        "工程师"
      ],
      "region": [
        "中国"
      ],
      "skill": [
        "基础"
      ],
      "scenario": [
        "VPN申请"
      ]
    },
    "cover_url": "https://files.example.com/cover.png",
    "cover_name": "cover.png",
    "linked_doc_id": "doc-source-001",
    "linked_section": "VPN申请",
    "change_impact_level": "minor",
    "units": [
      {
        "unit_id": "unit-1",
        "no": "U001",
        "title": "申请步骤",
        "kind": "text",
        "text": "在服务台提交申请。"
      }
    ]
  },
  "content_hash": "sha256:example-content-digest",
  "chunks": [
    {
      "chunk_id": "record-demo-001:1",
      "seq": 1,
      "heading": "VPN 申请流程",
      "content": "在服务台提交 VPN 申请，由负责人审核后开通账号。",
      "page_from": 1,
      "page_to": 1
    }
  ],
  "base_record_id": "record-demo-001"
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

同步完成回执，不返回仅排队的 202。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| success | boolean | 必返 | 操作完成。 | true |
| content_hash | string | 必返 | 完整切片集合的内容摘要，双方按固定序列化规则计算。 | "sha256:example-content-digest" |
| accepted_chunk_count | integer(int64) | 必返 | 本次完整切片条数；删除为 0。 【格式：int64；最小值 0】 | 1 |
| searchable | boolean | 必返 | AI 本地索引是否就绪；不是用户访问许可。 | true |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "success": true,
  "content_hash": "sha256:example-content-digest",
  "accepted_chunk_count": 1,
  "searchable": true
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

```json
{"request_id":"req-20261029-002","error":{"code":"STALE_REVISION","message":"知识基线已发生变化，请读取最新内容后重新提交。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### 03.4 删除已批准切片集合

`DELETE /v1/knowledge/entries/{publication_id}`

审批通过前的增删改均保存在 GSI 草稿；驳回或撤回不调用本接口。知识管理权限由 GSI 校验，AI 验证服务签名和资源归属，不查询 GSI 审批记录。 删除前 GSI 先撤销目标发布可见性，AI 删除指定 record_id 的正文和向量；不能用删除来保存审批中草稿。删除请求与较新修订冲突时返回 409。历史回答依赖该内容时不再展示。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| publication_id | string | 必填 | GSI 发布单元 ID；不是知识编号。 | "pub-demo-001" |

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| resource_type | string | 必填 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必填 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必填 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必填 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必填 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必填 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| reason | string | 必填 | 已批准删除的业务原因。 | "已审批废弃该知识切片集合" |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "reason": "已审批废弃该知识切片集合"
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

已完成删除，保留幂等及修订墓碑防止旧请求复活内容。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| success | boolean | 必返 | 删除完成。 | true |
| deleted | boolean | 必返 | 目标切片集合已删除。 | true |
| searchable | boolean | 必返 | 删除后固定 false。 | false |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "resource_type": "document",
  "resource_id": "doc-demo-001",
  "publication_id": "pub-demo-001",
  "record_id": "record-demo-001",
  "version_id": "1",
  "revision": 101,
  "success": true,
  "deleted": true,
  "searchable": false
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

```json
{"request_id":"req-20261029-002","error":{"code":"STALE_REVISION","message":"知识基线已发生变化，请读取最新内容后重新提交。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### 04 关键词 / 自然语言检索

`POST /v1/knowledge/search`

关键词和自然语言共用此接口。AI 根据可信 user_id 查询组织、受众成员、发布及生效快照表，在授权范围内检索后返回结果；没有团队或无有效发布返回空结果。不得先全库生成再过滤引用。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

GSI 计算授权后提交检索。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| query | string | 必填 | 查询词或自然语言问题，非空。 | "如何申请 VPN？" |
| mode | string | 必填 | keyword=关键词；natural_language=自然语言。 【枚举："keyword" / "natural_language"】 | "natural_language" |
| limit | integer(int64) | 必填 | 返回条数上限，1–100。 【格式：int64；最小值 1；最大值 100】 | 3 |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 必填 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "query": "如何申请 VPN？",
  "mode": "natural_language",
  "limit": 3,
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

按相关度排序的合法结果。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| hits | array<object> | 必返 | 命中列表，无证据时 []。 |  |
| hits[].resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| hits[].resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| hits[].publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| hits[].record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| hits[].version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| hits[].revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| hits[].knowledge_no | string | 必返 | 用户可见的知识编号，不作为权限标识。 | "KB-0000064" |
| hits[].chunk_id | string | 必返 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| hits[].title | string | 必返 | 知识标题。 | "VPN 申请操作手册" |
| hits[].snippet | string | 必返 | 命中摘要供页面展示；Chat 使用 AI 保存的已授权完整切片，不直接拿摘要代替正文。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| hits[].page_from | integer(int64) | 可选 | 起始页，页码从 1 开始；无分页内容可省略或 null。 【格式：int64；最小值 1】 | 1 |
| hits[].page_to | integer(int64) | 可选 | 结束页，可省略或 null；不小于起始页。 【格式：int64；最小值 1】 | 1 |
| hits[].score | number | 必返 | 召回相关度 0–1，不是回答正确率。 【最小值 0；最大值 1】 | 0.96 |
| returned_count | integer(int64) | 必返 | 实际返回条数，必须等于 hits.length。 【格式：int64；最小值 0】 | 1 |
| no_evidence | boolean | 必返 | hits 为空时 true，否则 false。 | false |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "hits": [
    {
      "resource_type": "document",
      "resource_id": "doc-demo-001",
      "publication_id": "pub-demo-001",
      "record_id": "record-demo-001",
      "version_id": "1",
      "revision": 101,
      "knowledge_no": "KB-0000064",
      "chunk_id": "record-demo-001:1",
      "title": "VPN 申请操作手册",
      "snippet": "在服务台提交 VPN 申请，由负责人审核后开通账号。",
      "page_from": 1,
      "page_to": 1,
      "score": 0.96
    }
  ],
  "returned_count": 1,
  "no_evidence": false
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

### 会话管理

#### 05 创建新会话

`POST /v1/chat/sessions`

创建属于 tenant_id + user_id 的会话，无会话版本；同 request_id 同正文重试返回原会话。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| title | string | 可选 | 可选标题，默认新对话，最多 100 字符。 【最大长度 100；默认："新对话"】 | "新对话" |
| authorization | object | 可选 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "title": "新对话",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 201 · Created：请求成功并创建了资源；响应体返回新资源标识。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| title | string | 必返 | 会话标题；默认新对话。由受限知识派生的标题必须随权限过滤。 | "新对话" |
| created_at | string | 必返 | 创建时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| updated_at | string | 必返 | 最后更新时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "title": "新对话",
  "created_at": "2026-10-27T02:00:00Z",
  "updated_at": "2026-10-27T02:00:00Z"
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 06 分页查询我的会话

`POST /v1/chat/sessions/query`

分页返回当前用户的会话；身份由 GSI 登录态提供。AI 对知识派生的标题与摘要实时校验权限，不暴露其他用户会话。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 必填 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |
| cursor | string | 可选 | 上一页返回的 next_cursor，原样传回；首次不传或 null；不能自己拼接。 | "eyJvZmZzZXQiOjIwfQ" |
| limit | integer(int64) | 可选 | 每页条数，缺省 20，范围 1–100。 【格式：int64；最小值 1；最大值 100；默认：20】 | 20 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  },
  "limit": 20
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| sessions | array<object> | 必返 | 当前页会话列表。 |  |
| sessions[].session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| sessions[].title | string | 必返 | 会话标题；默认新对话。由受限知识派生的标题必须随权限过滤。 | "新对话" |
| sessions[].created_at | string | 必返 | 创建时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| sessions[].updated_at | string | 必返 | 最后更新时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| next_cursor | string | 必返 | 下一页游标；null 表示最后一页。 | null |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "sessions": [
    {
      "session_id": "ai-session-001",
      "title": "新对话",
      "created_at": "2026-10-27T02:00:00Z",
      "updated_at": "2026-10-27T02:00:00Z"
    }
  ],
  "next_cursor": null
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 07 查询会话详情（可分页返回历史）

`POST /v1/chat/sessions/{session_id}/query`

无会话版本字段，也不要求写入前查询。include_messages=true 时按游标返回历史，替代原 10。AI 校验 tenant_id + user_id 归属，每次从指定表重算知识权限；撤权后的助手正文、引用及知识派生标题必须屏蔽。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| session_id | string | 必填 | AI 创建会话返回的 session_id，必须属于当前用户。 | "ai-session-001" |

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 必填 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |
| include_messages | boolean | 可选 | 是否返回历史；默认 false。 【默认：false】 | true |
| cursor | string | 可选 | 历史消息分页游标，第一页省略。 | "cursor-from-previous-page" |
| limit | integer(int64) | 可选 | 历史每页条数，默认 20，1–100。 【格式：int64；最小值 1；最大值 100；默认：20】 | 20 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  },
  "include_messages": true,
  "limit": 20
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

会话详情；include_messages=true 时 messages 和 next_cursor 条件必返（空历史返回 [] 和 null），否则省略这两项。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| title | string | 必返 | 会话标题；默认新对话。由受限知识派生的标题必须随权限过滤。 | "新对话" |
| created_at | string | 必返 | 创建时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| updated_at | string | 必返 | 最后更新时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| messages | array<object> | 可选 | 按时间顺序排列的历史消息。逐条按最新知识授权过滤。 |  |
| messages[].message_id | string | 必返 | AI 消息 ID。 | "ai-assistant-001" |
| messages[].role | string | 必返 | 消息角色。 【枚举："user" / "assistant"】 | "assistant" |
| messages[].content_visible | boolean | 必返 | 是否允许展示正文。false 时仅保留 ID、role、created_at、reason。 | true |
| messages[].created_at | string | 必返 | 创建时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| messages[].content | string | 可选 | content_visible=true 时必返；false 时禁止返回。 | "请在服务台提交 VPN 申请。[1]" |
| messages[].citations | array<object> | 可选 | 可见助手消息必返；user 消息可省略；被屏蔽时禁止返回。 |  |
| messages[].citations[].index | integer(int64) | 必返 | 引用序号，从 1 连续递增；与 answer 中 [1]、[2] 标记对应。 【格式：int64；最小值 1】 | 1 |
| messages[].citations[].citation_id | string | 必返 | GSI 给本轮证据设置的唯一引用别名，AI 原样返回。 | "c1" |
| messages[].citations[].resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| messages[].citations[].resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| messages[].citations[].publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| messages[].citations[].record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| messages[].citations[].version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| messages[].citations[].revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| messages[].citations[].chunk_id | string | 必返 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| messages[].citations[].quote | string | 必返 | 逐字引用的原文，必须能在对应 AI 已授权完整切片正文 中找到。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| messages[].source_snapshots | array<object> | 可选 | 可见助手消息必返完整来源；缺失时 GSI 隐藏答案。被屏蔽时不返回。 |  |
| messages[].source_snapshots[].resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| messages[].source_snapshots[].resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| messages[].source_snapshots[].publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| messages[].source_snapshots[].record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| messages[].source_snapshots[].version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| messages[].source_snapshots[].revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| messages[].no_evidence | boolean | 可选 | 可见助手消息必返，表示当时是否证据不足。 | false |
| messages[].reason | string | 可选 | content_visible=false 时必返 SOURCE_UNAVAILABLE。 【枚举："SOURCE_UNAVAILABLE"】 | "SOURCE_UNAVAILABLE" |
| next_cursor | string | 可选 | 下一页游标；null 表示最后一页。 | null |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "title": "新对话",
  "created_at": "2026-10-27T02:00:00Z",
  "updated_at": "2026-10-27T02:00:00Z",
  "messages": [
    {
      "message_id": "ai-assistant-001",
      "role": "assistant",
      "content_visible": true,
      "created_at": "2026-10-27T02:00:00Z",
      "content": "请在服务台提交 VPN 申请。[1]",
      "citations": [
        {
          "index": 1,
          "citation_id": "c1",
          "resource_type": "document",
          "resource_id": "doc-demo-001",
          "publication_id": "pub-demo-001",
          "record_id": "record-demo-001",
          "version_id": "1",
          "revision": 101,
          "chunk_id": "record-demo-001:1",
          "quote": "在服务台提交 VPN 申请，由负责人审核后开通账号。"
        }
      ],
      "source_snapshots": [
        {
          "resource_type": "document",
          "resource_id": "doc-demo-001",
          "publication_id": "pub-demo-001",
          "record_id": "record-demo-001",
          "version_id": "1",
          "revision": 101
        }
      ],
      "no_evidence": false
    }
  ],
  "next_cursor": null
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 08 修改会话标题

`PATCH /v1/chat/sessions/{session_id}`

只修改当前用户会话标题，不传会话版本。AI 内部按会话串行处理，request_id 保证幂等；被删会话不能通过迟到写入恢复。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| session_id | string | 必填 | AI 创建会话返回的 session_id，必须属于当前用户。 | "ai-session-001" |

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| title | string | 必填 | 新的非空标题，最多 100 字符。 【最小长度 1；最大长度 100】 | "VPN 咨询" |
| authorization | object | 可选 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "title": "VPN 咨询",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| title | string | 必返 | 会话标题；默认新对话。由受限知识派生的标题必须随权限过滤。 | "新对话" |
| created_at | string | 必返 | 创建时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |
| updated_at | string | 必返 | 最后更新时间，ISO 8601 时间字符串。 【格式：date-time】 | "2026-10-27T02:00:00Z" |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "title": "新对话",
  "created_at": "2026-10-27T02:00:00Z",
  "updated_at": "2026-10-27T02:00:00Z"
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 09 删除会话

`DELETE /v1/chat/sessions/{session_id}`

按可信身份删除会话，DELETE 正文包含 request_id、tenant_id、user_id。AI 使生成中的任务失效，禁止迟到回答重新写回；重试原请求返回原结果。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| session_id | string | 必填 | AI 创建会话返回的 session_id，必须属于当前用户。 | "ai-session-001" |

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 可选 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| deleted | boolean | 必返 | 删除成功为 true。 | true |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "deleted": true
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

### 对话历史

#### 11 清空会话历史

`DELETE /v1/chat/sessions/{session_id}/messages`

保留会话，清空消息、模型上下文、摘要及历史结果可见性；无需版本参数。AI 内部使旧生成任务失效，清空后不得恢复旧回答。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| session_id | string | 必填 | AI 创建会话返回的 session_id，必须属于当前用户。 | "ai-session-001" |

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 可选 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| cleared | boolean | 必返 | 清空成功为 true。 | true |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "cleared": true
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

### 对话生成

#### 12 发送问题并生成回答

`POST /v1/chat/completions`

GSI 传身份、问题、提示词及模型；AI 从指定表计算实时授权，在自有正式切片与 pgvector 中检索，读取合规历史后生成并保存问答。GSI 不传 history 或 contexts。AI 返回完整来源依赖，GSI 返回浏览器前再次核对发布与权限。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| session_id | string | 必填 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| question | string | 必填 | 本轮用户问题，非空。 | "如何申请 VPN？" |
| prompt_text | string | 必填 | GSI 服务端配置的提示词正文，不是 prompt_id。 | "仅依据本轮证据回答，引用用 [n]；证据不足时明确说明。" |
| model_key | string | 必填 | 双方约定的 Chat 模型标识，不是 model_id；当前 GSI 默认 default。 | "default" |
| stream | boolean | 必填 | 本期必须 false；true 返回 UNSUPPORTED_STREAM。 | false |
| authorization | object | 必填 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |
| options | object | 必填 | 输出设置。 |  |
| options.language | string | 必填 | 回答语言。 | "zh-CN" |
| options.max_output_tokens | integer(int64) | 必填 | 最大输出 token；请求范围 1–8192，实际可用上限需符合所选模型能力。 【格式：int64；最小值 1；最大值 8192】 | 1024 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "session_id": "ai-session-001",
  "question": "如何申请 VPN？",
  "prompt_text": "仅依据本轮证据回答，引用用 [n]；证据不足时明确说明。",
  "model_key": "default",
  "stream": false,
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  },
  "options": {
    "language": "zh-CN",
    "max_output_tokens": 1024
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

成功生成且已持久化用户和助手消息。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| session_id | string | 必返 | AI 分配的会话 ID；新 AI 会话不能使用 GSI 旧会话的 s- 前缀。 | "ai-session-001" |
| user_message_id | string | 必返 | AI 生成并持久化的用户消息 ID；幂等重试不改变。 | "ai-user-001" |
| assistant_message_id | string | 必返 | AI 生成并持久化的助手消息 ID；用于反馈。 | "ai-assistant-001" |
| status | string | 必返 | 本接口同步成功固定 completed。 【枚举："completed"】 | "completed" |
| answer | string | 必返 | 最终答案；有引用时使用 [n] 标记。证据不足时明确拒答。 | "请在服务台提交 VPN 申请，由负责人审核后开通账号。[1]" |
| citations | array<object> | 必返 | 本轮答案引用列表，无证据时为 []。 |  |
| citations[].index | integer(int64) | 必返 | 引用序号，从 1 连续递增；与 answer 中 [1]、[2] 标记对应。 【格式：int64；最小值 1】 | 1 |
| citations[].citation_id | string | 必返 | GSI 给本轮证据设置的唯一引用别名，AI 原样返回。 | "c1" |
| citations[].resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| citations[].resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| citations[].publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| citations[].record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| citations[].version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| citations[].revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| citations[].chunk_id | string | 必返 | GSI 生成的切片 ID，在本次切片集中唯一；后续检索和引用原样返回。 | "record-demo-001:1" |
| citations[].quote | string | 必返 | 逐字引用的原文，必须能在对应 AI 已授权完整切片正文 中找到。 | "在服务台提交 VPN 申请，由负责人审核后开通账号。" |
| source_snapshots | array<object> | 必返 | 进入本次生成上下文的全部知识和历史来源依赖，含未被显式引用的依赖。GSI 返回前逐项再次授权。 |  |
| source_snapshots[].resource_type | string | 必返 | 知识类型。document=文档，faq=问答。 【枚举："document" / "faq"】 | "document" |
| source_snapshots[].resource_id | string | 必返 | GSI 知识主记录 ID；不是知识编号，也不是解析任务 ID。 | "doc-demo-001" |
| source_snapshots[].publication_id | string | 必返 | GSI 发布单元 ID；同一知识可有多个发布单元。 | "pub-demo-001" |
| source_snapshots[].record_id | string | 必返 | 该发布单元当前对应的不可变内容快照 ID。 | "record-demo-001" |
| source_snapshots[].version_id | string | 必返 | 业务版本标识，字符串；不要转成数字比较。 | "1" |
| source_snapshots[].revision | integer(int64) | 必返 | GSI 生成的索引修订号；新事件必须高于同一发布单元已记录值。 【格式：int64；最小值 1】 | 101 |
| authorization_scope_id | string | 必返 | 原样返回请求 authorization.scope_id，用于对应授权。 | "scope-001" |
| no_evidence | boolean | 必返 | true=没有足够证据回答；不能使用模型常识编造企业规则。 | false |
| finish_reason | string | 必返 | 结束原因，stop=完成，no_evidence=证据不足。 【枚举："stop" / "no_evidence"】 | "stop" |

**响应示例 · success**（成功结构示例（时间与 ID 需替换））

```json
{
  "request_id": "request-001",
  "session_id": "ai-session-001",
  "user_message_id": "ai-user-001",
  "assistant_message_id": "ai-assistant-001",
  "status": "completed",
  "answer": "请在服务台提交 VPN 申请，由负责人审核后开通账号。[1]",
  "citations": [
    {
      "index": 1,
      "citation_id": "c1",
      "resource_type": "document",
      "resource_id": "doc-demo-001",
      "publication_id": "pub-demo-001",
      "record_id": "record-demo-001",
      "version_id": "1",
      "revision": 101,
      "chunk_id": "record-demo-001:1",
      "quote": "在服务台提交 VPN 申请，由负责人审核后开通账号。"
    }
  ],
  "source_snapshots": [
    {
      "resource_type": "document",
      "resource_id": "doc-demo-001",
      "publication_id": "pub-demo-001",
      "record_id": "record-demo-001",
      "version_id": "1",
      "revision": 101
    }
  ],
  "authorization_scope_id": "scope-001",
  "no_evidence": false,
  "finish_reason": "stop"
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

#### 13 查询超时操作的结果

`POST /v1/chat/requests/{target_request_id}/query`

用于恢复响应丢失或生成超时。正文 request_id 为查询请求的新 ID，路径 target_request_id 为原操作 ID；返回原操作的实际处理状态。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| target_request_id | string | 必填 | 要查询的原操作 request_id；与本次查询正文里的新 request_id 不同。 | "request-original-001" |

**请求正文**（`application/json`，UTF-8）

由 GSI 后端构造，不能透传浏览器自报的用户身份或权限。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| tenant_id | string | 必填 | GSI 服务端配置的租户标识，当前固定 gsi-default；不是前端可选参数。 | "gsi-default" |
| user_id | string | 必填 | GSI 当前登录用户的稳定数字主键，转成字符串；不是用户名。 | "42" |
| authorization | object | 必填 | 请求追踪及短期授权时效。AI 使用签名身份查询指定业务表计算权限；不再接收 GSI 枚举的授权快照列表。 |  |
| authorization.scope_id | string | 必填 | GSI 本次授权追踪标识。 | "scope-001" |
| authorization.issued_at | integer(int64) | 必填 | 签发时间，Unix 毫秒；运行时使用当前时间。 【格式：int64】 | 1800000000000 |
| authorization.expires_at | integer(int64) | 必填 | 到期时间，Unix 毫秒；最长 120 秒，过期拒绝。 【格式：int64】 | 1800000120000 |

**请求示例**

```json
{
  "request_id": "request-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "authorization": {
    "scope_id": "scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 返回的成功响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成的本次操作 ID。写操作幂等键：同 ID 同正文重试，换正文必须换 ID。 | "request-001" |
| operation | string | 必返 | 原操作类别。completion=生成，mutation=创建/改名/清空，delete=删除。 【枚举："completion" / "mutation" / "delete"】 | "completion" |
| status | string | 必返 | 原操作状态：completed 完成、pending 处理中、failed 失败、cancelled 已取消。 【枚举："pending" / "completed" / "failed" / "cancelled"】 | "completed" |
| result_visible | boolean | 可选 | completed 时必返。false 表示旧结果因撤权、清空等不可展示。 | true |
| result |  | 可选 | 仅 completed 且 result_visible=true 时返回原操作结果；结构按 operation 判断。不是永远返回 Chat 答案。 |  |
| reason | string | 可选 | result_visible=false 时必返，不返回 result。 【枚举："SOURCE_UNAVAILABLE"】 | "SOURCE_UNAVAILABLE" |
| error | object | 可选 | 错误详情。 |  |
| error.code | string | 必返 | 稳定的机器错误码。 | "INVALID_REQUEST" |
| error.message | string | 必返 | 说明错误原因，不泄漏密钥或受限内容。 | "参数无效，请检查必填字段。" |
| error.retryable | boolean | 必返 | 是否允许按原业务请求重试。重试须重新生成签名，不改变正文。 | false |

**响应示例 · completed**（生成完成且当前仍有权限）

```json
{
  "request_id": "query-001",
  "operation": "completion",
  "status": "completed",
  "result_visible": true,
  "result": {
    "request_id": "request-001",
    "session_id": "ai-session-001",
    "user_message_id": "ai-user-001",
    "assistant_message_id": "ai-assistant-001",
    "status": "completed",
    "answer": "请在服务台提交 VPN 申请，由负责人审核后开通账号。[1]",
    "citations": [
      {
        "index": 1,
        "citation_id": "c1",
        "resource_type": "document",
        "resource_id": "doc-demo-001",
        "publication_id": "pub-demo-001",
        "record_id": "record-demo-001",
        "version_id": "1",
        "revision": 101,
        "chunk_id": "record-demo-001:1",
        "quote": "在服务台提交 VPN 申请，由负责人审核后开通账号。"
      }
    ],
    "source_snapshots": [
      {
        "resource_type": "document",
        "resource_id": "doc-demo-001",
        "publication_id": "pub-demo-001",
        "record_id": "record-demo-001",
        "version_id": "1",
        "revision": 101
      }
    ],
    "authorization_scope_id": "scope-001",
    "no_evidence": false,
    "finish_reason": "stop"
  }
}
```

**响应示例 · running**（原请求仍在处理中）

```json
{
  "request_id": "query-001",
  "operation": "completion",
  "status": "pending",
  "result_visible": false
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 500 · Internal Server Error：服务发生意外内部错误，无法完成请求。**

```json
{"request_id":"req-20261029-001","error":{"code":"INTERNAL_ERROR","message":"服务内部处理失败，请联系服务方排查。","retryable":false}}
```


---

### 重复治理

#### D01 保存前重复检查

`POST /v1/knowledge/duplicates/check`

用于新增/修订保存前提示。只比较候选与授权范围；排除相同 resource_type + resource_id 的旧版本，避免自己与自己重复。不阻止保存、不自动修改业务数据。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| tenant_id | string | 必填 | 可信服务配置租户，当前 gsi-default。 | "gsi-default" |
| user_id | string | 必填 | 登录用户数字主键字符串；不能透传页面自报值。 | "42" |
| candidate | object | 必填 | 仅用于本次比较的草稿快照。AI 不得写入正式索引、问答语料或永久训练集。 |  |
| candidate.resource | object | 必填 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| candidate.resource.resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| candidate.resource.resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| candidate.resource.record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| candidate.resource.content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| candidate.resource.publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| candidate.resource.entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| candidate.title | string | 必填 | 保存前标题。 | "VPN申请" |
| candidate.summary | string | 必填 | 摘要，可空。 | "VPN申请流程" |
| candidate.content | string | 必填 | 已清洗的纯文本正文；FAQ 为答案。 | "通过服务台提交 VPN 申请。" |
| candidate.chunks | array<object> | 必填 | 草稿切片；未切片时 []。 |  |
| candidate.chunks[].chunk_id | string | 必填 | 草稿中稳定的切片标识。 | "chunk-001" |
| candidate.chunks[].title | string | 必填 | 切片标题。 | "申请入口" |
| candidate.chunks[].content | string | 必填 | 纯文本内容。 | "通过服务台提交 VPN 申请。" |
| scope | object | 必填 | GSI 后端按知识管理权限签发的范围，不是座席消费授权。每次请求重新核对；AI 只能比较这里列出的内容。 |  |
| scope.scope_id | string | 必填 | 本次范围追踪 ID。 | "gov-scope-001" |
| scope.issued_at | integer | 必填 | Unix 毫秒，示例时间须替换。 | 1800000000000 |
| scope.expires_at | integer | 必填 | 最长有效 120 秒。 | 1800000120000 |
| scope.resources | array<object> | 必填 | 授权治理内容清单，最多 1000；[] 表示无候选。过大返回 413，不静默截断。 |  |
| scope.resources[].resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.resources[].resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.resources[].record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.resources[].content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.resources[].publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.resources[].entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts | array<object> | 必填 | resources 中 AI 尚无正式内容的 GSI 草稿/审批快照；最多 1000；必须与清单身份和摘要一致。 |  |
| scope.drafts[].resource | object | 必填 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| scope.drafts[].resource.resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.drafts[].resource.resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.drafts[].resource.record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.drafts[].resource.content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.drafts[].resource.publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.drafts[].resource.entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts[].title | string | 必填 | 保存前标题。 | "VPN申请" |
| scope.drafts[].summary | string | 必填 | 摘要，可空。 | "VPN申请流程" |
| scope.drafts[].content | string | 必填 | 已清洗的纯文本正文；FAQ 为答案。 | "通过服务台提交 VPN 申请。" |
| scope.drafts[].chunks | array<object> | 必填 | 草稿切片；未切片时 []。 |  |
| scope.drafts[].chunks[].chunk_id | string | 必填 | 草稿中稳定的切片标识。 | "chunk-001" |
| scope.drafts[].chunks[].title | string | 必填 | 切片标题。 | "申请入口" |
| scope.drafts[].chunks[].content | string | 必填 | 纯文本内容。 | "通过服务台提交 VPN 申请。" |
| score_profile | string | 必填 | 双方配置的检测策略标识。算法及阈值版本固定，AI 返回实际使用的策略版本。 | "gsi-duplicate-v1" |
| kinds | array<string> | 必填 | 本次治理类型。 |  |

**请求示例**

```json
{
  "request_id": "req-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "candidate": {
    "resource": {
      "resource_type": "document",
      "resource_id": "doc-new",
      "record_id": "draft-new",
      "content_hash": "sha256:new-content",
      "publication_ids": [],
      "entry_mode": "manual"
    },
    "title": "VPN申请",
    "summary": "VPN申请流程",
    "content": "通过服务台提交 VPN 申请。",
    "chunks": [
      {
        "chunk_id": "chunk-001",
        "title": "申请入口",
        "content": "通过服务台提交 VPN 申请。"
      }
    ]
  },
  "scope": {
    "scope_id": "gov-scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000,
    "resources": [
      {
        "resource_type": "document",
        "resource_id": "doc-001",
        "record_id": "record-001",
        "content_hash": "sha256:content-001",
        "publication_ids": [
          "pub-001"
        ],
        "entry_mode": "manual"
      },
      {
        "resource_type": "document",
        "resource_id": "doc-002",
        "record_id": "record-002",
        "content_hash": "sha256:content-002",
        "publication_ids": [
          "pub-002"
        ],
        "entry_mode": "manual"
      }
    ],
    "drafts": []
  },
  "score_profile": "gsi-duplicate-v1",
  "kinds": [
    "doc_similar"
  ]
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 裸 JSON 响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| is_duplicate | boolean | 必返 | 是否达到当前策略阈值。 | true |
| similarity | number | 必返 | 最高匹配相似度；无匹配为 0。 【最小值 0；最大值 1】 | 0.91 |
| score_profile | string | 必返 | 双方配置的检测策略标识。算法及阈值版本固定，AI 返回实际使用的策略版本。 | "gsi-duplicate-v1" |
| matches | array<object> | 必返 | 重复候选，用于展示并定位；GSI 转换为 duplicateDocIds，不能混用文档与 FAQ 主键。 |  |
| matches[].hit_id | string | 必返 | 检测项稳定 ID；与来源内容身份绑定。 | "hit-001" |
| matches[].hit_kind | string | 必返 | doc=整篇，faq=问答，chunk=单切片。 【枚举："doc" / "faq" / "chunk"】 | "doc" |
| matches[].resource | object | 必返 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| matches[].resource.resource_type | string | 必返 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| matches[].resource.resource_id | string | 必返 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| matches[].resource.record_id | string | 必返 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| matches[].resource.content_hash | string | 必返 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| matches[].resource.publication_ids | array<string> | 必返 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| matches[].resource.entry_mode | string | 必返 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| matches[].chunk_id | string | 可选 | hit_kind=chunk 时必返。 | "chunk-001" |
| matches[].title | string | 必返 | 标题。 | "VPN申请" |
| matches[].subtitle | string | 可选 | 版本、负责人、团队等展示信息；GSI 可从业务数据补齐。 | "正式知识 · IT团队" |
| matches[].snippet | string | 必返 | 比较内容摘要。 | "通过服务台提交申请" |
| matches[].full_text | string | 可选 | 详情请求时必返完整比较文本；列表省略。 | "通过服务台提交 VPN 申请。" |
| matches[].similarity | number | 必返 | 0–1，按 score_method 解释，不是重复概率。 【最小值 0；最大值 1】 | 0.91 |
| matches[].reason | string | 必返 | 中文判断依据。 | "标题相近，申请步骤重合。" |

**响应示例**

```json
{
  "request_id": "req-001",
  "is_duplicate": true,
  "similarity": 0.91,
  "score_profile": "gsi-duplicate-v1",
  "matches": [
    {
      "hit_id": "hit-001",
      "hit_kind": "doc",
      "resource": {
        "resource_type": "document",
        "resource_id": "doc-001",
        "record_id": "record-001",
        "content_hash": "sha256:content-001",
        "publication_ids": [
          "pub-001"
        ],
        "entry_mode": "manual"
      },
      "title": "VPN申请",
      "subtitle": "正式知识 · IT团队",
      "snippet": "通过服务台提交申请",
      "similarity": 0.91,
      "reason": "标题相近，申请步骤重合。"
    }
  ]
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### D02 查询重复分组

`POST /v1/knowledge/duplicates/groups/query`

统一文档、FAQ 和 Chunk 分组查询，替代多个页面接口各自实现检测。首次传 scope/kinds，AI 计算范围内完整分组并保存只读结果；翻页带 result_id/cursor 和重新签发的同范围 scope。范围、内容或策略变化必须重建结果。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| tenant_id | string | 必填 | 可信服务配置租户，当前 gsi-default。 | "gsi-default" |
| user_id | string | 必填 | 登录用户数字主键字符串；不能透传页面自报值。 | "42" |
| scope | object | 必填 | GSI 后端按知识管理权限签发的范围，不是座席消费授权。每次请求重新核对；AI 只能比较这里列出的内容。 |  |
| scope.scope_id | string | 必填 | 本次范围追踪 ID。 | "gov-scope-001" |
| scope.issued_at | integer | 必填 | Unix 毫秒，示例时间须替换。 | 1800000000000 |
| scope.expires_at | integer | 必填 | 最长有效 120 秒。 | 1800000120000 |
| scope.resources | array<object> | 必填 | 授权治理内容清单，最多 1000；[] 表示无候选。过大返回 413，不静默截断。 |  |
| scope.resources[].resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.resources[].resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.resources[].record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.resources[].content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.resources[].publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.resources[].entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts | array<object> | 必填 | resources 中 AI 尚无正式内容的 GSI 草稿/审批快照；最多 1000；必须与清单身份和摘要一致。 |  |
| scope.drafts[].resource | object | 必填 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| scope.drafts[].resource.resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.drafts[].resource.resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.drafts[].resource.record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.drafts[].resource.content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.drafts[].resource.publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.drafts[].resource.entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts[].title | string | 必填 | 保存前标题。 | "VPN申请" |
| scope.drafts[].summary | string | 必填 | 摘要，可空。 | "VPN申请流程" |
| scope.drafts[].content | string | 必填 | 已清洗的纯文本正文；FAQ 为答案。 | "通过服务台提交 VPN 申请。" |
| scope.drafts[].chunks | array<object> | 必填 | 草稿切片；未切片时 []。 |  |
| scope.drafts[].chunks[].chunk_id | string | 必填 | 草稿中稳定的切片标识。 | "chunk-001" |
| scope.drafts[].chunks[].title | string | 必填 | 切片标题。 | "申请入口" |
| scope.drafts[].chunks[].content | string | 必填 | 纯文本内容。 | "通过服务台提交 VPN 申请。" |
| kinds | array<string> | 必填 | 本次治理类型。 |  |
| score_profile | string | 必填 | 双方配置的检测策略标识。算法及阈值版本固定，AI 返回实际使用的策略版本。 | "gsi-duplicate-v1" |
| result_id | string | 可选 | AI 返回的只读检测结果 ID，绑定租户、用户、范围摘要和内容摘要，保留 30 分钟。 | "dup-result-001" |
| cursor | string | 可选 | 结果内分页游标；首次省略，结束为 null。 | null |
| limit | integer | 可选 | 每页分组数，默认 20，1–100。 【最小值 1；最大值 100；默认：20】 | 20 |

**请求示例**

```json
{
  "request_id": "req-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "scope": {
    "scope_id": "gov-scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000,
    "resources": [
      {
        "resource_type": "document",
        "resource_id": "doc-001",
        "record_id": "record-001",
        "content_hash": "sha256:content-001",
        "publication_ids": [
          "pub-001"
        ],
        "entry_mode": "manual"
      },
      {
        "resource_type": "document",
        "resource_id": "doc-002",
        "record_id": "record-002",
        "content_hash": "sha256:content-002",
        "publication_ids": [
          "pub-002"
        ],
        "entry_mode": "manual"
      }
    ],
    "drafts": []
  },
  "kinds": [
    "doc_similar"
  ],
  "score_profile": "gsi-duplicate-v1",
  "limit": 20
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 裸 JSON 响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| result_id | string | 必返 | AI 返回的只读检测结果 ID，绑定租户、用户、范围摘要和内容摘要，保留 30 分钟。 | "dup-result-001" |
| expires_at | string | 必返 | 结果到期时间，ISO 8601。 【格式：date-time】 | "2026-10-28T10:30:00+08:00" |
| score_profile | string | 必返 | 双方配置的检测策略标识。算法及阈值版本固定，AI 返回实际使用的策略版本。 | "gsi-duplicate-v1" |
| groups | array<object> | 必返 | 当前页分组；确实无结果为 []。 |  |
| groups[].group_id | string | 必返 | 稳定分组键：类型 + 排序后的成员身份摘要；内容变化产生新键。 | "dup-group-001" |
| groups[].kind | string | 必返 | 沿用当前页面五类治理分组。 【枚举："doc_exact" / "doc_similar" / "chunk" / "faq_ai" / "faq_manual"】 | "doc_similar" |
| groups[].title | string | 必返 | 分组显示标题。 | "VPN申请" |
| groups[].similarity | number | 必返 | 组内推荐保留项与各成员相似度的最小值，避免把传递关联当两两相同。 【最小值 0；最大值 1】 | 0.91 |
| groups[].score_method | string | 必返 | 检测方法与版本，可区分标题字符串/全文一致/语义；不能直接套搜索相关度。 | "title_similarity_v1" |
| groups[].reason | string | 必返 | 中文分组依据。 | "两篇文档标题相近。" |
| groups[].items | array<object> | 必返 | 分组成员。 |  |
| groups[].items[].hit_id | string | 必返 | 检测项稳定 ID；与来源内容身份绑定。 | "hit-001" |
| groups[].items[].hit_kind | string | 必返 | doc=整篇，faq=问答，chunk=单切片。 【枚举："doc" / "faq" / "chunk"】 | "doc" |
| groups[].items[].resource | object | 必返 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| groups[].items[].resource.resource_type | string | 必返 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| groups[].items[].resource.resource_id | string | 必返 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| groups[].items[].resource.record_id | string | 必返 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| groups[].items[].resource.content_hash | string | 必返 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| groups[].items[].resource.publication_ids | array<string> | 必返 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| groups[].items[].resource.entry_mode | string | 必返 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| groups[].items[].chunk_id | string | 可选 | hit_kind=chunk 时必返。 | "chunk-001" |
| groups[].items[].title | string | 必返 | 标题。 | "VPN申请" |
| groups[].items[].subtitle | string | 可选 | 版本、负责人、团队等展示信息；GSI 可从业务数据补齐。 | "正式知识 · IT团队" |
| groups[].items[].snippet | string | 必返 | 比较内容摘要。 | "通过服务台提交申请" |
| groups[].items[].full_text | string | 可选 | 详情请求时必返完整比较文本；列表省略。 | "通过服务台提交 VPN 申请。" |
| groups[].items[].similarity | number | 必返 | 0–1，按 score_method 解释，不是重复概率。 【最小值 0；最大值 1】 | 0.91 |
| groups[].items[].reason | string | 必返 | 中文判断依据。 | "标题相近，申请步骤重合。" |
| groups[].suggested_keep_id | string | 必返 | 建议主项 hit_id，必须属于 items；仅建议。 | "hit-001" |
| next_cursor | string | 必返 | 结果内分页游标；首次省略，结束为 null。 | null |
| total_groups | integer | 必返 | 该固定检测结果的全部分组数。 【最小值 0】 | 1 |
| coverage | object | 必返 | 仅代表指定范围完整，不能声称全库无重复。 |  |
| coverage.requested_resources | integer | 必返 | 本次输入候选数量。 【最小值 0】 | 2 |
| coverage.evaluated_resources | integer | 必返 | 实际完成比较的候选数量；成功时必须等于 requested_resources。 【最小值 0】 | 2 |
| coverage.complete | boolean | 必返 | 200 固定 true；不能用不完整检测充当成功。 | true |

**响应示例**

```json
{
  "request_id": "req-001",
  "result_id": "dup-result-001",
  "expires_at": "2026-10-28T10:30:00+08:00",
  "score_profile": "gsi-duplicate-v1",
  "groups": [
    {
      "group_id": "dup-group-001",
      "kind": "doc_similar",
      "title": "VPN申请",
      "similarity": 0.91,
      "score_method": "title_similarity_v1",
      "reason": "两篇文档标题相近。",
      "items": [
        {
          "hit_id": "hit-001",
          "hit_kind": "doc",
          "resource": {
            "resource_type": "document",
            "resource_id": "doc-001",
            "record_id": "record-001",
            "content_hash": "sha256:content-001",
            "publication_ids": [
              "pub-001"
            ],
            "entry_mode": "manual"
          },
          "title": "VPN申请",
          "subtitle": "正式知识 · IT团队",
          "snippet": "通过服务台提交申请",
          "similarity": 0.91,
          "reason": "标题相近，申请步骤重合。"
        },
        {
          "hit_id": "hit-002",
          "hit_kind": "doc",
          "resource": {
            "resource_type": "document",
            "resource_id": "doc-002",
            "record_id": "record-002",
            "content_hash": "sha256:content-002",
            "publication_ids": [
              "pub-002"
            ],
            "entry_mode": "manual"
          },
          "title": "VPN开通申请",
          "subtitle": "正式知识 · IT团队",
          "snippet": "通过服务台提交申请",
          "similarity": 0.91,
          "reason": "标题相近，申请步骤重合。"
        }
      ],
      "suggested_keep_id": "hit-001"
    }
  ],
  "next_cursor": null,
  "total_groups": 1,
  "coverage": {
    "requested_resources": 2,
    "evaluated_resources": 2,
    "complete": true
  }
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### D03 查询分组详情与对比依据

`POST /v1/knowledge/duplicates/groups/{group_id}/query`

对应进入合并、全文对比。重新校验治理权限及成员内容摘要，任何成员无权/变化则返回 403/409，不能返回旧缓存正文。结果属于当前 tenant_id + user_id，过期后重新检测。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| group_id | string | 必填 | 检测结果中的分组标识。 | "dup-group-001" |

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| tenant_id | string | 必填 | 可信服务配置租户，当前 gsi-default。 | "gsi-default" |
| user_id | string | 必填 | 登录用户数字主键字符串；不能透传页面自报值。 | "42" |
| result_id | string | 必填 | AI 返回的只读检测结果 ID，绑定租户、用户、范围摘要和内容摘要，保留 30 分钟。 | "dup-result-001" |
| scope | object | 必填 | GSI 后端按知识管理权限签发的范围，不是座席消费授权。每次请求重新核对；AI 只能比较这里列出的内容。 |  |
| scope.scope_id | string | 必填 | 本次范围追踪 ID。 | "gov-scope-001" |
| scope.issued_at | integer | 必填 | Unix 毫秒，示例时间须替换。 | 1800000000000 |
| scope.expires_at | integer | 必填 | 最长有效 120 秒。 | 1800000120000 |
| scope.resources | array<object> | 必填 | 授权治理内容清单，最多 1000；[] 表示无候选。过大返回 413，不静默截断。 |  |
| scope.resources[].resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.resources[].resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.resources[].record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.resources[].content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.resources[].publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.resources[].entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts | array<object> | 必填 | resources 中 AI 尚无正式内容的 GSI 草稿/审批快照；最多 1000；必须与清单身份和摘要一致。 |  |
| scope.drafts[].resource | object | 必填 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| scope.drafts[].resource.resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.drafts[].resource.resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.drafts[].resource.record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.drafts[].resource.content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.drafts[].resource.publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.drafts[].resource.entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts[].title | string | 必填 | 保存前标题。 | "VPN申请" |
| scope.drafts[].summary | string | 必填 | 摘要，可空。 | "VPN申请流程" |
| scope.drafts[].content | string | 必填 | 已清洗的纯文本正文；FAQ 为答案。 | "通过服务台提交 VPN 申请。" |
| scope.drafts[].chunks | array<object> | 必填 | 草稿切片；未切片时 []。 |  |
| scope.drafts[].chunks[].chunk_id | string | 必填 | 草稿中稳定的切片标识。 | "chunk-001" |
| scope.drafts[].chunks[].title | string | 必填 | 切片标题。 | "申请入口" |
| scope.drafts[].chunks[].content | string | 必填 | 纯文本内容。 | "通过服务台提交 VPN 申请。" |

**请求示例**

```json
{
  "request_id": "req-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "result_id": "dup-result-001",
  "scope": {
    "scope_id": "gov-scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000,
    "resources": [
      {
        "resource_type": "document",
        "resource_id": "doc-001",
        "record_id": "record-001",
        "content_hash": "sha256:content-001",
        "publication_ids": [
          "pub-001"
        ],
        "entry_mode": "manual"
      },
      {
        "resource_type": "document",
        "resource_id": "doc-002",
        "record_id": "record-002",
        "content_hash": "sha256:content-002",
        "publication_ids": [
          "pub-002"
        ],
        "entry_mode": "manual"
      }
    ],
    "drafts": []
  }
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 裸 JSON 响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| result_id | string | 必返 | AI 返回的只读检测结果 ID，绑定租户、用户、范围摘要和内容摘要，保留 30 分钟。 | "dup-result-001" |
| group | object | 必返 | 重复分组，至少包含两个不同对象；所有成员必须位于请求的治理范围内。 |  |
| group.group_id | string | 必返 | 稳定分组键：类型 + 排序后的成员身份摘要；内容变化产生新键。 | "dup-group-001" |
| group.kind | string | 必返 | 沿用当前页面五类治理分组。 【枚举："doc_exact" / "doc_similar" / "chunk" / "faq_ai" / "faq_manual"】 | "doc_similar" |
| group.title | string | 必返 | 分组显示标题。 | "VPN申请" |
| group.similarity | number | 必返 | 组内推荐保留项与各成员相似度的最小值，避免把传递关联当两两相同。 【最小值 0；最大值 1】 | 0.91 |
| group.score_method | string | 必返 | 检测方法与版本，可区分标题字符串/全文一致/语义；不能直接套搜索相关度。 | "title_similarity_v1" |
| group.reason | string | 必返 | 中文分组依据。 | "两篇文档标题相近。" |
| group.items | array<object> | 必返 | 分组成员。 |  |
| group.items[].hit_id | string | 必返 | 检测项稳定 ID；与来源内容身份绑定。 | "hit-001" |
| group.items[].hit_kind | string | 必返 | doc=整篇，faq=问答，chunk=单切片。 【枚举："doc" / "faq" / "chunk"】 | "doc" |
| group.items[].resource | object | 必返 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| group.items[].resource.resource_type | string | 必返 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| group.items[].resource.resource_id | string | 必返 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| group.items[].resource.record_id | string | 必返 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| group.items[].resource.content_hash | string | 必返 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| group.items[].resource.publication_ids | array<string> | 必返 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| group.items[].resource.entry_mode | string | 必返 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| group.items[].chunk_id | string | 可选 | hit_kind=chunk 时必返。 | "chunk-001" |
| group.items[].title | string | 必返 | 标题。 | "VPN申请" |
| group.items[].subtitle | string | 可选 | 版本、负责人、团队等展示信息；GSI 可从业务数据补齐。 | "正式知识 · IT团队" |
| group.items[].snippet | string | 必返 | 比较内容摘要。 | "通过服务台提交申请" |
| group.items[].full_text | string | 可选 | 详情请求时必返完整比较文本；列表省略。 | "通过服务台提交 VPN 申请。" |
| group.items[].similarity | number | 必返 | 0–1，按 score_method 解释，不是重复概率。 【最小值 0；最大值 1】 | 0.91 |
| group.items[].reason | string | 必返 | 中文判断依据。 | "标题相近，申请步骤重合。" |
| group.suggested_keep_id | string | 必返 | 建议主项 hit_id，必须属于 items；仅建议。 | "hit-001" |
| evidence | array<object> | 必返 | 成对原文依据，引用必须能在 full_text 中找到。 |  |
| evidence[].left_hit_id | string | 必返 | 左侧项。 | "hit-001" |
| evidence[].right_hit_id | string | 必返 | 右侧项。 | "hit-002" |
| evidence[].basis | string | 必返 | 证据类型。 【枚举："title_exact" / "title_similarity" / "content_exact" / "semantic" / "answer_overlap"】 | "title_similarity" |
| evidence[].left_quote | string | 必返 | 左侧原文连续片段。 | "VPN申请" |
| evidence[].right_quote | string | 必返 | 右侧原文连续片段。 | "VPN开通申请" |
| evidence[].explanation | string | 必返 | 相似点及差异说明。 | "标题接近，但应检查审批人和适用团队是否不同。" |

**响应示例**

```json
{
  "request_id": "req-001",
  "result_id": "dup-result-001",
  "group": {
    "group_id": "dup-group-001",
    "kind": "doc_similar",
    "title": "VPN申请",
    "similarity": 0.91,
    "score_method": "title_similarity_v1",
    "reason": "两篇文档标题相近。",
    "items": [
      {
        "hit_id": "hit-001",
        "hit_kind": "doc",
        "resource": {
          "resource_type": "document",
          "resource_id": "doc-001",
          "record_id": "record-001",
          "content_hash": "sha256:content-001",
          "publication_ids": [
            "pub-001"
          ],
          "entry_mode": "manual"
        },
        "title": "VPN申请",
        "subtitle": "正式知识 · IT团队",
        "snippet": "通过服务台提交申请",
        "similarity": 0.91,
        "reason": "标题相近，申请步骤重合。",
        "full_text": "VPN申请：通过服务台提交 VPN 申请。"
      },
      {
        "hit_id": "hit-002",
        "hit_kind": "doc",
        "resource": {
          "resource_type": "document",
          "resource_id": "doc-002",
          "record_id": "record-002",
          "content_hash": "sha256:content-002",
          "publication_ids": [
            "pub-002"
          ],
          "entry_mode": "manual"
        },
        "title": "VPN开通申请",
        "subtitle": "正式知识 · IT团队",
        "snippet": "通过服务台提交申请",
        "similarity": 0.91,
        "reason": "标题相近，申请步骤重合。",
        "full_text": "VPN开通申请：通过服务台提交 VPN 开通申请。"
      }
    ],
    "suggested_keep_id": "hit-001"
  },
  "evidence": [
    {
      "left_hit_id": "hit-001",
      "right_hit_id": "hit-002",
      "basis": "title_similarity",
      "left_quote": "VPN申请",
      "right_quote": "VPN开通申请",
      "explanation": "标题接近，但应检查审批人和适用团队是否不同。"
    }
  ]
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### D04 生成合并预览（不落正式库）

`POST /v1/knowledge/duplicates/merge-preview`

对应 FAQ 答案合并预览。非 FAQ 仅返回处置摘要，不擅自合成文档。用户人工确认后由 GSI 保存冻结预览及审批单；此接口绝不执行合并、废弃或删除。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| tenant_id | string | 必填 | 可信服务配置租户，当前 gsi-default。 | "gsi-default" |
| user_id | string | 必填 | 登录用户数字主键字符串；不能透传页面自报值。 | "42" |
| result_id | string | 必填 | AI 返回的只读检测结果 ID，绑定租户、用户、范围摘要和内容摘要，保留 30 分钟。 | "dup-result-001" |
| group_id | string | 必填 | 组 ID。 | "dup-group-faq-001" |
| scope | object | 必填 | GSI 后端按知识管理权限签发的范围，不是座席消费授权。每次请求重新核对；AI 只能比较这里列出的内容。 |  |
| scope.scope_id | string | 必填 | 本次范围追踪 ID。 | "gov-scope-001" |
| scope.issued_at | integer | 必填 | Unix 毫秒，示例时间须替换。 | 1800000000000 |
| scope.expires_at | integer | 必填 | 最长有效 120 秒。 | 1800000120000 |
| scope.resources | array<object> | 必填 | 授权治理内容清单，最多 1000；[] 表示无候选。过大返回 413，不静默截断。 |  |
| scope.resources[].resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.resources[].resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.resources[].record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.resources[].content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.resources[].publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.resources[].entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts | array<object> | 必填 | resources 中 AI 尚无正式内容的 GSI 草稿/审批快照；最多 1000；必须与清单身份和摘要一致。 |  |
| scope.drafts[].resource | object | 必填 | 治理对象的不可变内容身份。resource_type + resource_id 标识业务知识，record_id/content_hash 标识本次比较版本。 |  |
| scope.drafts[].resource.resource_type | string | 必填 | 知识类型。 【枚举："document" / "faq"】 | "document" |
| scope.drafts[].resource.resource_id | string | 必填 | GSI 文档或 FAQ 主键；不是页面 hit_id。 | "doc-001" |
| scope.drafts[].resource.record_id | string | 必填 | 已发布快照 ID 或 GSI 草稿冻结快照 ID。 | "record-001" |
| scope.drafts[].resource.content_hash | string | 必填 | 本次完整内容摘要；变更后必须重新检测。 | "sha256:content-001" |
| scope.drafts[].resource.publication_ids | array<string> | 必填 | 本内容对应的发布单元集合；草稿为 []。同一内容多团队发布只参与比较一次。 |  |
| scope.drafts[].resource.entry_mode | string | 必填 | 录入来源，用于 FAQ 分类。 【枚举："ai" / "manual"】 | "manual" |
| scope.drafts[].title | string | 必填 | 保存前标题。 | "VPN申请" |
| scope.drafts[].summary | string | 必填 | 摘要，可空。 | "VPN申请流程" |
| scope.drafts[].content | string | 必填 | 已清洗的纯文本正文；FAQ 为答案。 | "通过服务台提交 VPN 申请。" |
| scope.drafts[].chunks | array<object> | 必填 | 草稿切片；未切片时 []。 |  |
| scope.drafts[].chunks[].chunk_id | string | 必填 | 草稿中稳定的切片标识。 | "chunk-001" |
| scope.drafts[].chunks[].title | string | 必填 | 切片标题。 | "申请入口" |
| scope.drafts[].chunks[].content | string | 必填 | 纯文本内容。 | "通过服务台提交 VPN 申请。" |
| keep_hit_id | string | 必填 | 人工选择的主项，必须在分组内；仅一个 keep。 | "faq-hit-001" |
| actions | array<object> | 必填 | 每个分组成员恰好一项；Chunk 不允许 merge_answer。 |  |
| actions[].target_hit_id | string | 必填 | 组内待处理项。 | "hit-002" |
| actions[].action | string | 必填 | keep 保留；drop 删除；discard 废弃；merge_answer 将 FAQ 答案合入主项。 【枚举："keep" / "drop" / "discard" / "merge_answer"】 | "merge_answer" |
| actions[].merged_answer_html | string | 可选 | 用户已编辑的候选答案，可选；只用于预览。 | "<p>通过服务台提交 VPN 申请。</p>" |
| prompt_text | string | 必填 | 实际预览提示词，不是配置 ID。 | "合并一致的事实，标出冲突，不补造步骤。" |
| model_key | string | 必填 | AI 模型调用键，不是配置记录 ID。 | "default" |

**请求示例**

```json
{
  "request_id": "req-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "result_id": "dup-result-001",
  "group_id": "dup-group-faq-001",
  "scope": {
    "scope_id": "gov-scope-001",
    "issued_at": 1800000000000,
    "expires_at": 1800000120000,
    "resources": [
      {
        "resource_type": "faq",
        "resource_id": "faq-001",
        "record_id": "record-001",
        "content_hash": "sha256:content-001",
        "publication_ids": [
          "pub-001"
        ],
        "entry_mode": "manual"
      },
      {
        "resource_type": "faq",
        "resource_id": "faq-002",
        "record_id": "record-002",
        "content_hash": "sha256:content-002",
        "publication_ids": [
          "pub-002"
        ],
        "entry_mode": "manual"
      }
    ],
    "drafts": []
  },
  "keep_hit_id": "hit-001",
  "actions": [
    {
      "target_hit_id": "hit-001",
      "action": "keep"
    },
    {
      "target_hit_id": "hit-002",
      "action": "merge_answer"
    }
  ],
  "prompt_text": "合并一致的事实，标出冲突，不补造步骤。",
  "model_key": "default"
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 裸 JSON 响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| preview_id | string | 必返 | 预览追踪 ID，不是审批通过凭证。 | "merge-preview-001" |
| preview_hash | string | 必返 | GSI 需保存的预览摘要，绑定来源与处置方案。 | "sha256:preview-001" |
| title | string | 必返 | 建议标题。 | "VPN申请" |
| answer_html | string | 必返 | FAQ 合并后的安全 HTML；非 FAQ 返回空字符串。 | "<p>通过服务台提交 VPN 申请。</p>" |
| summary | string | 必返 | 中文处置摘要。 | "保留主 FAQ，将另一答案中一致的申请步骤合并。" |
| source_hit_ids | array<string> | 必返 | 使用的全部来源项，不能超出分组或治理范围。 |  |
| conflicts | array<object> | 必返 | 未能自动解决的事实矛盾；必须人工处理后才能提交最终审批。 |  |
| conflicts[].field | string | 必返 | 冲突主题。 | "审批人" |
| conflicts[].description | string | 必返 | 差异说明。 | "两个来源的审批人不同，请确认适用场景。" |
| conflicts[].hit_ids | array<string> | 必返 | 涉及来源。 |  |
| warnings | array<string> | 必返 | 非致命提醒。 |  |

**响应示例**

```json
{
  "request_id": "req-001",
  "preview_id": "merge-preview-001",
  "preview_hash": "sha256:preview-001",
  "title": "VPN申请",
  "answer_html": "<p>通过服务台提交 VPN 申请。</p>",
  "summary": "保留主 FAQ，将另一答案中一致的申请步骤合并。",
  "source_hit_ids": [
    "hit-001",
    "hit-002"
  ],
  "conflicts": [],
  "warnings": [
    "受众不同，不可自动合并受众。"
  ]
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

#### D05 停用已生效知识（通用接口）

`POST /v1/knowledge/entries/{publication_id}/deactivate`

仅在 GSI 下架/废弃生效后调用。AI 同步停用目标索引，保留内容用于审计；不能被新问题、历史上下文或结果回放引用。审批由 GSI 校验。相同请求幂等，不同旧修订拒绝；重新发布走批准后的修改接口。

> 请求头：所有接口均需携带公共签名鉴权的 5 个请求头，见[四、公共请求头与签名](#四公共请求头与签名)。

**路径参数**

| 参数 | 类型 | 是否必需 | 说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| publication_id | string | 必填 | GSI 目标发布单元 ID。 | "pub-001" |

**请求正文**（`application/json`，UTF-8）

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必填 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| tenant_id | string | 必填 | 可信服务配置租户，当前 gsi-default。 | "gsi-default" |
| user_id | string | 必填 | 登录用户数字主键字符串；不能透传页面自报值。 | "42" |
| publication_id | string | 必填 | 需停用的发布单元，与路径一致。 | "pub-001" |
| record_id | string | 必填 | 目标当前快照。 | "record-001" |
| revision | integer | 必填 | 新操作修订号，必须大于 AI 已接收值；防止迟到请求。 【最小值 1】 | 103 |
| reason | string | 必填 | 业务原因。 | "重复治理审批通过，废弃重复知识。" |

**请求示例**

```json
{
  "request_id": "req-001",
  "tenant_id": "gsi-default",
  "user_id": "42",
  "publication_id": "pub-001",
  "record_id": "record-001",
  "revision": 103,
  "reason": "重复治理审批通过，废弃重复知识。"
}
```

**响应**

**HTTP 200 · OK：请求处理成功；响应体说明查询结果或已完成的操作。**

AI 裸 JSON 响应。

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| request_id | string | 必返 | GSI 生成请求 ID；同 ID 同正文幂等，正文改变必须换 ID。 | "req-001" |
| publication_id | string | 必返 | 需停用的发布单元，与路径一致。 | "pub-001" |
| record_id | string | 必返 | 目标当前快照。 | "record-001" |
| revision | integer | 必返 | 新操作修订号，必须大于 AI 已接收值；防止迟到请求。 【最小值 1】 | 103 |
| deactivated | boolean | 必返 | 固定 true。 | true |
| searchable | boolean | 必返 | 固定 false。 | false |

**响应示例**

```json
{
  "request_id": "req-001",
  "publication_id": "pub-001",
  "record_id": "record-001",
  "revision": 103,
  "deactivated": true,
  "searchable": false
}
```


**错误响应**（错误体结构见[七、业务错误码与处理方式](#七业务错误码与处理方式)）

**HTTP 400 · Bad Request：请求存在客户端错误，例如字段缺失、类型错误或参数值无效。**

```json
{"request_id":"req-20261029-001","error":{"code":"INVALID_REQUEST","message":"请求参数无效，请检查必填字段和参数格式。","retryable":false}}
```

**HTTP 401 · Unauthorized：缺少有效的认证凭据；本协议包括签名无效、签名时间过期或 nonce 重放。**

```json
{"error":{"code":"UNAUTHORIZED","message":"服务签名认证失败，请检查签名、时间戳和 nonce。","retryable":false}}
```

**HTTP 403 · Forbidden：服务理解请求但拒绝执行；本协议用于认证后权限不足或授权范围失效。**

```json
{"request_id":"req-20261029-001","error":{"code":"CONTEXT_OUT_OF_SCOPE","message":"当前身份无权访问请求范围内的资源。","retryable":false}}
```

**HTTP 404 · Not Found：目标资源不存在，或服务不愿披露其存在；可用于隐藏他人会话或检测结果。**

```json
{"request_id":"req-20261029-001","error":{"code":"NOT_FOUND","message":"请求的资源不存在或不可访问。","retryable":false}}
```

**HTTP 409 · Conflict：请求与目标资源当前状态冲突，例如幂等正文不一致、旧修订或基线变化。**

```json
{"request_id":"req-20261029-001","error":{"code":"IDEMPOTENCY_CONFLICT","message":"相同 request_id 已用于不同的请求正文。","retryable":false}}
```

```json
{"request_id":"req-20261029-002","error":{"code":"STALE_REVISION","message":"知识基线已发生变化，请读取最新内容后重新提交。","retryable":false}}
```

**HTTP 413 · Content Too Large：请求内容超过服务允许的容量；本协议包括正文大小或资源清单数量超限。**

```json
{"request_id":"req-20261029-001","error":{"code":"PAYLOAD_TOO_LARGE","message":"请求正文或资源清单超过允许的容量。","retryable":false}}
```

**HTTP 429 · Too Many Requests：请求速率超过限制；等待限流窗口后再尝试。**

```json
{"request_id":"req-20261029-001","error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后使用原请求重试。","retryable":true}}
```

**HTTP 503 · Service Unavailable：服务暂时不可用，例如过载、维护或依赖不可用；权限依赖故障不得绕过权限检查。**

```json
{"request_id":"req-20261029-001","error":{"code":"AUTHORIZATION_UNAVAILABLE","message":"权限数据暂不可用，请稍后重试。","retryable":true}}
```


---

## 七、业务错误码与处理方式

按以下 6 类处理，表中列出常见 error.code；具体原因查看 error.message。

| 分类 | 常见业务码 | HTTP | 处理方式 |
| --- | --- | --- | --- |
| 参数错误 | INVALID_REQUEST | 400 | 修正参数后重新提交。 |
| 认证失败 | UNAUTHORIZED | 401 | 检查签名、时间戳和 nonce。 |
| 无访问权限 | CONTEXT_OUT_OF_SCOPE | 403 | 重新确认用户权限，不直接重试。 |
| 资源不存在 | NOT_FOUND | 404 | 确认资源 ID 或刷新列表。 |
| 状态冲突 | IDEMPOTENCY_CONFLICT / STALE_REVISION | 409 | 核对请求 ID 与正文，或读取最新版本。 |
| 服务异常 | INTERNAL_ERROR | 500 | 根据 retryable 判断是否重试。 |

仅当 retryable=true 时自动重试；保留原请求 ID 和正文，重新签名。其他细分业务码按对应 HTTP 类别和 message 处理。任务查询返回的 failed 表示原任务失败，不代表查询请求失败。

### 统一错误体字段

| 字段路径 | 类型 | 是否必需 | 中文说明 / 约束 | 示例值 |
| --- | --- | --- | --- | --- |
| error | object | 必填 | 错误详情。 |  |
| error.code | string | 必填 | 稳定的机器错误码。 | "INVALID_REQUEST" |
| error.message | string | 必填 | 说明错误原因，不泄漏密钥或受限内容。 | "参数无效，请检查必填字段。" |
| error.retryable | boolean | 必填 | 是否允许按原业务请求重试。重试须重新生成签名，不改变正文。 | false |
| request_id | string | 可选 | 可选的请求追踪 ID；能够识别请求时原样返回。认证失败或请求无法解析时可省略。 | "req-20261029-001" |

---

## 八、重复治理 · AI 对接说明

D01 保存前查重，D02 查询重复分组，D03 获取详情与证据，D04 生成合并预览，D05 停用知识。参数表、请求及响应示例见对应接口卡片。

调用流程：GSI 生成操作者可管理的知识范围 → D01 保存前提示或 D02 查询分组 → D03 查看来源及全文 → D04 生成预览 → GSI 冻结方案并完成工作台审批 → 批准后执行正式修改、删除或停用。审批中、驳回、撤销均不写 AI 正式内容；AI 不提供审批接口。来源内容变化后必须重新检测确认。

| 批准后的处置 | 调用 AI | 约束 |
| --- | --- | --- |
| 修改主 FAQ / 移除重复切片 | 03.3 PUT /v1/knowledge/entries/{publication_id} | 提交生效的完整切片集合、元数据和 base_record_id；单片删除不能删除整篇知识。 |
| 废弃 / 下架整篇知识 | D05 POST /v1/knowledge/entries/{publication_id}/deactivate | 停止检索与回答引用；保留内容供审计。 |
| 删除整篇知识 | 03.4 DELETE /v1/knowledge/entries/{publication_id} | 删除指定快照正文与向量；GSI 保存业务审计。 |

GSI 内部通过 request_id 关联执行事件，不传递审批单信息。多个团队发布单元分别处理。GSI 持久化执行事件，AI 必须返回最终成功回执；响应丢失按相同 request_id、相同正文重试，并重新生成签名时间戳与 nonce。不新增知识业务状态，失败重试进度保存在治理任务中。

权限与数据：scope 是知识管理范围，不能用问答受众范围代替。AI 只比较其中明确的 resource_type、resource_id、record_id、content_hash 和 publication_ids；草稿通过 drafts 临时提供，不进入正式问答索引或模型训练。scope 有效期最长 120 秒，结果保留 30 分钟并绑定租户、用户、范围和策略；翻页、详情及预览重新校验权限和来源。最多 1000 个资源、正文 10 MB，超限返回 413，不得截断后声称检测完整。共享 service_brain 的 pgvector 仍由 AI 管理。

五类检测：doc_exact（规范化标题相同，并非全文相同）、doc_similar（文档相似）、chunk（切片重复）、faq_ai（AI FAQ）、faq_manual（手工 FAQ）。相似度与实际比较依据由 AI 返回；跨受众合并不能扩大来源内容的可见范围。

鉴权与异常：沿用公共 HMAC 五个请求头。user_id 为可信数字用户主键字符串，后台停用允许约定服务身份 "0"。400 SCOPE_REQUIRED 表示缺少范围；403 GOVERNANCE_SCOPE_DENIED 表示权限失效；409 SOURCE_CHANGED / RESULT_EXPIRED 表示来源变化 / 结果过期；409 IDEMPOTENCY_CONFLICT 表示请求 ID 对应正文变化；503 AUTHORIZATION_UNAVAILABLE 表示权限数据不可用。失败不能显示“未发现重复”。

---

---

> GSI × AI · v2.1 · 参数参考与 OpenAPI 同源生成
