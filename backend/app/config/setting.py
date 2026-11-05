import os
from typing import Final, Optional, Dict
from urllib.parse import quote_plus

from dotenv import dotenv_values
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, computed_field, model_validator
from app.config.path_conf import ENV_DIR, BASE_ENV_FILE
from app.common.enums import DatabaseTypeEnum

"""
应用配置模块
实现类似 Spring Boot profiles 的环境配置机制：
1. 优先读取操作系统环境变量 ENVIRONMENT
2. 其次读取 env/.env 文件中的 ENVIRONMENT
3. 以上均未配置时，默认使用 dev 环境
最终加载对应的 env/.env.{environment} 环境配置文件
"""
_ENVIRONMENT_KEY:Final[str] = "ENVIRONMENT"
_DEFAULT_ENVIRONMENT:Final[str] = "dev"

def _get_environment() -> str:
    """
    解析当前运行环境名。
    优先级：操作系统环境变量 > env/.env 文件 > 默认 dev 环境
    :return: 环境名（dev/prod）
    """
    environment = os.environ.get(_ENVIRONMENT_KEY)
    if not environment:
        base_config: Dict[str, Optional[str]] = dotenv_values(BASE_ENV_FILE)
        environment = base_config.get(_ENVIRONMENT_KEY)
    return environment or _DEFAULT_ENVIRONMENT

def _get_env_file() -> Path:
    """
    确定当前运行环境并返回对应的环境配置文件路径。

    优先级：操作系统环境变量 > env/.env 文件 > 默认 dev 环境
    :return: 环境配置文件路径
    """
    # 1~3. 解析环境名（环境变量 > env/.env > 默认 dev）
    environment = _get_environment()

    # 拼接环境配置文件路径：env/.env.{environment}
    env_file = ENV_DIR / f".env.{environment}"

    if not env_file.exists():
        raise FileNotFoundError(
            f"环境配置文件不存在: {env_file}\n"
            f"当前环境: ENVIRONMENT={environment}\n"
            f"请在 env/ 目录下创建 .env.{environment} 文件"
        )

    return env_file


