# PricePilot 核心逻辑接入

Takealot 继续作为主程序，保留现有 React/Tauri、FastAPI、数据表、导航、店铺隔离，以及成本利润、采购、装箱和库存实现。

源代码依据：PricePilot_原生安装源码包_20261012.zip 中 pricepilot/server/src 的纯计算模块。迁入 pricing/engine.ts、replenish/engine.ts、recommend/engine.ts 和 recommend/keywords.ts；核心算法保留，商品详情增加折叠经营助手。

## 已接入

- Buybox 和跟随现货最低价策略：Fastest 不可达时转到 Best Price；最低/最高价保护；未持有只降不升；持有后允许回弹；计算模块保留冷却、每日限额和无库存保护。
- 补货：30天/7天成交量，近期爆发封顶3倍，海运45天/空运15天和安全库存14天（可调整），扣除现货和已确认在途，零销量滞销识别。
- 相似选品：标题去噪、共同核心词门槛、价格门槛、链接去重和排序。

## 数据边界和当前限制

经营助手是计算入口，不是自动调价执行器。最低价默认采用 Takealot 已有20%利润率最低价，允许用户调整。本店现价和现货库存必须独立填写，不能把官网市场价或国内库存当作店铺数据。竞品仅采纳明确现货且不超过30分钟的快照；未来时间和无效数值被拒绝。本店自身报价按填写的卖家名称排除；名称须与快照一致。采集时间必须带时区。

补货输入真实成交记录，不能由采购、发货或库存差额推测销量。需完整近30天订单；当前不自动验证订单完整性。海运/空运建议为替代方案，不能相加。

现有 Seller API 仅支持读取；没有迁入 PricePilot 的数据库、队列、定时任务、平台改价写入、销售同步、采集器或跨机主备机制。这些依赖未接通，不能声称自动运行、后台改价或完整合并完成。没有修改现有商品、采购记录或生产报价。

验证：npm run build；node scripts/test-operations.cjs（22条业务回归断言）。

## 完整性复核（2026-10-13）

计算层和详情页入口可用；保留原程序主结构和数据库。定价引擎已修复新旧报价混用、未来快照和无效数值校验。22条经营规则断言、37项后端测试、首页任务和页面导入回归通过。后台自动调价、竞品自动采集、订单同步、持有Buybox自动识别、跨机主备尚未接入。UI测算不持久化调价状态，因此引擎虽保留冷却和每日次数规则，当前手工测算入口不执行这些运行限制。

## 空海接力与持续计算更新

商品详情的“空运 / 海运接力计划与自动选品”取代旧的两套独立补货建议。保存真实销量观测起点、库存日期和快照、运输时效、安全天数和采购周期后，每次读取由服务端确定性计算；网页每60秒刷新，不调用AI。新品按真实观测天数计算3/7/30天销速，观测满14天后爆发封顶长期日均三倍。

空运覆盖海运到货前及安全天数的缺口，海运接力覆盖后续采购周期；逐日模拟采购到货，不能用45天后到的海运货抵扣15天内缺口。到货前的损失需求不作为欠单追回。记录采购后需指定空/海和预计到达可售仓日期，足量按时采购覆盖的提醒消失，不足数量继续提醒。取消采购自动剔除；延期到货不假装已经入库；到货已纳入库存快照可勾选防重复计算。拆分同一采购为空海两批需建立两笔采购记录。

主页只呈现空/海缺口。库存快照超过3天提醒补录；真实销量和库存目前由用户维护，没有宣称接通自动订单采集或卖家库存同步。网页关闭不发推送；重新打开按当时日期和保存数据重算。库存快照和采购配置信息按店铺保存，普通全局设置接口不得读取或覆盖。

推荐基于本店现有选品库：同细分品类（明确保存的品类标签）和用户声明的关联品类分开呈现；候选需为合格选品且市场价不低于其自身20%利润率门槛。没有标签的商品不凭粗费率品类乱推，没有外部商品搜索或自动采购。

验证：46项后端回归、22条经营规则断言、首页与页面导入检查及生产构建通过。安装包workflow仅保留手动触发，网页更新不再自动打包。

## 独立补货页与后台订单评估

顶部增加自动调价和补货计划入口，商品详情不再放置经营表单。自动调价页明确注明自动执行尚未启用，保留定价建议，不能把页面入口等同于实际平台改价。

补货页只列出link_status为已购买/已上架，或有未取消的正数采购记录的商品。未买过的商品即使有旧手填profile，也不会生成计划或提醒。

