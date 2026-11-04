# test-data · 测试数据

> 契约测试用到的**全部 JSON 数据**都归集在本目录：`backend/tests/contract/` 下的测试代码只负责「发请求 + 断言 + 回写结果」，不再内嵌任何 JSON 数据。
> 同级的 `01`–`03` 测试报告中的入参/出参直接引用本目录文件，避免「文档写一份、代码写一份」的漂移。

---

## 一、目录与写入方

| 目录 | 内容 | 谁维护 |
| --- | --- | --- |
| [source_file/](source_file/) | 样例文档本体：PDF、MinerU 解析产出的 Markdown、人工译文 | 人工维护 |
| [requests/](requests/) | 每个用例一份请求正文，文件名 `{接口编号}_{场景}.json` | 人工维护 |
| [responses/](responses/) | 与 `requests/` **同名**（含编号与场景后缀）的实测响应，一请求一文件 | 脚本自动覆盖 |

- `01_` 前缀 = 解析接口（#01 `/v1/parse/jobs`、#02 `/v1/parse/jobs/{job_id}`）
- 请求与响应同名：`requests/01_parse_jobs_doc.json` 的实测响应就是 `responses/01_parse_jobs_doc.json`，编号与场景后缀一字不差，不在汇总文件里查 key
- `02_*.json` 只承载 GET 的路径参数（`job_id`），无请求正文，因此不参与签名摘要
- `03_` 前缀 = 知识接口（03.1 新增 / 03.2 读取 / 03.3 修改 / 03.4 删除）
- 请求文件里的 `"content_hash": "__COMPUTED_AT_RUNTIME__"` 是占位符：由 `tests/contract/case_data.py` 的 `resolve_content_hash()` 按 `chunks` 实算后替换。摘要不写死在 JSON 里，避免改了切片内容却忘记改摘要。
- `03_*.json` 的 `chunks` 不另编样例：逐字取自 02 解析产物 —— 文档形态取 `responses/02_parse_jobs_doc.json`，FAQ 形态取 `responses/02_parse_jobs_faq.json`（`heading` / 正文 / 页码原样沿用），只在知识包内按 1..N 重编 `seq`；`metadata.source_parse_job_id` 记录来源解析任务（`parse-doc-100001` / `parse-faq-200001`），保证「解析产物 → 审批写入」可追溯。
- `04_` 前缀 = 检索接口（#07 `POST /v1/knowledge/search`）
- `04_*.json` 的 `authorization` 时效写占位符：`__AUTH_ISSUED_AT__` / `__AUTH_EXPIRES_120S__` / `__AUTH_EXPIRES_300S__` / `__AUTH_EXPIRED_ISSUED__` / `__AUTH_EXPIRED_EXPIRES__`，由 `resolve_authorization()` 在发送前换算成当前时刻的毫秒值。静态值一落地就过期，区分不了「跨度超限」与「已过期」。
- `04_*.json` 的切片与 03 文档形态同源（`04_knowledge_search_prepare_entry.json` 就是上述 #1 的三条切片），但 `publication_id` 改填 GSI 发布单元 `0876a2b0-2788-4816-a1fd-7f26a2bfd061`（`team=AUD_SMOKE`，成员 `user_id=1`），检索的授权链才能成立。

---

## 二、基线数据

所有用例共用同一份样例文档（Lane et al., 2005，《流量历时曲线对造林的响应》，Journal of Hydrology）：

| 基线文件 | 用途 |
| --- | --- |
| [source_file/Lane_2005_造林与流量历时曲线.pdf](source_file/Lane_2005_造林与流量历时曲线.pdf) | 文档本体（336919 字节，请求里的 `file_size` 即取此值） |
| [source_file/Lane_2005_造林与流量历时曲线.md](source_file/Lane_2005_造林与流量历时曲线.md) | MinerU 解析产物，`chunks` 正文、页码与章节标题的取材来源 |
| [source_file/Lane_2005_造林与流量历时曲线_翻译.md](source_file/Lane_2005_造林与流量历时曲线_翻译.md) | 中文译文，`metadata.title` / `summary` / `content` / `tags` 的语义依据 |
| [requests/01_parse_jobs_doc.json](requests/01_parse_jobs_doc.json) | 文档解析基线（#01 用例 #1、幂等重试 #9） |
| [requests/01_parse_jobs_faq.json](requests/01_parse_jobs_faq.json) | FAQ 解析基线（#01 用例 #2） |

