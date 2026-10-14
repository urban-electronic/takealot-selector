"""
图片代理端点 - 转发外部图片以绕过 CDN Referer 防盗链

安全与降载（review F2/F3）：
- 仅放行已确认的 HTTPS 图片 CDN 主机（media.takealot.com:443，解析器抓取的
  product_image_url 均在此域）；拒绝 URL 凭据、非标准端口、IP 字面量/内网地址，
  并对每一跳跳转目标重新校验，避免跳转脱管。
- 上游 403/404 负缓存（300s）：CDN 防盗链拒绝时不再每次重复出站请求。
- 同 URL 并发去重（单航班）+ 30s 正向微缓存：同屏重复图片只出站一次。
- 共享连接池复用（不再每请求新建 client），响应体积上限 10MB。
"""

import asyncio
import ipaddress
import os
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Query, Header, HTTPException
from fastapi.responses import Response
import httpx

router = APIRouter(prefix="/api", tags=["image-proxy"])

_API_KEY = os.environ.get("API_KEY", "").strip()

# 已确认的图片 CDN 主机白名单（HTTPS、443 标准端口）
ALLOWED_IMAGE_HOSTS = frozenset({"media.takealot.com"})
MAX_REDIRECTS = 3
NEGATIVE_TTL_SECONDS = 300.0   # 上游 403/404 负缓存时长
POSITIVE_TTL_SECONDS = 30.0    # 正向微缓存时长（承接同屏/滚动重复请求）
POSITIVE_MAX_ENTRIES = 256     # 正向缓存条目上限，超出整体清空防增长
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_CLIENT_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)

_UPSTREAM_HEADERS = {
    "Referer": "https://www.takealot.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


def _validate_image_url(url: str) -> str:
    """校验图片 URL：仅 HTTPS + 白名单主机 + 无凭据 + 标准端口 + 非 IP 字面量。

    返回规范化后的 URL（丢弃 fragment，保留 query——CDN 可能带签名参数）。
    任何不满足约束的输入直接 400，不会进入出站请求。
    """
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="图片 URL 不能为空")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="图片 URL 不合法")

    if parts.scheme.lower() != "https":
        raise HTTPException(status_code=400, detail="仅支持 HTTPS 图片地址")
    # netloc 中含 @ 即携带 user:pass@ 凭据，一律拒绝
    if "@" in (parts.netloc or ""):
        raise HTTPException(status_code=400, detail="图片 URL 不允许携带凭据")
    host = (parts.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="图片 URL 缺少主机名")
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal or host not in ALLOWED_IMAGE_HOSTS:
        raise HTTPException(status_code=400, detail="图片主机不在允许列表")
    try:
        port = parts.port
    except ValueError:
        raise HTTPException(status_code=400, detail="图片 URL 端口不合法")
    if port is not None and port != 443:
        raise HTTPException(status_code=400, detail="图片端口不合法")

    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def _retrieve_task_exception(task: "asyncio.Task") -> None:
    """标记共享任务异常已读取，避免 'exception was never retrieved' 告警。"""
    if not task.cancelled():
        task.exception()


