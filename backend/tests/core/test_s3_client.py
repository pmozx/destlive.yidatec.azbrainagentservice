"""S3 客户端单元测试。

覆盖：
- 配置校验（未启用 / 缺凭据时快速失败）
- 桶管理（ensure_bucket 幂等创建）
- 上传下载（文件 / 字节 / 流）
- 预签名 URL 生成
- 对象存在性检查与删除
"""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.utils.s3_client import S3Client, S3ClientError, get_s3_client, reset_s3_client


# ==================== fixtures ====================


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个用例前后重置模块级单例，避免状态污染。"""
    reset_s3_client()
    yield
    reset_s3_client()


@pytest.fixture
def mock_settings():
    """模拟 S3 配置。"""
    with patch("app.utils.s3_client.settings") as s:
        s.S3_ENABLE = True
        s.S3_ENDPOINT = "https://obs.cn-north-4.myhuaweicloud.com"
        s.S3_ACCESS_KEY = "test-access-key"
        s.S3_SECRET_KEY = "test-secret-key"
        s.S3_BUCKET = "test-bucket"
        s.S3_REGION = ""
        s.S3_USE_SSL = True
        s.S3_ADDRESSING_STYLE = "virtual"
        s.S3_AUTO_CREATE = True
        s.S3_TIMEOUT = 30.0
        s.S3_PRESIGN_EXPIRE = 3600
        s.S3_KEY_PREFIX = "test/prefix"
        s.S3_PROVIDER = "huawei-obs"
        yield s


@pytest.fixture
def mock_boto_client():
    """模拟 boto3 客户端。"""
    with patch("app.utils.s3_client.boto3.client") as m:
        client = MagicMock()
        m.return_value = client
        yield client


# ==================== 配置校验 ====================


class TestConfigValidation:
    """配置校验测试。"""

    def test_s3_disabled_raises_error(self, mock_settings):
        """S3 未启用时应抛出异常。"""
        mock_settings.S3_ENABLE = False
        with pytest.raises(S3ClientError, match="对象存储未启用"):
            S3Client()

    def test_missing_endpoint_raises_error(self, mock_settings):
        """缺少 endpoint 时应抛出异常。"""
        mock_settings.S3_ENDPOINT = ""
        with pytest.raises(S3ClientError, match="对象存储地址未配置"):
            S3Client()

    def test_missing_credentials_raises_error(self, mock_settings):
        """缺少凭据时应抛出异常。"""
        mock_settings.S3_ACCESS_KEY = ""
        with pytest.raises(S3ClientError, match="对象存储凭据未配置"):
            S3Client()

    def test_missing_secret_key_raises_error(self, mock_settings):
        """缺少 secret key 时应抛出异常。"""
        mock_settings.S3_SECRET_KEY = ""
        with pytest.raises(S3ClientError, match="对象存储凭据未配置"):
            S3Client()


# ==================== 桶管理 ====================


class TestBucketManagement:
    """桶管理测试。"""

    def test_ensure_bucket_exists(self, mock_settings, mock_boto_client):
        """桶已存在时不创建。"""
        mock_boto_client.head_bucket.return_value = {}
        client = S3Client()
        client.ensure_bucket()
        mock_boto_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_boto_client.create_bucket.assert_not_called()

    def test_ensure_bucket_create_new(self, mock_settings, mock_boto_client):
        """桶不存在时自动创建。"""
        error_response = {"Error": {"Code": "NoSuchBucket"}}
        mock_boto_client.head_bucket.side_effect = ClientError(error_response, "HeadBucket")
        mock_boto_client.create_bucket.return_value = {}

        client = S3Client()
        client.ensure_bucket()

        mock_boto_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_ensure_bucket_auto_create_disabled(self, mock_settings, mock_boto_client):
        """桶不存在且未开启自动创建时应抛出异常。"""
        mock_settings.S3_AUTO_CREATE = False
        error_response = {"Error": {"Code": "NoSuchBucket"}}
        mock_boto_client.head_bucket.side_effect = ClientError(error_response, "HeadBucket")

        client = S3Client()
        with pytest.raises(S3ClientError, match="桶不存在且未开启自动创建"):
            client.ensure_bucket()


# ==================== 上传 ====================


class TestUpload:
    """上传测试。"""

    def test_upload_file(self, mock_settings, mock_boto_client, tmp_path: Path):
        """上传本地文件。"""
        # 准备测试文件
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        client = S3Client()
        result = client.upload_file(test_file, "docs/test.txt")

        assert result == "test/prefix/docs/test.txt"
        mock_boto_client.upload_file.assert_called_once()

    def test_upload_file_not_exists(self, mock_settings, mock_boto_client, tmp_path: Path):
        """上传不存在的文件应抛出异常。"""
        client = S3Client()
        with pytest.raises(S3ClientError, match="文件不存在"):
            client.upload_file(tmp_path / "not_exists.txt", "docs/test.txt")

    def test_upload_fileobj(self, mock_settings, mock_boto_client):
        """上传文件对象。"""
        data = b"test content"
        file_obj = io.BytesIO(data)

        client = S3Client()
        result = client.upload_fileobj(file_obj, "data/test.bin")

        assert result == "test/prefix/data/test.bin"
        mock_boto_client.upload_fileobj.assert_called_once()

    def test_upload_bytes(self, mock_settings, mock_boto_client):
        """上传字节数据。"""
        data = b"binary content"

        client = S3Client()
        result = client.upload_bytes(data, "data/binary.bin", content_type="application/octet-stream")

        assert result == "test/prefix/data/binary.bin"


# ==================== 下载 ====================


class TestDownload:
    """下载测试。"""

    def test_download_file(self, mock_settings, mock_boto_client, tmp_path: Path):
        """下载文件到本地。"""
        local_path = tmp_path / "downloaded.txt"
        mock_boto_client.download_file.return_value = None

        client = S3Client()
        result = client.download_file("docs/test.txt", local_path)

        assert result == local_path
        mock_boto_client.download_file.assert_called_once_with(
            Bucket="test-bucket",
            Key="test/prefix/docs/test.txt",
            Filename=str(local_path),
        )

    def test_download_bytes(self, mock_settings, mock_boto_client):
        """下载为字节。"""
        mock_body = MagicMock()
        mock_body.read.return_value = b"file content"
        mock_boto_client.get_object.return_value = {"Body": mock_body}

        client = S3Client()
        result = client.download_bytes("docs/test.txt")

        assert result == b"file content"


# ==================== 删除与检查 ====================


class TestDeleteAndExists:
    """删除与存在性检查测试。"""

    def test_delete_file(self, mock_settings, mock_boto_client):
        """删除对象。"""
        client = S3Client()
        client.delete_file("docs/test.txt")

        mock_boto_client.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="test/prefix/docs/test.txt",
        )

    def test_file_exists_true(self, mock_settings, mock_boto_client):
        """对象存在时返回 True。"""
        mock_boto_client.head_object.return_value = {}

        client = S3Client()
        assert client.file_exists("docs/test.txt") is True

    def test_file_exists_false(self, mock_settings, mock_boto_client):
        """对象不存在时返回 False。"""
        error_response = {"Error": {"Code": "404"}}
        mock_boto_client.head_object.side_effect = ClientError(error_response, "HeadObject")

        client = S3Client()
        assert client.file_exists("docs/test.txt") is False


# ==================== 预签名 ====================


class TestPresign:
    """预签名 URL 测试。"""

    def test_presign_get(self, mock_settings, mock_boto_client):
        """生成预签名下载 URL。"""
        mock_boto_client.generate_presigned_url.return_value = "https://presigned-url"

        client = S3Client()
        url = client.presign_get("docs/test.txt", expires_in=1800)

        assert url == "https://presigned-url"
        mock_boto_client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={"Bucket": "test-bucket", "Key": "test/prefix/docs/test.txt"},
            ExpiresIn=1800,
        )

    def test_presign_put(self, mock_settings, mock_boto_client):
        """生成预签名上传 URL。"""
        mock_boto_client.generate_presigned_url.return_value = "https://presigned-put-url"

        client = S3Client()
        url = client.presign_put("docs/upload.pdf", content_type="application/pdf")

        assert url == "https://presigned-put-url"


# ==================== 列表 ====================


class TestListObjects:
    """对象列表测试。"""

    def test_list_objects(self, mock_settings, mock_boto_client):
        """列出对象。"""
        from datetime import datetime

        mock_boto_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "test/prefix/docs/a.txt", "Size": 100, "LastModified": datetime.now()},
                {"Key": "test/prefix/docs/b.txt", "Size": 200, "LastModified": datetime.now()},
            ]
        }

        client = S3Client()
        result = client.list_objects("docs")

        assert len(result) == 2
        assert result[0]["key"] == "docs/a.txt"
        assert result[1]["key"] == "docs/b.txt"

    def test_list_objects_empty(self, mock_settings, mock_boto_client):
        """空列表。"""
        mock_boto_client.list_objects_v2.return_value = {}

        client = S3Client()
        result = client.list_objects()

        assert result == []


# ==================== 单例 ====================


class TestSingleton:
    """单例模式测试。"""

    def test_get_s3_client_singleton(self, mock_settings, mock_boto_client):
        """get_s3_client 返回单例。"""
        client1 = get_s3_client()
        client2 = get_s3_client()
        assert client1 is client2

    def test_reset_s3_client(self, mock_settings, mock_boto_client):
        """reset_s3_client 后获取新实例。"""
        client1 = get_s3_client()
        reset_s3_client()
        client2 = get_s3_client()
        assert client1 is not client2


# ==================== Provider 适配 ====================


class TestProviderAdaptation:
    """不同 provider 的适配测试。"""

    def test_minio_uses_path_style(self, mock_settings, mock_boto_client):
        """MinIO 使用 path-style 寻址。"""
        mock_settings.S3_PROVIDER = "minio"
        mock_settings.S3_ADDRESSING_STYLE = ""

        client = S3Client()
        assert client._provider == "minio"

    def test_huawei_obs_uses_virtual_style(self, mock_settings, mock_boto_client):
        """华为云 OBS 使用 virtual-hosted 寻址。"""
        mock_settings.S3_PROVIDER = "huawei-obs"
        mock_settings.S3_ADDRESSING_STYLE = "virtual"

        client = S3Client()
        assert client._provider == "huawei-obs"

    def test_get_object_url_minio(self, mock_settings, mock_boto_client):
        """MinIO 风格的对象 URL。"""
        mock_settings.S3_PROVIDER = "minio"
        mock_settings.S3_ADDRESSING_STYLE = "path"

        client = S3Client()
        url = client.get_object_url("docs/test.txt")

        assert "test-bucket" in url
        assert "test/prefix/docs/test.txt" in url

    def test_get_object_url_virtual(self, mock_settings, mock_boto_client):
        """Virtual-hosted 风格的对象 URL。"""
        mock_settings.S3_PROVIDER = "huawei-obs"
        mock_settings.S3_ADDRESSING_STYLE = "virtual"

        client = S3Client()
        url = client.get_object_url("docs/test.txt")

        assert "test-bucket." in url
