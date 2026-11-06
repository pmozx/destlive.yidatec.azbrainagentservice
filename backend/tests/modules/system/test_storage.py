"""文件存储（系统管理 → 文件存储）：路由登记、目录映射、越权键校验、上传/删除口径与降级口径。

页面不写库，因此这里用假 S3 客户端验证四件事：
1. 四个只读接口 + 上传 + 批量删除都挂在 /api/v1/system/storage 下；
2. 目录列举把 S3 的 ``CommonPrefixes`` / ``Contents`` 映射成（目录, 文件），并去掉全局前缀；
3. 键校验与代理读取上限能挡住 ``..`` 与超大对象，未启用时是 503 而不是 500；
4. 上传挡 413/409（同名不静默覆盖），删除只收文件键、目录键归入 failed。
"""

import io
from datetime import UTC, datetime

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient

from app import create_app
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.modules.system.storage import service as storage_service
from app.modules.system.storage.schema import StorageBrowseOutSchema, StorageConfigOutSchema
from app.modules.system.storage.service import (
    StorageService,
    normalize_dir,
    normalize_file_name,
    normalize_key,
)

STORAGE_PATHS: set[str] = {
    "/api/v1/system/storage/config",
    "/api/v1/system/storage/objects",
    "/api/v1/system/storage/presign",
    "/api/v1/system/storage/content",
    "/api/v1/system/storage/upload",
}


class FakeS3Client:
    """只实现本模块用到的方法，记录调用参数供断言。"""

    def __init__(
        self, *, list_dir_result=None, stat=None, data=b"", presign_url="https://s3.test/x", exists=False
    ) -> None:
        self._list_dir_result = list_dir_result or {"folders": [], "files": [], "next_token": None, "is_truncated": False}
        self._stat = stat or {"size": 0, "content_type": None, "last_modified": None, "etag": "", "metadata": {}}
        self._data = data
        self._presign_url = presign_url
        self._exists = exists
        self.list_dir_calls: list[tuple] = []
        self.presign_calls: list[str] = []
        self.uploaded: list[tuple] = []
        self.deleted: list[str] = []

    def list_dir(self, prefix="", delimiter="/", max_keys=100, continuation_token=None):
        self.list_dir_calls.append((prefix, delimiter, max_keys, continuation_token))
        return self._list_dir_result

    def stat_object(self, object_key):
        return self._stat

    def download_bytes(self, object_key):
        return self._data

    def presign_get(self, object_key, expires_in=None):
        self.presign_calls.append(object_key)
        return self._presign_url

    def file_exists(self, object_key):
        return self._exists

    def upload_fileobj(self, file_obj, object_key, content_type=None, metadata=None):
        self.uploaded.append((object_key, content_type, file_obj.read()))
        return object_key

    def delete_file(self, object_key):
        self.deleted.append(object_key)


@pytest.fixture
def fake_client(monkeypatch):
    """把服务里的客户端单例替换成假实现，避免用例联网。"""
    client = FakeS3Client(
        list_dir_result={
            "folders": [{"prefix": "parse-previews/job-1/", "name": "job-1"}],
            "files": [
                {
                    "key": "parse-previews/job-1/page-1.png",
                    "name": "page-1.png",
                    "size": 2048,
                    "last_modified": datetime(2026, 11, 4, 10, 0, tzinfo=UTC),
                }
            ],
            "next_token": "token-2",
            "is_truncated": True,
        }
    )
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)
    return client


def test_four_readonly_routes_are_registered() -> None:
    """配置、目录、预签名、代理内容四个只读接口与上传/删除接口都在 OpenAPI 中。"""
    paths = create_app().openapi()["paths"]
    assert STORAGE_PATHS <= set(paths)
    # /objects 同时承接列表与批量删除，方法要分开断言
    assert {"get", "delete"} <= set(paths["/api/v1/system/storage/objects"])
    assert "post" in paths["/api/v1/system/storage/upload"]


