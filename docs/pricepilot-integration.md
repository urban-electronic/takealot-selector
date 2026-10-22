Warning: truncated output (original token count: 46753)
Total output lines: 1617

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

## 当前接续约束与阻塞（2026-10-14）

用户最新明确要求：只使用网页版，不再安装本机程序或扩展。此前交付的启动包和GUI不构成该目标的完成证据，不再以用户安装、重复输入服务密钥或补交已有源码作为默认接续路径。保留同一系统共享行情、Buybox与调价分别调度启停。

已直接复核用户原源码：`SOURCE_MANIFEST.md` 将前端 `pricepilot/web`、后端API/worker和 `takealot-scraper/collector.py` 分开列出；原 `server/src/routes/collector.ts` 由常驻采集器领取任务并回传行情。用户可以只看网页，后台仍需有运行中的采集服务。原包明确排除了部署环境配置、浏览器档案和运行日志；不需重发源码，也不得索取Cookie或在公开仓库保存凭据。

已尝试打开用户提供的原服务入口，浏览器返回502/connection closed，刷新一次仍同；未到达登录页，未使用用户凭据。用户确认自己同样无法访问，但原运营方可以。不能据此宣称原服务停机、确认是Tailscale配置故障，或认定原后台仍在成功采集。

当前缺少：原运营方确认的服务运行主机、API与采集器启动方式、脱敏后的Serve/Funnel/反向代理配置及双方可用的授权访问入口。获得后先读取最近成功采集时间与SKU报价证据，再决定复用原后台或部署同等服务；不擅自扩大网络暴露、不关闭认证。以真实SKU/变体报价读取并回传当前网页为完成标准。安装包、模拟测试和部署成功均不能替代该验收。在访问与部署信息补齐前暂停自动改动，避免偏离网页版目标。

## 原生采集器可运行交付

Windows下载包增加独立 `start.cmd` / `login.cmd` 和 `launcher.py`，Python 3.10以上环境可双击准备依赖并选择原浏览器档案；密钥仍只隐藏输入、不保存。当前会话仅能修改云端工作区，未接入用户Windows安装通道，不能把启动包上线表述为已安装到用户电脑。Windows脚本需在真实Windows验证；本轮验证Python启动参数和打包，不声称已在Windows运行。

用户实测出现旧Playwright 1.47.0依赖的greenlet源码编译失败（要求Visual C++ 14.0）。原生包更新到Playwright 1.64.0/greenlet 3.5.6，升级pip并仅允许预编译wheel安装，按Python解释器版本分开运行环境；保留浏览器档案和选择路径，避免重复使用失败或不同解释器的旧venv。此变更只影响本机包，不修改云端依赖。安装通过后仍需要真实商品采集验证。

用户后续截图已显示Chromium安装成功，但采集请求返回HTTP400，根因尚未确认。新增经过现有认证/店铺校验的只读 `/api/collector/status` 预检（不领取任务、不触碰心跳），在浏览器启动前执行。客户端只显示白名单内的认证/店铺错误说明，不打印任意代理响应、密钥或Cookie；未知400保持为网络/地址待诊断，不宣称已解决。空任务明确显示等待调度，不再静默留空白浏览器。

按用户要求补入原生采集操作界面 `gui.py`，集中提供首次连接设置、档案选择、登录/配送、开始/暂停和打开系统；不再要求日常在命令窗口输入或操作。可选Windows当前账户DPAPI加密保存服务密钥，仅在本机解密，不上传。GUI以独立进程运行原采集器，暂停/关闭通过停止文件取消任务并释放未完成租约；采集目标与调价开关仍在同一网页系统独立调度。真实Windows GUI、DPAPI和真实报价验证仍需用户本机结果，HTTP400未知原因没有被此界面改动自动修复。

`tools/native-collector/collector.py` 已补齐本机运行入口，下载包由 `scripts/package-native-collector.py` 构建，包含与后端逐字一致的 PricePilot 解析核心。使用可见持久浏览器并支持 `--profile` 指向原 `browser_profile`；`--login` 仅进行本人登录/配送设置。任务由网页独立采集开关调度，每30秒领取、最多2个页面并发、逐商品回传实际时间、失败及取消释放未完成租约；没有调价写入路径。Windows/macOS单实例锁分别采用系统文件锁。服务访问密钥只从环境变量或隐藏输入读取，不写入包或档案。

本机包直接复用原解析核心的变体参数/点击与确认、双读/三读报价检查及其他卖家读取；网页扩展仍存在解析差异。此交付需在拥有原档案的电脑运行，当前工具环境未接入该电脑，不能声称实际报价已读取。后续验收应以至少一个真实SKU、对应变体、网页回传价格与本机商品页一致为证据。

用户已授权后续项目必要修改检查通过后自动合并部署。优先接续上述网页版后台服务接入阻塞；随后核对真实商品变体、Other Offers与配送文案、卖家归属和库存证据。没有证据不盲目重写或制造改动。不得开启真实调价、修改真实售价、公开密钥和浏览器Cookie。接口或代码上线不能代替实测通过。

## 2026-10-14 行情时效与断连恢复复核

修复整批上报把旧报价当成新报价的问题：新浏览器助手 v1.0.1 上报逐商品 `collectedAt`；后端只接受在任务领取与服务端接收之间、带时区的采集时间。原Python采集器缺少逐商品时间时，使用任务领取时间作为保守下界，不能因为延后上报把行情重新变新。超过10分钟的行情仍不能用于调价。

过期任务按原领取配置进行条件恢复，停采或修改配置后不会被旧任务恢复覆盖。SKU不匹配的回传不会消耗有效任务凭据，正确SKU仍可回传。过期任务的迟到结果不能覆盖新任务。补货接口测试使用业务代码相同的中国日期，避免执行环境在南非/UTC日期不同的时段误报失败；未更改补货业务时区或采购比例。

真实Chrome/Edge安装、登录配送状态、变体与多卖家报价等端到端验证仍未完成。本轮没有读取密钥或Cookie，没有调用真实改价接口。后续优先核对真实浏览器采集和原解析后备路径，接口上线与模拟测试不能替代真实验证。

调价列表的本店价格和库存按来源时间选择：卖家接口整店快照较新时，不再被旧调价规则中的价格或库存覆盖；调价任务或人工核验完成得更晚时，才展示该次逐SKU官方读取结果。这样旧规则里的库存 `0` 不会把后来恢复库存的SKU从页面错误隐藏。无时间戳的历史规则按旧数据处理。

浏览器助手 v1.0.2 在已认证请求上发送固定 `X-Collector-Client: browser_extension` 标识；没有该标识的原PricePilot协议按本机采集器处理。后端将两者保存为独立心跳记录，分别刷新90秒与15分钟连接租期；两者同时在线时，短租期不会覆盖长租期，任一仍在线都会阻止云端采集器重复接管。该标识不替代API认证，也不包含Cookie、登录态或密钥。

“全部紧急停止”同时覆盖已启用规则和处于 `write_pending` 的提交意图；即使在PATCH前等待窗口点击，也会通过修订号变化使本轮写入失效。对可能已经开始的网络请求仍保留 `write_pending`，要求读取平台现价核验后再重新启动，避免把不确定结果当作未改价。

待核验改价只有在卖家接口返回的 `offer_id` 与 SKU 都和原规则绑定一致时，才会用平台现价清除 `write_pending`。SKU缺失或已变化时继续暂停并保留原记录，要求重新同步绑定，不能用另一商品的价格错误解除保护。

## 2026-10-14 Railway 实机接入与纯网页版操作入口

已用用户指定的正确账号进入现有 Railway 项目并检查生产容器。服务当前为 Dockerfile 构建，单副本、US West，Serverless 关闭，资源上限8 vCPU/8 GB（不是实际用量或最低预留）。现有5 GB持久卷挂载 `/data`，容器内 DB_DIR 确认为 `/data`；Chromium/Playwright 1.47.0 已安装，`buybox_browser_profile` 目录存在。仅检查目录存在性，没有读取Cookie或服务密钥。

生产数据库只读检查：2个监控，均暂停、均无成功记录。最近失败分别为2026-10-14 00:54 UTC和2026-10-13 17:17 UTC，两者页面标题均为 `Just a moment...`，第二条历史记录误标为 `price_not_found`。统一工作区此前直接使用旧监控值，未像独立Buybox列表重新分类；现统一复用 `displayed_monitor`，不改写数据库或伪造一次新采集。

新增纯网页版入口：Buybox与自动调价页面的“云端 Buybox 采集”。选择本店SKU打开服务器上的原持久浏览器，通过内存JPEG画面、点击、有限按键和配送文字输入操作；无需用户安装程序或扩展，不要求再次填写服务密钥。服务器用本机Xvfb运行可见Chromium，不开放VNC/桌面端口，不添加验证码自动处理、指纹规避、代理或绕过脚本。两条监控已有明确验证拦截证据，本轮不通过反复访问或替代网络路径尝试绕过。

所有浏览器接口位于 `/api/buybox-browser/`，强制网站密钥鉴权（即使未配置API_KEY也拒绝访问，Collector Key不获此权限），校验默认店铺及本店未归档商品，不能传任意目标URL。画面和操作还需当前随机会话令牌，令牌仅在前端内存和请求头，不进URL或本地存储；画面禁止缓存，不存文件，文本不记日志。限定主页面为Takealot HTTPS，阻止本地/内网、购买/购物车及账户操作路径。5分钟无操作或15分钟绝对期限后关闭释放；普通帧刷新会计为活跃。它不是通用远程桌面，也不支持账号登录。

操作窗口与后台采集共享同一个线程锁、持久档案和配送状态，避免并发打开同一档案。打开时不更改监控/调价开关；“关闭并保存会话”只释放浏览器，“保存并采集此SKU”关闭后调用现有单次只读采集调度。采集仍沿用原PricePilot SKU/变体匹配、报价双读/三读、库存过滤及任务保护；后台仍独立调度，与调价共享行情。身份不确定、行情缺失/失败/过期的原有保持现价逻辑不变，没有开启真实调价。

验证边界：单元测试覆盖鉴权关闭、Collector Key拒绝、店铺隔离、请求参数、令牌、单会话互斥与释放、画面不缓存及历史误分类；前端构建通过。模拟浏览器测试不等于真实SKU报价成功。仍需用户在网页打开云端商品页，按网站提示本人处理验证并设置实际南非配送位置，再点击保存并采集；网站也可能拒绝云端会话。不得把该入口上线、空白浏览器烟雾测试或浏览器档案存在说成真实采集已成功。下一轮先读最新线上结果，不恢复推荐本机安装路径。


## 2026-10-14 后台采集与精简展示