class _ImageFetchService:
    """带白名单校验、负缓存与单航班去重的图片出站服务。

    单线程事件循环下 dict 读写均为原子操作，无需额外加锁；仅负缓存的
    清理涉及时间判断，同样是同步代码，保持无锁设计。
    """

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            timeout=15.0, limits=_CLIENT_LIMITS, follow_redirects=False
        )
        self._negative: dict[str, float] = {}   # url -> 负缓存到期时刻（monotonic）
        self._positive: dict[str, tuple[float, bytes, str]] = {}  # url -> (到期, 内容, 类型)
        self._inflight: dict[str, asyncio.Task] = {}

    # ---- 缓存 ----

    def _negatively_blocked(self, url: str) -> bool:
        until = self._negative.get(url)
        if until is None:
            return False
        if until <= time.monotonic():
            self._negative.pop(url, None)
            return False
        return True

    def _block_negative(self, url: str) -> None:
        if len(self._negative) > 4096:
            self._negative.clear()
        self._negative[url] = time.monotonic() + NEGATIVE_TTL_SECONDS

    def _positive_hit(self, url: str):
        cached = self._positive.get(url)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1], cached[2]
        return None

    def _remember_positive(self, url: str, content: bytes, content_type: str) -> None:
        if len(self._positive) >= POSITIVE_MAX_ENTRIES:
            self._positive.clear()
        self._positive[url] = (time.monotonic() + POSITIVE_TTL_SECONDS, content, content_type)

    # ---- 出站 ----

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """返回 (图片字节, content-type)；失败抛 HTTPException。"""
        if self._negatively_blocked(url):
            raise HTTPException(
                status_code=502, detail="图片 CDN 暂时拒绝访问（负缓存生效），请稍后重试"
            )
        hit = self._positive_hit(url)
        if hit is not None:
            return hit
        task = self._inflight.get(url)
        if task is None or task.done():
            task = asyncio.create_task(self._fetch_upstream(url))
            self._inflight[url] = task
            # 防止 leader 在 await 前被取消时异常无人读取的告警
            task.add_done_callback(_retrieve_task_exception)
        # 等待共享任务：leader 断开不会取消任务，后续请求仍可拿到结果
        return await task

    async def _fetch_upstream(self, url: str) -> tuple[bytes, str]:
        try:
            content, content_type = await self._fetch_with_redirects(url)
        finally:
            self._inflight.pop(url, None)
        self._remember_positive(url, content, content_type)
        return content, content_type

    async def _fetch_with_redirects(self, url: str) -> tuple[bytes, str]:
        current = url
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                resp = await self._client.get(current, headers=_UPSTREAM_HEADERS)
                try:
                    if resp.is_redirect:
                        location = resp.headers.get("location", "")
                        if not location:
                            raise HTTPException(
                                status_code=502, detail="上游图片跳转缺少 Location"
                            )
                        # 逐跳校验：跳转目标必须同样通过白名单
                        current = _validate_image_url(
                            str(httpx.URL(current).join(location))
                        )
                        continue
                    if resp.status_code != 200:
                        if resp.status_code in (403, 404):
                            self._block_negative(url)
                        raise HTTPException(
                            status_code=502, detail=f"上游图片返回 {resp.status_code}"
                        )
                    content_type = resp.headers.get("content-type", "image/jpeg")
                    if content_type.split(";")[0].strip().lower().startswith("text/html"):
                        raise HTTPException(
                            status_code=502, detail="上游返回 HTML 页面而非图片"
                        )
                    declared = resp.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                        raise HTTPException(status_code=502, detail="图片体积超限")
                    content = await resp.aread()
                    if len(content) > MAX_IMAGE_BYTES:
                        raise HTTPException(status_code=502, detail="图片体积超限")
                    return content, content_type
                finally:
                    await resp.aclose()
            raise HTTPException(
                status_code=502, detail=f"图片跳转超过 {MAX_REDIRECTS} 跳，已中止"
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="请求上游图片超时")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"图片代理失败: {type(e).__name__}")

    async def aclose(self) -> None:
        await self._client.aclose()


_service = _ImageFetchService()


async def aclose_service() -> None:
    """供应用 shutdown 调用，释放共享连接池。"""
    await _service.aclose()


@router.get("/image-proxy")
async def image_proxy(
    url: str = Query(..., description="原始图片 URL"),
    token: str = Query("", description="图片鉴权 token（<img> 无法带 header，走 query）"),
    x_api_key: str = Header("", alias="X-API-Key"),
):
    """代理转发图片，携带 Takealot Referer 头以绕过 CDN 防盗链。

    鉴权说明：/api/image-proxy 从全局中间件跳过，因为 <img> 标签无法携带
    X-API-Key 头；此处改用 query token（前端 getImageUrl 自动拼接）校验，
    同时兼容 X-API-Key 头（fetch 场景），避免鉴权被绕过。
    """
    if _API_KEY and token != _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="无效或缺失 API Key")
    safe_url = _validate_image_url(url)
    content, content_type = await _service.fetch(safe_url)
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
