"""S3 兼容对象存储客户端（当前对接自建的 MinIO）。

为什么用 S3 协议而不是 MinIO 私有 API：S3 是事实标准，日后换云厂商（OSS/COS/OSS/CEPH）
只改 endpoint 与凭据，调用方代码不动；MinIO 只是本地/私有化部署的默认实现。

约定：
1. 未启用或地址/桶名/凭据未配置时快速失败，报错点明该填哪个环境变量，不做静默降级；
2. boto3 是纯同步 SDK，所有网络调用一律丢到阻塞 IO 线程池（``run_in_io_pool``），不占事件循环；
3. 桶由 ``ensure_bucket()`` 幂等创建（S3_AUTO_CREATE），与建向量表/审计表的策略一致；
4. 对外只回对象键、字节与预签名链接，不泄露 boto3 原始响应结构。
"""

import logging
import mimetypes
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, NoReturn, TypeVar
from uuid import uuid4

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

from ..runtime import run_in_io_pool

T = TypeVar("T")

# MinIO 必须 path-style 寻址：virtual 风格会把桶名拼成子域名，内网没有对应 DNS 记录
_ADDRESSING_STYLES: tuple[str, ...] = ("path", "virtual")
# 桶/对象不存在：S3 回 404 + NoSuchBucket/NoSuchKey，MinIO 同口径
_NOT_FOUND_CODES: frozenset[str] = frozenset({"404", "NotFound", "NoSuchBucket", "NoSuchKey"})
# 403 说明桶存在但当前账号无权 head，不能当成"不存在"去建桶（否则掩盖真实权限问题）
_FORBIDDEN_CODES: frozenset[str] = frozenset({"403", "AccessDenied"})
# 并发启动时另一个实例已建好桶，不算失败
_BUCKET_EXISTS_CODES: frozenset[str] = frozenset({"BucketAlreadyExists", "BucketAlreadyOwnedByYou"})


def _error_code(exc: ClientError) -> str:
    """取 boto3 错误码，取不到就返回空串（后续按未知错误处理）。"""
    return str(exc.response.get("Error", {}).get("Code") or "")