用户明确要求日常操作无需打开云端浏览器。现有 Railway 服务在应用启动时启动 buybox-monitor 后台线程，单次采集及定时采集不依赖网页画面；本次不另建采集系统，也不改变采集/调价独立启停语义。

主工作区移除常驻浏览器面板；页面底部“采集维护（高级）”默认关闭，展开后才加载维护组件，关闭时卸载并按现有清理逻辑释放操作会话。每行错误改为短原因与可展开详情，保留真实错误和最近尝试时间。网站验证错误不再要求用户在主页面反复操作浏览器。

这次是界面简化，不代表验证拦截解除或真实报价成功。仍需保留线上实际成功证据的验收要求；不通过重复请求、切换网络、指纹伪装或替代受保护路径尝试绕过网站限制。真实调价没有开启。

本轮从 Railway 控制台只读核对：SKU 9902348624058 最近一次真实尝试为 2026-10-14 05:17:03 UTC（北京时间13:17），仍为 HTTP403 / Just a moment... / site_challenge，无成功时间；另一条记录仍是历史失败。此次未重复访问受拦截商品。维护面板收起期间若打开请求才返回，会立即关闭刚创建的会话，避免隐藏面板占住采集浏览器。

## 2026-10-14 官方接口实测与原始轮次同步

已在 Railway 当前生产容器通过现有官方读取函数核对 offer 234862405 / SKU 9902348624058。请求成功、身份匹配；返回字段为本店售价/库存/销量等，没有 Buybox、竞品卖家或竞品报价字段。只输出字段名与商品标识，没有输出密钥、Cookie或订单信息；未调用价格写入。不能用本店 selling_price 冒充 Buybox。

进一步核对原 PricePilot collector.ts，发现失败上报也更新 offersFetchedAt，同时旧 buyboxPrice 保留。因此此前外部报告的“22个时间更新且非空价格”不足以独立证明22个当轮采集成功。须核对原始当轮结果或结构化采集日志。

新增 tools/pricepilot-relay/selector_relay.py，仅转发原守护进程已有的原始轮次结果，不另起浏览器，不访问受限站点，不从 Product 表拼接旧价格。接收端对 pricepilot_relay 标记严格验证来源时间，接受领取租约之前但10分钟内的报价并保留原时间；时间缺失、过期或在未来时不以接收时间刷新行情。仍使用现有SKU、租约及暂停校验，不扩大接口鉴权范围，也不认可同价即持有。

验证：22项适配及接收接口测试通过。未接入原 Mac 进程，尚无真实同步记录；具体接入步骤及验收边界见 tools/pricepilot-relay/README.md。原电脑关闭后独立采集仍未解决。下一轮先确认原进程是否已接入及真实processed数量，不把接收端部署等同于真实采集成功。


### 本轮部署后补充：发现一次真实云端成功记录

部署后只读检查生产数据库发现：SKU 9902348624058 的 last_success_at 为 2026-10-14 05:27:32.735689 UTC（北京时间13:27:32），buybox_price=269。保留的成功 snapshot 包含 HTTP200、正确商品标题、相同 PLID100687738 的请求/落地链接、buybox_cards=1、buyboxSeller=Urban Electronics、error为空。这是原网页后台的历史成功快照，不是本轮 relay 上报，不是本轮新发起的商品请求。此前“从未取得过报价”的表述需按此证据纠正。

同一SKU最近失败为05:56:34 UTC，HTTP403 / Just a moment... / site_challenge；当前采集仍暂停、status=error。另一SKU仍无成功记录。结论是单SKU曾成功、随后被验证拦截，稳定采集未验收，不能把旧成功当成目前已恢复。两次读取均不改变报价/开关、不读取Cookie。接收适配部署后来源时间保留与缺失时间拒绝的生产纯函数检查均通过；仍待原机器接入 relay，或取得网站接受的持续数据来源。


## 2026-10-14 按用户缩减要求：打开程序提交一轮

用户要求仅在程序打开时采集。全局App在首次认证成功的店铺列表返回后，对自营店调用 POST /api/buybox/page-open；不是仅打开Buybox子页面才触发。每次App挂载/刷新请求一轮，应用内路由切换不再重复请求，不需要扩展、安装包或云端浏览器操作。顶部仅显示简短排队提示及“查看采集”链接，不能把“已提交”显示成采集成功。

新增接口仅对已配置卖家接口的自营店排队有库存且有商品链接的SKU，设 request_once=True / web_requested=False，使用现有后台worker；不写调价规则，也不新开启定时采集。已有显式自动采集开关保持原值。已暂停、正在运行/排队、60秒内重复打开及网站失败冷却中的SKU不会重复提交；旧扩展专属等待任务可转换为后台一次任务，不写入扩展心跳，避免让后台误以为必须等待扩展。正常完成或失败后request_once都会清除，一次模式不持续重试。

排队只使用短数据库事务和比较原值更新，暂停/租约逻辑沿用原保护。测试覆盖无库存/暂停/冷却/重复打开/旧扩展队列恢复/权限边界及worker一次完成后即便到期也不重复请求。实测报价验收仍需区分排队成功与真实页面成功；网站验证限制不会因为缩减为一轮自动消失，不得虚报完成真实采集。关闭网页后服务器可能继续完成已经提交的这一轮，不建立后续轮次；本轮不要求网页关闭时终止正在进行的网络读取。


## 2026-10-14 用户接受本机验证：核对原操作模式

原10月12日源码已保留，不要求用户重复提供。对照发现迁移解析器在domcontentloaded后立即检测Just a moment标题并退出，而原版先等待报价区域。修复为一次最多12秒的被动页面自然加载等待；持续验证则返回site_challenge，不刷新、不交互验证、不新增指纹/网络规避。原初始403仅在标题自然恢复后继续检查真实报价卡，其他HTTP失败保持拒绝。这个修复不等同于网站一定接受会话。

本机操作界面默认采集一轮（--once），新增按SKU回传的HTTP状态、页面标题、报价卡数等诊断；URL去查询参数，无Cookie/密钥/页面全文。接收端区分processed（处理了成功或失败）和successful（真实采信报价），客户端不再将processed当成功。保留原解析器的可能我方排除标记，避免一律isMine=False把我方价错当竞品；持有归属仍不确认。

152项后端测试通过，包括初始页面仅等待一次、持续验证停止、诊断脱敏、带旧价的验证页不得采信和原任务租约保护。工具源代码及下载包同步更新。尚未在用户Chrome/Edge和Windows桌面运行，助手没有原电脑桌面操作通道；不能称已复现真实采集。应由用户本机执行一轮，核对成功报价/采集时间/目标SKU。没有自动开启真实调价。

## 2026-10-15 变体门控修复：推荐卡 teaser 不再被当作变体控件（SKU 9902348621330 实测）

故障样本：SKU 9902348621330（PLID97512496，标题 "4K Thump action camera - professional package for motor, biking, skydiving"）。真实商品页 HTTP 200、报价卡 1、报价 R 1,999 / Urban Electronics / 现货，程序却报 `variant_unmatched`。

根因（本机用户 profile + Playwright 1.64.0 实测复现，A/B 对照）：商品名中的 " - " 是标题分隔符而非变体后缀，旧逻辑据此尝试 colour_variant/size_variant 切换。进一步勘查发现该页变体选择器确有命中，但全部位于 `[class*="product-card"]` 推荐商品卡内——Takealot 用 `colour-variant-icon-text` 渲染 "More Colours" teaser；主商品区实测 0 个变体控件，页面自身 API 响应也无任何 variant/colour 结构。单报价商品由 PLID 唯一确定，不存在默认/目标变体歧义。

修复（`backend/services/pricepilot_collector.py`，未重新设计系统）：
- 新增 `main_variant_elements()`：变体控件元素级排除推荐商品卡（`el.closest('[class*="product-card"]')`）。
- 新增 `_VARIANT_PROBE_JS` / `page_variant_controls()`：宽松探针统计主商品区控件数，刻意取宽（误报方向安全：继续尝试切换，失败仍保守上报；漏报会把默认规格当目标 SKU 报价采回，不可接受）。
- 变体切换前置门控：仅当主商品区真实存在变体控件时才尝试切换；无控件时清空后缀直接读取唯一报价，并上报 `variantControls` 计数。
- 选中态补判与点击路径同样只用主商品区控件——卡内 swatch 文案可能恰好等于变体后缀，点击会跳到推荐商品（采错价）。
- 核心区块就绪等待由宽 text/class 选择器改为 `_CORE_BLOCK_JS`（buybox 卡或主商品区变体控件），避免推荐卡 teaser 让等待提前返回。

诊断链路同步透传 `variant_controls`：本机 collector diagnostics、`/api/collector/offers` 白名单、云端 `automatic_pricing.collect`、`collection_diagnostics.classify`（variant_unmatched 消息带控件计数，便于人工确认）。未取消 SKU 身份校验，未开启真实调价，未上传密钥/Cookie/profile。

实测验收（用户本机、用户 profile、只读，无登录/购买/后端调用）：修复版解析器对该 SKU 返回 `variantControls=0`、无 `variantSwitchFailed`、`buyboxPrice=1999.0`、`buyboxSeller=Urban Electronics`、`stockMapped=in_stock`、报价卡 1、URL 未被改写；同一会话旧解析器仍复现 `variantSwitchFailed=true`。测试：`test_pricepilot_variant_gate.py` 7 项（含推荐卡 teaser 不计数、不点击两个新用例）及相关诊断/桥接/云端分类用例共 43 项通过；`test_scraper_fallback.py::test_site_shell_is_not_a_success` 为既有网络抖动项（单独运行通过，与本次改动无关）。模拟测试不等于真实采集验收：其余 SKU 与网站验证限制仍需按既有边界逐轮核对。

## 2026-10-15 Codex 复核 P1 修复：探测失败不得降级为"无变体"

Codex 首轮复核（CHANGES_REQUESTED）指出合并阻塞：`page_variant_controls` 将探测异常吞掉返回 0，`main_variant_elements` 将元素作用域判定异常直接跳过，"未能判断"被当作"确定无变体"，从而清空变体后缀并放行默认变体报价——正是漏报方向（把默认规格当目标 SKU 报价采回），不可接受。Codex 提供了可运行失败用例（变体查询抛 detached、探针抛 execution context destroyed，旧实现返回 buyboxPrice=1234 且无任何失败标记）。

修复：`main_variant_elements` 返回 `(elements, complete)`，作用域不可判定时 complete=False；`page_variant_controls` 遇不可判定或探针执行失败返回 None（unknown）。门控仅对"确定 0"清空后缀；None 时保留后缀、重试一次后继续切换校验，无法确认则上报 variantSwitchFailed，绝不按无变体放行。诊断字段 `variantControls` 可为 None，各消费方均已按 int 类型守卫。

