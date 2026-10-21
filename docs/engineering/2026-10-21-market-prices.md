# 多市场参考价（Product.market_prices）

## 是什么

代运营品牌的货来自品牌官网，定价时要同时看「品牌在中国卖多少、在源头市场（新加坡）
卖多少、淘宝渠道卖多少」。这三个数以前只能塞进备注，看不全也统计不了。

存储上不建表、不拆三个字段：`products.market_prices` 存一段 JSON 文本，空串表示没采过。
理由是这个数据只做展示与比价，不参与利润计算，也没有跨产品聚合需求；用 JSON 之后
加市场只改前端常量（`src/utils/marketPrice.ts` 的 `MARKET_ORDER` / `MARKET_META`），
不用再来一次迁移。启动时由 `main._ensure_columns()` 给已存在的库补列。

```json
{"CN":{"price":469,"cur":"CNY","src":"官网","at":"2026-10-21"},
 "SG":{"price":115,"cur":"SGD","src":"官网","at":"2026-10-21","url":"..."},
 "TB":{"price":268,"cur":"CNY","src":"淘宝均价","n":12,"at":"2026-10-21"}}
```

## 三道边界

1. **写入**：`product_routes._check_market_prices()` 在 Pydantic 层挡坏数据——必须是
   JSON 对象，每个市场的 `price` 必须是**非负数值**（`bool` 不算数值），否则 400。
   `market_prices` 归在 `metadata_fields` 里：只改参考价时既不触发利润重算，
   也不会清掉 `manual_*` 人工成本覆盖（那条清空逻辑只对非 metadata 字段生效）。
2. **展示**：`parseMarketPrices()` 把「像价格的值」归一成数字；`null`、空串、布尔、
   `NaN`、负数一律当**没有这个市场**，而不是 0。
   为什么展示层还要守一遍：`Number(null)` 与 `Number('')` 都是 0，旧写法会把
   「没填」渲染成「¥0」——一个看起来像真价格的假价格，比空着更容易误导定价。
   后端写入虽有校验，但 `/api/migrate` 是整行搬运、不过 Pydantic，桌面版导出的
   历史行可能带 null 或数字字符串，所以这条边界不能只靠后端。
3. **回写**：`serializeMarketPrices()` 同样丢掉没有有效价格的市场——后端每个市场都
   要求数值 `price`，把「只有 cur 没有 price」的条目塞进 PATCH 会被 400 掉，
   整次保存失败。全部市场都空时返回空串，即清空该字段。

## 展示口径

- 列表加一列「参考价」（`ProductList` 的 `allColumns`，默认 190px，跟随列开关与拖拽），
  一格并列 CN / SG / 淘 三个货币标签；悬停用 `position: fixed` 展开明细
  （来源、日期、淘宝样本条数、淘宝/中国官网折扣比），避免被表格滚动容器裁掉。
  注意：这一列的实际渲染宽度由表格自动布局决定（内容 215px + 内边距 → 约 247px），
  与 `defaultWidth` 不一致，这跟「标题」「状态」等列的行为一致，不是缺陷。
- 详情页加「多市场参考价」卡片，三个市场都在编辑态可手工录入，清空即删除该市场。
  淘宝的「样本条数」只在**已经有均价**时才出现：没有价格就没有「均了几个样本」，
  否则输入框可点可输但 `setMarketSample()` 会直接返回，敲进去的数字被静默丢掉。
- 比价结论只出同币种能直接比的那一项（淘宝 / 中国官网）。新加坡价是 SGD，
  跟人民币不能直接比，不引入汇率假设，只在上面的明细里展示。

## 覆盖

- 后端：`backend/tests/test_market_prices.py`（合法/空/坏 JSON/非对象/缺 price/
  字符串 price/bool/负数，以及 schema 层是否真的抛 `ValidationError`）。
- 前端：`scripts/test-market-prices.cjs`（51 条断言：解析、空值不当 0、数字字符串、
  坏输入、序列化丢空市场、往返一致、币种符号与小数位、比价结论除零与跨币种不比、
  「上次同步」的北京时间换算与非法时间戳、自动同步只碰官网来源）。
- 同步服务：`backend/tests/test_market_price_sync.py`（JSON-LD 解析、失败保留旧价、
  三次失败打 stale、手工条目永不被碰、调度到期判断、批量录入按店铺隔离、
  一条坏数据不打断整轮）。
- 版式：`scripts/test-responsive-layout.py` 的 `PRODUCT` fixture 带三个市场
  （四位数人民币 + 带小数的新加坡价，最宽的一档），`test-text-overlap.py` 复用同一份
  fixture，所以列表这一列与详情页卡片在 10 个宽度下都真的被量过。
  此前 fixture 不带 `market_prices`，这一列永远只渲染「—」，等于从没被量过。

## 自动同步（MARKET-SYNC-01 之后）

`backend/services/market_price_sync.py` 每 30 分钟检查一次「距上次成功是否超过
`MARKET_PRICE_SYNC_INTERVAL_HOURS`（默认 24）」，到期才真跑一轮：只重读 `src` 属于
`AUTO_SOURCES`（空 / `官网` / `官网自动`）**且带 `url`** 的条目，成功写
`src=官网自动` + `at=今天`，失败只累加 `miss`，连续 3 次打 `stale`，**价格永不删除**。
调度不用裸 `sleep 24h`：Railway 会不定时重启容器，裸 sleep 会把计时器一起重置。

单条坏数据不许打断整轮：`url` / `miss` 这些字段来自 `/api/migrate` 整行搬运的自由 JSON，
不是字符串时旧写法会抛 `AttributeError`，整轮回滚且 `last_run` 永不更新——表现是
「自动同步从来没跑过」。现在 `is_auto_entry()` 只认字符串，`apply_fetch()` 兜底
`miss`，`sync_prices()` 逐条 try/except，坏的那条报 `failed` 并保留原值。

## 不做的事

中国官网（charleskeith.cn）是已停摆的 ECStore 站点，在售商品只剩十几个旧款、也没有
本店选品的货号；淘宝/天猫要登录态，京东搜索会 302。这两项**不写抓取**，按红线不绕过
站点风控，只接受人工录入（`src=手工` / `淘宝均价`），自动同步永远跳过它们。
自动同步只覆盖官网直营市场（当前只有 SG）：页面 JSON-LD 里的
`price` / `priceCurrency` 可稳定解析，不需要登录态，也不做任何反爬对抗。