def test_normalize_dir_adds_trailing_slash() -> None:
    """目录前缀统一补尾斜杠，避免 docs 命中 docs2/。"""
    assert normalize_dir("") == ""
    assert normalize_dir("/parse-previews") == "parse-previews/"
    assert normalize_dir("parse-previews/") == "parse-previews/"


def test_normalize_key_rejects_traversal() -> None:
    """对象键拒绝空值与 .. 段（页面参数可控，不能拼出桶外语义）。"""
    with pytest.raises(CustomException):
        normalize_key("")
    with pytest.raises(CustomException):
        normalize_key("parse-previews/../secret.txt")
    assert normalize_key("/parse-previews/a.png") == "parse-previews/a.png"


async def test_browse_maps_folders_and_files(fake_client) -> None:
    """CommonPrefixes → 目录、Contents → 文件，并回传续传令牌。"""
    result = await StorageService().browse(prefix="parse-previews")

    assert isinstance(result, StorageBrowseOutSchema)
    assert result.prefix == "parse-previews/"
    assert [folder.name for folder in result.folders] == ["job-1"]
    assert [file.name for file in result.files] == ["page-1.png"]
    assert result.files[0].content_type == "image/png"
    assert result.next_token == "token-2"
    assert result.is_truncated is True
    # 目录浏览必须带尾斜杠下发，否则会跨到 parse-previews2/
    assert fake_client.list_dir_calls[0][0] == "parse-previews/"


async def test_browse_keyword_is_concatenated_without_trailing_slash(fake_client) -> None:
    """名称过滤拼在目录前缀后，但不补尾斜杠，否则 page-1 这种半截键永远匹配不到。"""
    await StorageService().browse(prefix="parse-previews/", keyword="page-1")
    assert fake_client.list_dir_calls[0][0] == "parse-previews/page-1"


async def test_browse_rejects_when_s3_disabled(monkeypatch) -> None:
    """未启用对象存储时按 503 返回（配置问题不应表现为 500）。"""
    monkeypatch.setattr(settings, "S3_ENABLE", False)
    with pytest.raises(CustomException) as excinfo:
        await StorageService().browse()
    assert excinfo.value.status_code == 503


def test_presign_returns_url(fake_client) -> None:
    """预签名链接由客户端签发，键去掉前导斜杠。"""
    result = StorageService().presign("/parse-previews/a.png")
    assert result.key == "parse-previews/a.png"
    assert result.url == "https://s3.test/x"
    assert fake_client.presign_calls == ["parse-previews/a.png"]


async def test_read_rejects_oversize_object(monkeypatch) -> None:
    """超过代理读取上限直接拒绝，提示改用预签名链接，不把大对象读进内存。"""
    client = FakeS3Client(stat={"size": 40 * 1024 * 1024, "content_type": "application/pdf"})
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)
    with pytest.raises(CustomException) as excinfo:
        await StorageService().read("docs/big.pdf")
    assert excinfo.value.status_code == 413


async def test_read_returns_bytes_and_media_type(monkeypatch) -> None:
    """小文件走代理读取：返回内容、MIME 与文件名。"""
    client = FakeS3Client(stat={"size": 4, "content_type": "image/png"}, data=b"png!")
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)
    content, media_type, filename = await StorageService().read("parse-previews/page-1.png")
    assert content == b"png!"
    assert media_type == "image/png"
    assert filename == "page-1.png"


async def test_config_endpoint_reflects_settings() -> None:
    """配置回显是 settings 的只读投影，且未启用时仍然 200（页面据此降级）。"""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/system/storage/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    data = payload["data"]
    assert isinstance(StorageConfigOutSchema(**data), StorageConfigOutSchema)
    assert data["enabled"] == settings.S3_ENABLE
    assert data["bucket"] == settings.S3_BUCKET
    assert data["key_prefix"] == settings.S3_KEY_PREFIX.strip("/")


async def test_objects_endpoint_reports_disabled(monkeypatch) -> None:
    """未启用时 /objects 走业务异常出口（503 + 统一错误体），不是未捕获异常。"""
    monkeypatch.setattr(settings, "S3_ENABLE", False)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/system/storage/objects")

    assert response.status_code == 503
    assert response.json()["success"] is False
    assert "对象存储未启用" in response.json()["msg"]