class Settings(BaseSettings):
    """
    应用配置（类似 Spring Boot 的 application.yml）
    所有配置项从 env/.env.{environment} 文件中加载
    """
    model_config = {
        "env_file": _get_env_file(),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        # 校验失败时不要把整个配置字典（含数据库密码、API Key）打进异常信息
        "hide_input_in_errors": True,
    }

    # ==================== 应用配置 ====================
    ENVIRONMENT: str = Field(default=_get_environment(), description="当前运行环境：dev/prod（prod 不回显异常细节）")
    TITLE: str = Field(default="service_brain_agent", description="应用标题")
    DESCRIPTION: str = Field(default="服务大脑智能体应用", description="应用描述")
    VERSION: str = Field(default="1.0.0", description="应用版本")

    DEBUG: bool = Field(default=True, description="是否开启调试模式")
    # 展示时区：库内 timestamptz 一律按 UTC 存，接口输出前统一换算到该时区再格式化，
    # 否则页面把 UTC 墙上时间当本地时间显示（北京时间会差 8 小时）
    TIMEZONE: str = Field(default="Asia/Shanghai", description="接口时间输出与入参解析使用的业务时区（IANA 名）")

    # ==================== 服务配置 ====================
    HOST: str = Field(default="0.0.0.0", description="服务监听地址")
    PORT: int = Field(default=8000, description="服务监听端口")
    RELOAD: bool = Field(default=True, description="是否开启热重载")

    # ==================== 数据库配置 ====================
    # 下列默认值仅用于 env 文件缺项时的兜底，实际取值以 env/.env.<env> 为准；
    # 端口口径：39990 是宿主发布端口，容器内按容器名直连仍是 5432
    DATABASE_TYPE: str = Field(default=DatabaseTypeEnum.POSTGRES.value, description="数据库类型：postgres/mysql/sqlite")
    DATABASE_HOST: str = Field(default="localhost", description="数据库主机地址")
    DATABASE_PORT: int = Field(default=5432, description="数据库端口")
    DATABASE_USER: str = Field(default="postgres", description="数据库用户名")
    DATABASE_PASSWORD: str = Field(default="", description="数据库密码（**只从 env 读取**，源码不留默认值）")
    DATABASE_NAME: str = Field(default="service_brain", description="数据库名称")
    DATABASE_ECHO: bool = Field(default=False, description="是否输出 SQL 日志")

    # ==================== 向量存储配置（pgvector） ====================
    VECTOR_ENABLE: bool = Field(default=True, description="是否启用 pgvector 向量存储（仅 DATABASE_TYPE=postgres 生效）")
    VECTOR_DIM: int = Field(default=1024, description="向量维度，须与 embedding 模型输出一致（bge-m3 为 1024）")
    VECTOR_AUTO_CREATE: bool = Field(default=True, description="首次读写向量表时幂等创建 vector 扩展、向量表与 HNSW 索引")
    CHECKPOINT_ENABLE: bool = Field(
        default=True, description="是否启用 LangGraph PostgresSaver 断点（文档流水线节点级快照，DB 非 postgres 时自动跳过）"
    )

    # ==================== 模型服务集成配置（LLM / Embedding / Rerank，两个集成域共用） ====================
    # 三类推理服务都按“OpenAI 兼容 / 私有化部署”统一收口；地址留空 = 尚未接入，调用时抛可读 503，
    # 不做静默降级（避免“看起来跑通、实际未向量化”）。
    LLM_BASE_URL: str = Field(default="", description="聊天模型地址（OpenAI 兼容，如 https://api.deepseek.com/v1、自建 vLLM/Azure 网关）")
    LLM_API_KEY: str = Field(default="", description="聊天模型密钥（私有化部署可留空）")
    LLM_MODEL: str = Field(default="", description="聊天模型名称")
    LLM_TIMEOUT: float = Field(default=60.0, description="聊天请求超时（秒）")
    LLM_MAX_RETRIES: int = Field(default=2, description="聊天请求重试次数（仅超时/429/5xx 重试）")

    EMBEDDING_PROTOCOL: str = Field(default="openai", description="向量化协议：openai（/v1/embeddings）或 ollama（/api/embed）")
    EMBEDDING_BASE_URL: str = Field(default="", description="向量化服务地址；ollama 填到端口（如 http://service-brain-ollama:11434）")
    EMBEDDING_API_KEY: str = Field(default="", description="向量化服务密钥（ollama 私有化无需）")
    EMBEDDING_MODEL: str = Field(default="bge-m3", description="向量化模型名称，输出维度须与 VECTOR_DIM 一致")
    EMBEDDING_BATCH_SIZE: int = Field(default=16, description="单次向量化条数（过大易触发服务端 413/超时）")
    EMBEDDING_TIMEOUT: float = Field(default=60.0, description="向量化请求超时（秒）")
    EMBEDDING_CONCURRENCY: int = Field(default=3, description="向量化分批并发数（同一批文档的分片并行提交，过大易打爆模型服务）")
    EMBEDDING_MAX_RETRIES: int = Field(default=2, description="向量化单批失败重试次数（指数退避，4xx 也会重试，代价可控）")

    RERANK_BASE_URL: str = Field(default="", description="重排序服务地址（llama.cpp --rerank，如 http://service-brain-rerank:8001）")
    RERANK_PATH: str = Field(default="/v1/rerank", description="重排序接口路径")
    RERANK_API_KEY: str = Field(default="", description="重排序服务密钥（私有化可留空）")
    RERANK_MODEL: str = Field(default="bge-reranker-v2-m3", description="重排序模型名称（服务端单模型时可留空）")
    RERANK_TIMEOUT: float = Field(default=30.0, description="重排序请求超时（秒）")
    RERANK_SCORE_IS_LOGIT: bool = Field(
        default=True,
        description="重排序服务返回的是否为 logit（llama.cpp 默认返回 logit，可正可负）；Jina/Cohere 同构服务返回 0–1 概率，此时置 false",
    )

    # ==================== 检索配置（#07 关键词 + 自然语言） ====================
    SEARCH_INDEX_AUTO_CREATE: bool = Field(
        default=True,
        description="启动期幂等补建分片表关键词 GIN 索引；大表建议关闭，改用 sql/v1.6_keyword_search_index.sql 人工 CONCURRENTLY 建",
    )
    SEARCH_KEYWORD_CANDIDATE_MAX: int = Field(
        default=200, description="关键词检索 SQL 候选上限（应用层打分排序后再截断到契约 limit）"
    )
    SEARCH_VECTOR_CANDIDATE_MAX: int = Field(
        default=200, description="向量检索候选上限（HNSW 粗排窗口，融合精排前截断）"
    )
    SEARCH_RRF_K: int = Field(
        default=60,
        description="RRF 融合平滑常数 k：单通道得分 1/(k+rank)，越大越弱化头部差异（Google 论文经验值 60）",
    )
    SEARCH_RERANK_WINDOW: int = Field(
        default=6,
        description=(
            "重排序窗口：融合后送入精排的候选上限（超出按融合序截断），同时是该通道的返回条数上限。"
            "默认 6 由实测推导：CPU 上 bge-reranker 与条数严格线性（1 条 1.6s / 3 条 3.2s / 6 条 6.1s / "
            "10 条 9.9s / 20 条 18.9s，正文 500 字符），6 条≈6s 加向量化与库检索仍能压在 "
            "SEARCH_RERANK_TIMEOUT 内；窗口给到 20 会必然耗尽预算再降级，等于白付一次尾段耗时"
        ),
    )
    SEARCH_RERANK_DOC_CHARS: int = Field(
        default=500,
        description="送精排的单条正文字符上限（标题不截断）；实测 800→500 字符可省约 25% 耗时且不影响排序",
    )
    SEARCH_RERANK_TIMEOUT: float = Field(
        default=8.0,
        description="检索链路的精排尾段预算（秒）：超时即降级为 RRF 融合序，不让调用方白等 RERANK_TIMEOUT",
    )
    SEARCH_SNIPPET_LENGTH: int = Field(default=120, description="命中摘要长度（字符数，超出以省略号截断）")

    PARSE_THREAD_POOL_SIZE: int = Field(default=4, description="文档解析线程池并发（CPU/IO 密集，建议不超过逻辑核数）")
    PARSE_CHUNK_SIZE: int = Field(default=800, description="默认分片长度（字符数）")
    PARSE_CHUNK_OVERLAP: int = Field(default=120, description="默认相邻分片重叠字符数，须小于分片长度")
    PARSE_FAQ_MAX_CHARS: int = Field(default=30000, description="FAQ 提取时送给模型的文档正文上限（字符数），超出截断并告警")
    PARSE_MAX_FILE_BYTES: int = Field(
        default=10 * 1024 * 1024,
        description="单文件解析上限（字节，默认 10MB）：声明大小与实拉字节都要校验，超限按 413 拒绝",
    )

    # ---- 解析页图预览（#02 result.pages，T2.4）----
    # 仅 PDF 能被本地光栅化；Office/图片等仍返回 []，不伪造图像 URL。
    # 页图上传对象存储，image_url 是预签名 GET URL（地址与凭据见 S3_*），不再落本地、不再自建预览路由。
    PARSE_PREVIEW_ENABLE: bool = Field(
        default=True,
        description="是否渲染 PDF 页图并返回预签名预览 URL；S3_ENABLE=false 时整项退化为返回 []",
    )
    PARSE_PREVIEW_MAX_PAGES: int = Field(default=50, description="单个文档最多渲染的页数，超出只渲染前 N 页并告警")
    PARSE_PREVIEW_SCALE: float = Field(default=2.0, description="页图缩放倍率（1.0≈72dpi，2.0≈144dpi）")
    PARSE_PREVIEW_TTL_SECONDS: int = Field(
        default=604800,
        description="页图预签名 URL 有效期（秒），默认 7 天；每次投影响应都按该值重签",
    )

    # ==================== AI 问答检索生成策略（kb 域：/v1 检索 + 问答、/kb/chat 管理端） ====================
    # 检索与生成的「策略」参数收口在这里，换模型无需改代码（模型地址见 LLM_* / EMBEDDING_* / RERANK_*）。
    # KB_LLM_TIMEOUT / KB_LLM_FIRST_TOKEN_TIMEOUT / KB_CHAT_STREAM_* 由 /kb/chat/ask/stream 消费；
    # KB_RETRIEVE_TOP_K / KB_FOLLOWUP_COUNT 仍为零引用（检索层用自己的窗口），保留为口径基线。
    KB_RETRIEVE_TOP_K: int = Field(default=20, description="向量召回候选条数（精排前的粗排窗口）")
    KB_RETRIEVE_MIN_SCORE: float = Field(default=0.2, description="向量召回余弦相似度下限（1-距离）")
    KB_RERANK_TOP_N: int = Field(default=8, description="精排后保留条数（拼上下文的上限）")
    KB_RERANK_MIN_SCORE: float = Field(
        default=0.3,
        description="精排相关度绝对下限（0–1）：低于它判为无证据；同时作为逐条命中的保留下限。重排序服务不可用时该闸门跳过",
    )
    KB_RERANK_RELATIVE_RATIO: float = Field(
        default=0.6, description="精排相对闸门：低于 top1 相关度该比例的命中被丢弃（必须在 0–1 空间，logit 乘比例会反向）"
    )
    KB_GRADE_MIN_CHUNKS: int = Field(default=1, description="命中条数下限，低于该值直接拒答")
    KB_CONTEXT_MAX_CHARS: int = Field(default=6000, description="拼给模型的知识片段总字符上限")
    KB_SNIPPET_CHARS: int = Field(default=160, description="引用来源摘要片段长度")
    KB_HISTORY_TURNS: int = Field(default=4, description="带入多轮上下文的历史消息条数")
    KB_ANSWER_TEMPERATURE: float = Field(default=0.2, description="回答温度（知识问答建议低温）")
    KB_ANSWER_MAX_TOKENS: int = Field(default=1500, description="回答最大 token 数")
    KB_LLM_TIMEOUT: float = Field(default=90.0, description="单轮回答总预算（秒）：覆盖检索 + 生成 + 落库")
    KB_LLM_FIRST_TOKEN_TIMEOUT: float = Field(default=15.0, description="首 token 超时（秒），超时转错误帧而不是无限等待")
    KB_CHAT_STREAM_IDLE_TIMEOUT: float = Field(
        default=30.0, description="SSE 流式生成的空闲超时（秒）：两片之间超过它即判超时并发 error 帧"
    )
    KB_CHAT_STREAM_PING_INTERVAL: float = Field(
        default=15.0, description="SSE 心跳间隔（秒）：任何阶段空闲超过它发一次 ping 帧，防网关空闲断连"
    )
    KB_FOLLOWUP_COUNT: int = Field(default=3, description="问题拓展条数")

    # ---- 重复治理（#16–#19）----
    DUP_MAX_SCOPE_ITEMS: int = Field(
        default=1000,
        description="单次治理请求 scope.resources / scope.drafts 各自的上限，超限按 413 拒绝（不静默截断）",
    )

    # ---- 集成期知识空间（文档上传 → 线程池解析 → 分片入库）----
    KB_SEED_ENABLE: bool = Field(
        default=True, description="启动时导入 sql/ 下的种子数据（一张表一个同名 JSON，表非空则整表跳过）"
    )
    SPACE_UPLOAD_MAX_FILES: int = Field(default=10, description="单次批量上传文档数上限")
    SPACE_UPLOAD_MAX_SIZE_MB: int = Field(default=50, description="单个文档大小上限（MB），超限逐份报错不回滚整批")

    # ---- MinerU 文档解析（当前暂定的“内容提取”主路径）----
    # 只处理二进制/版面类文档（PDF / Office / 图片）；纯文本与 HTML 仍走内置解析器，不浪费一次上传。
    MINERU_ENABLE: bool = Field(default=True, description="是否用 MinerU 接管 PDF/Office/图片解析")
    MINERU_API_TOKEN: str = Field(default="", description="MinerU API Token（密钥，仅写 env）；留空则降级为 flash 模式")
    MINERU_BASE_URL: str = Field(default="", description="MinerU 标准 API 地址（留空用 SDK 官方默认，私有化部署时覆盖）")
    MINERU_FLASH_BASE_URL: str = Field(default="", description="MinerU 轻量解析 API 地址（留空用 SDK 内置默认）")
    MINERU_MODEL: str = Field(default="vlm", description="精准解析模型：vlm / pipeline / html")
    MINERU_TIMEOUT: int = Field(default=300, description="解析轮询总超时（秒）；大文档成分钟级，不建议调小")
    MINERU_OCR: bool = Field(default=True, description="是否启用 OCR（扫描件必须开）")
    MINERU_LANGUAGE: str = Field(default="ch", description="文档语言代码（ch/en 等，影响 OCR 识别）")

    # ---- S3 兼容对象存储（支持 MinIO / 华为云 OBS / 阿里云 OSS 等）----
    # 走 S3 协议：换云厂商只改 endpoint 与凭据；provider 标识服务商类型（用于适配差异）
    # endpoint 留空 = 未接入，调用时抛可读 503。MinIO 必须 path-style 寻址。
    S3_ENABLE: bool = Field(default=False, description="是否启用对象存储（文档原件/解析产物）")
    S3_PROVIDER: str = Field(default="minio", description="存储服务商类型：minio / huawei-obs / aliyun-oss / aws-s3")
    S3_ENDPOINT: str = Field(default="", description="S3 兼容服务地址；容器内填 http://service-brain-minio:9000，宿主进程填 http://<IP>:39900")
    S3_ACCESS_KEY: str = Field(default="", description="访问密钥（建议用最小权限子账号，不用 root）")
    S3_SECRET_KEY: str = Field(default="", description="访问密钥（敏感，仅写 env）")
    S3_BUCKET: str = Field(default="service-brain", description="默认桶名，启动时按 S3_AUTO_CREATE 幂等创建")
    S3_REGION: str = Field(default="us-east-1", description="区域；MinIO 不区分，但签名必须带（非 us-east-1 会附带 LocationConstraint）")
    S3_USE_SSL: bool = Field(default=False, description="endpoint 是否走 HTTPS（内网 MinIO 通常为 false）")
    S3_ADDRESSING_STYLE: str = Field(default="path", description="寻址风格：path（MinIO 必需）/ virtual（云厂商子域名风格）")
    S3_AUTO_CREATE: bool = Field(default=True, description="启动时自动建桶（已存在则跳过）")
    S3_TIMEOUT: float = Field(default=30.0, description="连接与读超时（秒）；大文件上传可适当调大")
    S3_PRESIGN_EXPIRE: int = Field(default=3600, description="预签名下载链接有效期（秒）")
    S3_KEY_PREFIX: str = Field(default="", description="对象键统一前缀（多环境共用一个桶时用于隔离）")
    IO_THREAD_POOL_SIZE: int = Field(default=4, description="阻塞 IO 线程池并发（对象存储上传/下载等网络等待）")

    # 连接池配置
    ECHO_POOL: bool = Field(default=False, description="是否输出连接池日志")
    POOL_PRE_PING: bool = Field(default=True, description="连接前是否进行 ping 检测")
    FUTURE: bool = Field(default=True, description="是否使用 2.0 风格连接")
    POOL_RECYCLE: int = Field(default=3600, description="连接回收时间（秒）")
    POOL_SIZE: int = Field(default=5, description="连接池大小")
    MAX_OVERFLOW: int = Field(default=10, description="最大溢出连接数")
    POOL_TIMEOUT: int = Field(default=30, description="连接超时时间（秒）")
    POOL_USE_LIFO: bool = Field(default=False, description="是否使用 LIFO 连接策略")

    # 会话配置
    AUTOCOMMIT: bool = Field(default=False, description="是否自动提交")
    AUTOFLUSH: bool = Field(default=False, description="是否自动刷新（每次查询前把挂起变更刷入事务）")
    EXPIRE_ON_COMMIT: bool = Field(default=False, description="提交后是否过期实例")

    # ==================== 日志配置 ====================
    LOGGER_LEVEL: str = Field(default="INFO", description="日志级别")

    # ==================== 接口审计日志配置（表 sbp_ai_system_api_log） ====================
    API_LOG_ENABLE: bool = Field(default=True, description="是否开启接口审计日志")
    API_LOG_RECORD_METHOD: list[str] = Field(
        default=["POST", "PUT", "PATCH", "DELETE"],
        description="需要记录的请求方法（GET/HEAD/OPTIONS 默认不记录，避免查询与预检淹没写操作）",
    )
    API_LOG_SKIP_PATHS: list[str] = Field(
        default=["/docs", "/redoc", "/openapi.json", "/ui", "/static"],
        description="不参与审计的路径前缀：接口文档、前端产物与静态资源",
    )
    API_LOG_FORCE_PATHS: list[str] = Field(
        default=["/v1"],
        description="命中即忽略方法白名单的路径前缀：契约 /v1 的 GET（如 #02 轮询）也必须全量留痕",
    )
    API_LOG_MAX_PAYLOAD: int = Field(default=2000, description="请求/响应体入库最大字符数，超出截断")
    API_LOG_USER_HEADER: str = Field(
        default="",
        description="回填操作人的请求头名；留空表示只认 request.state 回填，避免客户端伪造操作人",
    )
    API_LOG_AUTO_CREATE: bool = Field(default=True, description="启动时自动创建审计表（已存在则跳过）")
    API_LOG_RETENTION_DAYS: int = Field(default=90, description="审计日志保留天数，<=0 表示启动时不做过期清理")
    TRUSTED_PROXY_HOPS: int = Field(default=0, description="前置可信反向代理跳数，0 表示忽略代理头、仅取 TCP 对端地址")

    # ==================== /v1 接口安全请求头（契约接入层） ====================
    # 验签参数只有这一组：调用方标识、HMAC 共享密钥、时间戳窗口、nonce 保留时长、authorization 跨度。
    # 算法在 app/core/security_signature.py，错误码口径在 app/modules_api/base/errors.py。
    # 密钥只写在 env：留空＝未配置，验签失败关闭（契约 503 AUTHORIZATION_UNAVAILABLE）——
    # 宁可服务不可用，也不能因为缺密钥就把签名校验放过。
    CONTRACT_V1_CLIENT_ID: str = Field(default="gsi-service", description="认可的调用方标识（X-AI-Client-Id）")
    CONTRACT_V1_SHARED_SECRET: str = Field(default="", description="HMAC 共享密钥；留空＝未配置，验签失败关闭")
    CONTRACT_CLOCK_SKEW_MS: int = Field(default=300_000, description="时间戳容许窗口（毫秒），契约口径前后 5 分钟")
    CONTRACT_NONCE_TTL_SECONDS: int = Field(
        default=600, description="nonce 台账保留时长（秒）；须 ≥ 2×时钟窗口，否则窗口内重放可能漏判"
    )
    CONTRACT_AUTHORIZATION_MAX_TTL_MS: int = Field(
        default=120_000, description="authorization 签发-过期最大跨度（毫秒）"
    )

    # ==================== 正式接入层（/v1，T1.1–T1.5，app/modules_api/base） ====================
    CONTRACT_V1_ENABLE: bool = Field(
        default=True, description="是否挂载正式 /v1 契约路由；关闭后 /v1 完全不挂载（404）"
    )
    CONTRACT_TEST_ENABLE: bool = Field(
        default=False, description="是否挂载测试辅助路由（/v1/test/*）；生产环境必须关闭"
    )
    CONTRACT_IDEMPOTENCY_TTL_DAYS: int = Field(default=90, description="幂等台账保留天数（墓碑不清理）；契约同步查询窗口 30 分钟")
    CONTRACT_TENANT_ID: str = Field(default="gsi-default", description="契约固定租户（v2.1 口径）")

    @model_validator(mode="after")
    def _require_database_password(self) -> "Settings":
        """必填校验：非 sqlite 时口令不能为空。

        口令曾经写死在源码里当默认值，等于把生产凭据随代码仓库分发；现在默认空串，
        配错/漏配必须在启动期就失败——否则报错会延后到第一次建连，且容易被当成网络问题。
        """
        if self.DATABASE_TYPE != DatabaseTypeEnum.SQLITE.value and not self.DATABASE_PASSWORD:
            raise ValueError(
                "DATABASE_PASSWORD 未配置：请在 env/.env.{0} 中设置 DATABASE_PASSWORD（源码不再内置默认口令）".format(
                    self.ENVIRONMENT
                )
            )
        return self

    @computed_field
    @property
    def database_url(self) -> str:
        """根据数据库类型自动拼接连接 URL

        口令按 URL 规则编码，避免密码含 @ / : / # 等字符时连接串被截断；
        charset 仅 MySQL 需要——asyncpg 遇到未知 query 参数会在建连阶段直接报错。
        """
        driver = DatabaseTypeEnum.async_driver_for(self.DATABASE_TYPE)
        if self.DATABASE_TYPE == DatabaseTypeEnum.SQLITE.value:
            return f"{driver}:///{self.DATABASE_NAME}"
        credential = f"{quote_plus(self.DATABASE_USER)}:{quote_plus(self.DATABASE_PASSWORD)}"
        url = (
            f"{driver}://{credential}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )
        if self.DATABASE_TYPE == DatabaseTypeEnum.MYSQL.value:
            return f"{url}?charset=utf8mb4"
        return url

    @computed_field
    @property
    def async_db_url(self) -> str:
        """异步数据库连接 URI（别名，兼容 database.py）"""
        return self.database_url

    @computed_field
    @property
    def psycopg_url(self) -> str:
        """同步 psycopg v3 DSN（LangGraph PostgresSaver 专用）。

        asyncpg 的 URL 不能直接给 psycopg（驱动名不同），这里单独拼 ``postgresql://``；
        非 postgres 环境返回空串，调用方据此关闭断点能力而不是把连接串写错。
        """
        if self.DATABASE_TYPE != DatabaseTypeEnum.POSTGRES.value:
            return ""
        credential = f"{quote_plus(self.DATABASE_USER)}:{quote_plus(self.DATABASE_PASSWORD)}"
        return f"postgresql://{credential}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"


# 全局配置单例（导入即用）
settings = Settings()
