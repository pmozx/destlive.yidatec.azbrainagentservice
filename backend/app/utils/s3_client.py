"""S3 兼容对象存储客户端（支持 MinIO / 华为云 OBS / 阿里云 OSS / AWS S3）。

统一封装上传、下载、预签名、桶管理等能力；通过 S3_PROVIDER 适配不同厂商差异。
boto3 客户端线程安全，模块级单例即可。
"""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from loguru import logger

from app.config.setting import settings


class S3ClientError(Exception):
    """S3 客户端业务异常（统一包装 boto3 ClientError，便于上层捕获）。"""


class S3Client:
    """S3 兼容对象存储客户端。

    支持：
    - MinIO（自建，path-style）
    - 华为云 OBS（virtual-hosted-style）
    - 阿里云 OSS（virtual-hosted-style）
    - AWS S3（virtual-hosted-style，按 region 路由）

    用法：
        client = S3Client()
        client.ensure_bucket()
        url = client.upload_fileobj(file_obj, "docs/test.pdf")
        presigned = client.presign_get("docs/test.pdf")
    """

    def __init__(self) -> None:
        if not settings.S3_ENABLE:
            raise S3ClientError("对象存储未启用：请将 S3_ENABLE 设为 true")
        if not settings.S3_ENDPOINT:
            raise S3ClientError("对象存储地址未配置：请设置 S3_ENDPOINT")
        if not settings.S3_ACCESS_KEY or not settings.S3_SECRET_KEY:
            raise S3ClientError("对象存储凭据未配置：请设置 S3_ACCESS_KEY 和 S3_SECRET_KEY")

        self._provider = settings.S3_PROVIDER.lower()
        self._bucket = settings.S3_BUCKET
        self._key_prefix = settings.S3_KEY_PREFIX.strip("/")
        self._presign_expire = settings.S3_PRESIGN_EXPIRE

        # 根据 provider 选择寻址风格
        addressing_style = self._resolve_addressing_style()

        boto_config = BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=settings.S3_TIMEOUT,
            read_timeout=settings.S3_TIMEOUT,
            s3={"addressing_style": addressing_style},
            signature_version="s3v4",
        )

        self._client = boto3.client(
            "s3",
            endpoint_url=self._build_endpoint_url(),
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION or None,
            use_ssl=settings.S3_USE_SSL,
            config=boto_config,
        )
        logger.info(
            "S3 客户端初始化完成: provider={}, endpoint={}, bucket={}, addressing={}",
            self._provider,
            settings.S3_ENDPOINT,
            self._bucket,
            addressing_style,
        )

    def _resolve_addressing_style(self) -> str:
        """根据 provider 决定寻址风格。"""
        # 显式配置优先
        if settings.S3_ADDRESSING_STYLE:
            return settings.S3_ADDRESSING_STYLE
        # 各厂商默认
        if self._provider in ("minio",):
            return "path"
        return "virtual"

    def _build_endpoint_url(self) -> str:
        """构建 endpoint URL，确保格式正确。"""
        endpoint = settings.S3_ENDPOINT.rstrip("/")
        # 如果没带协议头，根据 USE_SSL 补全
        if not endpoint.startswith(("http://", "https://")):
            scheme = "https" if settings.S3_USE_SSL else "http"
            endpoint = f"{scheme}://{endpoint}"
        return endpoint

    def _full_key(self, object_key: str) -> str:
        """拼接完整对象键（含前缀）。"""
        key = object_key.lstrip("/")
        if self._key_prefix:
            return f"{self._key_prefix}/{key}"
        return key

    def ensure_bucket(self) -> None:
        """确保桶存在，不存在则创建（幂等）。"""
        try:
            self._client.head_bucket(Bucket=self._bucket)
            logger.debug("桶已存在: {}", self._bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket", "NotFound"):
                if not settings.S3_AUTO_CREATE:
                    raise S3ClientError(f"桶不存在且未开启自动创建: {self._bucket}") from e
                self._create_bucket()
            else:
                raise S3ClientError(f"检查桶状态失败: {e}") from e

    def _create_bucket(self) -> None:
        """创建桶。"""
        try:
            create_kwargs = {"Bucket": self._bucket}
            # AWS S3 除 us-east-1 外需要指定 LocationConstraint
            if self._provider == "aws-s3" and settings.S3_REGION and settings.S3_REGION != "us-east-1":
                create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": settings.S3_REGION}
            self._client.create_bucket(**create_kwargs)
            logger.info("桶创建成功: {}", self._bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                logger.debug("桶已存在（并发创建）: {}", self._bucket)
            else:
                raise S3ClientError(f"创建桶失败: {e}") from e

    def upload_file(
        self,
        file_path: str | Path,
        object_key: str,
        content_type: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """上传本地文件。

        Args:
            file_path: 本地文件路径
            object_key: 对象键（不含前缀，_full_key 会自动拼接）
            content_type: MIME 类型，留空则按扩展名推断
            metadata: 自定义元数据

        Returns:
            上传后的对象键（含前缀）
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise S3ClientError(f"文件不存在: {file_path}")

        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(file_path))
            content_type = content_type or "application/octet-stream"

        full_key = self._full_key(object_key)
        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata

        try:
            self._client.upload_file(
                Filename=str(file_path),
                Bucket=self._bucket,
                Key=full_key,
                ExtraArgs=extra_args,
            )
            logger.info("文件上传成功: {} -> {}", file_path.name, full_key)
            return full_key
        except ClientError as e:
            raise S3ClientError(f"上传文件失败: {e}") from e

    def upload_fileobj(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """上传文件对象（内存流/文件句柄）。

        Args:
            file_obj: 可读的二进制流
            object_key: 对象键（不含前缀）
            content_type: MIME 类型
            metadata: 自定义元数据

        Returns:
            上传后的对象键（含前缀）
        """
        if content_type is None:
            content_type = "application/octet-stream"

        full_key = self._full_key(object_key)
        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata

        try:
            self._client.upload_fileobj(
                Fileobj=file_obj,
                Bucket=self._bucket,
                Key=full_key,
                ExtraArgs=extra_args,
            )
            logger.info("流上传成功: {}", full_key)
            return full_key
        except ClientError as e:
            raise S3ClientError(f"上传流失败: {e}") from e

    def upload_bytes(
        self,
        data: bytes,
        object_key: str,
        content_type: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """上传字节数据。

        Args:
            data: 字节内容
            object_key: 对象键（不含前缀）
            content_type: MIME 类型
            metadata: 自定义元数据

        Returns:
            上传后的对象键（含前缀）
        """
        with io.BytesIO(data) as buf:
            return self.upload_fileobj(buf, object_key, content_type, metadata)

    def download_file(self, object_key: str, local_path: str | Path) -> Path:
        """下载文件到本地。

        Args:
            object_key: 对象键（不含前缀）
            local_path: 本地保存路径

        Returns:
            本地文件路径
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        full_key = self._full_key(object_key)
        try:
            self._client.download_file(
                Bucket=self._bucket,
                Key=full_key,
                Filename=str(local_path),
            )
            logger.info("文件下载成功: {} -> {}", full_key, local_path)
            return local_path
        except ClientError as e:
            raise S3ClientError(f"下载文件失败: {e}") from e

    def download_bytes(self, object_key: str) -> bytes:
        """下载对象为字节。

        Args:
            object_key: 对象键（不含前缀）

        Returns:
            对象内容字节
        """
        full_key = self._full_key(object_key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=full_key)
            data = response["Body"].read()
            logger.debug("字节下载成功: {} ({} bytes)", full_key, len(data))
            return data
        except ClientError as e:
            raise S3ClientError(f"下载字节失败: {e}") from e

    def delete_file(self, object_key: str) -> None:
        """删除对象。

        Args:
            object_key: 对象键（不含前缀）
        """
        full_key = self._full_key(object_key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=full_key)
            logger.info("对象删除成功: {}", full_key)
        except ClientError as e:
            raise S3ClientError(f"删除对象失败: {e}") from e

    def file_exists(self, object_key: str) -> bool:
        """检查对象是否存在。

        Args:
            object_key: 对象键（不含前缀）

        Returns:
            是否存在
        """
        full_key = self._full_key(object_key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=full_key)
            return True
        except ClientError:
            return False

    def presign_get(self, object_key: str, expires_in: int | None = None) -> str:
        """生成预签名下载 URL。

        Args:
            object_key: 对象键（不含前缀）
            expires_in: 有效期（秒），留空使用配置默认值

        Returns:
            预签名 URL
        """
        full_key = self._full_key(object_key)
        expire = expires_in or self._presign_expire
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": full_key},
                ExpiresIn=expire,
            )
            logger.debug("预签名 URL 生成成功: {} ({}s)", full_key, expire)
            return url
        except ClientError as e:
            raise S3ClientError(f"生成预签名 URL 失败: {e}") from e

    def presign_put(
        self,
        object_key: str,
        content_type: str = "application/octet-stream",
        expires_in: int | None = None,
    ) -> str:
        """生成预签名上传 URL（PUT）。

        Args:
            object_key: 对象键（不含前缀）
            content_type: 要求的 Content-Type
            expires_in: 有效期（秒）

        Returns:
            预签名 PUT URL
        """
        full_key = self._full_key(object_key)
        expire = expires_in or self._presign_expire
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": full_key,
                    "ContentType": content_type,
                },
                ExpiresIn=expire,
            )
            logger.debug("预签名 PUT URL 生成成功: {} ({}s)", full_key, expire)
            return url
        except ClientError as e:
            raise S3ClientError(f"生成预签名 PUT URL 失败: {e}") from e

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        """列出对象。

        Args:
            prefix: 键前缀过滤（不含全局前缀）
            max_keys: 最大返回数

        Returns:
            对象列表 [{"Key": ..., "Size": ..., "LastModified": ...}, ...]
        """
        full_prefix = self._full_key(prefix) if prefix else (f"{self._key_prefix}/" if self._key_prefix else "")
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=full_prefix,
                MaxKeys=max_keys,
            )
            objects = response.get("Contents", [])
            # 返回时去掉全局前缀，保持与调用方传入的 key 口径一致
            result = []
            for obj in objects:
                key = obj["Key"]
                if self._key_prefix and key.startswith(f"{self._key_prefix}/"):
                    key = key[len(self._key_prefix) + 1 :]
                result.append(
                    {
                        "key": key,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                )
            return result
        except ClientError as e:
            raise S3ClientError(f"列出对象失败: {e}") from e

    def get_object_url(self, object_key: str) -> str:
        """获取对象的直接访问 URL（非预签名，需桶策略允许公开读）。

        Args:
            object_key: 对象键（不含前缀）

        Returns:
            对象访问 URL
        """
        full_key = self._full_key(object_key)
        endpoint = self._build_endpoint_url()
        if settings.S3_ADDRESSING_STYLE == "path" or self._provider == "minio":
            return f"{endpoint}/{self._bucket}/{quote(full_key)}"
        # virtual-hosted style
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        return f"{parsed.scheme}://{self._bucket}.{parsed.netloc}/{quote(full_key)}"


# 模块级便捷函数（延迟初始化，避免导入时就连接）

_client_instance: S3Client | None = None


def get_s3_client() -> S3Client:
    """获取 S3 客户端单例。"""
    global _client_instance
    if _client_instance is None:
        _client_instance = S3Client()
    return _client_instance


def reset_s3_client() -> None:
    """重置客户端实例（测试或配置变更后使用）。"""
    global _client_instance
    _client_instance = None