- `file_url` 仍指向公开下载地址 <https://cdn-mineru.openxlab.org.cn/demo/example.pdf>（即上述 PDF 的原始地址），服务端需能通过 http/https 拉取。
- 请求字段取值必须与本文档语义一致：`metadata.title` 为论文标题译文，`summary` 说明方法与数据，`content` 概括结论，`tags` 取自论文关键词，`linked_section` 指向真实章节（如 “2.1 流量制度特征”）。

---

## 三、用例 ↔ 数据文件对照

### 3.1 解析接口（对应 `01`）

| 用例 | 接口 | 请求数据 | 响应数据（同名） |
| --- | --- | --- | --- |
| #1 文档解析提交 | POST `/v1/parse/jobs` | [01_parse_jobs_doc.json](requests/01_parse_jobs_doc.json) | [01_parse_jobs_doc.json](responses/01_parse_jobs_doc.json) |
| #2 FAQ 解析提交 | POST `/v1/parse/jobs` | [01_parse_jobs_faq.json](requests/01_parse_jobs_faq.json) | [01_parse_jobs_faq.json](responses/01_parse_jobs_faq.json) |
| #3 查询文档解析结果 | GET `/v1/parse/jobs/{job_id}` | [02_parse_jobs_doc.json](requests/02_parse_jobs_doc.json) | [02_parse_jobs_doc.json](responses/02_parse_jobs_doc.json) |
| #4 查询 FAQ 解析结果 | GET `/v1/parse/jobs/{job_id}` | [02_parse_jobs_faq.json](requests/02_parse_jobs_faq.json) | [02_parse_jobs_faq.json](responses/02_parse_jobs_faq.json) |
| #5 文件类型不支持 | POST `/v1/parse/jobs` | [01_parse_jobs_doc_unsupported_type.json](requests/01_parse_jobs_doc_unsupported_type.json) | [01_parse_jobs_doc_unsupported_type.json](responses/01_parse_jobs_doc_unsupported_type.json) |
| #6 文件超过 10MB | POST `/v1/parse/jobs` | [01_parse_jobs_doc_too_large.json](requests/01_parse_jobs_doc_too_large.json) | [01_parse_jobs_doc_too_large.json](responses/01_parse_jobs_doc_too_large.json) |
| #7 FAQ 缺失 prompt_text | POST `/v1/parse/jobs` | [01_parse_jobs_faq_missing_prompt.json](requests/01_parse_jobs_faq_missing_prompt.json) | [01_parse_jobs_faq_missing_prompt.json](responses/01_parse_jobs_faq_missing_prompt.json) |
| #8 任务不存在 | GET `/v1/parse/jobs/{job_id}` | [02_parse_jobs_not_found.json](requests/02_parse_jobs_not_found.json)（仅 `job_id`） | [02_parse_jobs_not_found.json](responses/02_parse_jobs_not_found.json) |
| #9 幂等重试 | POST `/v1/parse/jobs` | 复用 [01_parse_jobs_doc.json](requests/01_parse_jobs_doc.json) | 同一份 [01_parse_jobs_doc.json](responses/01_parse_jobs_doc.json)，重试回执记在 `idempotent_retry` |

### 3.2 知识接口（对应 `02`）

