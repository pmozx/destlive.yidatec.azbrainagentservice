"""文件存储（系统管理 → 文件存储）：S3 兼容对象存储的浏览与维护模型。

对象键口径与 ``app/core/clients/s3.py`` 保持一致：**不含** ``S3_KEY_PREFIX``，
全局前缀由客户端统一拼接。所以页面上看到的是"应用目录内"的相对键
（如 ``parse-previews/<job_id>/page-1.png``），换环境共用同一个桶时不会串目录。

分页口径：S3 的 ``list_objects_v2`` 只给续传令牌、不给总数，因此这里用
``next_token`` + ``is_truncated`` 表达分页，而不是管理端通用的 ``total``。
"""

from pydantic import BaseModel, Field

from app.core.validator import DateTimeStr


class StorageConfigOutSchema(BaseModel):
    """存储配置回显：只读投影，不提供写回（改了内存值不会落到 env，反而误导）。"""

    enabled: bool = Field(..., description="对象存储是否启用（S3_ENABLE）")
    provider: str = Field(default="", description="服务商类型：minio/huawei-obs/aliyun-oss/aws-s3")
    endpoint: str = Field(default="", description="S3 兼容服务地址")
    bucket: str = Field(default="", description="桶名")
    key_prefix: str = Field(default="", description="应用级对象键前缀（多环境共桶时用于隔离）")
    region: str = Field(default="", description="区域")
    addressing_style: str = Field(default="", description="寻址风格：path/virtual")
    use_ssl: bool = Field(default=False, description="endpoint 是否走 HTTPS")
    auto_create: bool = Field(default=True, description="启动时是否幂等建桶")
    presign_expire: int = Field(default=3600, description="预签名下载链接有效期（秒）")
    upload_max_size_mb: int = Field(default=100, description="单文件上传上限（MB），超限按 413 拒绝")


class StorageFolderOutSchema(BaseModel):
    """子目录行：``prefix`` 含尾斜杠，可直接作为下一次浏览的 ``prefix`` 入参。"""

    prefix: str = Field(..., description="目录前缀（含尾斜杠）")
    name: str = Field(..., description="目录名（末段）")


class StorageFileOutSchema(BaseModel):
    """对象行：只回列目录所需的轻量字段，内容走 ``/storage/content`` 或预签名链接。"""

    key: str = Field(..., description="对象键（不含全局前缀）")
    name: str = Field(..., description="文件名（键的末段）")
    size: int = Field(default=0, description="字节数")
    last_modified: DateTimeStr | None = Field(default=None, description="最后修改时间")
    content_type: str | None = Field(default=None, description="按扩展名推断的 MIME（不额外发请求）")


class StorageBrowseOutSchema(BaseModel):
    """目录浏览结果：文件夹在前、文件在后，同层内按 S3 的键字典序。"""

    prefix: str = Field(default="", description="当前目录前缀（空串为根目录）")
    keyword: str | None = Field(default=None, description="名称过滤词（拼在前缀后做前缀匹配）")
    folders: list[StorageFolderOutSchema] = Field(default_factory=list, description="子目录")
    files: list[StorageFileOutSchema] = Field(default_factory=list, description="当前层文件")
    next_token: str | None = Field(default=None, description="下一页续传令牌；为空表示没有下一页")
    is_truncated: bool = Field(default=False, description="是否还有下一页")


class StoragePresignOutSchema(BaseModel):
    """预签名下载链接：浏览器可直连对象存储，适合大文件。"""

    key: str = Field(..., description="对象键")
    url: str = Field(..., description="预签名 GET URL")
    expires_in: int = Field(..., description="有效期（秒）")


class StorageUploadOutSchema(BaseModel):
    """上传结果：回显落库后的键与大小，供页面提示覆盖情况。"""

    key: str = Field(..., description="写入后的对象键（不含全局前缀）")
    name: str = Field(..., description="文件名（键的末段）")
    size: int = Field(default=0, description="字节数")
    content_type: str = Field(default="application/octet-stream", description="写入时声明的 MIME")
    overwritten: bool = Field(default=False, description="是否覆盖了已存在的同名对象")


class StorageDeleteInSchema(BaseModel):
    """批量删除入参：仅文件，目录需进入后逐个删除。"""

    keys: list[str] = Field(
        ..., min_length=1, max_length=100, description="待删除对象键列表（不含全局前缀，最多 100 个）"
    )


class StorageDeleteFailureSchema(BaseModel):
    """删除失败的单个对象：键校验不通过或对象存储拒绝。"""

    key: str = Field(..., description="对象键")
    reason: str = Field(..., description="失败原因")


class StorageDeleteOutSchema(BaseModel):
    """批量删除结果：成功与失败分开回显，部分失败不改整体状态码。"""

    deleted: list[str] = Field(default_factory=list, description="已删除的对象键")
    failed: list[StorageDeleteFailureSchema] = Field(default_factory=list, description="删除失败的键与原因")
