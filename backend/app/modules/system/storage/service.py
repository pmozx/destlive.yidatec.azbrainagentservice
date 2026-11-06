"""文件存储服务：浏览与维护编排 core 层 S3 客户端（无数据表，不注入 db session）。

四条实现约束：

1. **阻塞操作放线程**：boto3 是同步实现，列目录 / 上传 / 下载 / head / 删除统一用
   ``asyncio.to_thread``；预签名只做本地签名计算，不发网络请求，可直接在事件循环里调用。
2. **删除只针对文件**：桶内对象被库里的 ``object_key``（如解析页图）引用着，删掉会把这些
   引用变成死链，页面必须把风险写进确认框；S3 没有真正的目录，删前缀等于批量删文件，
   因此目录删除不开放。
3. **代理读取有大小闸门**：``/storage/content`` 把对象读进内存再回给浏览器，
   超过 ``MAX_PROXY_BYTES`` 直接拒绝并提示改用预签名链接，避免一屏预览把进程内存打满。
4. **上传有大小闸门与同名确认**：超过 ``STORAGE_UPLOAD_MAX_SIZE_MB`` 按 413 拒绝；同名默认
   409，页面确认后带 ``overwrite=true`` 重传，避免静默覆盖正在被引用的对象。
"""

import asyncio
import mimetypes
import os

from fastapi import UploadFile, status

from app.config.setting import settings
from app.core.clients.s3 import S3Client, S3ClientError, get_s3_client
from app.core.exceptions import CustomException

from .schema import (
    StorageBrowseOutSchema,
    StorageConfigOutSchema,
    StorageDeleteFailureSchema,
    StorageDeleteOutSchema,
    StorageFileOutSchema,
    StorageFolderOutSchema,
    StoragePresignOutSchema,
    StorageUploadOutSchema,
)

# 单页上限：S3 单次最多 1000 键，页面默认 100 已足够看清一层目录
MAX_PAGE_SIZE = 200
# 代理读取上限：超过后浏览器应走预签名链接直连对象存储
MAX_PROXY_BYTES = 30 * 1024 * 1024
# 单次批量删除上限：与页面勾选能力匹配，避免一个请求串行删掉整层目录
MAX_DELETE_KEYS = 100
# 文件名长度上限（S3 键上限 1024 字节，但文件名本身留出目录前缀余量）
MAX_FILE_NAME_BYTES = 255
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


def normalize_file_name(name: str | None) -> str:
    """上传文件名归一：浏览器可能给完整路径（甚至带反斜杠），只取末段。"""
    cleaned = (name or "").replace("\\", "/").strip().rsplit("/", 1)[-1]
    if not cleaned or cleaned in (".", ".."):
        raise CustomException(msg="上传文件缺少有效文件名")
    if len(cleaned.encode("utf-8")) > MAX_FILE_NAME_BYTES:
        raise CustomException(msg=f"文件名过长（上限 {MAX_FILE_NAME_BYTES} 字节）")
    return cleaned


def upload_limit_bytes() -> int:
    """当前生效的单文件上传上限（运行时读配置，便于测试与调参）。"""
    return settings.STORAGE_UPLOAD_MAX_SIZE_MB * 1024 * 1024


def _stream_size(file_obj) -> int:
    """测量上传流字节数并把指针复位到 0（boto3 的 upload_fileobj 从头读）。"""
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(0)
    return size


