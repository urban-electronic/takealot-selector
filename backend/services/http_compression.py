"""按响应类型选择性 gzip（HTTP-COMPRESS-01）。

Starlette 自带的 `GZipMiddleware` 不看内容类型：image-proxy 代理回来的 JPEG/PNG
本来就压过，再压一遍只烧 CPU、体积几乎不变。这里只压文本类响应，图片原样透传。

为什么值得做（本地库，329 个商品，`gzip -5`）：

| 接口 | 原始 | gzip | 省 | 压缩耗时 |
| --- | --- | --- | --- | --- |
| `GET /api/products` | 763.1 KB | 109.9 KB | 85.6% | 6.2ms |
| `GET /api/inventory` | 174.7 KB | 36.0 KB | 79.4% | 1.8ms |
| `GET /api/packing/products` | 142.7 KB | 23.1 KB | 83.8% | 1.3ms |

级别取 5 不是默认值：同一份商品列表 level 6 是 107.2 KB / 7.3ms、level 9 是
105.0 KB / 10.5ms——多花 40% 的 CPU 只换 2 KB，不划算；level 4 是 119.4 KB /
5.5ms，省下的 0.7ms 要多传 9.5 KB。5 是这一档的拐点。

商品列表一次返回全表（前端自己分页，`page_size` 在后端不生效），所以这一条直接决定
列表页首屏要下多少字节。
"""
import gzip

from starlette.datastructures import Headers, MutableHeaders

# 文本类：JSON / JS / XML / SVG。图片、xlsx、pdf 不在此列——它们已经压缩过。
TEXT_TYPES = ('application/json', 'application/javascript', 'application/xml', 'image/svg+xml')
MINIMUM_SIZE = 1024
# 见上面的级别对比：5 之后体积收益已经很小，CPU 却还在涨。
COMPRESS_LEVEL = 5


def compressible(content_type: str) -> bool:
    value = (content_type or '').split(';')[0].strip().lower()
    return value.startswith('text/') or value in TEXT_TYPES


class SelectiveGZipMiddleware:
    """只压文本类响应的 gzip 中间件。

    非文本响应在 `http.response.start` 就放行，后续 body 不再经过缓冲区，
    所以 image-proxy 这类大响应不会被整体读进内存。
    """

    def __init__(self, app, minimum_size: int = MINIMUM_SIZE):
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(self, scope, receive, send):
        accepts = (Headers(scope=scope).get('accept-encoding') or '').lower() if scope['type'] == 'http' else ''
        if scope['type'] != 'http' or 'gzip' not in accepts:
            await self.app(scope, receive, send)
            return

        state = {'buffering': False, 'start': None}
        chunks = []

        async def send_wrapper(message):
            kind = message['type']
            if kind == 'http.response.start':
                headers = Headers(raw=message['headers'])
                # 已经带 content-encoding 的（上游就是 gzip）不能再压一次
                if headers.get('content-encoding') or not compressible(headers.get('content-type', '')):
                    state['buffering'] = False
                    await send(message)
                    return
                state['buffering'] = True
                state['start'] = message
                return
            if kind == 'http.response.body':
                if not state['buffering']:
                    await send(message)
                    return
                chunks.append(message.get('body', b''))
                if message.get('more_body'):
                    return
                await self._finish(state['start'], b''.join(chunks), send)
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _finish(self, start, body, send):
        headers = MutableHeaders(raw=start['headers'])
        self._add_vary(headers)
        if len(body) < self.minimum_size:
            # 太短，压缩后反而更大（gzip 头 18 字节起步），原样发。
            await send(start)
            await send({'type': 'http.response.body', 'body': body})
            return
        data = gzip.compress(body, compresslevel=COMPRESS_LEVEL)
        headers['content-encoding'] = 'gzip'
        headers['content-length'] = str(len(data))
        await send(start)
        await send({'type': 'http.response.body', 'body': data})

    @staticmethod
    def _add_vary(headers):
        """同一 URL 会因 Accept-Encoding 返回不同字节，缓存必须按它分流。"""
        vary = headers.get('vary')
        if vary is None:
            headers['vary'] = 'Accept-Encoding'
        elif 'accept-encoding' not in vary.lower():
            headers['vary'] = f'{vary}, Accept-Encoding'
