# -*- coding: utf-8 -*-
"""响应压缩的行为锁定（HTTP-COMPRESS-01）。

商品列表接口一次返回全表（763 KB），前端自己分页；服务端此前完全没有压缩，
列表页首屏要下满这些字节。这里锁住四条不该变的结论：

1. 文本类大响应在客户端接受 gzip 时必须被压缩，且解压后与原字节一致；
2. 客户端没声明 Accept-Encoding 时不许压缩（否则浏览器拿到乱码）；
3. 太短的响应不压——gzip 头 18 字节起步，压了反而更大；
4. 图片等已压缩类型原样透传——这正是没用 Starlette 自带 GZipMiddleware 的原因。

第 4 条是核心：换成内置中间件时它不会报错，只会安静地给每张 JPEG 白压一遍。
"""

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from services.http_compression import SelectiveGZipMiddleware, compressible

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 40


def build_app():
    app = FastAPI()
    app.add_middleware(SelectiveGZipMiddleware)

    @app.get('/big.json')
    def big():
        return {'items': [{'name': '商品名称' * 5, 'price': 199} for _ in range(80)]}

    @app.get('/small.json')
    def small():
        return {'ok': True}

    @app.get('/image.png')
    def image():
        return Response(content=PNG, media_type='image/png')

    @app.get('/stream.json')
    def stream():
        # 分块发出的 JSON：要收齐所有 more_body 片段后再压
        def chunks():
            yield b'{"items": ['
            yield b'"' + b'x' * 2000 + b'"'
            yield b']}'

        from starlette.responses import StreamingResponse
        return StreamingResponse(chunks(), media_type='application/json')

    return app


def test_text_response_is_gzipped_and_round_trips():
    client = TestClient(build_app())
    plain = client.get('/big.json', headers={'Accept-Encoding': 'identity'}).content
    packed = client.get('/big.json', headers={'Accept-Encoding': 'gzip'})

    assert packed.headers['content-encoding'] == 'gzip'
    # content-length 是压缩后在线上的字节数，httpx 会替我们解压 content
    assert int(packed.headers['content-length']) < len(plain) / 3
    assert packed.content == plain
    # 同一 URL 有两种字节表示，缓存必须按 Accept-Encoding 分流
    assert 'accept-encoding' in packed.headers['vary'].lower()


def test_streaming_response_is_collected_then_compressed():
    client = TestClient(build_app())
    response = client.get('/stream.json', headers={'Accept-Encoding': 'gzip'})
    assert response.headers['content-encoding'] == 'gzip'
    assert response.content == b'{"items": ["' + b'x' * 2000 + b'"]}'


def test_without_accept_encoding_nothing_is_compressed():
    client = TestClient(build_app())
    response = client.get('/big.json', headers={'Accept-Encoding': 'identity'})
    assert 'content-encoding' not in response.headers
    assert response.json()['items']


def test_small_response_is_left_alone():
    client = TestClient(build_app())
    response = client.get('/small.json', headers={'Accept-Encoding': 'gzip'})
    assert 'content-encoding' not in response.headers
    assert response.json() == {'ok': True}


def test_image_is_never_recompressed():
    client = TestClient(build_app())
    response = client.get('/image.png', headers={'Accept-Encoding': 'gzip'})
    assert 'content-encoding' not in response.headers
    assert response.content == PNG


def test_compressible_types():
    assert compressible('application/json')
    assert compressible('application/json; charset=utf-8')
    assert compressible('text/html')
    assert compressible('image/svg+xml')
    # 已压缩的二进制不进压缩通道
    assert not compressible('image/png')
    assert not compressible('image/jpeg')
    assert not compressible('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert not compressible('')


def test_main_app_installs_the_selective_middleware():
    """换回 Starlette 自带的 GZipMiddleware 会让图片被白压一遍，这里钉住实现。"""
    import main

    installed = [m.cls for m in main.app.user_middleware]
    assert SelectiveGZipMiddleware in installed