新增回归 3 项（Codex 失败用例、元素作用域不可判定用例、离线真实 DOM 用例——用 page.set_content 合成"主商品区控件+推荐卡 teaser"页，执行真实 _VARIANT_PROBE_JS/_CORE_BLOCK_JS 与作用域过滤，浏览器不可用时跳过）；全量后端 166 项通过。修复后第三次本机实测 SKU 9902348621330：variantControls=0、无 variantSwitchFailed、R1999/Urban Electronics/现货、URL 未改写，无回归。native collector 打包已随本轮重新生成并校验字节一致（SHA-256）。

## 2026-10-15 Codex 运行复核与报价来源归因

基于 main 37cf155（PR90）接续；PR89运行保护、PR90图片组件已上线，协作仍使用PR87，禁止合并其旧后端覆盖main。后端全量221通过、1项真实Chromium DOM用例因执行环境未安装浏览器跳过；上轮图片组件26项断言复测通过，不代表实际CDN或采集端到端通过。

新复现：云端成功会继承旧本机来源，扩展回传误标本机采集。修复成功报价来源与最后尝试来源分离，失败保留旧有效报价的来源及时间，不回填历史未知来源、不刷新旧快照；细节见[报价来源验收](review/2026-10-15-quote-provenance.md)。来源标记只代表协议/执行路径，不证明硬件或卖家身份。

仍未收到WorkBuddy正式Windows GUI同轮回传网页的证据，不能宣布稳定采集或GUI端到端通过；其需使用现有已保存配置，不读取或发送密钥/profile，在正常一轮日志中提供SKU、脱敏诊断、successful数及生产同轮时间/来源。网站验证失败仍须与变体错误区分，不反复访问已确认拦截页面；不得开启真实调价。未新增生产交易或测试行情。

## 2026-10-15 10:45 Codex 独立生产复核：两SKU新云端报价，Windows闭环仍待验

环境：main 65d6883（PR91），Railway部署660a787e。读取现有生产监控记录，事务结束rollback/close，未触发新采集、交易或改价；未读取密钥、Cookie或profile。WorkBuddy第12轮报告单SKU入队后正式CLI一次运行领取0任务；Codex在PR87要求其补充精确时序，不能称GUI按钮验收通过。

| SKU / PLID | 成功时间（北京时间，2026-10-15） | 有效报价记录 | 页面诊断 |
| --- | --- | --- | --- |
| 9902348621330 / 97512496 | 09:38:51（01:38:51.797416Z） | R1999，Urban Electronics | HTTP200，报价卡1，主商品变体控件0，目标/最终PLID一致，无错误 |
| 9902348624058 / 100687738 | 09:58:09（01:58:09.012766Z） | R269，Urban Electronics | HTTP200，报价卡2，主商品变体控件0，目标/最终PLID一致，无错误 |

两行均为status=success、source/last_attempt_source=cloud_browser、quote_time_source=cloud_completion；holding_buybox=null，只确认价格相同，不宣称我方身份或持有归属。enabled=false/request_once=false，不存在本次开启持续采集或调价。它们是生产新成功记录，不能再说两个SKU均未采到；也不能据两次成功宣布持续稳定。核验未重新访问Takealot页面。

本机心跳当前last_seen为02:03:09.765400Z（10:03:09），晚于上述两条成功记录；这是最近心跳而非完整历史，不能单独证明此前无心跳，也不能据一次products=0推断本机永远领不到任务。当前代码status预检不写心跳，products领取才写心跳；cloud worker在轮次选择任务前检查connected。后续需用入队/启动浏览器/首次products/云端开始结束的完整日志验证竞争，不先关闭云worker或删除网页自动一轮。

验收结论：新云端行情记录与来源已独立核对；受测后端221通过/1 Chromium缺失跳过，组件26断言通过。Windows正式GUI→有效报价回传→网页同轮展示仍未验证；CLI零任务不算该链路通过。03:09无来源旧快照仍不追认来源。Mac原程序若提供对照，仅需相同SKU的一轮脱敏中间证据，不要传全部业务数据或会话profile。

## 2026-10-15 PR94本机持续模式候选审核

WorkBuddy新增可选GUI“常态采集”，映射既有CLI持续模式；Codex在最新main独立提取，保留一轮按钮、加无效模式保护和准确暂停/启动说明，同步README/下载包与8项GUI回调回归。见[完整审核与生产证据](review/2026-10-15-native-continuous.md)。不直接合并旧review基准，不修改服务器调度或用户开关。

当前只读生产吸尘袋16:36是验证页失败、旧R269保留；相机16:41云端R1999成功。WorkBuddy声称持续演示曾本机成功，但当前快照无法印证，已要求精确历史证据。入口/打包通过不等于GUI用户会话端到端或持续稳定，身份核验任务仍未完成；禁止新增真实调价、绕过验证或导出凭据。



## 2026-10-15 18:34 候选会话检测复核

061148c543候选隔离执行复现普通商品login参数/Sign In标题误报auth_expired，事件触发后同批等待任务仍开始；GUI终止提示还会被后续日志覆盖。已在PR94评论6190136045交接，未发布候选后台/无头/守护改动。最新生产相机17:03云端R1999成功后17:05本机验证页失败，吸尘袋仍16:36验证页失败、旧R269；本机稳定/GUI同轮及身份仍未验收。最新main允许老库存不限次导出，修正遗留测试而不恢复旧限制；229通过/1Chromium跳过、页面17断言、build通过。详见[本轮验收](review/2026-10-15-auth-candidate-review.md)。


## 2026-10-15 主界面及分页面视觉同步

按用户要求统一导航、标题/卡片/表单节奏，保留各业务页面及双栏装箱工作台。采购/设置/商品详情的窄窗口溢出已复现后修复；真实Chromium隔离API进行11页×9窗口99次渲染、菜单/弹窗/补货面板及装箱跨页选择检查，无页面错误。后端235通过（测试Chromium已可用，无跳过）、Buybox/图片17+26断言、构建通过。同步0a809bb并发装箱导出改动，没有回退自定义箱号与库存语义；没有修改采集/调价业务路径或触发生产交易。验收方法与限制见[窗口显示复核](review/2026-10-15-workspace-layout.md)。布局通过不代表真实本机采集或身份任务完成。


## 2026-10-15 19:40 接管候选复核

b005f783a5候选独立执行未通过：守护入口参数冲突，successful1但首次最近成功标记缺失，旧后端回退本机/扩展/云端来源。120秒无成功不能证明租约死锁，要求按client和在途/空闲/验证暂停提供证据；新增规避参数要求删除，不执行。已在PR94评论6191231160、PR87评论6191257199交接，未采纳正式业务代码。当前51相关测试、页面17断言、99窗口检查通过；本轮控制台被另一会话占用未抢占，未取得新的生产监控数据，真实GUI/身份仍待证据。详见[独立运行验收](review/2026-10-15-takeover-candidate.md)。

## 2026-10-15 原Mac运行证据与第二轮视觉统一

已审阅并保留mac-runtime-trace提交888d166的[原始报告](review/2026-10-15-mac-runtime-trace.md)。相机不在原默认名单、宽选择器受推荐区干扰、错误变体默认价仍会回传、同价holding不证明身份，是本次关键区别。继续保留主商品区门控/双读/租约/暂停和独立调度，不照搬错误报价或开启真实调价。库存假设需结合已有stock6/11且queued的生产证据检验；已通过PR87评论6194342863向Windows WorkBuddy发出WB-COLLECT-TRACE-02任务。

统一表头与辅助层级、弹窗遮罩/标题/操作区、装箱窄窗单列表单；资料核对预览改为局部横向表格，避免320px下逐字换行。99窗口/53后端/17+26组件断言及构建通过。原Mac结果来源、事实与推测、Windows未验收限制详见[审核与交接](review/2026-10-15-mac-trace-followup.md)。

## 2026-10-15 按原程序核验本店价格

按用户22:39要求接入原程序的价格推定：同价推定持有，异价以本店官方API当前售价复核，持有转失去连续两轮确认。新price_ownership与卖家身份分离，展示对照价/来源/时点，不用于已验证持有或直接调价；历史比较明确标历史，不补造成功时间。本机有效报价先入库、响应后核验，确切值CAS防暂停/新报价被覆盖。测试与待WorkBuddy本机验收限制见[价格逻辑对照](review/2026-10-15-price-based-holding.md)，未开启真实调价。

## 2026-10-15 23:33 自主采集候选实际失败

WorkBuddy新头2be9efde7c隔离执行失败：守护参数冲突、零库存SKU被自主领走、successful1但首次成功标记行数0，且回退来源/价格推定与变体诊断。已PR94评论6195960947退回，未采纳业务代码。正式版恢复测试Chromium后245后端/99窗口通过；生产只读29项最新成功仍17:05（北京时间），两目标最新尝试仍为历史验证页失败，23:37有心跳而无新报价。当前排队不冒称新执行失败或成功；Windows同轮未完成，须补实际执行证据。详见[明确验收记录](review/2026-10-15-autonomous-candidate.md)。

## 2026-10-16 领取中取消最小修复

PR103候选99d6bc5的15项测试通过，但补测发现吞领取错误及30秒截止后迟到租约丢失，未采纳。Codex独立修正main领取中取消：保留在途HTTP任务、异步等待后释放、重复取消安全、错…21753 tokens truncated…lfcheck 全绿（新套件自动纳入）。

### STORE-SWITCH-02 当前店铺被暂停后不再静默跳店（2026-10-21）

