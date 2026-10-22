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
   `NaN`、负数、**0** 一律当**没有这个市场**，而不是 0。
   为什么展示层还要守一遍：`Number(null)` 与 `Number('')` 都是 0，旧写法会把
   「没填」渲染成「¥0」——一个看起来像真价格的假价格，比空着更容易误导定价。
   后端写入虽有校验，但 `/api/migrate` 是整行搬运、不过 Pydantic，桌面版导出的
   历史行可能带 null 或数字字符串，所以这条边界不能只靠后端。
   为什么 0 也算没有：参考价不可能是 0，而系统里另外三处早就这么认了——
   `/api/market-prices/status` 用 `entry.get('price')` 的真值判「填没填」、
   自动同步的 `_to_price()` 丢弃 `<= 0`、批量录入的 `buildMarketBatch()` 把 0 判无效。
   只有 `toPrice()` 与详情页输入框还收 0，于是同一个字段「列表显示 ¥0、覆盖率算未填」，
   批量面板还会因为它和草稿一致而清除不掉（MARKET-ZERO-01）。
   详情页的价格框因此改为「原文草稿 + 行内提示」：敲 `0`、`0.`、`-1`、`abc` 都不会被
   当成价格写进草稿（否则 `0.5` 的第一个字符就会把输入框清空，最后存成 5），
   原文留在框里并提示「价格需大于 0，不会保存」——与批量录入「无效行点名」同一口径。
3. **回写**：`serializeMarketPrices()` 同样丢掉没有有效价格的市场——后端每个市场都
   要求数值 `price`，把「只有 cur 没有 price」的条目塞进 PATCH 会被 400 掉，
   整次保存失败。全部市场都空时返回空串，即清空该字段。
   手工改一个市场统一走 `manualPriceEntry()`：`at` 由 `todayLocal()` 生成（Asia/Shanghai，
   与采购/库存/装箱同一份业务日期），并清掉上次自动同步留下的 `stale`/`miss`/`last_error`
   ——与 `/api/market-prices/batch` 同一口径。`src` 与 `url` 原样保留：这一格仍由每日
   同步负责，成功抓取照常覆盖回来。

## 展示口径

- 列表加一列「参考价（R）」（`ProductList` 的 `allColumns`，默认 210px，跟随列开关与拖拽）：
  **一个方框，只装采到的市场**（`visibleMarkets()`），采不到的不占位——留个灰占位会让人
  以为该去填，而淘宝/中国官网这两路本来就抓不到。新加坡官网价加粗打头（唯一由服务端
  每天重读的权威值），国内原价 / 到手价用次要色跟在后面。一路都没有时整格留 `—`。
- 悬停用 `position: fixed` 展开四列明细（市场 / 原币 / 兰特 / 来源 · 日期），避免被表格
  滚动容器裁掉；宽度取 `min(440, 视口-16)`，窄屏收窄而不是顶出视口。
- 详情页「多市场参考价」表同样是 市场 / 价格 / 兰特 / 样本 · 日期 四列。
  三个市场都在编辑态可手工录入，清空即删除该市场。编辑态的草稿只有一份（`resetDraft()`
  用服务端的产品重建），「取消」会连参考价一起退回——只读态读的也是这份草稿，不还原就会把
  被丢弃的价格留在页面上。保存失败走行内的 `saveError`，不写加载用的 `error`：那个状态
  渲染时会把整页换成一行红字，连草稿一起丢掉。
  淘宝的「样本条数」只在**已经有均价**时才出现：没有价格就没有「均了几个样本」，
  否则输入框可点可输但 `setMarketSample()` 会直接返回，敲进去的数字被静默丢掉。
- 价格前面的**符号跟着条目上的 `cur` 走**（`symbolFor()` → `entryCurrency()`），
  不跟市场写死：`entryCurrency()` 本来就承认迁移数据可能带别的币种，而
  `MARKET_META` 再存一份符号表就会出现同一行「S$99」配「无汇率」——左边说新加坡元、
  右边说不认识这个币种。认不出的币种写成 `GBP 99`（带代码），不猜符号。
- 比价结论只出同币种能直接比的那一项（到手价 / 原价），不出跨币种比值。

## 兰特折算

三地本币（S$ / ¥）跟 Takealot 售价不同币种，摆在旁边没法直接比；用户在 Takealot 卖货、
定价用兰特，所以界面上统一折成 R。

- 汇率来源 `open.er-api.com/v6/latest/ZAR`（免密钥）。**上游给的是「1 ZAR = x 外币」，
  取倒数才是「1 外币 = x ZAR」**——写反了整列会差两个数量级，`test_fx_rates.py` 用
  具体数字钉住方向（1 SGD = 12.8345 ZAR，1 CNY = 2.4453 ZAR）。
- **不另起定时线程**：汇率只有打开页面才需要，缓存写在 `SystemSettings` 的 `fx:zar`，
  超过 24 小时由下一次读取触发刷新。容器重启不会让缓存失效，也不会白跑一个定时任务。
