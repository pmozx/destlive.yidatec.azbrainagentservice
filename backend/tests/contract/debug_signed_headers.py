r"""调试脚本（**不是测试**）：生成一组可直接粘贴到 curl / Apifox / Postman 的签名头。

```bash
cd backend && .venv\Scripts\python.exe -m tests.contract.debug_signed_headers
```

四处必须与服务端完全一致，否则一律 401「服务签名认证失败」：

1. 密钥 = `env/.env.<ENVIRONMENT>` 的 `CONTRACT_V1_SHARED_SECRET`（不要再硬编码另一份）；
2. path = 真实请求路径，服务端用 `request.url.path` 比对，传空串必错；
3. body 原始字节：多行 JSON 完全支持，但缩进/换行/末尾换行差一个字节就验不过，
   所以统一从 `06-.../test-data/requests/` 读文件字节，客户端也用同一个文件发（curl 必须 `--data-binary @file`，
   `-d` / `--data-raw` 会把换行删掉）；
4. 五个头都要有值，缺值会先被判「缺少签名请求头」。

**为什么不放在 `test_*.py` 里**：它只有 print 没有断言，唯一的 assert（密钥非空）在
test/uat/prod 反而会误报——那三个环境的密钥是**故意留空待 GSI 下发**的。
"""

from app.config.setting import settings
from app.core.rich_print import pretty_log
from app.core.security_signature import body_digest, build_signed_headers
from tests.contract import case_data

###### 01 提交文档 / FAQ 解析任务 ######
DEBUG_PATH = "/v1/parse/jobs"
DEBUG_METHOD = "POST"
# 待签名正文：默认取文档解析基线，换成 "01_parse_jobs_faq.json" 即 FAQ 基线
DEBUG_BODY_NAME = "01_parse_jobs_doc.json"
DEBUG_BODY_FILE = case_data.REQUEST_DIR / DEBUG_BODY_NAME
DEBUG_BODY = case_data.request_bytes(DEBUG_BODY_NAME)

###### 02 查询解析进度与完整结果 ######
# DEBUG_PATH = "/v1/parse/jobs/parse-faq-200001"
# DEBUG_METHOD = "GET"
# DEBUG_BODY = b''

def main() -> None:
    secret = settings.CONTRACT_V1_SHARED_SECRET
    if not secret.strip():
        print("⚠️  CONTRACT_V1_SHARED_SECRET 为空：服务端会返回 503，先在 env/.env.dev 配置密钥")
        return

    # timestamp_ms / nonce 不显式传：默认取当前毫秒 + 随机 nonce；
    # 需要复现某次历史签名（例如比对报告里的旧头）时，再固定 timestamp_ms=... 与 nonce="..."
    signed_headers = build_signed_headers(
        secret,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method=DEBUG_METHOD,
        path=DEBUG_PATH,
        body=DEBUG_BODY,
    )

    print("==== 自定义安全头 ====")
    print("== 请求体 ==")
    print(DEBUG_BODY)
    print("== 请求体长度 ==")
    print(len(DEBUG_BODY))
    print("== 请求体 SHA-256 ==")
    print(body_digest(DEBUG_BODY))
    print("== 自定义安全头 ==")
    print(pretty_log(signed_headers))
    print("== apifox 请求头 ==")
    # 逐行 `头名:值`（冒号后不留空格），Apifox 的「批量编辑 / 原始文本导入」可直接粘贴
    print("\n".join(f"{name}:{value}" for name, value in signed_headers.items()))
    # print("== curl 命令 ==")
    # 多行 body 的核对依据：客户端发完后用字节数与摘要确认发的是同一份数据
    # print(f"\n# body {len(DEBUG_BODY)} 字节 / SHA-256 {body_digest(DEBUG_BODY)}")
    # print(
    #     f"curl --location --request {DEBUG_METHOD} 'http://localhost:{settings.PORT}{DEBUG_PATH}' \\\n"
    #     + "".join(f"  --header '{name}:{value}' \\\n" for name, value in signed_headers.items())
    #     + "  --header 'Content-Type: application/json' \\\n"
    #     + f"  --data-binary @{DEBUG_BODY_FILE}"
    # )


if __name__ == "__main__":
    main()