| 用例 | 接口 | 请求数据 |
| --- | --- | --- |
| #1 新增切片集合 | POST `/v1/knowledge/entries` | [03_knowledge_entries_create.json](requests/03_knowledge_entries_create.json) |
| #2 读取基线 | POST `/v1/knowledge/entries/{pub}/query` | [03_knowledge_entries_query_v1.json](requests/03_knowledge_entries_query_v1.json) |
| #3 修改切片集合 | PUT `/v1/knowledge/entries/{pub}` | [03_knowledge_entries_revise.json](requests/03_knowledge_entries_revise.json) |
| #4 读取修订后基线 | POST `/v1/knowledge/entries/{pub}/query` | [03_knowledge_entries_query_v2.json](requests/03_knowledge_entries_query_v2.json) |
| #5 删除切片集合 | DELETE `/v1/knowledge/entries/{pub}` | [03_knowledge_entries_delete.json](requests/03_knowledge_entries_delete.json) |
| #6 读取已删除快照 | POST `/v1/knowledge/entries/{pub}/query` | [03_knowledge_entries_query_deleted.json](requests/03_knowledge_entries_query_deleted.json) |
| #7 重复创建（首次，FAQ 形态） | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_dup_1.json](requests/03_knowledge_entries_create_dup_1.json) |
| #7 重复创建（第二次，FAQ 形态） | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_dup_2.json](requests/03_knowledge_entries_create_dup_2.json) |
| #8 content_hash 不一致 | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_bad_hash.json](requests/03_knowledge_entries_create_bad_hash.json) |
| #9 预置基线 | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_conflict.json](requests/03_knowledge_entries_create_conflict.json) |
| #9 base_record_id 不匹配 | PUT `/v1/knowledge/entries/{pub}` | [03_knowledge_entries_revise_bad_base.json](requests/03_knowledge_entries_revise_bad_base.json) |
| #10 查询不存在的 publication | POST `/v1/knowledge/entries/{pub}/query` | [03_knowledge_entries_query_not_found.json](requests/03_knowledge_entries_query_not_found.json) |
| #11 必填字段缺失 | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_incomplete.json](requests/03_knowledge_entries_create_incomplete.json) |
| #12 chunks.seq 不连续（FAQ 形态） | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_bad_seq.json](requests/03_knowledge_entries_create_bad_seq.json) |
| #13 删除不存在的 publication | DELETE `/v1/knowledge/entries/{pub}` | [03_knowledge_entries_delete_not_found.json](requests/03_knowledge_entries_delete_not_found.json) |
| #14 resource_type 非法值（FAQ 形态） | POST `/v1/knowledge/entries` | [03_knowledge_entries_create_bad_type.json](requests/03_knowledge_entries_create_bad_type.json) |

切片取材（逐字取自 02 解析产物，按知识形态分两组）：

文档形态（`resource_type=document`，源 `responses/02_parse_jobs_doc.json`）：

| 用例 | 切片组成（解析产物 seq → 知识包 seq） |
| --- | --- |
| #1 新增 / #2·#4 读取 / #5·#6 删除链路 | Abstract p1、1. Introduction p2、2. Methods p3 → seq 1/2/3 |
| #3 修改（`revise`） | 上述 3 条 + 3. Data sets p5 → seq 4（修订补充数据集章节） |
| #9 冲突修订（`revise_bad_base`） | 上述 3 条 + 4.2 Adjusted FDCs p7 → seq 4 |

FAQ 形态（`resource_type=faq`，源 `responses/02_parse_jobs_faq.json`，`page_from` / `page_to` 为 `null`）：

| 用例 | 切片组成 |
| --- | --- |
| #7 重复创建（`dup_1` / `dup_2`） | 「这项研究的核心目标是什么？」「研究的主要结论是什么？」「气候调整后的FDC揭示了哪两类响应？」→ seq 1/2/3 |
| #12 seq 不连续（`create_bad_seq`） | 只放核心目标与主要结论，编号 1 / 3 → 故意断号 |
| #14 resource_type 非法（`create_bad_type`） | 同 #7 的三条问答，除 `resource_type=invalid_type` 外其余字段均合法 |

- `metadata.doc_type` 与 `resource_type` 一致：文档形态 `document` + `language=en`（MinerU 按原文解析，切片正文是英文），FAQ 形态 `faq` + `language=zh`（问答均为中文），`title` 保留人工中文命名
- #8 `content_hash` 写死为「上一版文档切片算出的摘要」，正文用的却是更新后的切片 —— 复现真实漏改摘要的情形，服务端自算摘要必然与之不符

### 3.3 检索接口（对应 `03`）

先铺场（03.1 把切片写进 GSI 发布单元），再用不同 `user_id` / `tenant_id` / `query` / `authorization` 调用 #07：