- **拉不到就降级**：旧缓存（标 `stale`）→ 代码里的兜底常量（标 `fallback`）。
  界面永远算得出兰特，并在明细底部写清「1 SGD=R12.83 · 1 CNY=R2.45 · 日期」。
  整列空白比一个标了来源的旧汇率更糟——它看起来像「这款没有参考价」。
- 折算取整到兰特整数：参考价本来就是参考，`R1,282` 与 `R1,282.16` 对决策没有差别，
  后者还会把方框撑宽。
- 前端 `useFxRates()` 先把 localStorage 里的上次汇率当初始值，再去请求，避免刷新页面时
  先显示 `S$99.9`、一秒后跳成 `R1,282` 的闪动。**缓存里的 `fallback` 原样保留**：
  存进去的可能就是后端那组兜底常量，读回来时洗掉标记，界面就会把硬编码数字当成今天拉到的
  汇率，说明里也不再出现「兜底汇率」——`scripts/test-fx-rates.cjs` 钉住这一条。
- `stale` 不存死，读缓存时按年龄重算：`fetched_at` 缺失/非法、或距今 ≥ 24 小时
  （与后端 `TTL_HOURS` 同一口径）一律标 `stale`。写进 localStorage 的那份 `stale`
  是「写入那一刻」的判断，浏览器开着放一天不会自己变，读回来时若照抄就会把隔夜的旧汇率
  当成新的。
- 批量录入的组装在 `buildMarketBatch()`：面板输入是自由文本（`inputMode="decimal"`，
  不是 `type="number"`），`0` / `-5` / `abc` 都能留在框里。无效行不再静默跳过，
  而是连同 SKU 一起交回给调用方点名（`describeSkipped()`），有无效行时面板不关闭，
  让人改完再存——旧写法直接 `return` 掉，然后提示「已保存 N 条」或「没有需要保存的改动」。

## 覆盖

- 后端：`backend/tests/test_market_prices.py`（合法/空/坏 JSON/非对象/缺 price/
  字符串 price/bool/负数，以及 schema 层是否真的抛 `ValidationError`）；
  `backend/tests/test_fx_rates.py`（汇率倒置方向、基准货币校验、坏数据、缓存命中与过期、
  拉失败退回旧缓存 / 兜底常量、兜底值不写缓存）；
  `backend/tests/test_market_price_sync.py::test_auto_markets_only_lists_writable_markets`
  （`status.auto_markets` 里的每个市场都必须能通过 `PriceInput` 校验——这个字段不参与
  任何校验，写错了会一直很安静）。
- 前端：`scripts/test-market-prices.cjs`（124 条断言：解析、空值与 0 不当价格、数字字符串、
  坏输入、序列化丢空市场 / 丢 0、往返一致、币种符号与小数位、符号跟 `cur` 走（USD 不许冒用 S$）、
  只显示采到的市场、兰特折算方向与千分位、缺汇率时退回原币、汇率说明文案、
  批量录入只发真改过的行 / 无效行点名 / 清空发 `price:null`、
  手工改价按北京时间记日期并清掉 stale/miss/last_error）；
  `scripts/test-datetime.cjs`（36 条断言，含「扫整个 `src/` 不许自己拼本地日期」
  与「`toLocale*String` 必须显式给 `timeZone`」两道源码守卫，以及 `beijingClock()`）；
  `scripts/test-fx-rates.cjs`（21 条断言：没有缓存退回兜底、缓存命中、坏 JSON / 没有可用
  货币时当没缓存、兜底标记与过期标记原样保留、过期与时间戳非法时按年龄重算 `stale`、
  localStorage 读不了时不抛异常）。
- 版式：`scripts/test-responsive-layout.py` 的 `PRODUCT` fixture 带三个市场
  （四位数人民币 + 带小数的新加坡价，最宽的一档），`test-text-overlap.py` 复用同一份
  fixture，所以列表这一列与详情页卡片在 10 个宽度下都真的被量过。
  此前 fixture 不带 `market_prices`，这一列永远只渲染「—」，等于从没被量过。
  fixture 里的 `/api/market-prices/fx` 必须声明：页面一挂载就会读汇率，
  漏了会让整个布局测试以「未声明接口」失败，而不是悄悄少测一列。

## 手动触发

`POST /api/market-prices/sync` 起一个后台线程跑 `run_once()` 后立刻返回，前端不用等一分钟。
`run_once()` 是进程内串行的：拿不到 `_state_lock` 就直接返回 `{'skipped': ...}`，什么都不做。
所以接口在起线程前先问 `market_price_sync.is_running()`，已经有一轮在跑时回
`started: false` + 原因，而不是回「已开始重读官网价」——后者会让用户等一分钟、刷新状态，
发现 `last_sync` 一点没动，只能以为按钮坏了。

## 不做的事

中国官网（charleskeith.cn）是停摆的 ECStore 站点、淘宝/天猫要登录态，这两项**不写抓取**，
按红线不绕过站点风控，改由手工录入。新加坡官网价可从商品页的 JSON-LD
（`price` / `priceCurrency`）读，由 `services/market_price_sync.py` 每天重读一次。
汇率只用于展示折算，不参与利润计算——利润仍用系统设置里的 `cny_per_zar`。