基线 `cff1ad684d`。该行为改变来自并行提交（#161，`chore: sync local tree to
remote cff1ad684d`），原提交只带代码与断言、没有文档记录，这里补记。

- 症状：STORE-SWITCH-01 让下拉保留「当前店铺（已暂停）」，但 `App.tsx` 的
  `loadStores()` 里还留着 `if (activeRows.length && !activeRows.some(...))` 分支，
  会把 `activeStore` / localStorage 一起改成第一家运营店。也就是说：用户在店铺管理
  页把正在用的店停用后，下一次打开应用界面和请求一起悄悄换到另一个店，用户在不知情
  的状态下继续装箱、改库存、看行情。
- 改动：删掉该分支，只保留「默认店铺首次打开时提交一轮 Buybox 采集」那条；当前店铺
  暂停或缺失时由 `storeOptions()` 保留下拉选项并标注，等用户明确选择再切。
- 复现：`scripts/test-store-switcher.cjs` 增加 2 条源码级断言（`App.tsx` 不得出现
  `setActiveStore(activeRows[0].id)` / `setActiveStoreId(activeRows[0].id)`）。把分支
  加回去即转红。
- 复测：全量 selfcheck 全绿。

### TEST-SCRAPER-HERMETIC-01 采集回落用例不再连线上站点（2026-10-21）

WorkBuddy；基线 `cff1ad684d`。

- 症状：`backend/tests/test_scraper_fallback.py` 的三个用例只 patch 了
  `_scrape_with_curl_cffi`，没挡渲染器。在装了 Chromium 的机器上（本机 venv、以及
  装了浏览器的后端 job）`scrape_product()` 会真的打开 takealot 商品页，拿到
  R699 和主图后走 `finish_official()`：`data_source` 变成 `takealot`、`warnings`
  为空，三个用例全红（本机 47 秒跑完 17 例，就是在等真网络）。反过来，CI 里数据中心
  IP 被 Cloudflare 挡住时同一个用例又「绿」——那次通过是网络凑巧，不是断言成立，
  属于最坏的一种假通过。
- 改动：新增 `_blocked_browser()`，把 `_try_scrape_with_browser` 一起挡掉；三个用例
  的断言一条没动、没删、没放宽。`test_partial_real_product_does_not_require_price_or_playwright`
  的名字要的是「只有部分字段也照样成功并给出缺项提示」，不是「本机没装 Playwright」，
  所以用显式桩表达前提比依赖环境更准确。
- 复现：不挡渲染器时本机稳定三红；挡掉后 17 passed / 1.1 秒（原 47 秒）。
- 复测：后端全量 425 passed / 1 skipped。

### CREATE-SILENT-READ-01 新建产品页两处辅助读失败要说出来（2026-10-21）

WorkBuddy；基线 `cff1ad684d`。

- 症状：`ProductCreate.tsx` 有两处 `.catch(() => {})`。
  ① `/api/fee-mapping-rules` 读失败时，页面导入后不再按 Takealot 分类路径自动匹配
  Fee 品类，「系统推荐」直接写「无推荐」——和「规则里确实没有匹配项」长得一模一样，
  而页面导入正是靠这一步省掉手选品类的；
  ② `/api/products` 读失败时「沿用上一件成本」按钮整块消失，用户看到的是「这个页面
  没有这个功能」，不是「这次没读到」，只能把成本、尺寸、重量全部重填。
  这与 FEE-CATEGORY-LOAD-01 修的是同一类问题（同一份数据在不同页面给出两种结论）。
- 改动：两处改为落成可见提示（`feeMatchError` / `costSourceError`），费率那条明确写
  「『无推荐』不代表分类路径不匹配」；自动翻译保持静默——中文品名是选填项，译不出来
  用户自己填，弹提示只是噪音。
- 复现：`scripts/test-fee-categories.cjs` 增加 4 条源码级断言（除 `translateProductName`
  外不许再有 `.catch(() => {})`，且两个错误状态与那句说明必须在）。把费率那条改回
  `.catch(() => {})` 即转红（已还原）。
- 复测：tsc（`noUnusedLocals`）通过；`test-fee-categories` 14 → 18 条断言；全量
  selfcheck 全绿。

### MARKET-SYNC-01 参考价「上次同步」按北京时间显示，坏行不再打断整轮同步（2026-10-21）

WorkBuddy；基线 `c14f7eb6c6`（并行提交的多市场参考价自动同步 #162）。

- 症状：①列表页「多市场参考价」面板把 `last_sync.at` 直接 `String()` 出来，界面上是
  `2026-10-21T05:12:33+00:00`——机器格式，而且比北京时间慢 8 小时，早上 13 点同步完
  显示 05:12，用户会以为今天没跑过；②`market_price_sync.sync_prices()` 对条目字段
  直接 `.strip()` / `int()`，`market_prices` 是自由 JSON（`/api/migrate` 整行搬运
  不过 Pydantic），历史行里 `url` 是数字、`miss` 是字符串时会抛 `AttributeError`，
  而这个异常发生在 `run_once()` 的产品循环里 → 整轮回滚、`last_run` 永不更新，
  于是每 30 分钟重试一次、每次都在同一条上炸掉，表现就是「自动同步从来没跑过」。
- 改动：①`src/utils/marketPrice.ts` 新增 `formatSyncTime()`（UTC ISO → 北京时间
  `YYYY-MM-DD HH:mm`，非法/缺失分别给「时间未知」/「尚未运行」，用 `getUTC*` 计算
  所以不依赖浏览器时区），列表页改走它；②`is_auto_entry()` 只认字符串 `src`/`url`，
  `apply_fetch()` 对 `miss` 兜底，`sync_prices()` 逐条 try/except——坏的那条原值保留并
  报 `failed`，其余市场照常同步；③`/api/market-prices/status` 的 `auto_markets`
  由 `['SG','MY']` 改为 `['SG']`：MY 不在 `MARKETS` / `CURRENCY` / 前端 `MARKET_ORDER`
  里，客户端照着它提交会被 422，属声明与实现不符（`parse_official_price` 仍能解析
  MYR 页面，将来真加 MY 再放回来）。
- 复现：`_probe_sync.py` 用 `{'SG': {'price': 115, 'src': '官网', 'url': 123}}` 调
  `sync_prices()`，旧代码抛 `AttributeError: 'int' object has no attribute 'strip'`；
  改后该条被跳过、其余条目正常返回。
- 复测：`test_market_price_sync.py` 30 → 33 例（新增坏 url 只跳该条、非数字 miss
  计一次失败、apply_fetch 兜底）；把 `is_auto_entry` 改回旧写法即转红（已还原）。
  `test-market-prices.cjs` 42 → 51 条断言（时间换算跨日、非法串、列表页必须走
  `formatSyncTime`）；把列表页改回 `String(...)` 即转红（已还原）。全量 selfcheck 全绿。
- 本轮开工时远端已前进到 `4719956fb7`（#168 同一 SKU 多行时批量录入全部写入，改了
  `market_price_routes._matches()` 与两处测试）。这三处改动是在 #168 的版本上叠加的，
  未回退 `_matches()` 的「写全部命中行」语义。

### MARKET-DUP-01 同一 SKU 多行时批量录入写全部命中行（2026-10-21，并行提交 #168）

基线 `c14f7eb6c6`。`_match()` 只取第一行，CK 选品表里 Apfra 重复了一条，于是两条
一模一样的记录一条有价、一条空白，用户会以为没保存成功。改为 `_matches()` 返回全部
命中行并逐行写入，结果里带 `products` 计数。本轮只补记录，代码未改。

### AUTO-MARKETS-01 `auto_markets` 不再声明写不进去的市场（2026-10-21）

WorkBuddy；基线 `8f3af26cc3`（并行提交的兰特折算与参考价列改版）。

- 症状：`GET /api/market-prices/status` 的 `auto_markets` 又变回 `['SG','MY']`。
  MARKET-SYNC-01 已经把这条改成 `['SG']`，本次改版把旧版本整段覆盖了回来。
  `MY` 不在 `MARKETS`、不在 `CURRENCY`、不在前端 `MARKET_ORDER`，
  `PriceInput.market` 是 `Literal['CN','SG','TB']`——客户端照着这个列表提交 MY 会 422；
  而 `auto_markets` 本身不参与任何校验，所以错的那版跑得很安静。
- 改动：`backend/api/market_price_routes.py` 恢复 `['SG']` 并保留解释性注释；
  `backend/services/market_price_sync.py` 模块 docstring 里「（新加坡 / 马来西亚等）」
  改成「（当前只有新加坡）」——同一处错误认知的另一半。
- 复现：`test_auto_markets_only_lists_writable_markets`——把值改回 `['SG','MY']` 即转红
  （`assert ['MY'] == []`，已还原）。
- 复测：`test_market_price_sync.py` 33 → 34 例全过；全量 selfcheck 全绿。

### FX-CACHE-01 汇率缓存的兜底标记不再被洗掉（2026-10-21）

WorkBuddy；基线 `8f3af26cc3`。

- 症状：后端从没成功拉过上游时会返回一组写死的兜底汇率并标 `fallback: true`，
  前端把它整段存进 localStorage；刷新页面时 `parseCached()` 无条件写成
  `fallback: false`，于是界面上这组硬编码数字看起来像今天拉到的真汇率，
  汇率说明里的「兜底汇率」也消失了。后端有 `test_fallback_is_not_written_to_cache`
  守着「兜底不写缓存」，前端这一侧却把标记丢了。
- 改动：`src/utils/useFxRates.ts` 读缓存时原样返回，不再改写 `fallback`；
  `stale` 本来就没被改写，一并由新测试钉住。
- 复现：新增 `scripts/test-fx-rates.cjs`（18 条断言）。把 `parseCached()` 改回
  `{ ...value, fallback: false }`，`兜底汇率刷新后仍要标着兜底` 即转红（已还原）。
- 复测：`test-fx-rates.cjs` 18 条断言；tsc 通过；全量 selfcheck 全绿。

### FX-CACHE-02 汇率缓存的「过期」按年龄重算，不再沿用写入那一刻的判断（2026-10-21）

并行提交 `85b255e269`（WorkBuddy 会话之外的改动）；基线 `8f3af26cc3`。本轮只补记录，
代码未改。

- 症状：`parseCached()` 把 localStorage 里那份 `stale` 原样返回。这个值是**写入那一刻**
  的判断，浏览器开着放一天不会自己变，读回来时照抄就把隔夜的旧汇率当成今天的。
- 改动：`useFxRates.parseCached()` 用 `fetched_at` 重新算一遍——时间戳缺失/非法、
  或距今 ≥ 24 小时（与后端 `TTL_HOURS` 同一口径）一律标 `stale`；`fallback` 仍然原样保留
  （那是「这组数字来自内置常量」，与年龄无关，FX-CACHE-01 的结论不受影响）。
- 复测：`scripts/test-fx-rates.cjs` 18 → 21 条断言（48 小时前、`'invalid'`、缺失
  `fetched_at` 三种都要标过期）。`docs/engineering/2026-10-21-market-prices.md` 里
  「`fallback` / `stale` 原样保留」那句已按新行为改写——它描述的是旧实现。

### SYNC-BUSY-01 「立即重读官网价」在已有一轮在跑时不再谎称已开始（2026-10-21）

WorkBuddy；基线 `85b255e269`。

- 症状：`POST /api/market-prices/sync` 无条件回 `started: true` +
  「已开始重读官网价，约 1 分钟出结果」。但 `run_once()` 是进程内串行的：拿不到
  `_state_lock` 就返回 `{'skipped': ...}`，什么都不做。定时同步（每 30 分钟 tick，
  一轮要读几十个官网页面）跑着的时候点按钮，就是「等一分钟 → 刷新状态 → `last_sync`
  一点没动」，用户只能以为按钮坏了。
- 改动：`market_price_sync.is_running()` 暴露锁状态；路由在起线程前先问一句，
  已在跑时回 `started: false` + 「已有一轮同步在进行，本次未启动」。
  前端 `runMarketSync()` 本来就显示后端给的 `message`，不需要改。
- 复现：`test_sync_now_reports_busy_instead_of_starting_a_second_run`——占住
  `_state_lock` 后调路由，断言 `started is False`、提示含「已有一轮同步」、且**没有**起线程。
  把守卫去掉（`is_running()` 短路）即转红（已还原）。
- 复测：`test_market_price_sync.py` 34 → 35 例；全量 selfcheck 全绿。

### BATCH-INVALID-01 批量录入的无效价格不再被静默丢掉（2026-10-21）

WorkBuddy；基线 `85b255e269`。

- 症状：参考价面板的输入框是自由文本（`inputMode="decimal"`，不是 `type="number"`），
  `0` / `-5` / `abc` 都能留在框里。`saveMarketPanel()` 对
  `!Number.isFinite(value) || value <= 0` 直接 `return`，然后提示「已保存 N 条」
  （N 不含它）或「没有需要保存的改动」——用户明明改过，界面却说没改，改的那一行既没保存
  也没有任何解释。
- 改动：组装逻辑抽成 `src/utils/marketPrice.ts` 的 `buildMarketBatch()`，返回
  `{ items, skipped }`；`describeSkipped()` 生成「SKU 市场名」点名文案（最多点 3 个 +
  「等 N 行」）。`ProductList.saveMarketPanel()` 有无效行时**不关闭面板**，提示
  「已保存 N 条；<点名> 价格无效（需大于 0）未保存」，让人改完再存。保存规则未变
  （空串 = 删除该市场、只发真改过的行）。
- 复现：`test-market-prices.cjs` 新增一组断言——`buildMarketBatch()` 只发改过的有效行、
  `0`/`abc` 进 `skipped` 且带 SKU、清空发 `price: null`、`describeSkipped()` 的点名格式；
  另加两条源码断言（面板必须用 `buildMarketBatch` / 不许再出现「无效就静默 return」）。
  把 `skipped.push(...)` 去掉（退回静默丢弃）即转红（已还原）。
- 复测：`test-market-prices.cjs` 78 → 89 条断言；tsc（`noUnusedLocals`）通过；
  全量 selfcheck 全绿。

### SELLER-REASON-01 卖家接口读不到时，密钥坏了和限流不再说同一句话（2026-10-21）

WorkBuddy；基线 `a0c8a92c87`。

- 症状：`_load_offers()` 对**任何**非 200 都抛同一句「卖家接口暂时不可用，已暂停请求，
  请稍后再试」。密钥被撤销（401）的人会一直等下去，因为重试永远不会好；更糟的是
  冷却期那句把它整个盖掉——连「密钥无效」都看不到。同一句话还从三个出口露给用户：
  `/api/products/scrape-takealot` 的降级警告、`/api/products/seller-assist/*` 的 503
  （这一条更彻底，连异常类型都不留）、调价列表的「上次结果」。
- 改动：①`seller_api_reason()` 从 `automatic_pricing` 挪到 `takealot_seller_api`
  （它描述的就是这个模块返回的状态码），文案一字未改，`automatic_pricing` 改为导入，
  避免两份定义各自漂移；②新增 `_unavailable_reason(status)`：401/403 收尾写「请先检查
  卖家 API 密钥」，429/5xx 收尾写「请稍后再试」——密钥坏了劝人等、上游限流劝人改配置，
  都是把人往错的方向推；③失败原因存进 `state['last_error']`，冷却期内复用它（成功一次
  即清除），不再让「请稍后再试」覆盖真实原因；④`seller_assist` 的 503 与
  `scrape-takealot` 的卖家降级警告改用 `_read_reason(error)` 带归因。
- 复现：`test_seller_api.py` 新增 2 例——401 文案含 `HTTP 401` 和「密钥」且**不含**
  「稍后再试」，冷却期第二次报同一句且只发 1 次请求；429 含「限流」且不含「密钥」。
  `test_seller_assist.py` 新增 1 例——`_load_offers` 抛 429 时 503 detail 含 `HTTP 429`
  和「限流」。把两处修复撤回 → 三条断言分别报
  `assert ('HTTP 401' in '卖家接口暂时不可用，已暂停请求，请稍后再试')`（已还原）。
- 复测：后端 496 passed / 1 skipped；全量 selfcheck 全绿。

### TIME-UTC-01 产品详情与库存调整的时间不再早 8 小时（2026-10-21）

WorkBuddy；基线 `fcba9ad87c`。

- 症状：后端的 `created_at` / `updated_at` 是 SQLAlchemy 的 naive `DateTime`
  （默认 `datetime.utcnow`），Pydantic 序列化成 `2026-10-21T12:00:00`——**不带时区标记**。
  前端 `new Date(...).toLocaleString('zh-CN')` 按**本地时间**解析这种串，于是
  东八区浏览器把 UTC 12:00 显示成 12:00（实际 20:00），「创建时间 / 更新时间」与
  库存页操作日志的时间全天早 8 小时；UTC 16:00 之后创建的记录连日期都是前一天。
- 改动：`src/utils/datetime.ts` 新增 `parseServerTime()`（没有 `Z` / `±hh:mm` 的一律
  当 UTC，纯日期补 `T00:00:00Z`，空格分隔先换 `T`；解析不了返回 `null` 不抛）与
  `beijingDateTime()`（固定 `Asia/Shanghai`，坏值走调用方占位符）。
  `ProductDetail.tsx`（创建/更新时间）与 `Inventory.tsx`（操作日志时间列）改用它。
  显示格式沿用 `toLocaleString('zh-CN')`，只有小时数被修正，版式不变。
- 复现：新增 `scripts/test-datetime.cjs`（28 条）——naive / `Z` / `+00:00` / `+08:00` /
  空格分隔 / 纯日期必须指向同一时刻或按标记解析；坏输入返回 `null`；
  naive UTC 12:00 必须显示 `20:00` 且**不含** `12:00`；UTC 20:00 必须进位到次日 `04:00`；
  另加源码断言（这两页不许出现不给 `timeZone` 的 `toLocaleString('zh-CN')`）。
  把 `ProductDetail` 的创建时间改回旧写法即转红（已还原）。
- 复测：`test-datetime.cjs` 28 条断言；tsc（`noUnusedLocals`）通过；全量 selfcheck
  32 步中 31 步绿（pytest 步是本机 safe-delete 守卫拦 pytest 清临时目录的环境性假红，
  同命令单独跑 498 passed / 1 skipped / exit 0）。

### PROC-TIME-01 采购记录的采购日期按北京时间落库（2026-10-21）

WorkBuddy；基线 `fcba9ad87c`。

- 症状：`procurement_routes._now_str()` 用 `datetime.now()`——容器时区是 UTC，
  而 `recorded_at` 是给用户看的「采购日期」（前端 `Procurement.tsx` 与
  `PipelinePlan.tsx` 都取 `.slice(0, 10)`，库存对账的 `start_date` 也拿它做字符串比较）。
  北京时间 00:00–08:00 创建的采购记录被写成前一天，账期整体错位一天——正是
  `todayLocal()` 当初修掉的那个坑，从服务端又漏了回来。
- 改动：`_now_str()` 改成 `datetime.now(timezone(timedelta(hours=8)))`，显式按 UTC+8
  生成，不依赖容器 TZ 配置。落库格式（`YYYY-MM-DD HH:MM:SS`）未变，历史行不受影响。
- 复现：新增 `backend/tests/test_procurement_recorded_at.py` 2 例——①`_now_str()` 与
  UTC+8 的偏差 < 60 秒，且函数体里不许出现裸 `datetime.now()`（开发机本身是东八区，
  只靠时间差抓不到，所以同时钉实现）；②走 `create_procurement_record` 建一条记录，
  `recorded_at[:10]` 必须等于北京时间当天，列表接口原样带出。把 `_now_str()` 改回
  `datetime.now()` → 第一例转红（已还原）。
- 复测：后端 498 passed / 1 skipped（新增 2 例）。

### PACK-DATE-01 装箱单日期与其它页共用同一份业务日期（2026-10-21）

WorkBuddy；基线 `fcba9ad87c`。

- 症状：`src/pages/Packing.tsx` 有一份自己的 `todayStr()`（`getFullYear/getMonth/getDate`），
  取的是**浏览器本地时区**；采购、库存、补货三个页面都走 `todayLocal()`（Asia/Shanghai）。
  非东八区的机器上，同一个装箱单的日期与其它页差一天，文件名也会跟着错。
- 改动：`todayStr()` 改为 `todayLocal()` 的别名（保留调用点不动），删掉私有实现；
  业务日期恢复「只有一份实现」。
- 复现：`test-datetime.cjs` 加源码断言——`Packing.tsx` 不许出现
  `getFullYear()/getMonth()/getDate()`，且必须从 `utils/datetime` 导入。把私有实现放回去即转红（已还原）。
- 复测：`test-datetime.cjs` 28 条断言；`test-packing-display.cjs` 通过；全量 selfcheck 同上。

### MARKET-DATE-02 参考价的采集日期按北京时间写（2026-10-22）

WorkBuddy；基线 `5a790f13cd`。

- 症状：`src/utils/marketPrice.ts` 里有一份私有 `today()`（`getFullYear/getMonth/getDate`），
  取的是**浏览器本地时区**；采购、库存、补货、装箱四个页面都走 `todayLocal()`
  （Asia/Shanghai）。非东八区的机器上，早上录进详情页的参考价 `at` 会被记成前一天，
  列表悬停卡与详情页显示的「采集日期」比同一时刻的其它业务日期差一天。
  这是 PACK-DATE-01 修掉的同一类残留：当时只盯了 `Packing.tsx`，没扫全目录。
- 改动：删掉私有 `today()`，改由 `todayLocal()` 生成；新增 `manualPriceEntry()` 统一
  「手工改一个市场」的写法（日期 + 币种 + 来源 + 清失败痕迹），`ProductDetail.tsx`
  的 `setMarketPrice()` 只调它。
- 复现：`test-datetime.cjs` 的源码守卫从「只看 `Packing.tsx`」改成**扫整个 `src/`**，
  出现 `getFullYear()/getMonth()/getDate()` 即失败（28→30 条）；`test-market-prices.cjs`
  钉住 `at: todayLocal()` 与「不许再有 `function today(`」。开发机本身是东八区，
  只比日期差抓不到，所以同时钉实现。
- 复测：`test-datetime.cjs` 30 条、`test-market-prices.cjs` 110 条；tsc（`noUnusedLocals`）通过。

### MARKET-STALE-01 手工改价不再留着「已过期」标记（2026-10-22）

WorkBuddy；基线 `5a790f13cd`。

- 症状：`market_prices` 的两个写入口径不一致。`POST /api/market-prices/batch` 写入时
  `pop('stale')`/`pop('miss')`，详情页 `setMarketPrice()` 却原样 spread 旧条目。
  于是新加坡价连续三次没读到（`stale=true`、`miss=3`）之后，用户手工把正确价格填进去，
  列表与详情页仍写「· 已过期（最近一次没读到）」，悬停卡还把这一格压到 0.6 透明——
  看起来像这次录入没生效；下一次抓取失败（`miss` 已是 3）会立刻再打回 stale。
- 改动：`manualPriceEntry()` 在写入新价时清掉 `stale`/`miss`/`last_error`，与 `/batch`
  一致。**不改 `src`**：这一格仍是每日自动重读的市场，成功同步照常覆盖回来；
  `url` 也原样保留，否则自动同步再也回不来。
- 复现：`test-market-prices.cjs` 新增断言——手工改价后三个失败字段必须消失，
  `src`/`url` 必须保留。把 `delete next.stale/miss/last_error` 去掉即报
  `手工改价必须清掉 stale（否则界面仍写「已过期」）`（已还原）。
- 复测：`test-market-prices.cjs` 89→110 条。

### MARKET-CUR-01 参考价的符号跟着条目币种走（2026-10-22）

WorkBuddy；基线 `5a790f13cd`。

- 症状：`formatMarketPrice()` 一直用 `MARKET_META[code].symbol` 渲染价格，而
  `entryCurrency()` 明确承认「条目上写了 `cur` 就以它为准（迁移数据可能有别种货币）」。
  两条口径并存的结果是同一行自相矛盾：迁移数据里一条 `{"price":99,"cur":"USD"}` 的
  新加坡条目显示成 `S$99`，兰特列却写「无汇率」——左边说是新加坡元，右边说不认识这个币种。
- 改动：符号表从 `MARKET_META` 挪到 `CURRENCY_SYMBOL`（按币种索引），
  `symbolFor()` 按 `entryCurrency()` 取符号；认不出的币种写成 `GBP 99` 这种带代码的形式，
  不猜符号、也不冒用市场的符号。`MARKET_META` 只剩 `label/short/cur`，不再自带第二份符号表。
- 复现：`test-market-prices.cjs` 新增 7 条断言（`SGD→S$`、`CNY→¥`、`USD→US$`、
  未知币种带代码、没写 `cur` 退回市场默认、`MARKET_META` 不许再有 `symbol`）。
  把 `symbolFor()` 改回「按市场取符号」即报 `别的币种不许冒用 S$`（已还原）。
- 复测：同上；`test-product-pagination.cjs` 的 TS 加载器补上递归 `m.require`
  （marketPrice 现在 import 了 `utils/datetime`，只编译第一层会 MODULE_NOT_FOUND）。

### DETAIL-DRAFT-01 详情页「取消」不再留下被丢弃的参考价（2026-10-22）

WorkBuddy；基线 `ecc9db7d34`。

- 症状：`ProductDetail` 的参考价表格**两种模式都读 `mpDraft`**（编辑态渲染输入框、只读态渲染
  `formatMarketPrice`），而「取消」只 `setEditMode(false)`，不还原草稿。把新加坡价从 115 改成
  999 再点取消，表格继续显示 `S$999`；再点「编辑」，输入框里还是 999——看起来像「取消没生效」，
  只有刷新页面才回到 115。普通字段没暴露这个问题，因为只读态读的是 `product`，正好掩盖了
  草稿没还原这件事。
- 改动：抽出 `resetDraft(product)`（原来 `useEffect` 里那 34 个字段的映射搬进来），加载、
  取消、保存成功三处共用：取消退回服务端那一份；保存成功用 `updateProduct` 的返回值重建
  （后端会归一价格小数位、给 `at` 补今天，不重建的话草稿和库里差一点）。
- 复现：`test-market-prices.cjs` 新增源码级断言——`cancelEdit()` 必须调 `resetDraft(product)`、
  取消按钮必须 `onClick={cancelEdit}`、保存成功后必须 `resetDraft(updated)`。把
  `resetDraft(product)` 去掉即报「取消编辑必须用服务端那一份重建草稿」（已还原）。
- 复测：`test-market-prices.cjs` 110 → 119 条断言；tsc（`noUnusedLocals`）通过；全量
  selfcheck 33 步全绿。

### DETAIL-SAVE-ERR-01 保存失败不再把整页换成一行红字（2026-10-22）

WorkBuddy；基线 `ecc9db7d34`。

- 症状：`handleSave` 的 catch 写的是 `setError(...)`，而 `error` 是「加载失败」的状态——渲染时
  `if (error) return <div className="alert alert-error">`，整个页面（含所有输入框）被替换成
  一行红字。网络抖一下、或后端 422（例如市场价条目不合规）之后，用户刚填的一整页表单连同
  草稿一起消失，只能重填；从列表再点进来也拿不回。
- 改动：新增 `saveError` 状态，在页头以 `role="alert"` 行内提示渲染；保存成功与取消时清空。
  `error` 只留给加载失败。
- 复现：断言按 `handleSave` 这一段切片检查——必须出现 `setSaveError(`、不许出现 `setError(`。
  第一版断言用全文匹配，被 `cancelEdit` 里的 `setSaveError('')` 蒙过去了（探针不转红），已改成
  只扫 handleSave 段；把 catch 改回 `setError(` 即转红（已还原）。
- 复测：同上。

### MARKET-COPY-02 「参考价」按钮不再叫「中国官网价」（2026-10-22）

WorkBuddy；基线 `ecc9db7d34`。

- 症状：列表页按钮 `title="批量录入中国官网价 / 淘宝价…"`，但点开面板后表头写的是
  「国内原价 ¥ / 国内到手价 ¥」，正文还写着「中国官网（charleskeith.cn）已停摆」——同屏三处
  口径互相打脸。`MARKET_META` 的注释早就写明「标签必须写国内，不能写官网」（CN/TB 只是历史键名，
  数据实际来自天猫、京东）。
- 改动：title 改为「批量录入国内原价 / 到手价（照天猫、京东商品页抄）；新加坡官网价由后端
  每天自动重读」。
- 复现：断言 title 不许再出现「批量录入中国官网价」、必须写「批量录入国内原价 / 到手价」。
  改回旧文案即转红（已还原）。
- 复测：同上。

### MARKET-ZERO-01 参考价「0」不再被当成价格（2026-10-22）

WorkBuddy；基线 `5aa7464abf`。

- 症状：同一个字段在不同地方对 0 的判据相反。`/api/market-prices/status` 用
  `entry.get('price')` 的真值判「填没填」、自动同步的 `_to_price()` 丢弃 `<= 0`、
  批量录入的 `buildMarketBatch()` 把 0 判无效；但 `toPrice()` 收 `>= 0`、详情页输入框
  `min="0"` 且只在 `value < 0` 时才清空。于是在详情页敲 0 会存进一条 `price: 0`：
  列表显示「¥0」「R0」、悬停卡也算得出「R0」，覆盖率却把这行算成「未填」；
  批量面板读到草稿 `0` 与库里 `0` 相同，判「没有改动」，这一格再也清不掉。
- 改动：`toPrice()` 只认正数（0 与负数、null、空串同档），解析和序列化都丢掉 0；
  详情页价格框改成「原文草稿 + 行内提示」——`setMarketPrice()` 遇 0/负数/非数字
  先把草稿里该市场删掉（保存时不会带上），原文留在框里并提示「价格需大于 0，不会保存」。
  原文草稿是必需的：受控输入直接绑 `entry.price` 时，敲 `0.5` 的第一个字符 `0` 会
  把草稿清掉、输入框随之清空，最后存进去的是 5——那比原来的 bug 更难发现。
- 复现：`test-market-prices.cjs` 的空值组加 `0` 与 `'0'`，另加 3 条 payload 断言
  （0 不许写进 payload、同批次的正常价格照常写入、只剩 0 时整段返回空串）。
  把 `toPrice()` 改回 `>= 0` 即报「price=0 必须当成没数据，不能渲染成 ¥0」（已还原）。
- 复测：`test-market-prices.cjs` 119 → 124 条断言；tsc（`noUnusedLocals`）通过；
  全量 selfcheck 33 步全绿。

### TIME-GUARD-01 时区守卫真的开始查 `toLocale*` 了（2026-10-22）

WorkBuddy；基线 `5aa7464abf`。

- 症状：`scripts/test-datetime.cjs` 头注释写着「不许再出现 `toLocaleString('zh-CN')`
  但不给 `timeZone` 的写法」，但守卫只查 `getFullYear()/getMonth()/getDate()`，
  于是 `ProductList` 的「已更新 · HH:MM」用 `new Date().toLocaleTimeString('zh-CN', …)`
  取**运行机器的时区**照样能过。开发机是 UTC+8 看不出来，换到 UTC 机器上同一个刷新动作
  写「已更新 · 03:00」而北京时间是 11:00，与全站其它时间列对不上，会被当成「刷新记录错乱」。
- 改动：守卫补一条源码检查——除 `utils/datetime.ts` 自己的 Intl 失败兜底外，`src/` 里每个
  `toLocale(Date|Time)?String(` 都必须在同一处给出 `timeZone`；同时新增
  `datetime.beijingClock()`（Asia/Shanghai 的「时:分」），列表页改用它。
- 复现：把列表页那行改回 `new Date().toLocaleTimeString(...)`，守卫报
  「时间文案必须显式指定 timeZone（或走 utils/datetime）」并列出该行（已还原）。
- 复测：`test-datetime.cjs` 30 → 36 条断言（含 `beijingClock` 的跨日换算、naive 串、
  空值与坏值）；tsc 通过；全量 selfcheck 33 步全绿。


### MARKET-ZERO-SAVE-02 无效参考价不得触发清空（2026-10-22）

Codex；基线 `f58ff3521dc6`，接续 WorkBuddy 的 MARKET-ZERO-01。

- 操作：已有 CN=100，编辑为 0 / -1 / Infinity 后点保存。原实现删除草稿 CN，
  仍调用 updateProduct，提示「不会保存」实际却清空原价；隔离执行原页面 handler，
  断言 0 次写入，实得 1 次，复现失败。
- 修复：handleSave 在调用写入前检查所有非空价格原文，点名需修正或清空的市场并返回。
  保留 WorkBuddy 的原文输入态、小数输入和显式清空行为。
- 同条件复测：三个无效输入均零写入且有错误提示；空串可清空；0.5 与 150 可保存。
  在现有 test-market-prices.cjs 中执行实际 handler（mock 写 API），新增 11 条隔离检查；
  原有 124 条、datetime 36 条及 tsc --noEmit 通过。
- 状态：代码与隔离测试完成；PR Check、部署和真实会话验收各自以当前 PR 证据为准。
  未使用业务数据或执行真实价格写入。

### STALE-MARK-01 「已过期」标记在每个展示面上都挂上了（2026-10-22）

WorkBuddy；基线 `f58ff3521d`。

- 症状：`stale` 是后端在自动同步连续 3 次没读到时打的标（价格保留、但已经是旧的）。
  这个标记在四个展示面里有两种写法、还有一处**根本不写**：列表悬停卡写「· 已过期」、
  详情页写「· 已过期（最近一次没读到）」、批量录入面板的新加坡价那一列**一个字都不写**。
  面板正是用户决定「国内价填多少」的地方——旁边那个 S$115 可能是一周前的旧价，
  却和刚同步成功的值长得一模一样。
  列表方框的弱化又只看**打头那个市场**（`leadEntry.stale`）：SG 是新的、国内到手价
  两周前且已过期时，整格看起来完全正常。
- 改动：`marketPrice.staleNote(entry)` 收敛成唯一文案（`' · 已过期'`）；悬停卡、批量面板
  都改用它；列表方框的 `opacity` 从「整格按 lead 判」改成按**单个市场**判；
  详情页保留长文案（那张表有整列空位）。
- 复现：删掉批量面板那行 `staleNote(prices.SG)`，`test-market-prices.cjs` 报
  「批量录入面板的新加坡价也要标「已过期」」（已还原）。
- 复测：`test-market-prices.cjs` 124 → 133 条断言；tsc 通过；全量 selfcheck 33 步。

### MARKET-WRITE-ZERO-01 新写入的参考价不再收 0（2026-10-22）

WorkBuddy；基线 `f58ff3521d`。

- 症状：MARKET-ZERO-01 让四个**读取**端统一认「>0 才算填了」（`/status` 的真值判断、
  自动同步的 `_to_price()`、前端 `parseMarketPrices()` 与 `buildMarketBatch()`），
  但**写入**端还留着 `PriceInput.price` 的 `ge=0`。于是 `POST /api/market-prices/batch`
  带 `price: 0` 会回 `saved: 1`，库里多一条谁都看不见的条目：列表没有价格、
  覆盖率算「未填」，下次想清掉还得再猜一次。
- 改动：`PriceInput.price` 改 `gt=0`（HTTP 侧 422），清空该市场只能发 `price: null`。
  `_check_market_prices()` 保持放行 0——它是**对历史行的容忍**：`/api/migrate` 整行搬运
  的旧行可能有 `price: 0`，而 `/batch` 写一个市场时会把整段 map 重校验（写 TB 连 CN 一起验），
  收紧成 `<= 0` 会让一条历史 0 把无关市场的写入也挡死。这条理由写进了代码注释。
- 复现：把 `gt=0` 改回 `ge=0`，`test_batch_rejects_non_positive_price[0]` 转红（已还原）。
- 复测：pytest 498 → 499 passed / 1 skipped；全量 selfcheck 33 步。

### AUDIT-MP-PANEL-01 批量录入弹窗第一次被量（2026-10-22）

WorkBuddy；基线 `f58ff3521d`。

- 症状：「多市场参考价」弹窗（`aria-modal="true"`，四列表头 + 两列输入框 + 只读的
  新加坡价）是唯一能一次填十几行的入口，却从来没被任何宽度量过——它连 fixture 里需要的
  `/api/market-prices/status` 都没声明，真去点开只会 404 后挂一句「读取同步状态失败」。
  它也不响应 Esc、不锁背后滚动（同页的「核对本店资料」弹窗两条都有）。
- 改动：`test-responsive-layout.py` 在 1280 / 390 / 320 三档点开它，量弹窗是否越界、
  body 是否溢出、窄屏表格是否自己有滚动条、Esc 能否关闭；`DATA` 补
  `/api/market-prices/status`；`PRODUCT` 的 SG 条目带 `stale`（否则「已过期」文案
  在任何宽度、任何页面上都不会被渲染）。`ProductList` 的 `mpPanel` 补 Esc 关闭 + 锁定
  背后滚动，与同页弹窗同一口径。
- 复现：给弹窗强行加 `minWidth:2000`，审计在 1280/390/320 报 13 条
  「market price dialog outside viewport / table lacks local scroll / action clipped」（已还原）。
- 复测：版式审计 99 个面 0 失败、`undeclared_api` 为空；全量 selfcheck 33 步。

### CONTRAST-OPACITY-01 对比度审计开始算 opacity，过期参考价改用文字标记（2026-10-22）

WorkBuddy；基线 `98876342a8`。

- 症状：`ui_contrast_audit.py` 只跳过「自身 `opacity` 等于 0」的节点，其余一律按
  计算样式里的 `color` 直算比值。而 `opacity` 在祖先链上是相乘的：`opacity: .6`
  弱化的文字，`color` 一个字都没变，真正画出来的像素是「前景 × α + 背景 × (1-α)」。
  列表「参考价」方框里的过期价（`R3,156`，12.5px）因此被按 11.84:1 判过，实测
  只有 3.65:1，低于 AA 的 4.5:1——审计连报 20 轮「0 处低对比」。
- 改动：
  1. `ui_contrast_audit.py` 增加 `alphaChain()`，把祖先链 opacity 相乘后与背景混合
     再算比值；`disabled` / `aria-disabled="true"` 内的文字按 WCAG 2.1 1.4.3 例外
     单列成 `inactiveLowContrastCount`（不计失败，但出现在汇总的「停用控件文字」列，
     不从分母消失）。此前这类文字是按未弱化算的——既不是通过，也不是豁免。
  2. `ProductList` 三处过期价弱化（列表打头市场、列表其余市场、批量面板 SG 列）
     去掉 `opacity: .6`，改用 `staleNote()` 的文字标记，与悬停卡、批量面板同一口径。
     顺带解决「只有看得见的人能收到过期信号」：透明度对低视力用户等于没有信号。
- 复现：把 `opacity: leadEntry.stale ? 0.6 : 1` 加回列表打头市场，
  `scripts/test-contrast-audit.py` 退出 1，`/products` 三档各报 2 处低对比（已还原）。
- 复测：对比度审计 33/33 面 0 处低对比（停用控件文字 69 处单列）；版式审计 99 个面
  0 失败（加长后的单元格未越界）；`test-market-prices` 133 → 135 断言。

### PRODUCT-LABEL-01 产品标签 PDF 上线但零文档（2026-10-22，补记）

并行提交（远端 `d4101cabf3`）；自检时发现功能已合并、`docs/` 一个字都没有。

- 功能：一个 SKU 一张 40×30mm 标签（页面即标签，标签打印机直接打；多 SKU 打包成 zip），
  版式对齐官方 product_labels.pdf（Helvetica 8pt、名字 3 行、EAN-13 横排、1-6-6 分组
  数字、条码左侧 M/P）。只有 13 位合法 EAN-13 的 SKU 才会画——印出来扫不出的标签是废纸。
  后端 `POST /api/products/labels` 另有 `mode=sheet` 的 A4 拼版（40×30 每页 45 张）。
- 同日后端之外又多了一份浏览器内生成器 `src/utils/labelPdf.ts`（零依赖手写 PDF+ZIP），
  网页上的「下载标签」走它，理由是 Railway 部署长期不跟上、标签不该卡在部署上。
- 补的文档：新增 `docs/engineering/2026-10-22-product-labels.md`——版式常量表、两份
  生成器必须逐字对齐的参数、30×40 会把条宽压到 0.258mm（低于 EAN-13 的 0.264mm 下限）
  的取舍、多规格 SKU 的拆分与匹配。

### LABEL-ERR-01 生成标签失败不再把详情页整页替换（2026-10-22）

WorkBuddy；基线 `d4101cabf3`。

- 症状：详情页「下载标签」的失败分支写的是 `setError(...)`。`error` 是「加载失败」状态，
  渲染时整页被换成一行红字（`if (error) return …`），于是生成失败之后，用户正在编辑的
  整张表单连同草稿一起消失——与 DETAIL-SAVE-ERR-01 同一类错，只是换了入口。
- 改动：新增 `labelError` 状态，渲染成页面顶部一条行内提示；失败可重试，草稿不动。
- 复现：把 `setLabelError` 改回 `setError`，`test-label-ean.cjs` 报
  「详情页生成失败不许写 error（会整页替换、草稿丢失）」（已还原）。
- 复测：`test-label-ean.cjs` 37 条检查通过；全量 selfcheck 35 步。

### LABEL-SKIP-01 跳过几个 SKU 要说出来，而且不能报成错误（2026-10-22）

WorkBuddy；基线 `d4101cabf3`。

- 症状：勾 5 个 SKU、其中 2 个不是合法 EAN-13 时只会出 3 张标签。第一版界面一个字都
  不说，用户以为标签丢了；补上提示后又被写进 `setError`——那是页面顶部「加载失败」的
  红色告警，把一次成功的下载报成错误。
- 改动：走独立的 `labelNote`（信息条）：「已生成 3 张标签；另有 2 个 SKU 不是合法
  EAN-13，已跳过」；一个都不合法才走错误提示（那确实什么都没生成）。
- 复现：把该分支的 `setLabelNote` 改回 `setError`，`test-label-ean.cjs` 报
  「被跳过的 SKU 必须提示」（已还原）。
- 复测：`test-label-ean.cjs` 37 条检查通过（含「三份 EAN 实现判据一致」的守卫：
  后端 `is_ean13`、`utils/ean.ts`、`utils/labelPdf.ts`）。

### LABEL-WRAP-01 折行只在真的丢词时加省略号（2026-10-22）

WorkBuddy；基线 `d4101cabf3`。

- 症状：`wrapName()`（`src/utils/labelPdf.ts`，网页实际用的那份）与 `_wrap()`
  （`backend/services/label_pdf.py`）原来的判据是「凑满 3 行就在末行补 `...`」，于是
  「正好折成 3 行、第三行又刚好填满」的产品名会被截掉最后一个词——名字本来完整，印出来
  却带省略号。40×30 标签的内容宽只有 34.3mm、8pt，长名字很容易正好占满 3 行。
- 改动：两边都用 `dropped` 标记区分「被截断」与「刚好填满」，只有真丢词才截末行补省略号。
- 复现：把判据改回 `if (lines.length === maxLines)`，
  `test-label-pdf.cjs` 报「7 个词放得下，不许丢词」（实得 `MMMM...`）、
  `test_wrap_keeps_full_last_line` 同时转红（均已还原）。
- 复测：`test-label-pdf.cjs` 117 → 126 条检查、`test_label_pdf.py` 25 例；后端全量
  533 passed / 1 skipped；全量 selfcheck 35 步。

### LABEL-WRAP-GUARD-01 折行用例在并行重写里被冲掉，原样补回（2026-10-22）

WorkBuddy；基线 `579a03621d`。

- 症状：并行提交把 `backend/tests/test_label_pdf.py` 整文件重写（改用 `tests/pdfmeta.py`
  量页面、去掉 pymupdf 运行依赖，这步本身是对的），但 `test_wrap_keeps_full_last_line`
  与 `test_wrap_marks_dropped_words` 两条一起消失了，`_wrap` 也不再被 import。
  `_wrap()` 里的 `dropped` 判据于是没有后端用例守着（前端 `test-label-pdf.cjs` 那条只覆盖
  `wrapName()`），而本文件与 engineering 文档还在引用这两条用例。
- 改动：两条用例与 `_filled_line()` 原样补回，断言一字未改。
- 复现：把 `_wrap()` 的判据改回 `if len(lines) >= max_lines`，
  `test_wrap_keeps_full_last_line` 转红（已还原）。
- 复测：`test_label_pdf.py` 23 → 25 例。

### LABEL-COUNT-01 列表页「下载标签」按钮的数字改成真正能出标签的数量（2026-10-22）

WorkBuddy；基线 `579a03621d`。

- 症状：按钮写 `selectedSkus.size`（勾选总数），而 `src/utils/ean.ts` 的头注释明说
  「前端先筛一遍是为了按钮上能显示真实数量——否则后端跳过几个，用户以为出错了」；
  详情页的 `labelCount` 一直是合法数。勾 3 个只有 1 个合法时按钮写「(3)」、实际下到
  1 张，看着像文件丢了。
- 改动：`const labelCount = Array.from(selectedSkus).filter(isEan13).length`，按钮文案与
  `disabled` 都改用它；一个都不合法时在 title 里说明「都不是合法 EAN-13 条码」。
- 复现：把按钮数字改回 `selectedSkus.size`，`test-label-ean.cjs` 报
  「按钮上的数字必须是合法 EAN-13 的数量」（已还原）。
- 复测：`test-label-ean.cjs` 37 → 41 条检查。

### LABEL-404-01 「产品查不到」不再报成「产品没有 SKU」（2026-10-22）

WorkBuddy；基线 `579a03621d`。

- 症状：`download_labels` 在 `products` 为空时一律回「选中的产品都没有 SKU，生成不了
  条码」。列表页的勾选持久化在 localStorage 里，产品被删掉之后再点下载走的就是这条路：
  提示让人去补 SKU，实际该做的是刷新列表重新勾。另外 `items` 为空时 400 文案会拼出空
  括号（「没有可生成的标签（）」）。
- 改动：拆成 404「没有找到对应的产品（可能已被删除，或不属于当前店铺）」与
  400「选中的产品没有可用的 SKU（SKU 为空或已被修改）」，后者在进 `generate_*` 之前就拦。
- 复现：404 文案改回旧句，`test_missing_product_is_not_reported_as_missing_sku` 转红
  （已还原）。
- 复测：`test_label_routes.py` 9 → 10 例。

### LABEL-PARITY-01 两份标签生成器的版式常量开始逐项比对（2026-10-22）

WorkBuddy；基线 `eb6db9bf07`。

- 症状：同一套版式有两份实现——网页用的 `src/utils/labelPdf.ts`（零依赖手写 PDF）与
  接口用的 `backend/services/label_pdf.py`。engineering 文档写着「参数必须逐字对齐，
  改一边不改另一边，同一个 SKU 在网页和接口里会印出两张不一样的标签」，但没有任何
  东西真的比过这两份常量：改错一位的表现只是「打印出来对不齐」，自检永远是绿的。
- 改动：`scripts/test-label-ean.cjs` 把两份源码里的 14 个版式常量、`LABEL_SIZES`、
  默认尺寸、折行调用与缩放公式逐项读出来比数值（41 → 96 条检查）。改名、漏改、
  只在调用处换数都会点名。
- 复现：`labelPdf.ts` 的 `BAR_WIDTH` 从 0.935 改成 0.93，
  `test-label-ean.cjs` 报「BAR_WIDTH 两份生成器不一致：labelPdf.ts=0.93 /
  label_pdf.py=0.935」（已还原）。
- 已知差异（不是漂移）：后端 `_latin1()` 保留重音字符，前端 `asciiOnly()` 换成 `?`，
  因为前端手写 PDF 的 xref 靠「字符数 == 字节数」算。写进 engineering 文档，避免以后
  被当成 bug「修」成坏文件。
- 复测：`test-label-ean.cjs` 96 条检查通过；全量 selfcheck 35 步。

### LABEL-NAME-01 下载标签的产品名不再只取当前筛选结果（2026-10-22）

WorkBuddy；基线 `eb6db9bf07`。

- 症状：勾选存在 localStorage 里、不随筛选变化，而 `products` 只是当前筛选结果。
  下载标签时按 SKU 反查产品名是在 `products` 里现扫：勾 3 个再按关键词筛出 1 个，
  剩下 2 个查不到名字，`buildLabelBundle()` 退回用 SKU 当产品名——印出来是
  `CK2-90151648-1` 而不是商品名，而且看不出哪里错。
- 改动：把见过的 SKU→品名累积在 `labelNames` ref（新值覆盖、空名不覆盖），筛选只影响
  展示。顺带给「已生成 3 张、跳过 2 个」这条提示补 `role="status"`（同页另外三条信息条
  都有，读屏用户点完下载原本听不到反馈）。
- 复现：产品名改回现扫 `products` → 「产品名要从 labelNames.current 取」转红；
  去掉 `role="status"` → 对应断言转红（均已还原）。
- 复测：`test-label-ean.cjs` 96 条检查；`tsc --noEmit` 通过。


### PRICING-TARGET-01 / PRICING-COOLDOWN-02（2026-10-23）

- 优先级 P1；Codex 修复，WorkBuddy 本机只读验收。基线 `4a94d84cc2ad`。
- 复现：同一 fastest 池 R90/R115，本店 R120、底价 R100，原建议 R114；最低价仍为 R90，
  不能通过丢弃它宣称目标可达。原冷却只限制 holding，刚降价仍失位时可以继续建议降价。
- 修复：每个目标池先取真实领跑价再判底价；另一独立卡位可继续评估。没有类型不猜第二池。
  涨跌均遵守冷却；target_lane + 理由明确“尝试/分步/待新行情确认”。
- 验证：同条件新增 6 例 + 原有 11 例纯逻辑回归隔离通过；无 pytest 的本机环境不能称完整后端通过，
  完整 PR Check 单独验收。未执行真实价格修改。
- 参考与后续：见 docs/pricing/2026-10-18-buybox-pricing-model.md（外部 skill/论文、反馈闭环路线）。
- 下一动作：Codex 审核最新 head 的完整 CI 后合并部署；WorkBuddy 仅提交本机脱敏决策证据，
  禁止自行合并/部署或开启真实调价。验收 R90/R115 同池保持、跨池可尝试、冷却内保持、边界后再评估。
### PACK-DEFAULT-01 装箱单默认箱数/件数一个口径，六处共用（2026-10-23）

WorkBuddy；基线 `8f3a1df552`。

- 症状：「刚勾上、还没动过」的一行，界面写着「1 箱 · 小计 12 件」（输入框默认 1、
  placeholder 写「默认 12」），但导出过滤要求**手填过**才有效（`parseInt(lines[sku].count)`
  为 NaN 整行跳过），于是「勾一个产品、什么都不改、点导出」报「请填写有效的箱数和每箱
  件数」；「将导出 N 行」数的是勾选数；按 SKU 展开的箱内数量是空串（印成「×0」）；
  导出成功提示写「已自动展开 0 箱」。
- 改动：`src/pages/Packing.tsx` 抽出 `lineCartons()` / `lineCount()`（没动过 = 1 箱 ×
  `default_count`，显式清空 = 0），显示、汇总、按 SKU 展开、箱内合计校验、导出过滤与
  导出内容六处全部改走它们；`expandCartons()` 跳过导不出去的行并说明原因。
- 复现：`exportableRows` 改回「必须手填」→ `test-packing-display.cjs` 在
  「将导出 1 行」转红（已还原）。
- 复测：`test-packing-display.cjs` 28 → 40 条断言；`tsc --noEmit` 通过；
  `scripts/test-packing-oms.py` 8 组仍全过。

### PACK-MODAL-01 装箱页三个弹层补 Esc 关闭与滚动锁（2026-10-23）

WorkBuddy；基线 `8f3a1df552`。

- 症状：查箱子 / 编辑装箱产品 / 确认删除都写了 `aria-modal="true"`，但都不能用 Esc 关闭、
  也不锁背景滚动（产品列表、补货页的同类弹层都做了）。键盘用户关不掉，窄屏上手指划的
  其实是背后的装箱表。
- 改动：`src/pages/Packing.tsx` 加一个 effect：有弹层打开时锁 `body` 滚动，Esc 关
  「最上面那层」（确认删除 > 编辑产品 > 查箱子），关闭后恢复原滚动值。
- 复现：把 Esc 分支短路 → 「Escape closes 「查箱子」」转红（已还原）。
- 复测：同上 40 条断言里的 4 条弹层断言。

### AUDIT-PORT-01 四个浏览器审计各自现挑端口（2026-10-23）

WorkBuddy；基线 `8f3a1df552`。

- 症状：`test-responsive-layout.py` / `test-packing-oms.py` 写死 5173、
  `test-contrast-audit.py` 5183、`test-text-overlap.py` 5199。本机同时有另一份工作副本
  在跑 selfcheck 时（实测 01:16 有另一个 `_repo` 的 pytest 在跑），两边探到的是**对方**
  起的服务，先结束的一方 teardown 把对方正在用的服务杀掉 → 随机出现
  `ERR_CONNECTION_REFUSED`、或「按钮找不到」这类假红；vite 在端口被占时还会**静默**
  换端口，断言就可能打在另一份构建上而结论写着「通过」。
- 改动：新增 `scripts/_audit_server.py`（`free_port()` + `vite_server()` 上下文管理器，
  带 `--strictPort`、就绪超时、terminate→kill 兜底），四个审计改为从它取 BASE。
- 复现：并发跑 `test-packing-oms.py` 与 `test-responsive-layout.py`，改前两者抢 5173
  （其中一个必然半路掉线），改后两个都 exit 0。
- 复测：全量 selfcheck 36 步通过，四个审计各自的真实浏览器断言全过。