新增服务端每小时官方Seller API v2 offers/sales读取，不依赖AI，也不要求网页打开。接口密钥使用现有加密保存的配置，不写进源码。当前配置只归属默认店铺，其他店铺未独立绑定时显示未配置，不共享实际销售数据。订单按order_item_id去重，排除取消/退款/退货，南非日期转换为中国日期；SKU精确匹配，缺SKU时可唯一TSIN或PLID匹配，不用标题猜测。多SKU汇总可售库存和销量，遇到身份歧义保留原数据。任一分页、订单或库存格式失败整批回滚，不以空数据覆盖成功快照。

同步保留原采购到货分配、时效、安全天数、采购周期和细分品类；不修改店铺售价，不增改采购订单，不把国内采购入库误当为Takealot入库。官方库存和销量在界面只读。平台自报在途尚无到货日，不直接抵扣早期缺口。

数据库租约避免多进程重复同步，失败30分钟后重试，正常每小时重读，后台状态可在补货页查看。订单接口权限和现有密钥适配需线上同步验证；读取失败时页面明确显示原因，不能声称已经取得实际销售数据。

验证：50项后端测试通过，新增订单去重、取消过滤、未购买排除、跨店隔离、重复SKU拒绝、配置保留测试；生产前端构建通过。

## 2026-10-13 智能逐SKU调价与补货利润

价格引擎、公开页Buybox解析迁自PricePilot。按精确offer_id+SKU绑定，每个SKU独立开关、范围、日志和计数；默认全部关闭，只有用户点击逐SKU或勾选批量启动才会触发持续后台处理。分类是独立的调价分组，不建立第二套采购/商品/库存资料。取现价和库存后采集公开现货竞品；未持有只降不升，持有可回弹，最低价破线放弃，支持每日次数/冷却限制。调用官方PATCH前再次核对现价及开关，先记录改价意图，后读回核验；不明结果暂停，不盲目重试；可核验平台价后再手动启动。

最低价默认读Takealot系统20%利润底价，缺失时通过原成本引擎计算，仍不能得出时允许新SKU手工设定。最高价可留空。确认本店有现货、持有Buybox且存在明确非现货竞品时，约3%小步试涨，每12小时最多一步，累计不超过启用时参考价20%；没有行情或卖家状态不明不推断为竞品断货。试涨48小时没有新订单时回退此前价格并观察24小时，依赖近期成功同步的逐SKU订单时间；不把暂时无订单解释为已证实的价格因果关系。

补货按空运14天头程、海运55天头程加2–3天派送（默认3天）重算，海运承担后续周期及安全库存，空运仅填桥接缺口。使用Takealot原成本引擎分别计算空海运单件利润；默认最低补货利润率15%（可调整），不达标暂停对应运输补货建议。人工总价或人工运费不能强行当作两种运输的报价，缺资料显示参考数量，不声称利润已评估。

补货UI保留PricePilot式概览卡、分级、销速、可售天数、确认在途、CSV导出；正常接口状态不逐行重复。计划设置即时打开侧栏并按ID读取商品，修复列表分页造成按钮找不到商品及设置落到表格底部的问题。成本、采购、库存沿用主系统，未采购商品继续不生成计划。安装包自动构建保持停用。

真实店铺价格没有在开发验证中更改，改价执行仅做模拟接口测试；上线可做只读预检。公共页面若无法采集，后台保持现价并明确提示。

### Balanced dispatch calendar (current default)
The product list is the first/default view; the procurement/departure calendar is second. The current default is balanced: limited profitable air for early unmet demand, a larger sea departure now and a smaller sea departure fifteen days later from the same planned purchase. The adaptive air reference is 20%, adjusted upward for an actual early gap and favorable air profit (bounded at 40%); the configurable absolute air duration guard defaults to 21 days; confirmed pending air is deducted. Sea uses 75%/25% as a reference, shifts with the sales trend, shortens the second departure interval when its first-batch buffer is small, and increases the first share when delaying those units would cause a new stockout. A user can specify a current planned batch quantity, e.g. 100; this is a planning constraint, never a purchase order. Stock/velocity/margins can reduce the allocation. Subsequent rolling batches remain sea. Explicit sea-only settings are supported. The existing 120-day outlook, 30-day review interval, 60-day sea target and safety stock remain configurable.

Daily lost demand is shown in dated shortage windows; it is not purchased as an overdue backlog. A small air allocation cannot magically cover a 41-day lead-time difference. Examples test both the 20/60/20 split and continuous coverage when existing stock is adequate, and show uncovered demand when it is not. All purchase/departure dates and target coverage dates are estimates requiring review; no new procurement is created. Unknown carrying/storage costs are not profit guarantees.