| 用例 | 接口 | 请求数据 |
| --- | --- | --- |
| 铺场：写入可检索切片 | POST `/v1/knowledge/entries` | [04_knowledge_search_prepare_entry.json](requests/04_knowledge_search_prepare_entry.json) |
| #1 关键词命中 | POST `/v1/knowledge/search` | [04_knowledge_search_keyword.json](requests/04_knowledge_search_keyword.json) |
| #2 命中条数受 limit 约束 | POST `/v1/knowledge/search` | [04_knowledge_search_keyword_limit.json](requests/04_knowledge_search_keyword_limit.json) |
| #3 库外关键词无证据 | POST `/v1/knowledge/search` | [04_knowledge_search_no_evidence.json](requests/04_knowledge_search_no_evidence.json) |
| #4 非受众团队成员 | POST `/v1/knowledge/search` | [04_knowledge_search_out_of_scope_user.json](requests/04_knowledge_search_out_of_scope_user.json) |
| #5 用户无任何受众团队 | POST `/v1/knowledge/search` | [04_knowledge_search_no_team_user.json](requests/04_knowledge_search_no_team_user.json) |
| #6 user_id 非数字主键 | POST `/v1/knowledge/search` | [04_knowledge_search_non_numeric_user.json](requests/04_knowledge_search_non_numeric_user.json) |
| #7 租户隔离 | POST `/v1/knowledge/search` | [04_knowledge_search_other_tenant.json](requests/04_knowledge_search_other_tenant.json) |
| #8 自然语言检索（T4.2 未交付） | POST `/v1/knowledge/search` | [04_knowledge_search_natural_language.json](requests/04_knowledge_search_natural_language.json) |
| #9 authorization 跨度超限 | POST `/v1/knowledge/search` | [04_knowledge_search_authorization_too_long.json](requests/04_knowledge_search_authorization_too_long.json) |
| #10 authorization 已过期 | POST `/v1/knowledge/search` | [04_knowledge_search_authorization_expired.json](requests/04_knowledge_search_authorization_expired.json) |
| #11 缺少 authorization 块 | POST `/v1/knowledge/search` | [04_knowledge_search_authorization_missing.json](requests/04_knowledge_search_authorization_missing.json) |
| #12 query 只有空白 | POST `/v1/knowledge/search` | [04_knowledge_search_empty_query.json](requests/04_knowledge_search_empty_query.json) |
| #13 limit 超出 1–100 | POST `/v1/knowledge/search` | [04_knowledge_search_limit_out_of_range.json](requests/04_knowledge_search_limit_out_of_range.json) |
| #14 mode 非枚举值 | POST `/v1/knowledge/search` | [04_knowledge_search_invalid_mode.json](requests/04_knowledge_search_invalid_mode.json) |

- #1–#3、#6–#14 只差一个字段：`query` 换成 `flow` / 库外词 / 空白，`limit` 换成 1 / 101，`user_id` 换成 `2` / `999999` / `gsi-user-1`，`tenant_id` 换成 `gsi-other`，`mode` 换成 `natural_language` / `hybrid`，`authorization` 换跨度或去掉整块
- 铺场可重复执行：`request_id` 与正文都不变时幂等台账回放 201；改动铺场正文必须同步换 `request_id`，否则 `409 IDEMPOTENCY_CONFLICT`，后续检索用例会全部 0 命中

### 3.4 实测结果文件（与请求文件同名）

`responses/` 不设汇总文件：一个请求文件对应一个同名响应文件（编号与场景后缀一致）。

