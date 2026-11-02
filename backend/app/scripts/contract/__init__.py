"""`/v1` 契约联调与验收脚本（不参与运行时启动）。

| 脚本 | 用途 |
| --- | --- |
| `mock_gsi.py` | GSI 侧冒烟驱动器：本地样例文件 + 负向用例 + `--allow-skeleton` |
| `verify_m1.py` | M1 门禁：20 条路由逐条验签 + 审计/幂等台账核对 + `--record` 证据 |
| `testing.py` | 两个脚本共用的响应解析与错误码取值 |

运行示例（在 `backend/` 目录）：

```
.venv\\Scripts\\python.exe -m app.scripts.contract.verify_m1 --base-url http://127.0.0.1:8002
.venv\\Scripts\\python.exe -m app.scripts.contract.mock_gsi --base-url http://127.0.0.1:8003 --allow-skeleton
```
"""