### Independent Buybox monitoring
The `_pricing_monitor:` settings and dedicated background worker collect quotes independently of `_pricing_rule:` rules. Per-SKU and bulk scheduling support a 15–1440 minute configured interval; the UI provides 15/30/60/120 presets and a one-shot queued collection action. Collection makes official GET reads and public-page reads only, never price PATCH/POST requests. It requires an exact bound SKU and valid shop credentials but does not require a pricing floor. The queue runs without an open webpage, respects leases and concurrent stop/configuration updates, spaces jobs, and backs off after failure. Old valid quotes and their collection timestamp are preserved with an error marker. Matching the public price does not prove seller identity; the monitoring UI marks equal prices as pending confirmation.

Pricing changes remain individually opt-in. The UI separates blue collection controls, green pricing controls, amber review states, red failures and neutral disabled states. New monitor tests verify read-only behavior, cached quotes, retry timing, cancellation, disabled jobs, atomic bulk validation and store isolation. Public-page restrictions remain explicit; adding scheduling does not make an inaccessible page collectible.

Adaptive mode is the default; percentages are references, not fixed quotas. Existing stock can eliminate air demand; poor/unknown margins suppress it. Explicit sea-only and optional non-adaptive controls remain available.

### Recording split shipments from one purchase
A procurement can now have multiple air/sea allocations with individual quantities and ETA dates. A legacy single allocation without quantity still means the whole purchase. Multiple allocations require explicit positive quantities, unique mode/ETA/snapshot markers and a sum no larger than the actual procurement quantity. Included-in-stock and overdue rules apply per shipment; only the correct quantities enter each arrival date. Unallocated domestic goods are never treated as landed stock. If a later purchase edit makes saved splits exceed the purchase, the planner rejects their stock contribution and flags the mapping rather than inflating inventory. The settings panel exposes shipment quantity, mode, ETA and add/remove batch controls; it does not create procurement orders.

### Independent Buybox page, five-minute segments and diagnostics
Buybox now has its own `/buybox` route. That page does not expose pricing ranges or start-pricing buttons; the pricing page does not expose collection controls. Both reuse SKU display only. Buybox defaults to a five-minute target interval and ten-SKU UI segments, with selected/current-segment start, named segments and segment filtering. Stopping a segment preserves its label and does not change any pricing rule. A bounded two-job collection queue avoids an unbounded browser fleet; valid recent official offer snapshots reduce duplicate Seller API reads. Successful cycles target start-to-start intervals; failed/forbidden/challenged reads back off and show actual next-attempt times. Queue overload cannot guarantee five-minute refresh for an unlimited enabled SKU count.

Collector results retain safe HTTP status, page title, query-free requested/final URLs, card count and bounded error type. The error classifier distinguishes certificate/network failures, missing pages, redirects, selector failures, unstable prices, variant mismatch, and explicit challenge evidence; a bare 403 is not labeled CAPTCHA. These fields diagnose actual production failures rather than reporting every failure as a generic blocked page. No page HTML, cookies, API tokens or private authorization headers are stored. Variant failures prevent automatic price writes. Public-page restrictions are not bypassed.

### Production diagnosis and fully separate collection API
The production read-only diagnostic for a previously failed SKU returned HTTP 200, the original PLID address, page title `Just a moment...`, and zero Buybox cards. This is a validation interstitial rather than product HTML. It does not demonstrate a Seller API credential failure. The collector now exits on that title before selector waits or variant navigation; the UI labels the condition explicitly. Previously saved diagnostics are reclassified during read without requesting the blocked page again. The failure remains unresolved as a data-access restriction; scheduling alone does not obtain quotes.

The `/api/buybox` collection API reads its own SKU catalog and monitor state without reading pricing rules or computing profit floors. Existing collection URLs remain compatible. Starting, stopping, segmenting and one-shot reading affect only `_pricing_monitor:` state. Pricing remains behind its own endpoints and explicit activation. Tests confirm the collection catalog remains usable when the pricing floor calculator is unavailable. Diagnostic raw errors strip URL query tokens before persistence.

## 恢复任务校正：功能关联，调度独立
Buybox与调价合并在同一工作区，采集和调价开关独立，继续使用同一商品与SKU绑定。Buybox调度器保存完整行情快照；调价调度器只读取最近10分钟内的成功快照，不再自行发起网页采集。失败、旧版本不完整快照、SKU不符或行情过期均保持现价。库存列表仅显示本店库存大于0的SKU；停止全部调价仍由后端处理所有规则。

