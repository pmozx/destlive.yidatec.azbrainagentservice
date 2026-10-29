"""MinerU 解析客户端（mineru-open-sdk 门面）：提交 → 轮询 → 取 Markdown 与版面块。

与基座 ``fastapi_admin_backend/app/core/mineru_client.py`` 的差异及原因：
1. 那边对外是 async、内部 ``asyncio.to_thread``——它的解析 Worker 直接跑在事件循环里；
   本工程已有独立解析线程池（``integration/runtime``），解析本来就在工作线程执行，
   再包一层协程只会多一次线程跳转，故这里保持同步，由 ``run_in_parse_pool`` 负责隔离；
2. 那边串了 MinIO 组合解析（桶内拉取 → 临时文件 → 解析）；本工程暂无对象存储，
   入参就是本地路径或已读字节，临时文件中转交给解析器处理（MinerU 只认路径/URL）；
3. token 缺省时 SDK 进入 flash-only 模式：这里显式降级为 ``flash_extract``（免 token、
   ≤10MB 且 ≤20 页、仅 Markdown），而不是让调用方吃到 NoAuthClientError。

SDK 异常（MinerUError 及子类）统一折算成 502 可读报错，保留异常类型名与原始信息——
轮询超时、额度超限、文件超限的处置动作完全不同，不能都糊成"调用失败"。
"""

import importlib.util
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger


@dataclass(frozen=True)
class MineruImage:
    """解析产物中的内嵌图片（Markdown 以 ``images/{name}`` 引用）。"""

    name: str
    data: bytes


@dataclass(frozen=True)
class MineruDocument:
    """MinerU 解析产物：主文本是 Markdown，content_list 保留版面块以便回填页码。

    ``images`` 仅在精准解析（有 token）时非空：flash 模式只回 Markdown，既没有版面块
    也没有图片，这一点由 ``mode`` 字段标明，上层据此决定能不能落 json/images 产物。
    """

    markdown: str
    content_list: list[dict[str, Any]]
    images: tuple[MineruImage, ...]
    page_count: int
    filename: str
    task_id: str
    mode: str
    model: str
    elapsed_ms: int


def mineru_sdk_available() -> bool:
    """SDK 是否可用（只查包是否安装，不发网络请求）。"""
    return importlib.util.find_spec("mineru") is not None


def _load_sdk() -> Any:
    """惰性导入 SDK：未安装时给出安装指引，而不是让整个模块 import 失败。"""
    try:
        import mineru
    except ImportError as exc:
        raise CustomException(
            msg="MinerU 解析不可用：未安装 mineru-open-sdk（在 backend 目录执行 uv add mineru-open-sdk）",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    return mineru


class MineruClient:
    """MinerU 客户端；一次解析一个实例，用完即 close（SDK 持有 HTTP 连接池）。"""

    service_name = "MinerU 解析服务"

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        ocr: bool | None = None,
        language: str | None = None,
    ) -> None:
        self.token = (token if token is not None else settings.MINERU_API_TOKEN or "").strip()
        self.base_url = base_url if base_url is not None else settings.MINERU_BASE_URL
        self.model = model or settings.MINERU_MODEL
        self.timeout = int(timeout or settings.MINERU_TIMEOUT)
        self.ocr = settings.MINERU_OCR if ocr is None else ocr
        self.language = language or settings.MINERU_LANGUAGE

    @property
    def mode(self) -> str:
        """precise=有 token 走精准解析；flash=无 token 的免鉴权轻量解析。"""
        return "precise" if self.token else "flash"

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _new_client(self) -> tuple[Any, Any]:
        sdk = _load_sdk()
        kwargs: dict[str, Any] = {}
        # 空值不显式传入，交回 SDK 内置默认地址（私有化部署时再用配置覆盖）
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if settings.MINERU_FLASH_BASE_URL:
            kwargs["flash_base_url"] = settings.MINERU_FLASH_BASE_URL
        return sdk.MinerU(self.token or None, **kwargs), sdk

    def _to_document(self, result: Any, *, filename: str, elapsed_ms: int) -> MineruDocument:
        state = str(getattr(result, "state", "") or "unknown")
        if state != "done":
            detail = f"{getattr(result, 'err_code', '')} {getattr(result, 'error', '')}".strip()
            raise CustomException(
                msg=f"{self.service_name} 解析未完成：state={state}，{detail or '无详细原因'}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        markdown = (getattr(result, "markdown", None) or "").strip()
        if not markdown:
            raise CustomException(
                msg=f"{self.service_name} 解析结果为空：{filename}（可能是扫描件未开 OCR 或文件损坏）",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        content_list = [item for item in (getattr(result, "content_list", None) or []) if isinstance(item, dict)]
        images = tuple(
            MineruImage(name=image.name, data=image.data)
            for image in (getattr(result, "images", None) or [])
            if getattr(image, "name", "") and getattr(image, "data", None)
        )
        page_count = max((int(item.get("page_idx", 0)) + 1 for item in content_list), default=0)
        if not page_count:
            page_count = int(getattr(getattr(result, "progress", None), "total_pages", 0) or 0)

        return MineruDocument(
            markdown=markdown,
            content_list=content_list,
            images=images,
            page_count=page_count,
            filename=filename,
            task_id=str(getattr(result, "task_id", "") or ""),
            mode=self.mode,
            model=self.model if self.mode == "precise" else "flash",
            elapsed_ms=elapsed_ms,
        )

    # ── 对外能力 ──────────────────────────────────────────────────────

    def extract(self, source: str | Path) -> MineruDocument:
        """同步解析单个文件/URL（分钟级阻塞调用，须在解析线程池内执行）。"""
        if not settings.MINERU_ENABLE:
            raise CustomException(
                msg="MinerU 解析已关闭（MINERU_ENABLE=false），无法解析二进制文档",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        target = str(source)
        is_url = target.startswith(("http://", "https://"))
        if not is_url and not Path(target).is_file():
            raise CustomException(
                msg=f"{self.service_name} 无法解析：文件不存在 {target}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        client, sdk = self._new_client()
        started = time.perf_counter()
        try:
            if self.mode == "flash":
                # flash 不认 model/extra_formats：传了会被服务端拒绝
                result = client.flash_extract(
                    target,
                    language=self.language,
                    is_ocr=self.ocr,
                    timeout=self.timeout,
                )
            else:
                result = client.extract(
                    target,
                    model=self.model,
                    ocr=self.ocr,
                    language=self.language,
                    timeout=self.timeout,
                )
        except CustomException:
            raise
        except sdk.MinerUError as exc:
            hint = "；未配置 MINERU_API_TOKEN 时走 flash 模式（≤10MB 且 ≤20 页），大文件请配置 token" if self.mode == "flash" else ""
            raise CustomException(
                msg=f"{self.service_name} 解析失败（{type(exc).__name__}）：{exc}{hint}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        except Exception as exc:  # 网络层/序列化异常：同样折算为可读 502，不外泄堆栈给调用方
            raise CustomException(
                msg=f"{self.service_name} 调用异常（{type(exc).__name__}）：{exc}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        finally:
            try:
                client.close()
            except Exception:
                logger.warning("MinerU 客户端关闭失败（不影响本次解析结果）", exc_info=True)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        document = self._to_document(result, filename=Path(target).name, elapsed_ms=elapsed_ms)
        logger.info(
            "==== MinerU 解析完成（{}）：{} 页，{} 字，{} 图，模式 {}，耗时 {}ms ====",
            document.filename,
            document.page_count,
            len(document.markdown),
            len(document.images),
            document.mode,
            elapsed_ms,
        )
        return document
