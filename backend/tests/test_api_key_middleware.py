# -*- coding: utf-8 -*-
"""API Key 中间件的行为锁定。

之前这条中间件没有任何测试：改比较方式（`!=` → hmac.compare_digest）、
给 collector 加第二个头、给 image-proxy 开例外，全靠人肉回归。
这里用一条自建的探针路由验证四个不该变的结论：

1. 没设 API_KEY 时保持放行（本地开发不能被打断）；
2. 设了 API_KEY 后，缺 key / 错 key 都返回 401，且 401 带 CORS 头
   （否则浏览器把 401 报成 CORS 错误，看不到真实原因）；
3. 对 key 放行；
4. OPTIONS 预检不受鉴权影响（浏览器预检不带 X-API-Key）。

探针路由只在本测试进程里注册，不影响线上。
"""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import main

# 自建探针：不碰数据库，只验证中间件放行与否。
_probe = APIRouter()


@_probe.get('/api/__auth_probe')
def _auth_probe():
    return {'ok': True}


if not any(getattr(r, 'path', '') == '/api/__auth_probe' for r in main.app.routes):
    main.app.include_router(_probe)

KEY = 'selfcheck-key-123'
ORIGIN = 'https://example.invalid'


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, '_API_KEY', KEY)
    return TestClient(main.app)


def test_missing_key_is_rejected_with_cors_header(client):
    res = client.get('/api/__auth_probe', headers={'Origin': ORIGIN})
    assert res.status_code == 401
    # 401 必须带 CORS 头，否则浏览器只报「CORS 错误」，看不到「缺 key」。
    assert res.headers.get('access-control-allow-origin') == ORIGIN


def test_wrong_key_is_rejected(client):
    assert client.get('/api/__auth_probe', headers={'X-API-Key': KEY + 'x'}).status_code == 401


def test_correct_key_passes(client):
    res = client.get('/api/__auth_probe', headers={'X-API-Key': KEY})
    assert res.status_code == 200
    assert res.json() == {'ok': True}


def test_options_preflight_never_needs_key(client):
    res = client.options('/api/__auth_probe', headers={
        'Origin': ORIGIN,
        'Access-Control-Request-Method': 'GET',
    })
    assert res.status_code < 400


def test_empty_key_means_open(monkeypatch):
    # 没配 API_KEY（本地开发默认）时不能把所有人挡在门外。
    monkeypatch.setattr(main, '_API_KEY', '')
    res = TestClient(main.app).get('/api/__auth_probe')
    assert res.status_code == 200


def test_non_ascii_configured_key_returns_401_not_500(monkeypatch):
    """compare_digest 对非 ASCII 的 str 会抛 TypeError，中间件里必须先编码。

    HTTP 头只能装 ASCII，所以「key 配成了中文」这种误配在请求侧必然对不上——
    该给 401，不能因为比较函数抛异常变成 500（500 比 401 难排查得多）。
    """
    monkeypatch.setattr(main, '_API_KEY', '中文密钥')
    res = TestClient(main.app).get('/api/__auth_probe', headers={'X-API-Key': 'ascii-only'})
    assert res.status_code == 401
