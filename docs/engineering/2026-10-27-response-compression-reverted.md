# 应用层 gzip 已实现、已上线、已回退：平台边缘本来就在压（2026-10-27）

这次改动**没有留在代码里**。留下来的是结论和方法——避免下一个人再花一轮做同一件事。

## 起点

商品列表 `GET /api/products` 一次返回全表 **763.1 KB**（分页在前端做），库存 174.7 KB、
装箱 142.7 KB。`backend/` 里没有任何压缩中间件（`grep -r GZip backend/` 为空），
所以第一反应是「服务端没压，加上就能省 85%」。

本地测出来也确实漂亮（`gzip -5`）：

| 接口 | 原始 | gzip | 省 | 压缩耗时 |
| --- | --- | --- | --- | --- |
| `/api/products` | 763.1 KB | 109.9 KB | 85.6% | 6.2ms |
| `/api/inventory` | 174.7 KB | 36.0 KB | 79.4% | 1.8ms |
| `/api/packing/products` | 142.7 KB | 23.1 KB | 83.8% | 1.3ms |

于是写了按 content-type 分流的 `SelectiveGZipMiddleware`（不用 Starlette 自带的
`GZipMiddleware`，它会连 image-proxy 的 JPEG/PNG 一起压），7 例守卫 + 两条反向探针，
全量门禁 37/37，上线。

## 上线后的疑点

线上 `/openapi.json`（92 KB，无需鉴权，正好用来验压缩）确实返回
`Content-Encoding: gzip`，`Content-Length: 13429`——和本地 `gzip.compress(raw, 5)`
的 13429 **逐字节吻合**，说明压缩是我的中间件做的。

但另一个现象对不上：首页 `index.html` 只有 866 字节，低于中间件 1024 字节的门槛，
**也被压了**（866 → 500）。中间件不会碰它，那是谁压的？

## 对照部署（决定性实验）

把中间件注册注释掉，单独部署一版（`361f792bb60b`），再请求同一接口：

```
$ curl -H "Accept-Encoding: gzip" -D - .../openapi.json
Content-Encoding: gzip
vary: accept-encoding          ← 小写，边缘加的
（无 Content-Length）
```

**没有中间件，JSON 照样被压。** 再测实际传输字节：

| 链路 | 传输字节 | 相对原始 |
| --- | --- | --- |
| `Accept-Encoding: identity` | 92184 | — |
| 边缘 gzip（无中间件） | **13288~13291** | 省 85.6% |
| 应用内 gzip level 5 | 13429 | 省 85.4% |

边缘压得比我还好 1%，而且不占应用 CPU。

## 结论

- Railway 边缘（`server: railway-hikari`）对文本类响应**已经做了 gzip**，浏览器带
  `Accept-Encoding: gzip` 时拿到的本来就是压缩后的字节；
- 应用内再加一层：传输**零收益**，服务端**每次多 6~10ms CPU**（列表页 17.7ms → 27.6ms）；
- 结论是净负面，于是回退：`backend/main.py` 恢复原样，`services/http_compression.py`
  与它的 7 例测试一并删除。

## 怎么判断「平台是不是已经压过了」

不用猜，两步就够：

1. **对照部署**：把要加的中间件停掉部署一版，用同一个 URL + 同一个
   `Accept-Encoding` 看响应头。有 `Content-Encoding` 就说明平台已经做了。
2. **字节精确比对**：`curl -o /dev/null -w "%{size_download}"`（不带 `--compressed`）
   量的是线上字节。把它和自己实现压缩后的大小比：差 1% 以内就别自己压。
   顺带看响应头里 `Content-Length` 在不在——平台压缩通常是 chunked，没有
   `Content-Length`；应用内压缩一般会自己写。

`Accept-Encoding: br` / `deflate` 单独试一次也有用：平台只认 gzip 时，这两种会原样
返回未压缩内容，能反过来说明「边缘确实在挑编码」。

## 这次留下的教训

本地测「763 KB → 110 KB」是真的，但它是**在「链路上没人压」这个假设下**的收益。
真实链路的传输层归平台管，应用层再压一遍只是把同一件事做了两次。

**优化传输前先量线上真实传输量**，而不是量应用输出的字节数——这两者在这个项目里
差了 7 倍（763 KB 输出 vs 110 KB 线上传输，且后者在我动手之前就已经是 110 KB 了）。

下一步的「流畅性」杠杆不在传输层：

- 接口一次返回全表（763 KB 的**源头**）：真要做服务端分页，得连前端整表排序、
  跨页选中的语义一起改；
- 前端渲染：列表页 `products.slice()` 只渲染 20 行，DOM 压力不大；
- 剩余的两处逐行查库：`seller_operations_sync.apply_snapshot` 里按商品取档案
  （每小时跑一次，量级可接受）。