## 原本机采集器重新接入

原程序采集器 `takealot-scraper/collector.py` 使用本机 `browser_profile/`，保存登录与配送地址。网页系统现在兼容原协议 `GET /api/collector/products` 和 `POST /api/collector/offers`，采集器认证使用网站的服务访问密钥（不是 Takealot 商家密钥）。

在原采集器的启动配置中，将 `PP_API_BASE` 改为 `https://takealot-selector-production.up.railway.app`，将 `PP_COLLECTOR_KEY` 设置为网站设置里的服务访问密钥。保留原 `browser_profile/`，不要迁移到源码或上传登录 Cookie。暂停原实例后，在原目录运行 `python collector.py --interval 30 --concurrency 2`。后台决定每个SKU是否到达5分钟采集时间，每30秒轮询仅取到期任务，不每30秒抓所有产品。网页采集开关及分段设置仍生效，调价开关不受连接影响。

网页会显示“原本机采集器已连接”。每个任务只接受一次回传，暂停或改设置会使在途任务失效，采集回传不会开启调价。近期没有采集器轮询时，界面显示本机未连接，云端继续按计划采集。

云端现在同样保存浏览器档案（默认在数据库持久化目录下 `buybox_browser_profile/`），避免每轮丢掉配送及会话状态；首次云端会话仍可能遇到验证。云端没有原机器的已验证会话，接口上线不等于验证问题已解决。需要原采集器主机重新指向上述网页后端才能复用原会话。

## 打开网页自动采集（浏览器助手）

已增加 `tools/browser-collector/` 的 Chrome/Edge Manifest V3 扩展，下载文件位于网页“Buybox与调价”的“打开网页自动采集”面板。只需安装一次；扩展在系统网页打开时轮询到期任务、在同一浏览器的Takealot标签页采集、上报共享行情。页面关闭或全局暂停后停止本机采集，未执行的任务释放租约。原后台采集与调价开关独立，单SKU明确停采也不会被打开网页操作覆盖。

网页采集器沿用原程序的严格报价卡选择器、连续两次一致的价格确认、配送现货筛选及变体核对。变体不明确时失败返回，不把默认规格、广告或推荐商品的价格用于调价。遇到验证会保留商品页供本人完成，不导出Cookie或自动处理验证。当前扩展版本已通过JS语法及模拟解析测试；真实用户Chrome/Edge中的安装、真实商品变体、多卖家报价及验证后恢复尚待实测。因此不能宣称原Python采集器所有路径均已复现，不能宣称采集成功率已达标。

已修复调价提交等待期的并发暂停窗口。相同Buybox价格不再推断卖家归属；缺少身份核实时保持现价，试涨需要明确归属证据。旧采集器只按价格相等生成holding标记的快照不能作为身份核实证据。

## 后续复核待办

用户已授权后续项目修改检查通过后自动合并部署，已创建每小时后台复核任务。优先接续：用户浏览器端的真实安装与采集验证；原Python采集器与浏览器版本的变体选择、Other Offers展开和配送文案差异；实际卖家身份信息和库存证据；多客户端租约、公网权限边界、浏览器休眠及断连恢复。没有证据不盲目重写或制造改动。不得开启真实调价、修改真实售价、公开密钥和浏览器Cookie。接口或代码上线不能代替实测通过。

## 2026-10-14 行情时效与断连恢复复核

修复整批上报把旧报价当成新报价的问题：新浏览器助手 v1.0.1 上报逐商品 `collectedAt`；后端只接受在任务领取与服务端接收之间、带时区的采集时间。原Python采集器缺少逐商品时间时，使用任务领取时间作为保守下界，不能因为延后上报把行情重新变新。超过10分钟的行情仍不能用于调价。

过期任务按原领取配置进行条件恢复，停采或修改配置后不会被旧任务恢复覆盖。SKU不匹配的回传不会消耗有效任务凭据，正确SKU仍可回传。过期任务的迟到结果不能覆盖新任务。补货接口测试使用业务代码相同的中国日期，避免执行环境在南非/UTC日期不同的时段误报失败；未更改补货业务时区或采购比例。

真实Chrome/Edge安装、登录配送状态、变体与多卖家报价等端到端验证仍未完成。本轮没有读取密钥或Cookie，没有调用真实改价接口。后续优先核对真实浏览器采集和原解析后备路径，接口上线与模拟测试不能替代真实验证。
