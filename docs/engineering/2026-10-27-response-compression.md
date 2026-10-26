# 列表响应一次下 763 KB，服务端一条都没压（2026-10-27）

## 现象

商品列表页每打开一次就下载整张表：`GET /api/products` 返回 **763.1 KB** JSON
（329 个商品，`page_size` 在后端不生效，分页是前端 `products.slice()` 做的）。
库存 174.7 KB、装箱 142.7 KB。

而服务端**没有任何压缩**：`main.py` 里只有 CORS 和 API Key 两个中间件，
线上响应头里没有 `content-encoding`。

## 为什么不是「加个 GZipMiddleware 就完事」

Starlette 自带的 `GZipMiddleware` 只按体积判断，不看内容类型。这个项目里有
`/api/image-proxy`——它代理的是 Takealot 的 JPEG/PNG 缩略图，本来就已经压过。
用内置中间件的话，每张图都会被 gzip 一遍：CPU 白烧，体积基本不变，而图片是列表页
请求数最多的一类资源。

所以按内容类型分流的版本才有意义。

## 改法

| 位置 | 改动 |
| --- | --- |
| `services/http_compression.py`（新增） | `SelectiveGZipMiddleware`：只压 `application/json` / `text/*` / `javascript` / `xml` / `svg`；非文本响应在 `http.response.start` 就放行，body 不进缓冲区（image-proxy 不会被整体读进内存）；已带 `content-encoding` 的响应跳过；补 `Vary: Accept-Encoding` |
| `main.py` | 注册为最外层中间件，401/500 的 JSON 也一起压 |

压缩级别取 **5**，不是默认 6：

| 级别 | 商品列表体积 | 耗时 |
| --- | --- | --- |
| 1 | 136.4 KB | 3.7ms |
| 4 | 119.4 KB | 5.5ms |
| **5** | **109.9 KB** | **6.2ms** |
| 6 | 107.2 KB | 7.3ms |
| 9 | 105.0 KB | 10.5ms |

6 以上多花 40% CPU 只换 2 KB，不划算。

## 验证

本地库 329 个商品，`Accept-Encoding: gzip` 与 `identity` 各测三次取最好：

| 接口 | 不压缩 | 压缩后线上字节 | 省 | 服务端耗时 |
| --- | --- | --- | --- | --- |
| `/api/products` | 763.1 KB | 109.9 KB | 85.6% | 17.7ms → 27.6ms |
| `/api/inventory` | 174.7 KB | 36.0 KB | 79.4% | 16.3ms → 18.7ms |
| `/api/packing/products` | 142.7 KB | 23.1 KB | 83.8% | 16.6ms → 18.4ms |
| `/api/operations/overview` | 3.9 KB | 1.8 KB | 53.8% | 8.8ms → 8.9ms |

代价是服务端每次多 6~10ms CPU，换掉 656 KB 传输。列表页在移动网络下这是数量级的差别；
本机 localhost 测会看起来「变慢了」，那是没算传输。

`tests/test_http_compression.py` 7 例锁定：压缩后解压与原字节一致、`content-length`
是压缩后长度、`Vary` 存在、分块响应（`StreamingResponse`）也收齐再压、客户端没声明
`Accept-Encoding` 时不压、太短的不压、图片原样透传、主应用确实装了这版中间件。

两条反向探针都实测转红：

- 把 `compressible()` 改成永远 `True`（等价于换回内置中间件）→
  `test_image_is_never_recompressed` 与 `test_compressible_types` 失败；
- 把 `main.py` 里的注册注释掉 → `test_main_app_installs_the_selective_middleware` 失败。

## 未做

- **br（brotli）**：比 gzip 再省 15~20%，但要加依赖（`brotli`），且需要按
  `Accept-Encoding` 协商优先级。当前收益/复杂度不划算，等有真实弱网反馈再说。
- **服务端分页**：763 KB 的根因是接口一次返回全表，压缩只是把传输成本降下来。
  真要做要连前端筛选/排序语义一起改（现在整表排序、跨页选中都依赖全量数据在内存里），
  属于独立改动。