class S3StorageClient:
    """S3/MinIO 对象存储客户端（默认取 settings 中的 S3_* 配置）。"""

    service_name = "对象存储"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        bucket: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        addressing_style: str | None = None,
        timeout: float | None = None,
        key_prefix: str | None = None,
    ) -> None:
        self.endpoint = (settings.S3_ENDPOINT if endpoint is None else endpoint).rstrip("/")
        self.bucket = settings.S3_BUCKET if bucket is None else bucket
        self.access_key = settings.S3_ACCESS_KEY if access_key is None else access_key
        self.secret_key = settings.S3_SECRET_KEY if secret_key is None else secret_key
        self.region = (settings.S3_REGION if region is None else region) or "us-east-1"
        self.addressing_style = (settings.S3_ADDRESSING_STYLE if addressing_style is None else addressing_style) or "path"
        self.timeout = settings.S3_TIMEOUT if timeout is None else timeout
        self.key_prefix = settings.S3_KEY_PREFIX if key_prefix is None else key_prefix
        self.use_ssl = settings.S3_USE_SSL
        # boto3 client 线程安全，惰性创建后由 IO 线程池共用
        self._client: Any = None

    @property
    def configured(self) -> bool:
        """是否已具备可用配置（false 时调用会抛 503 并提示补哪个变量）。"""
        return bool(settings.S3_ENABLE and self.endpoint and self.bucket)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _require_configured(self) -> None:
        if not settings.S3_ENABLE:
            raise CustomException(
                msg="对象存储未启用（S3_ENABLE=false），请在 backend/env/.env.<env> 中打开",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not self.endpoint:
            raise CustomException(
                msg="对象存储地址未配置，请先在 backend/env/.env.<env> 填写 S3_ENDPOINT",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not self.bucket:
            raise CustomException(
                msg="对象存储桶名未配置（S3_BUCKET）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not self.access_key or not self.secret_key:
            raise CustomException(
                msg="对象存储凭据未配置（S3_ACCESS_KEY / S3_SECRET_KEY）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def _get_client(self) -> Any:
        """惰性创建 boto3 客户端；签名固定 s3v4，超时与重试按配置收口。"""
        if self._client is None:
            if self.addressing_style not in _ADDRESSING_STYLES:
                raise CustomException(
                    msg=f"不支持的 S3_ADDRESSING_STYLE={self.addressing_style}，可选：{'/'.join(_ADDRESSING_STYLES)}",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            # botocore 在 DEBUG 级别会把每次请求的签名事件全打出来（几百行），
            # 开发环境会淹没业务日志；压到 WARNING，真实错误仍由 CustomException 抛给调用方
            for name in ("boto3", "botocore", "urllib3"):
                logging.getLogger(name).setLevel(logging.WARNING)
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                use_ssl=self.use_ssl,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": self.addressing_style},
                    connect_timeout=self.timeout,
                    read_timeout=self.timeout,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def _raise_storage_error(self, action: str, exc: Exception) -> NoReturn:
        """把 boto3 的两类异常归一成可读的 502，避免上层看到 botocore 结构。"""
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            message = f"{error.get('Code') or 'ClientError'}: {error.get('Message') or ''}".strip()
        else:
            message = f"{type(exc).__name__}: {exc}"
        logger.warning("对象存储{}失败（{} / {}）：{}", action, self.endpoint, self.bucket, message)
        raise CustomException(
            msg=f"对象存储{action}失败：{message}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    async def _call(self, action: str, func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """在线程池里执行同步调用并归一异常。"""
        try:
            return await run_in_io_pool(func, *args, **kwargs)
        except (ClientError, BotoCoreError) as exc:
            self._raise_storage_error(action, exc)

    # ── 桶 ────────────────────────────────────────────────────────────

    def _bucket_exists(self) -> bool:
        try:
            self._get_client().head_bucket(Bucket=self.bucket)
            return True
        except ClientError as exc:
            code = _error_code(exc)
            if code in _FORBIDDEN_CODES:
                return True
            if code in _NOT_FOUND_CODES:
                return False
            raise

    def _create_bucket(self) -> None:
        client = self._get_client()
        # us-east-1 是默认 region，不能带 LocationConstraint（否则 MinIO 报 InvalidLocationConstraint）
        if self.region and self.region != "us-east-1":
            client.create_bucket(Bucket=self.bucket, CreateBucketConfiguration={"LocationConstraint": self.region})
        else:
            client.create_bucket(Bucket=self.bucket)

    async def bucket_exists(self) -> bool:
        self._require_configured()
        return await self._call("查询桶", self._bucket_exists)

    async def ensure_bucket(self) -> bool:
        """幂等建桶，返回 True 表示本次新建。"""
        self._require_configured()
        if await self._call("查询桶", self._bucket_exists):
            return False
        try:
            await self._call("建桶", self._create_bucket)
        except CustomException:
            # 并发启动：别的实例已建好桶时 create 回 AlreadyExists，以 head 结果为准
            if await self._call("查询桶", self._bucket_exists):
                return False
            raise
        logger.info("==== 对象存储桶已就绪：{}（{}）====", self.bucket, self.endpoint)
        return True

    # ── 对象 ──────────────────────────────────────────────────────────

    def build_key(self, filename: str, *, prefix: str | None = None) -> str:
        """生成对象键 ``{前缀}/{YYYY-MM-DD}/{uuid}_{安全文件名}``，避免同名覆盖。"""
        safe_name = Path(filename or "object").name.replace(" ", "_") or "object"
        segments = [
            self.key_prefix if prefix is None else prefix,
            date.today().isoformat(),
            f"{uuid4().hex}_{safe_name}",
        ]
        return "/".join(segment.strip("/") for segment in segments if segment and segment.strip("/"))

    def _put(self, key: str, data: bytes, content_type: str, metadata: dict[str, str] | None) -> None:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": data}
        if content_type:
            params["ContentType"] = content_type
        if metadata:
            params["Metadata"] = {str(name): str(value) for name, value in metadata.items()}
        self._get_client().put_object(**params)

    def _put_file(self, key: str, path: Path, content_type: str) -> None:
        # 传文件对象让 boto3 分块读，避免整份文件进内存（PDF 动辄上百 MB）
        with path.open("rb") as handle:
            self._get_client().put_object(Bucket=self.bucket, Key=key, Body=handle, ContentType=content_type)

    async def put_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """写入字节对象，返回对象键。"""
        self._require_configured()
        if not key:
            raise CustomException(msg="对象键不能为空", status_code=status.HTTP_400_BAD_REQUEST)
        await self._call("上传对象", self._put, key, data, content_type, metadata)
        return key

    async def put_file(self, path: str | Path, *, key: str | None = None, content_type: str = "") -> str:
        """上传本地文件，返回对象键（不传 key 时按 ``build_key`` 自动生成）。"""
        self._require_configured()
        file_path = Path(path)
        if not file_path.is_file():
            raise CustomException(msg=f"待上传文件不存在：{file_path}", status_code=status.HTTP_400_BAD_REQUEST)
        object_key = key or self.build_key(file_path.name)
        mime = content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        await self._call("上传文件", self._put_file, object_key, file_path, mime)
        return object_key

    def _get(self, key: str) -> bytes:
        response = self._get_client().get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    async def get_object(self, key: str) -> bytes:
        """读取对象字节；对象不存在回 404，而不是笼统的 502。"""
        self._require_configured()
        try:
            return await run_in_io_pool(self._get, key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise CustomException(msg=f"对象不存在：{key}", status_code=status.HTTP_404_NOT_FOUND) from exc
            self._raise_storage_error("下载对象", exc)
        except BotoCoreError as exc:
            self._raise_storage_error("下载对象", exc)

    def _head_object(self, key: str) -> bool:
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise

    async def object_exists(self, key: str) -> bool:
        self._require_configured()
        return await self._call("查询对象", self._head_object, key)

    def _delete(self, key: str) -> None:
        self._get_client().delete_object(Bucket=self.bucket, Key=key)

    async def delete_object(self, key: str) -> None:
        """删除对象；S3 语义幂等，对象不存在也返回成功。"""
        self._require_configured()
        await self._call("删除对象", self._delete, key)

    def _list_keys(self, prefix: str) -> list[str]:
        """列举前缀下的全部对象键（自动翻页；单页上限 1000）。"""
        client = self._get_client()
        keys: list[str] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                params["ContinuationToken"] = token
            response = client.list_objects_v2(**params)
            keys.extend(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                return keys
            token = response.get("NextContinuationToken")

    async def list_keys(self, prefix: str) -> list[str]:
        """列出前缀下的对象键（产物图片清单/目录清理用）。"""
        self._require_configured()
        return await self._call("列举对象", self._list_keys, prefix)

    def _delete_keys(self, keys: list[str]) -> int:
        """批量删除（每批 1000）：S3 单次 delete_objects 上限就是 1000。

        个别 S3 兼容实现（实测自建 MinIO）对 DeleteObjects 强制要求 Content-MD5，而新版
        botocore 改发 CRC32 校验头，批量接口会回 MissingContentMD5；这里退化为逐个删除
        —— 删文档必须把产物清干净，不能因兼容性半途而废。
        """
        client = self._get_client()
        removed = 0
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            try:
                response = client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except (ClientError, BotoCoreError) as exc:
                if len(batch) == 1:
                    raise
                logger.warning("批量删除对象失败，改为逐个删除（{} 个）：{}", len(batch), exc)
                removed += sum(1 for key in batch if self._delete_one(key))
                continue
            removed += len(batch) - len(response.get("Errors", []))
        return removed

    def _delete_one(self, key: str) -> bool:
        """逐个删除：单键 delete_object 不带校验头，兼容性最好。"""
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=key)
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning("删除对象失败（{}）：{}", key, exc)
            return False

    async def delete_prefix(self, prefix: str) -> int:
        """删除前缀下的所有对象，返回删除条数（前缀为空时拒绝执行，避免误清全桶）。"""
        self._require_configured()
        if not prefix or not prefix.strip("/ "):
            raise CustomException(msg="删除前缀不能为空", status_code=status.HTTP_400_BAD_REQUEST)
        keys = await self._call("列举对象", self._list_keys, prefix)
        if not keys:
            return 0
        return await self._call("批量删除对象", self._delete_keys, keys)

    def presigned_url(self, key: str, *, expires_in: int | None = None) -> str:
        """生成下载预签名链接（纯本地签名，不发网络请求）。

        注意：链接的 host 就是 ``S3_ENDPOINT``。要让浏览器直接访问，endpoint 必须是
        浏览器可达的地址；容器内按容器名直连的地址（如 http://service-brain-minio:9000）
        仅容器内可用，外部访问会解析失败。
        """
        self._require_configured()
        seconds = max(1, expires_in or settings.S3_PRESIGN_EXPIRE)
        try:
            return self._get_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=seconds,
            )
        except BotoCoreError as exc:
            self._raise_storage_error("生成下载链接", exc)

    # ── 自检 ──────────────────────────────────────────────────────────

    async def self_check(self) -> dict[str, Any]:
        """真实读写探针：建桶（幂等）→ 写入 → 读回比对 → 删除。

        只写一个几十字节的临时对象且读完立即删除，既验证连通性与凭据权限，
        又顺带验证 path-style 寻址是否正确（virtual 风格在这一步就会失败）。
        """
        created = await self.ensure_bucket()
        object_key = f"_self_check/{uuid4().hex}.txt"
        payload = b"service-brain s3 self check"
        await self.put_object(object_key, payload, content_type="text/plain; charset=utf-8")
        try:
            read_back = await self.get_object(object_key)
        finally:
            await self.delete_object(object_key)
        return {
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "region": self.region,
            "addressing_style": self.addressing_style,
            "bucket_created": created,
            "object_key": object_key,
            "roundtrip_ok": read_back == payload,
        }