| 请求文件 | 响应文件 | 实测内容 |
| --- | --- | --- |
| `requests/01_parse_jobs_doc.json` | `responses/01_parse_jobs_doc.json` | 202 受理回执；`idempotent_retry` 记 #9 重试回执（与首次一致） |
| `requests/01_parse_jobs_faq.json` | `responses/01_parse_jobs_faq.json` | 202 受理回执 |
| `requests/02_parse_jobs_doc.json` | `responses/02_parse_jobs_doc.json` | 200 文档任务终态（`pages` / `chunks` / `suggested` 全量） |
| `requests/02_parse_jobs_faq.json` | `responses/02_parse_jobs_faq.json` | 200 FAQ 任务终态（`suggested_doc_type=FAQ`） |
| `requests/01_parse_jobs_doc_unsupported_type.json` | 同名 | 400 `INVALID_REQUEST`（`file_name=readme.exe`；`.txt` 属已支持后缀，不拿来做本用例） |
| `requests/01_parse_jobs_doc_too_large.json` | 同名 | 413 `PAYLOAD_TOO_LARGE` |
| `requests/01_parse_jobs_faq_missing_prompt.json` | 同名 | 400 `INVALID_REQUEST` |
| `requests/02_parse_jobs_not_found.json` | 同名 | 404 `NOT_FOUND` |
| `requests/03_knowledge_entries_create.json` | 同名 | 201 写入回执（`record_id=rec-test-crud-001-v1`） |
| `requests/03_knowledge_entries_query_v1.json` | 同名 | 200 基线快照（3 条切片） |
| `requests/03_knowledge_entries_revise.json` | 同名 | 200 修订回执（`revision=2`、4 条切片） |
| `requests/03_knowledge_entries_query_v2.json` | 同名 | 200 修订后基线（4 条切片） |
| `requests/03_knowledge_entries_delete.json` | 同名 | 200 删除回执（`revision=3`） |
| `requests/03_knowledge_entries_query_deleted.json` | 同名 | 404 `NOT_FOUND`（快照已删除） |
| `requests/03_knowledge_entries_create_dup_1.json` / `..._dup_2.json` | 同名 | 201 铺场 / 409 `STALE_REVISION`（FAQ 形态） |
| `requests/03_knowledge_entries_create_bad_hash.json` | 同名 | 409 `STALE_REVISION`（摘要不一致） |
| `requests/03_knowledge_entries_create_conflict.json` / `..._revise_bad_base.json` | 同名 | 201 铺场 / 409 `STALE_REVISION`（基线不匹配） |
| `requests/03_knowledge_entries_query_not_found.json` / `..._delete_not_found.json` | 同名 | 404 `NOT_FOUND` |
| `requests/03_knowledge_entries_create_incomplete.json` / `..._bad_seq.json` / `..._bad_type.json` | 同名 | 400 `INVALID_REQUEST`（后两者为 FAQ 形态） |
| `requests/04_knowledge_search_prepare_entry.json` | 同名 | 201 铺场回执（`publication_id=0876a2b0-…`、3 条切片） |
| `requests/04_knowledge_search_keyword.json` / `..._keyword_limit.json` | 同名 | 200 命中 2 条（`score=0.8111`，同分按 seq 升序）/ 1 条（`limit=1`） |
| `requests/04_knowledge_search_no_evidence.json` | 同名 | 200 空命中（`no_evidence=true`） |
| `requests/04_knowledge_search_out_of_scope_user.json` / `..._no_team_user.json` / `..._non_numeric_user.json` / `..._other_tenant.json` | 同名 | 200 空命中（授权先于召回，均为 0 条） |
| `requests/04_knowledge_search_natural_language.json` | 同名 | 501 `INTERNAL_ERROR`（T4.2 未交付） |
| `requests/04_knowledge_search_authorization_too_long.json` / `..._authorization_expired.json` / `..._authorization_missing.json` | 同名 | 400 / 401 `UNAUTHORIZED` / 400 `INVALID_REQUEST` |
| `requests/04_knowledge_search_empty_query.json` / `..._limit_out_of_range.json` / `..._invalid_mode.json` | 同名 | 400 `INVALID_REQUEST` |

结果文件结构：`request_file`（同名请求文件）、`request`（实际发出的正文）或 `request_path`（无正文的 GET/查询路径）、
`response_status`、`response_body`；铺场请求（`03_*_dup_1`、`03_*_conflict`、`04_*_prepare_entry`）额外带 `purpose` 字段说明用途。

> 知识接口（`03_*`）已按同一规则重跑，16 个请求文件对应 16 个同名响应文件，不再有汇总文件；
> 逐用例的请求行与实测状态见 [02-知识写入接口出入参测试报告.md](../02-知识写入接口出入参测试报告.md) 第 6.1 节。
> 检索接口（`04_*`）15 个请求文件对应 15 个同名响应文件；逐用例记录见 [03-检索接口出入参测试报告.md](../03-检索接口出入参测试报告.md) 第 7.1 节。

---

## 四、使用方式

```bash
cd backend

# 1. 生成签名头（读 01_parse_jobs_doc.json 作为待签名正文，输出可直接粘贴到 Apifox / curl）
.venv\Scripts\python.exe -m tests.contract.debug_signed_headers

# 2. 解析接口契约测试（读 requests，回写 responses）
.venv\Scripts\python.exe -m tests.contract.run_parse_tests

# 3. 知识写入接口契约测试
.venv\Scripts\python.exe -m tests.contract.run_knowledge_tests

# 4. 检索接口契约测试（先铺场再检索，按同名规则回写 15 个响应）
.venv\Scripts\python.exe -m tests.contract.run_search_tests
```

需先启动服务，并在 `env/.env.dev` 中配置 `CONTRACT_TEST_ENABLE=true`（签名头生成接口的开关）。

---

## 五、维护约定

1. **测试代码不存 JSON 数据**：新增/修改用例，先在本目录加文件，代码只按文件名读取。
2. **请求文件即「原样发出的字节」**：签名摘要对文件内容逐字节计算，改动缩进、换行、末尾换行都会让签名失效，慎改格式。
3. **结果文件由脚本覆盖**：只反映最近一次执行的实测值；改了 `requests/` 的正文就要重跑对应脚本，否则 `responses/` 里的 `request` 快照与实际入参不符。历史结论以对应测试报告为准。
