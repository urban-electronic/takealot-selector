"""图片代理（review F2/F3）安全与降载测试。

全部通过 httpx.MockTransport 验证，不向内网/真实 CDN 发出任何请求：
- URL 白名单：仅 HTTPS + media.takealot.com + 无凭据 + 标准端口 + 拒绝 IP 字面量
- 跳转逐跳校验：跳向非允许主机在发出跳转请求前即被拒绝
- 上游 403/404 负缓存：第二次请求不再出站；到期后恢复出站
- 同 URL 并发去重：5 个并发请求只出站一次
- 30s 正向微缓存：重复请求命中缓存
- 体积上限 / HTML 错误页 / 超时 / 传输错误的映射
- 端点鉴权不被绕过（token / X-API-Key）
"""

import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import image_proxy

ALLOWED = "https://media.takealot.com/covers_images/abc123.jpg"
ALLOWED_B = "https://media.takealot.com/covers_images/def456.jpg"


def make_service(handler):
    """构造挂 MockTransport 的服务实例，返回 (service, calls)。"""
    calls = []

    def sync_handler(request):
        calls.append(str(request.url))
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(sync_handler), timeout=5.0)
    return image_proxy._ImageFetchService(client=client), calls


def image_bytes(body=b"imgbytes", content_type="image/jpeg", **extra):
    headers = {"content-type": content_type}
    headers.update(extra)
    return httpx.Response(200, content=body, headers=headers)


# ---------- URL 白名单 ----------

@pytest.mark.parametrize("url", [
    "https://media.takealot.com/covers_images/abc.jpg",
    "https://media.takealot.com:443/covers_images/abc.jpg?w=120&sig=xyz",
    "https://MEDIA.TAKEALOT.COM/covers_images/abc.jpg",
    "https://media.takealot.com",
])
def test_allowed_urls_pass_validation(url):
    assert image_proxy._validate_image_url(url).startswith("https://media.takealot.com/")


@pytest.mark.parametrize("url", [
    "http://media.takealot.com/covers_images/abc.jpg",          # 非 HTTPS
    "https://user:pass@media.takealot.com/abc.jpg",             # 携带凭据
    "https://media.takealot.com@evil.com/abc.jpg",              # 凭据样式的跳板主机
    "https://evil.com/covers_images/abc.jpg",                   # 非白名单主机
    "https://media.takealot.com.evil.com/abc.jpg",              # 伪装子域
    "https://media.takealot.com:8443/abc.jpg",                  # 非标准端口
    "https://127.0.0.1/abc.jpg",                                # 环回
    "https://10.0.0.5/abc.jpg",                                 # 内网
    "https://169.254.169.254/latest/meta-data",                 # 云元数据地址
    "https://[::1]/abc.jpg",                                    # IPv6 环回
    "ftp://media.takealot.com/abc.jpg",                         # 非 HTTP(S)
    "https:///abc.jpg",                                         # 缺少主机名
    "",                                                         # 空
])
def test_rejected_urls_never_reach_outbound(url):
    with pytest.raises(HTTPException) as exc:
        image_proxy._validate_image_url(url)
    assert exc.value.status_code == 400


# ---------- 正常出站 ----------

def test_allowed_image_is_fetched_with_referer_and_passthrough_type():
    seen = {}

    def handler(request):
        seen["referer"] = request.headers.get("referer")
        return image_bytes(b"webpbytes", content_type="image/webp")

    service, calls = make_service(handler)
    content, ctype = asyncio.run(service.fetch(ALLOWED))
    assert content == b"webpbytes" and ctype == "image/webp"
    assert seen["referer"] == "https://www.takealot.com/"
    assert calls == [ALLOWED]


def test_successful_response_is_micro_cached_for_repeat_calls():
    service, calls = make_service(lambda request: image_bytes())
    assert asyncio.run(service.fetch(ALLOWED))[0] == b"imgbytes"
    assert asyncio.run(service.fetch(ALLOWED))[0] == b"imgbytes"
    assert len(calls) == 1  # 30s 正向微缓存命中，未重复出站


# ---------- 负缓存 ----------

@pytest.mark.parametrize("status", [403, 404])
def test_upstream_rejection_is_negatively_cached(status):
    service, calls = make_service(lambda request: httpx.Response(status, content=b"no"))
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(service.fetch(ALLOWED))
        assert exc.value.status_code == 502
    assert len(calls) == 1  # 第二次命中负缓存，没有再次出站


def test_negative_cache_expiry_allows_retry():
    service, calls = make_service(lambda request: httpx.Response(403, content=b"no"))
    with pytest.raises(HTTPException):
        asyncio.run(service.fetch(ALLOWED))
    assert len(calls) == 1
    service._negative[ALLOWED] = time.monotonic() - 1  # 模拟到期
    with pytest.raises(HTTPException):
        asyncio.run(service.fetch(ALLOWED))
    assert len(calls) == 2  # 到期后恢复出站


def test_negative_cache_is_scoped_per_url():
    service, calls = make_service(lambda request: httpx.Response(403, content=b"no"))
    with pytest.raises(HTTPException):
        asyncio.run(service.fetch(ALLOWED))
    with pytest.raises(HTTPException):
        asyncio.run(service.fetch(ALLOWED_B))
    assert len(calls) == 2  # 另一个 URL 不受影响


