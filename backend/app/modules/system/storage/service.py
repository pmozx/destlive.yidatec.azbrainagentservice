"""文件存储浏览服务：只读编排 core 层 S3 客户端（无数据表，不注入 db session）。

三条实现约束：

1. **阻塞操作放线程**：boto3 是同步实现，列目录 / 下载 / head 统一用 ``asyncio.to_thread``；
   预签名只做本地签名计算，不发网络请求，可直接在事件循环里调用。
2. **只读**：不提供删除。桶内对象被库里的 ``object_key``（如解析页图）引用着，
   在运维页删对象会把这些引用变成死链；过期清理交给桶的生命周期策略。
3. **代理读取有大小闸门**：``/storage/content`` 把对象读进内存再回给浏览器，
   超过 ``MAX_PROXY_BYTES`` 直接拒绝并提示改用预签名链接，避免一屏预览把进程内存打满。
"""

import asyncio
import mimetypes

from app.config.setting import settings
from app.core.clients.s3 import S3Client, S3ClientError, get_s3_client
from app.core.exceptions import CustomException

from .schema import (
    StorageBrowseOutSchema,
    StorageConfigOutSchema,
    StorageFileOutSchema,
    StorageFolderOutSchema,
    StoragePresignOutSchema,
)

# 单页上限：S3 单次最多 1000 键，页面默认 100 已足够看清一层目录
MAX_PAGE_SIZE = 200
# 代理读取上限：超过后浏览器应走预签名链接直连对象存储
MAX_PROXY_BYTES = 30 * 1024 * 1024
# 未启用/未配置时的统一提示（页面的配置卡据此显示降级状态）
DISABLED_MSG = "对象存储未启用或未配置完整：请检查 S3_ENABLE / S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY"


def normalize_dir(prefix: str | None) -> str:
    """目录前缀归一：去掉首尾斜杠后统一补尾斜杠，避免 ``docs`` 命中 ``docs2/``。"""
    cleaned = (prefix or "").strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def normalize_key(key: str | None) -> str:
    """对象键校验：非空、去掉前导斜杠、拒绝 ``.`` / ``..`` 段。"""
    cleaned = (key or "").strip().lstrip("/")
    if not cleaned:
        raise CustomException(msg="请指定对象键")
    if any(part in ("", ".", "..") for part in cleaned.split("/")):
        raise CustomException(msg="对象键不合法")
    return cleaned


class StorageService:
    """对象存储只读浏览"""

    def _client(self) -> S3Client:
        """取客户端单例；未启用/缺凭据时转 503（配置问题不是 500）。"""
        if not settings.S3_ENABLE:
            raise CustomException(msg=DISABLED_MSG, status_code=503)
        try:
            return get_s3_client()
        except S3ClientError as exc:
            raise CustomException(msg=str(exc), status_code=503) from exc

    def config(self) -> StorageConfigOutSchema:
        """配置回显：纯 settings 投影，不发网络请求（页面首屏不该被远端拖慢）。"""
        return StorageConfigOutSchema(
            enabled=settings.S3_ENABLE,
            provider=settings.S3_PROVIDER,
            endpoint=settings.S3_ENDPOINT,
            bucket=settings.S3_BUCKET,
            key_prefix=settings.S3_KEY_PREFIX.strip("/"),
            region=settings.S3_REGION,
            addressing_style=settings.S3_ADDRESSING_STYLE,
            use_ssl=settings.S3_USE_SSL,
            auto_create=settings.S3_AUTO_CREATE,
            presign_expire=settings.S3_PRESIGN_EXPIRE,
        )

    async def browse(
        self,
        prefix: str = "",
        keyword: str | None = None,
        page_size: int = 100,
        continuation_token: str | None = None,
    ) -> StorageBrowseOutSchema:
        """按目录列文件夹与文件；``keyword`` 拼到目录前缀后做前缀匹配（同层过滤）。"""
        client = self._client()
        dir_prefix = normalize_dir(prefix)
        kw = (keyword or "").strip().lstrip("/")
        s3_prefix = f"{dir_prefix}{kw}" if kw else dir_prefix

        try:
            result = await asyncio.to_thread(
                client.list_dir, s3_prefix, "/", min(page_size, MAX_PAGE_SIZE), continuation_token
            )
        except S3ClientError as exc:
            raise CustomException(msg=f"浏览对象存储失败：{exc}", status_code=503) from exc

        return StorageBrowseOutSchema(
            prefix=dir_prefix,
            keyword=kw or None,
            folders=[StorageFolderOutSchema(**row) for row in result["folders"]],
            files=[
                StorageFileOutSchema(
                    key=row["key"],
                    name=row["name"],
                    size=row["size"],
                    last_modified=row["last_modified"],
                    content_type=mimetypes.guess_type(row["name"])[0],
                )
                for row in result["files"]
            ],
            next_token=result["next_token"],
            is_truncated=result["is_truncated"],
        )

    def presign(self, key: str) -> StoragePresignOutSchema:
        """签发预签名 GET 链接：只做本地签名，不发网络请求。"""
        object_key = normalize_key(key)
        client = self._client()
        try:
            url = client.presign_get(object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"生成下载链接失败：{exc}", status_code=503) from exc
        return StoragePresignOutSchema(key=object_key, url=url, expires_in=settings.S3_PRESIGN_EXPIRE)

    async def read(self, key: str) -> tuple[bytes, str, str]:
        """代理读取对象内容，返回 ``(内容, MIME, 文件名)``；超限或不存在按业务异常返回。"""
        object_key = normalize_key(key)
        client = self._client()

        try:
            stat = await asyncio.to_thread(client.stat_object, object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"读取对象失败：{exc}", status_code=404) from exc

        if stat["size"] > MAX_PROXY_BYTES:
            raise CustomException(
                msg=f"对象 {stat['size'] / 1024 / 1024:.1f} MB 超过代理读取上限，请改用预签名链接下载",
                status_code=413,
            )

        try:
            data = await asyncio.to_thread(client.download_bytes, object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"读取对象失败：{exc}", status_code=404) from exc

        media_type = stat["content_type"] or mimetypes.guess_type(object_key)[0] or "application/octet-stream"
        return data, media_type, object_key.rsplit("/", 1)[-1]
