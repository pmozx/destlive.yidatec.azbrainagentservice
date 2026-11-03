# 02 · AI 对话 SSE 流式输出规范

> 适用范围：本项目所有「模型逐字输出」的 HTTP 接口——基座管理端 `/api/v1/kb/chat/ask/stream`，以及后续与 GSI 业务系统对接的 `/v1` 契约流式接口。
> 版本：v1.0（2026-11-03） · 维护人 孟星旭
> 上下游关系：`01-团队开发规范.md` §4.5 规定**用哪些工具**（`core/chat/streaming.iter_stream` + `utils/sse.format_sse` + `KbChatService`），本文件规定**帧长什么样**（事件序、字段、错误码、心跳、中断与落库口径）。两份一起看才完整。
> 落地状态：管理端流式接口已按本规范实现（§十四 代码映射）；`/v1` 契约链路**尚未接入流式**，§十 只给接入要求，不代表已上线。

---

## 一、为什么单独立一份规范

三条理由，每条都对应一类真实事故：

| 问题 | 只写「用 iter_stream」会漏掉什么 |
| --- | --- |
| 跨端契约 | 前端要知道「哪一帧代表回答结束、失败长什么样、能不能重试」，否则只能靠 `timeout` 猜 |
| 跨团队对接（GSI） | 对接方要按帧写自己的消费代码；事件名或字段语义一改，对方解析器静默失效 |
| 失败语义 | 流一旦以 200 开始，**HTTP 状态码就再也改不了**；错误只能进帧，必须提前约定，不能临场发挥 |

规范里的每条规则都带编号（`S1`…`S40`），PR 与评审按编号引用，编号一经使用不复用。

---

## 二、传输与连接

**规则 S1：流式问答必须 `POST` + JSON 请求体，问题正文不得进 URL。** 提问是整句、可能含个人信息，放 query 会进访问日志、代理日志与浏览器历史。因此**不用** `EventSource`（它只支持 GET、不能带请求体与自定义头）。

**规则 S2：调用方必须声明 `Accept: text/event-stream`。** 服务端不因缺失该头改变行为（仍返回流），但 Nginx / CDN / 部分网关依赖它决定是否缓冲；不声明的后果是「本地正常、上线一帧都不出」。