class StorageService:
    """对象存储浏览与维护"""

    def _client(self) -> S3Client:
        """取客户端单例；未启用/缺凭据时转 503（配置问题不是 500）。"""
        if not settings.S3_ENABLE:
            raise CustomException(msg=DISABLED_MSG, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            return get_s3_client()
        except S3ClientError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

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
            upload_max_size_mb=settings.STORAGE_UPLOAD_MAX_SIZE_MB,
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
            raise CustomException(msg=f"浏览对象存储失败：{exc}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

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
            raise CustomException(msg=f"生成下载链接失败：{exc}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
        return StoragePresignOutSchema(key=object_key, url=url, expires_in=settings.S3_PRESIGN_EXPIRE)

    async def upload(self, upload: UploadFile, *, prefix: str = "", overwrite: bool = False) -> StorageUploadOutSchema:
        """上传单个文件到指定目录；同名默认 409，``overwrite`` 为 true 时覆盖。

        大小先由上传流实测（不信客户端声明），超限直接 413，不进对象存储；
        同名判断放在写入前，页面拿 409 弹确认后带 ``overwrite=true`` 重传。
        """
        client = self._client()
        file_name = normalize_file_name(upload.filename)
        object_key = normalize_key(f"{normalize_dir(prefix)}{file_name}")

        size = await asyncio.to_thread(_stream_size, upload.file)
        if size <= 0:
            raise CustomException(msg="上传文件内容为空")
        limit = upload_limit_bytes()
        if size > limit:
            raise CustomException(
                msg=f"文件 {size / 1024 / 1024:.1f} MB 超过上传上限 {settings.STORAGE_UPLOAD_MAX_SIZE_MB} MB",
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )

        try:
            exists = await asyncio.to_thread(client.file_exists, object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"检查同名对象失败：{exc}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
        if exists and not overwrite:
            raise CustomException(
                msg=f"对象已存在：{object_key}",
                status_code=status.HTTP_409_CONFLICT,
                data={"key": object_key, "name": file_name, "size": size},
            )

        content_type = upload.content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        try:
            await asyncio.to_thread(client.upload_fileobj, upload.file, object_key, content_type)
        except S3ClientError as exc:
            raise CustomException(msg=f"上传对象失败：{exc}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

        return StorageUploadOutSchema(
            key=object_key,
            name=file_name,
            size=size,
            content_type=content_type,
            overwritten=exists,
        )

    async def delete(self, keys: list[str]) -> StorageDeleteOutSchema:
        """批量删除文件：单个键失败只计入 ``failed``，不阻断其余对象。

        目录键（以 ``/`` 结尾）直接拒绝：S3 没有目录对象，删前缀等于批量删文件，
        与页面「只删文件」的语义不一致。
        """
        client = self._client()
        if len(keys) > MAX_DELETE_KEYS:
            raise CustomException(msg=f"单次最多删除 {MAX_DELETE_KEYS} 个对象")

        deleted: list[str] = []
        failed: list[StorageDeleteFailureSchema] = []
        for raw in keys:
            if str(raw or "").strip().endswith("/"):
                failed.append(
                    StorageDeleteFailureSchema(key=str(raw), reason="不支持删除目录，请进入目录后逐个删除文件")
                )
                continue
            try:
                object_key = normalize_key(raw)
            except CustomException as exc:
                failed.append(StorageDeleteFailureSchema(key=str(raw), reason=exc.msg))
                continue
            try:
                await asyncio.to_thread(client.delete_file, object_key)
            except S3ClientError as exc:
                failed.append(StorageDeleteFailureSchema(key=object_key, reason=str(exc)))
                continue
            deleted.append(object_key)

        return StorageDeleteOutSchema(deleted=deleted, failed=failed)

    async def read(self, key: str) -> tuple[bytes, str, str]:
        """代理读取对象内容，返回 ``(内容, MIME, 文件名)``；超限或不存在按业务异常返回。"""
        object_key = normalize_key(key)
        client = self._client()

        try:
            stat = await asyncio.to_thread(client.stat_object, object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"读取对象失败：{exc}", status_code=status.HTTP_404_NOT_FOUND) from exc

        if stat["size"] > MAX_PROXY_BYTES:
            raise CustomException(
                msg=f"对象 {stat['size'] / 1024 / 1024:.1f} MB 超过代理读取上限，请改用预签名链接下载",
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )

        try:
            data = await asyncio.to_thread(client.download_bytes, object_key)
        except S3ClientError as exc:
            raise CustomException(msg=f"读取对象失败：{exc}", status_code=status.HTTP_404_NOT_FOUND) from exc

        media_type = stat["content_type"] or mimetypes.guess_type(object_key)[0] or "application/octet-stream"
        return data, media_type, object_key.rsplit("/", 1)[-1]