# ---------- 并发去重 ----------

def test_concurrent_requests_for_same_url_issue_single_outbound():
    calls = []

    async def slow_handler(request):
        calls.append(str(request.url))
        await asyncio.sleep(0.02)  # 保证 leader 在飞行中，其余请求挂到同一任务
        return image_bytes()

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler), timeout=5.0)
    service = image_proxy._ImageFetchService(client=client)

    async def scenario():
        return await asyncio.gather(*[service.fetch(ALLOWED) for _ in range(5)])

    results = asyncio.run(scenario())
    assert all(content == b"imgbytes" for content, _ in results)
    assert len(calls) == 1  # 5 个并发请求只出站一次
    assert service._positive  # 结果已进入正向缓存


# ---------- 跳转 ----------

def test_redirect_to_allowed_host_is_followed():
    def handler(request):
        if request.url.path == "/covers_images/abc123.jpg":
            return httpx.Response(302, headers={"location": ALLOWED_B})
        return image_bytes(b"final", content_type="image/png")

    service, calls = make_service(handler)
    content, ctype = asyncio.run(service.fetch(ALLOWED))
    assert content == b"final" and ctype == "image/png"
    assert calls == [ALLOWED, ALLOWED_B]


def test_redirect_to_non_allowed_host_is_rejected_before_outbound():
    service, calls = make_service(
        lambda request: httpx.Response(302, headers={"location": "https://evil.com/x.jpg"})
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 400  # 白名单校验失败
    assert calls == [ALLOWED]            # 跳转请求从未发出


def test_redirect_to_internal_address_is_rejected_before_outbound():
    service, calls = make_service(
        lambda request: httpx.Response(302, headers={"location": "https://127.0.0.1/x.jpg"})
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 400
    assert calls == [ALLOWED]


def test_redirect_chain_over_limit_is_aborted():
    def handler(request):
        return httpx.Response(302, headers={"location": str(request.url) + "?next=1"})

    service, calls = make_service(handler)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 502
    assert len(calls) == image_proxy.MAX_REDIRECTS + 1  # 达到上限即中止


# ---------- 异常映射 ----------

def test_html_error_page_is_rejected():
    service, _ = make_service(
        lambda request: httpx.Response(
            200, content=b"<html>denied</html>",
            headers={"content-type": "text/html; charset=utf-8"})
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 502


def test_oversized_image_is_rejected():
    service, _ = make_service(
        lambda request: image_bytes(
            b"x" * (image_proxy.MAX_IMAGE_BYTES + 1), content_type="image/jpeg")
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 502


def test_timeout_maps_to_504():
    async def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    service = image_proxy._ImageFetchService(client=client)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 504


def test_transport_error_maps_to_502():
    async def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    service = image_proxy._ImageFetchService(client=client)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.fetch(ALLOWED))
    assert exc.value.status_code == 502


# ---------- 端点行为 ----------

@pytest.fixture
def client():
    """带鉴权的端点客户端；portal 复用同一事件循环。"""
    app = FastAPI()
    app.include_router(image_proxy.router)
    with TestClient(app) as c:
        yield c


def test_endpoint_requires_token(client, monkeypatch):
    monkeypatch.setattr(image_proxy, "_API_KEY", "secret-key")
    service, _ = make_service(lambda request: image_bytes())
    monkeypatch.setattr(image_proxy, "_service", service)
    resp = client.get("/api/image-proxy", params={"url": ALLOWED})
    assert resp.status_code == 401
    resp = client.get("/api/image-proxy", params={"url": ALLOWED}, headers={"X-API-Key": "secret-key"})
    assert resp.status_code == 200  # 合法 key 放行
    resp = client.get("/api/image-proxy", params={"url": ALLOWED, "token": "wrong"})
    assert resp.status_code == 401


def test_endpoint_rejects_internal_and_credential_urls(client, monkeypatch):
    monkeypatch.setattr(image_proxy, "_API_KEY", "")
    for bad in ("https://127.0.0.1/x.jpg", "https://user:pass@media.takealot.com/x.jpg",
                "https://media.takealot.com:8443/x.jpg", "http://media.takealot.com/x.jpg"):
        resp = client.get("/api/image-proxy", params={"url": bad})
        assert resp.status_code == 400, bad


def test_endpoint_returns_image_with_cache_header(client, monkeypatch):
    monkeypatch.setattr(image_proxy, "_API_KEY", "secret-key")
    service, calls = make_service(lambda request: image_bytes(b"pngbytes", content_type="image/png"))
    monkeypatch.setattr(image_proxy, "_service", service)
    resp = client.get("/api/image-proxy", params={"url": ALLOWED, "token": "secret-key"})
    assert resp.status_code == 200
    assert resp.content == b"pngbytes"
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert len(calls) == 1


def test_endpoint_negative_cache_avoids_second_outbound(client, monkeypatch):
    monkeypatch.setattr(image_proxy, "_API_KEY", "secret-key")
    service, calls = make_service(lambda request: httpx.Response(403, content=b"no"))
    monkeypatch.setattr(image_proxy, "_service", service)
    for _ in range(2):
        resp = client.get("/api/image-proxy", params={"url": ALLOWED, "token": "secret-key"})
        assert resp.status_code == 502
    assert len(calls) == 1