def _upload(filename: str, data: bytes) -> UploadFile:
    """构造一个内存上传流（Starlette 的 UploadFile 支持直接包 BytesIO）。"""
    return UploadFile(file=io.BytesIO(data), filename=filename)


def test_normalize_file_name_keeps_last_segment() -> None:
    """浏览器可能传完整路径，只保留末段；空名与 . / .. 拒绝。"""
    assert normalize_file_name("C:\\tmp\\report.pdf") == "report.pdf"
    assert normalize_file_name("docs/report.pdf") == "report.pdf"
    assert normalize_file_name("  a.txt  ") == "a.txt"
    with pytest.raises(CustomException):
        normalize_file_name("")
    with pytest.raises(CustomException):
        normalize_file_name("docs/..")


async def test_upload_joins_dir_prefix_and_infers_content_type(fake_client) -> None:
    """上传键 = 当前目录前缀 + 原文件名；MIME 按扩展名推断并随写入下发。"""
    result = await StorageService().upload(_upload("report.pdf", b"pdf!"), prefix="manuals/")

    assert result.key == "manuals/report.pdf"
    assert result.size == 4
    assert result.content_type == "application/pdf"
    assert result.overwritten is False
    assert fake_client.uploaded == [("manuals/report.pdf", "application/pdf", b"pdf!")]


async def test_upload_rejects_empty_file(fake_client) -> None:
    """空文件不写入：否则桶里会多出无法预览的 0 字节对象。"""
    with pytest.raises(CustomException) as excinfo:
        await StorageService().upload(_upload("empty.txt", b""))
    assert excinfo.value.status_code == 400
    assert fake_client.uploaded == []


async def test_upload_rejects_oversize_before_writing(monkeypatch) -> None:
    """超限按 413 拒绝，且不调用对象存储写入。"""
    client = FakeS3Client()
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)
    monkeypatch.setattr(settings, "STORAGE_UPLOAD_MAX_SIZE_MB", 0)

    with pytest.raises(CustomException) as excinfo:
        await StorageService().upload(_upload("big.bin", b"0123456789"))

    assert excinfo.value.status_code == 413
    assert client.uploaded == []


async def test_upload_conflicts_when_same_key_exists(monkeypatch) -> None:
    """同名且未要求覆盖时返回 409，data 里带上键，供页面弹确认后重传。"""
    client = FakeS3Client(exists=True)
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)

    with pytest.raises(CustomException) as excinfo:
        await StorageService().upload(_upload("report.pdf", b"pdf!"))

    assert excinfo.value.status_code == 409
    assert excinfo.value.data["key"] == "report.pdf"
    assert client.uploaded == []


async def test_upload_overwrites_when_requested(monkeypatch) -> None:
    """页面确认后带 overwrite=true 重传，结果标记 overwritten。"""
    client = FakeS3Client(exists=True)
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: client)

    result = await StorageService().upload(_upload("report.pdf", b"pdf!"), overwrite=True)

    assert result.overwritten is True
    assert client.uploaded[0][0] == "report.pdf"


async def test_delete_splits_file_dir_and_invalid_keys(fake_client) -> None:
    """目录键与非法键只进 failed，正常文件键进 deleted，不阻断其余删除。"""
    result = await StorageService().delete(["docs/a.pdf", "docs/", "docs/../secret.txt"])

    assert result.deleted == ["docs/a.pdf"]
    assert [item.key for item in result.failed] == ["docs/", "docs/../secret.txt"]
    assert "不支持删除目录" in result.failed[0].reason
    assert fake_client.deleted == ["docs/a.pdf"]


async def test_delete_rejects_too_many_keys(fake_client) -> None:
    """单次批量上限挡住「一键清空整层目录」的误操作。"""
    with pytest.raises(CustomException):
        await StorageService().delete([f"docs/{i}.txt" for i in range(101)])
    assert fake_client.deleted == []