**规则 S3：响应头固定为下面五项，缺一不可。**

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no
X-Stream-Version: 1
```

`X-Accel-Buffering: no` 是硬要求：不写它，Nginx 会把整条流攒到结束才一次性吐出，前端看到的「流式」等于非流式；`no-transform` 阻止中间层改写/压缩分块。

**规则 S4：流式响应禁止 `Content-Encoding: gzip`。** 压缩需要攒块，与逐帧推送直接冲突。网关若统一开 gzip，必须把 `text/event-stream` 排除。

**规则 S5：状态码只表达「流有没有开始」。** 首帧之前发生的拒绝（字段校验不通过 → `422`、身份非法 → `400`、会话不属于该用户 → `404`）用普通 JSON 响应 + 对应 HTTP 码；**一旦返回 `200` + `text/event-stream`，后续任何错误都只能走 `error` 帧**，禁止在流中间偷偷换成 JSON 错误体。

```python
# ✅ 校验在返回 StreamingResponse 之前做完，失败就是干净的 400/404
frames = await KbChatAdminService().open_stream(data, is_disconnected=request.is_disconnected)
return StreamingResponse(_frames(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)

# ❌ 把校验写进 async generator：函数体要等第一次迭代才执行，此时响应头已经发出去了
async def ask_stream(data):
    user_pk = self._user_pk(data.user_id)   # 抛 CustomException 时已经来不及改状态码
    yield ...
```

**规则 S6：一个请求只有一条流、一个话题。** 流内不支持切换会话或并行追问；多轮对话 = 多次请求（`session_id` 串起来）。

---

## 三、帧格式

**规则 S7：帧结构固定为 `event:` + 单行 `data:` + 空行结束，`data` 必须是单个 JSON 对象。**

```text
event: delta
data: {"seq":1,"text":"按 [1] 在服务台"}

```

- `data` 只写一行：多行 `data:`（SSE 规范允许，拼接时插入 `\n`）会让 JSON 解析依赖客户端实现细节；
- 中文不转义（`ensure_ascii=False`）：线上抓包可读，字节数也更小；
- 序列化只在 `app/utils/sse.py: format_sse` 一处收口，业务代码只产出 `{"event": str, "data": dict}`。

**规则 S8：不使用 `id:` / `retry:` / `Last-Event-ID` 续传。** 本项目流式是「一次提问一次成流」，断线重来就是一次新提问（还可能产生重复消息）；把 SSE 的浏览器自动重连语义引进来，等于给重复提问开后门。需要「结果回查」时用契约 `#15` 的结果查询，不用断点续传。

**规则 S9：单帧控制在 8KB 以内。** 模型分片天然很小；若业务要推大块（整段引用列表），分多帧发，避免代理按缓冲区大小切碎后客户端解析错位。

**规则 S10：客户端必须忽略未知事件名与未知字段。** 新增事件/字段属于兼容变更（§十三），老客户端不能因为不认识就断开或报错。

---

## 四、事件与状态机

**规则 S11：事件名封闭集合，只有七个。** 新增事件必须先改本规范并升 `v`（§十三）。

| 事件 | 何时发 | 次数 | 关键字段 |
| --- | --- | --- | --- |
| `meta` | 流的第一帧 | 恰好 1 次，必须是第一帧 | `v`、`request_id`、`session_id` |
| `stage` | 进入检索 / 生成阶段 | 0–2 次 | `phase`、`elapsed_ms` |
| `ping` | 任何阶段空闲超过心跳间隔 | 任意次 | `elapsed_ms` |
| `delta` | 模型每产出一片增量 | 末尾拒答时恰好 1 次 | `seq`、`text` |
| `citations` | 生成结束、落库之前 | 恰好 1 次 | `citations[]` |
| `done` | 本轮成功且已落库 | 与 `error` 二选一，且必须是最后一帧 | `user_message_id`、`assistant_message_id`、`answer`、`finish_reason`、`no_evidence`、`elapsed_ms` |
| `error` | 本轮失败 | 与 `done` 二选一，且必须是最后一帧 | `code`、`message`、`retryable`、`session_id` |

**规则 S12：正常流的顺序是固定的。**

```text
meta → stage(retrieving) → [ping]* → stage(generating) → delta* → [ping]* → citations → done
```

不变式（客户端可以据此断言）：
1. `meta` 第一且唯一；
2. `citations` 必须在 `done` 之前（前端先拿到来源集合，再收尾渲染，避免"回答已结束但引用还没到"的闪烁）；
3. `delta` 只能出现在 `meta` 之后、`citations`/`done`/`error` 之前；
4. `ping` 可以插在任意两帧之间，**但不出现在最后一帧之后**。

**规则 S13：`done` / `error` 必须有且只有一个，且是最后一帧。** 客户端不靠「连接关闭」判断结束——代理超时、网络抖动都会让连接关得毫无征兆；没有收到 `done`/`error` 就是「未完成」，客户端必须按中断处理（§七）。

**规则 S14：`done.answer` 是落库终稿，客户端必须以它为准覆盖已渲染文本。** 理由：`delta` 是模型原始增量，越界引用编号（模型编了 `[9]` 而本轮只有 5 条引用）与多余空白是在**生成之后**统一清洗的（`core/chat/cite.sanitize_citations`），流式过程中无法回收已发出的字节。终稿随 `done` 下发，一句话解决「屏幕上的答案 ≠ 库里的答案」。

**规则 S15：拒答也走 `delta`，不额外加事件。** 证据不足时先发一帧 `delta` 承载固定拒答话术（`NO_EVIDENCE_ANSWER`），再发 `citations: []` 与 `done{finish_reason: "no_evidence", no_evidence: true}`。前端因此不需要为拒答写分支。

### 4.1 各事件字段表

**`meta`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `v` | int | 是 | 协议版本，当前 `1` |
| `request_id` | string | 是 | 本次流的追踪 ID（同时进应用日志），排查时按它 grep |
| `session_id` | string | 是 | 本轮落库的会话 ID；首轮自动建的会话也在此返回 |

**`stage`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `phase` | string | 是 | `retrieving`（授权 + 检索 + 取证）/ `generating`（模型生成） |
| `elapsed_ms` | int | 是 | 从流开始到进入该阶段的耗时 |

**`ping`**：`elapsed_ms`（int）。不含任何业务字段，仅为保活。

**`delta`**：`seq`（int，从 1 连续递增，便于定位丢帧）、`text`（string，增量正文）。

**`citations`**：`citations`（array，元素与契约 `#14` 引用八元组同构：`index` / `citation_id` / `resource_type` / `resource_id` / `publication_id` / `record_id` / `version_id` / `revision` / `chunk_id` / `quote`）。证据不足时为 `[]`。

**`done`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 是 | 会话 ID |
| `user_message_id` | string | 是 | 本轮用户消息 ID（客户端用它替换本地临时消息） |
| `assistant_message_id` | string | 是 | 本轮助手消息 ID |
| `answer` | string | 是 | 落库终稿正文（见 S14） |
| `no_evidence` | bool | 是 | 是否因证据不足拒答 |
| `finish_reason` | string | 是 | `stop` / `no_evidence` |
| `elapsed_ms` | int | 是 | 整条链路耗时（毫秒） |

**`error`**：`code`（见 §五）、`message`（给用户看的可读文案，不含堆栈与 SQL）、`retryable`（bool）、`session_id`（可为 `null`）。

### 4.2 完整样例

```text
event: meta
data: {"v":1,"request_id":"7f3a1c9e","session_id":"0b1d…"}

event: stage
data: {"phase":"retrieving","elapsed_ms":0}

event: stage
data: {"phase":"generating","elapsed_ms":1840}

event: delta
data: {"seq":1,"text":"按 [1] 在服务台提交申请，"}

event: delta
data: {"seq":2,"text":"由负责人开通账号。"}

event: citations
data: {"citations":[{"index":1,"citation_id":"c1","resource_type":"document","resource_id":"doc-pub-1","publication_id":"pub-1","record_id":"record-1","version_id":"1","revision":101,"chunk_id":"record-1:1","quote":"在服务台提交 vpn 申请，由负责人开通账号。"}]}

event: done
data: {"session_id":"0b1d…","user_message_id":"m1","assistant_message_id":"m2","answer":"按 [1] 在服务台提交申请，由负责人开通账号。","no_evidence":false,"finish_reason":"stop","elapsed_ms":3120}

```

---

## 五、错误码

**规则 S16：错误码表是封闭枚举，客户端按 `code` 分支、按 `message` 展示。**

| `code` | 首帧前的 HTTP | 语义 | `retryable` |
| --- | --- | --- | --- |
| `INVALID_REQUEST` | 400 | 业务入参不合法：`user_id` 非数字或非正整数、`session_id` 格式不合法 | false |
| `SESSION_NOT_FOUND` | 404 | 会话不存在或不属于该用户（**同一出口**，不泄漏他人会话是否存在） | false |
| `TURN_DISCARDED` | 409 | 生成期间会话被清空/删除，本轮回答必须丢弃 | false |
| `DEPENDENCY_UNAVAILABLE` | 503 | 检索依赖不可用（授权数据 / 向量化服务），不降级、不冒充「没有依据」 | true |
| `MODEL_FAILED` | 502 | 模型上游异常（连接失败、非 200、流中断） | true |
| `TIMEOUT` | 504 | 首字超时 / 空闲超时 / 总预算耗尽 | true |
| `INTERNAL` | 500 | 落库等内部错误（事务已回滚） | true |
| `CLIENT_CLOSED` | — | 客户端主动断开，**服务端不再发帧**，只记日志 | — |

**表外还有一类首帧前拒绝：字段级校验返回 `422`。** 问题为空/超长这类由 Pydantic 的字段 validator 拦下，沿用全站统一口径返回 `422` + `loc` 字段定位，不包装成 `error` 帧、也不复用 `INVALID_REQUEST`——`422` 表示「请求体结构不对」，`400` 表示「结构对但算不出授权范围」。两者都不可重试，客户端按 `loc` 把错误落到具体输入框上。

**规则 S17：`retryable=true` 的含义是「服务端确认一条消息都没写」。** 本轮成功落库后只会有 `done`，不会再有 `error`；因此看到 `error` 就代表库里没有这一轮，调用方原样重试是安全的。反过来，`done` 之后的重试一定会产生重复消息，调用方必须自己防重（前端按按钮禁用，服务端靠「一次点击一次请求」）。

**规则 S18：`message` 面向使用者，不得包含堆栈、SQL、内部地址。** 技术细节进 `logger.exception`，帧里只给人能读懂的一句话（例如「知识库依赖不可用，请稍后重试」）。

---

## 六、超时、心跳与预算

**规则 S19：三类超时分开计时，缺一不可。**

| 超时 | 参数 | 默认 | 触发后 |
| --- | --- | --- | --- |
| 首字超时 | `KB_LLM_FIRST_TOKEN_TIMEOUT` | 15s | `error{TIMEOUT}`——上游排队太久，继续等只会让用户盯着空屏 |
| 空闲超时 | `KB_CHAT_STREAM_IDLE_TIMEOUT` | 30s | `error{TIMEOUT}`——长答案不该被总时长误杀，两片之间没动静才算死 |
| 总预算 | `KB_LLM_TIMEOUT` | 90s | `error{TIMEOUT}`——覆盖检索 + 生成 + 落库，防止「每步都在超时以内、整体早该放弃」 |

**规则 S20：心跳间隔 `KB_CHAT_STREAM_PING_INTERVAL`（默认 15s）。** 任何阶段（检索/生成）空闲超过它就必须发一帧 `ping`，因为网关的空闲断连阈值常在 30–60s，一次慢检索就能把连接掐掉。

**规则 S21：客户端在 3× 心跳间隔（默认 45s）内没收到任何帧，判定连接已死并 `abort`。** 不要无限等：服务端已经因为断连停止消费，客户端继续等只会看着一个永远不动的气泡。

**规则 S22：预算按「剩余时间」传递给每个等待点，不要用 `asyncio.timeout` 包住 `yield`。** 生成器在 `yield` 处是挂起的，而 `asyncio.timeout` 计的是墙钟时间——慢客户端会把「写出去花的时间」也算进预算，最终在完全没有慢操作的地方抛 `CancelledError`。

```python
# ✅ 起始时间一次记录，每个等待点自己算剩余预算
deadline = loop.time() + settings.KB_LLM_TIMEOUT
async for chunk in iter_stream(stream, first_token_timeout=..., idle_timeout=..., deadline=deadline):
    yield delta_frame(chunk)

# ❌ 包住整个 yield 循环：慢客户端会误杀自己
async with asyncio.timeout(settings.KB_LLM_TIMEOUT):
    async for chunk in stream:
        yield frame(chunk)
```

---

## 七、中断、落库与一致性

**规则 S23：一次成功的问答 = 一对消息都落库。** 落库在 `citations` 帧之后、`done` 帧之前，事务失败则一条不写并发 `error{INTERNAL}`。不允许「半截答案入库」——它会让下一轮的上下文里出现一条没头没尾的助手消息。

**规则 S24：客户端断开时立即停止消费模型流，不落库、不发帧。**

```python
async for chunk in iter_stream(stream, ..., is_disconnected=request.is_disconnected):
    ...
```

`iter_stream` 每 20 片探一次断连；断开抛 `StreamInterruptedError`，由编排层判定「是真断连还是上游异常」：真断连记 `logger.warning` 后直接返回；上游异常发 `error{MODEL_FAILED}`。

**规则 S25：首轮自动建会话导致「流失败留下空会话壳」是可接受的。** 会话在首帧前就建好（否则 `meta` 给不出 `session_id`），若随后失败，这个壳没有消息、会在「会话记录」页显示为 0 条。**不做自动回收**：并发提问下「删空会话」与「刚写进去一条消息」会互相踩。清理交给会话记录页的删除能力。

**规则 S26：迟到必丢弃。** 生成前记下会话状态快照，落库时用行锁重读；期间被删除或清空则抛 `ChatTurnDiscardedError` → `error{TURN_DISCARDED}`，绝不把会话写活（与契约 `#14` 的 `RESULT_EXPIRED` 同一口径）。

---

## 八、安全与审计

**规则 S27：帧内不出现内部实现细节**：不返回模型名、上游地址、检索分数、提示词正文、堆栈。`delta` 只含回答正文，`citations` 只含授权范围内的切片。

**规则 S28：引用必须可逐字校验。** `quote` 取切片原文（首尾裁剪 + 长度截断），不用折叠空白的 `snippet`——折叠过的文本在调用方侧「找不到原文」，等于给了一份校验不过的证据。

**规则 S29：流式请求同样进审计表，但要知道它的两个限制。**
1. `response_json` 为空：`ApiLogRoute` 只采集 `application/json` 响应体，SSE 不读（避免整条流进内存）；
2. `process_time` 是**首字节耗时**，不是流的总时长——路由处理器在返回 `StreamingResponse` 时就算完耗时了。总时长看 `done.elapsed_ms`（客户端上报或服务端日志）。

流内失败不改变 HTTP 200，所以审计里看不到失败：`error` 帧必须同时 `logger.warning/error` 并带上 `request_id`，否则「服务端说成功、用户说没回答」无从对账。

---

## 九、客户端消费约束

**规则 S30：必须用 `fetch` + `ReadableStream`，不用 `EventSource`**（理由见 S1），解析器收口在 `frontend/src/utils/sse.js`，页面不自己切字符串。

**规则 S31：必须处理四件事**：`error` 帧（展示 `message`、按 `retryable` 决定是否给「重试」）、`done` 帧（用 `answer` 覆盖、用两个 message_id 替换本地临时 key）、连接意外结束（未收到 `done`/`error` → 按中断处理）、`AbortController`（「停止」按钮与页面卸载）。

**规则 S32：增量渲染必须节流。** 每片 `delta` 都重渲染 Markdown（`markdown-it` + `DOMPurify`）会把主线程打满；按 ≥50ms 合并一次再交给 `MarkdownView`，并且 `v-html` 必须继续走 `MarkdownView`（它已内置 `DOMPurify`）。

**规则 S33：`meta` 到达前不要显示「空回答」。** 用 `stage.phase` 显示「正在检索知识…」/「正在生成回答…」，否则用户看到的是一个没有任何反馈的空白气泡。

---

## 十、与 `/v1` 契约链路的关系（尚未接入）

**规则 S34：契约侧接入流式时，帧契约完全复用本文件，只换鉴权与幂等层。**

| 维度 | 管理端 `/api/v1/kb/chat/ask/stream`（已落地） | 契约 `/v1/chat/completion`（待接入） |
| --- | --- | --- |
| 鉴权 | 管理端登录态 + 显式 `user_id` | HMAC 五头 + `authorization` 时效块 |
| 幂等 | 不占幂等键（人在点，一次点击一次提问） | 必须沿用 `request_id` 幂等台账；重放返回首次结果 |
| 结果兜底 | 无（失败重问） | `#15` 结果查询（`done` 丢失时按 `request_id` 回查） |
| 帧格式 / 事件序 / 错误码 | 同本文件 | **同本文件**（唯一区别：`error.code` 增加契约侧错误码映射） |

要点：**流式不是新接口语义，只是同一轮问答的另一种传输**。落库、引用、拒答、迟到丢弃全部走 `KbChatService`，禁止为流式另写一套编排。

---

## 十一、测试要求

**规则 S35：至少四条断言，缺一条视为未实现。**
1. **帧序**：`meta` 第一、`done`/`error` 最后且唯一、`citations` 在 `done` 之前；
2. **落库一致**：`done.answer` 与库里消息正文一致，`quote` 能在切片正文里逐字查到，消息数成对增加；
3. **拒答路径**：一条 `delta` + 空 `citations` + `finish_reason=no_evidence`，且**不调用模型**；
4. **首帧前拒绝**：字段校验 → 422、身份非法 → 400、会话不属于该用户 → 404，且响应体是 JSON 而不是流。

**规则 S36：超时与断连用打桩验，不靠真等。** 首字超时用一个「永不产出」的假流 + 极小的超时参数；断连用一个 `is_disconnected` 恒为 `True` 的桩。

**规则 S37：SSE 帧解析器（`tests/support.py: parse_sse_frames`）本身也要有测试**，否则帧格式回归会表现为「所有流式用例一起挂」而看不出原因。

---

## 十二、部署与代理配置

**规则 S38：反向代理必须满足下面四条，否则「开发能流、生产不流」。**

```nginx
location /api/v1/kb/chat/ask/stream {
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;          # 必须：否则整条流被攒到结束
    gzip off;                     # 或 gzip_types 排除 text/event-stream
    proxy_read_timeout 300s;      # 必须 > 总预算 90s + 心跳 15s 的余量
}
```

容器化部署同理：K8s Ingress 需关 `buffering`，网关/CDN 需放行 `text/event-stream` 不分块缓存。

---

## 十三、版本与兼容

**规则 S39：兼容变更**（不改 `v`）：新增事件名、给已有事件新增字段、新增 `error.code`。前提是客户端遵守 S10（忽略未知）。

**规则 S40：不兼容变更必须升 `v` 并在 `meta.v` 首帧声明**：删除/重命名事件、改变已有字段语义或类型、改变 `done`/`error` 的一次性约束。服务端保留旧版本至少一个迭代周期，`meta.v` 是客户端唯一的判据来源。

---

## 十四、落地映射（基座当前实现）

| 规范要求 | 代码位置 |
| --- | --- |
| 帧序列化（S7） | [backend/app/utils/sse.py](../../backend/app/utils/sse.py) `format_sse` / `SSE_HEADERS` / `SSE_MEDIA_TYPE` |
| 事件名、字段、错误码（§四 / §五） | [backend/app/modules/kb/chat/sse.py](../../backend/app/modules/kb/chat/sse.py) |
| 首字/空闲/总预算/断连（S19–S24） | [backend/app/core/chat/streaming.py](../../backend/app/core/chat/streaming.py) `iter_stream` / `iter_with_heartbeat` / `close_stream` |
| 编排与落库（S23–S26） | [backend/app/modules/kb/chat/service.py](../../backend/app/modules/kb/chat/service.py) `completion_stream` |
| 首帧前校验与异常转换（S5） | [backend/app/modules/kb/chat/admin_service.py](../../backend/app/modules/kb/chat/admin_service.py) `open_stream` |
| 路由与响应头（S3） | [backend/app/modules/kb/chat/controller.py](../../backend/app/modules/kb/chat/controller.py) `POST /chat/ask/stream` |
| 客户端解析（S30–S33） | [frontend/src/utils/sse.js](../../frontend/src/utils/sse.js) + [frontend/src/views/module_kb/aiChat/index.vue](../../frontend/src/views/module_kb/aiChat/index.vue) |
| 测试（§十一） | [backend/tests/modules/kb/test_chat_stream.py](../../backend/tests/modules/kb/test_chat_stream.py)（帧序 / 落库一致 / 拒答 / 超时）、[backend/tests/core/test_sse.py](../../backend/tests/core/test_sse.py)（帧形与解析器）、[backend/tests/core/test_chat_streaming.py](../../backend/tests/core/test_chat_streaming.py)（三类超时 / 断连 / 心跳） |
