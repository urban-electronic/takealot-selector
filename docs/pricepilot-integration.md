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

PR103候选99d6bc5的15项测试通过，但补测发现吞领取错误及30秒截止后迟到租约丢失，未采纳。Codex独立修正main领取中取消：保留在途HTTP任务、异步等待后释放、重复取消安全、错误不冒称空闲；4项门闩回归通过，同步原生ZIP。细节与真实Windows限制见[领取取消审核](review/2026-10-16-claim-cancel.md)。真实采集未恢复的验收结论不变。

## 2026-10-16 原生运行可靠性集成

独立采纳WorkBuddy PR103/7d4a3a5的原生运行修复，补Codex写盘失败后排队任务停止、模块导入和实际状态回调测试；相关75项通过（非全后端/真实采集）。原生ZIP10成员核对完整，未改价格/解析器/身份保护。真实Windows登录采集同轮仍待验收，部署不会自动替换本机进程。详见[集成验收](review/2026-10-16-native-runtime-integration.md)。

## 2026-10-17 系统维护：库存调整重复提交与数量精度

用户要求各流程运行检查。本轮复现库存页连续确认发出两次记账请求、1.5被parseInt截为1；已补同步提交互斥、请求期间锁定编辑及安全整数校验，失败保留草稿可重试。实际React组件隔离API10项交互断言通过，后端248通过/1浏览器缺失跳过、采集相关82通过；其他前端业务回归及构建通过。真实窗口因Chromium下载截断未执行，不冒称所有页面视觉或生产业务验收。详见[运行维护记录](review/2026-10-17-system-maintenance.md)。原Mac安全候选与真实报价回传闭环仍按PR103接续，不恢复真实调价。


## 2026-10-17 03:40 实际运行接续

PR115后端同步路由已在生产openapi出现，修正此前临时未上线判断；本轮库存/在途/装箱/补货38项及库存组件10交互断言通过。真实装箱更正因当前浏览器认证未保留而未提交，默认0不作库存事实。PR114候选flow64通过，但补测合法租约与畸形ID混合导致TypeError且未释放；PR113新脚本在最新main实际MODULE_NOT_FOUND，均已退回相关PR。WorkBuddy已报告26个生产products探针租约未归还，要求停止有副作用的健康压测，仅正常领取并finally清理本人租约。详细版本、复现、证据来源及验收边界见[本轮运行复核](review/2026-10-17-runtime-followup-0340.md)。真实同轮行情回传仍无新证据，不恢复调价。


## 2026-10-17 04:40 库存交互检查可复现性

WorkBuddy尚未更新PR113，Codex独立采纳其边界交互用例，补PR115新增组件替身、jsdom开发依赖与lockfile，统一test:inventory入口。全新空目录npm ci及两组实际React交互检查通过，未复用已有node_modules；后端相关38项复测通过。线上前端200、两条同步路由可见，未认证库存401仍不能作为真实库存或业务通过。真实装箱更正与目标机报价闭环阻塞不变，PR114上轮失败未追认修复。见[验收证据与贡献来源](review/2026-10-17-inventory-test-reproducibility.md)。

## 2026-10-17 05:40 异常领取租约清理

WorkBuddy PR114未更新；Codex在正式main3bfcb84源码独立复现混合异常ID的TypeError与合法租约未归还，最小修复批次形状校验、清理合法ID、取消后迟到异常响应清理，保留shield与取消语义并同步下载包。flow65项通过；可操作错误提示仍待接续，真实本机报价与实际库存更正未完成，不恢复调价。详见[同条件复现及验收边界](review/2026-10-17-malformed-claim-cleanup.md)。

## 2026-10-17 06:40 服务错误提示接续

WorkBuddy PR114仍未更新；Codex在main a73568a实际run入口复现HTTP403被误报连接超时，独立实现固定类别ServiceError并保留既有精确说明白名单，未知异常不输出正文。上轮77项复测通过，新增实际Api→run→状态/日志8项后完整85项通过，同步原生ZIP。代码/入口测试不等于Windows GUI或真实报价闭环；本机复测任务继续通过PR103/114交接，369件业务更正仍未提交。见[复现与验收](review/2026-10-17-service-error-classification.md)。

## 2026-10-17 17:38 协作报告审核与运行复测

基线main ce6d169（PR121翻页、PR123等待状态、PR124中英文名称、PR125标题溢出及折叠说明已上线）。Pages39019253367与该SHA的Railway状态均success；部署状态不是实际业务验收。

新增证据来自WorkBuddy PR126只读报告，Codex未独立读取其生产会话：377条商品中，SKU9902416416912对应中文字段为Gold/Silver；SKU9902348624058报告16:58:55的一轮R269/Urban Electronics记录。没有数据写回证据，中文数据更正仍未完成，不据此宣布稳定采集。报告G2表实际17行但统计写16、类别有交集且未给去重ID，36/74不能作为已核实的唯一商品数量；规格标签、父级英文标题不单独证明SKU错配。own_price_asof是本店售价时点，当前request_once=false不证明历史排队原因。已在PR126评论6232385441要求修正统计、提供快照同轮时间/source并接续确定品名更正，银色材质不能由Gold标题猜测。

执行验收：最新main实际React组件48项Buybox断言与翻页交互检查通过，覆盖长标题完整title、默认折叠、中英文及等待状态。此为隔离组件运行，不是生产窗口几何验收；相同窗口长标题复测仍待PR120本机截图。PR120头afd72fc未更新，同条件假浏览器入口30个goto全部抛错仍正常返回TOTAL0/0，装箱sync-from-products仍不在拦截列表；未对生产执行该审计，继续不合并。PR103实时head为ddb4dfcea8，与既知值一致，已要求WorkBuddy核对remote/ref，未重写其分支。

本轮另执行名称映射与native-collector/tests合计80项通过；该集合不等于此前85项后端加GUI集合。线上入口HTML可读且引用新布局CSS；只证明静态站点可访问，不作为生产商品或报价验收。

仍未验证：生产全库品名更正读回、真实目标Windows GUI同轮回传网页、369件库存/海外在途更正。已有授权会话一端继续处理，不转交密钥、Cookie或profile，不开启调价，不创建测试业务交易。

## 2026-10-17 审计阻塞直接修复

Codex独立接续WorkBuddy PR120/afd72fc审计脚本，修复生产写入漏拦和30ERROR仍TOTAL0正常退出，改用既有隔离fixture、context级默认拒绝未知/外部请求，页面标题与关键数据加载后才计入测量。原失败条件执行转为明确失败，10种请求拦截路径与生产base拒绝通过，接入发布前检查。贡献来源、运行证据及未完成真实Chromium验收见[审计修复](review/2026-10-17-audit-safety-fix.md)。本次未采纳PR120视觉整包、未修改生产业务数据或价格。

## 2026-10-17 20:35 可读性修正接续

直接采纳WorkBuddy PR130/1684e243的文字/徽标/控件修正，排除隐藏双城标签和窄屏导航改动，并将静态护栏接入网页发布前检查。13组静态护栏、翻页、库存基础10+edge、审计安全和Buybox48断言及构建通过；实际生产窗口未独立验收。范围及来源见[可读性采纳](review/2026-10-17-readable-controls.md)。PR126/122新文档尚无品名真实写回，库存369件亦不视为已完成。

## 2026-10-17 技能方法落地与刷新竞态修复

用户指定的三个唯一仓库已筛选；原始方法、固定版本、许可证和适用边界见[设计与验收规则](engineering/design-and-validation.md)，根AGENTS.md只保留入口与核心约束。未整包安装第三方工具或复制其脚本，不改现有视觉框架。

基线main 0a42a81：实际React页面隔离运行，未完成的初始读取期间点击刷新并触发轮询，会并发重复GET；另一同条件用例让暂停前请求晚于暂停后请求返回，旧价格/运行状态覆盖新状态，断言实际失败。Codex实现普通刷新共用在途请求、操作完成后独立读取、序号与卸载保护；迟到成功和失败不再覆盖新状态，按钮显示aria-busy并禁用。失败保留已加载数据，可立即重试。相关测试扩为62项断言通过（原48项+14项刷新生命周期），13组静态视觉护栏及构建通过，test:buybox接入Pages发布检查。

上述为隔离JSDOM程序验收，不是本机Chrome或真实暂停/改价操作；无生产写入。发布状态与静态资源版本须另行核对。真实SKU本轮回传网页、中文名写回与369件更正仍按既有协作任务接续，不能由本次刷新修复代替验收。

## 2026-10-17 Vercel界面规范实践

UI-KEYBOARD-01（Codex，P2）：基线0c8de0f实际隔离React运行，中文品名编辑没有可命名的键盘控件；规格编辑器role/名称为空、打开焦点留在背景、Escape不关闭，两次同帧保存触发2次更新。修复为原生行内编辑按钮/商品链接、明确表单标签，规格弹窗初始焦点/Tab循环/Escape/返回焦点与保存期间互斥；同条件两次保存仅1次fixture更新，SKU/规格对应保持。此次仅隔离内存API，无生产写入。

用户指定web-design-guidelines已按项目范围安装，源码/规则固定来源见工程设计文档。产品翻页原回归及新增键盘/弹窗/重复保存交互通过，Buybox62项、13组静态视觉约束与构建通过；全站不是100%规范合规，真实Chromium布局/键盘及Windows/Tauri仍需对应环境验收。保持现有圆润布局，去除两处transition:all，并加入数量等宽数字；不以静态颜色或JSDOM焦点断言代替浏览器窗口测量。Pages新增产品交互发布门禁。

## 2026-10-17 保存链路与审计接续

基线19cfecc；开放候选head未变。新增证据为WorkBuddy PR103评论6234658688、PR130评论6234671113：采购页不渲染SKU，现有审计统一等待TEST-001产生三种宽度的错误；其12组长标题/焦点证据来自WorkBuddy隔离浏览器，Codex未独立实测该窗口。

PRODUCT-INLINE-01（P1，Codex）：同一实际React组件、内存API延迟返回，中文名/售价/SKU回车后失焦触发两次更新；失败后编辑框关闭、草稿丢失。另独立复现SKU1改SKU1-NEW成功后装箱勾选仍存SKU1。修复为编辑会话及同步写入锁、pending字段锁定、失败保留草稿/错误/焦点并可重试、取消后迟到失焦不保存；SKU成功读回后同步已勾选SKU。三字段同触发复测与原翻页/规格弹窗回归通过。以上是隔离程序验收，无生产改名、改SKU或改价。

AUDIT-READY-01（P2，Codex，发现来源WorkBuddy）：采购审计改等候实际渲染的商品序号单元格，不伪添不存在的SKU。执行审计main的程序替身回归：无SKU但有采购记录可30/30测量；采购行缺失仍27测量/3错误并非0退出；全部导航失败仍拒绝通过，请求隔离红线保持。test:audit-safety共5组通过，不冒称真实Chromium30页已测。相关Buybox62断言、13组静态视觉护栏、构建通过；真实浏览器/Windows/Tauri读回另交接，实际业务更正及目标机报价验收不由本次代码修复替代。

## 2026-10-17 长标题/焦点候选审核

接续main33a0e647；新增WorkBuddy PR136/f0af6ee候选及PR103评论6235103092。PR135采购等待修复已在main，后续“仍等待修法”的旧说法不作新根因。无新生产名称写回、目标机报价或369件更正证据。

AUDIT-FOCUS-01（P1，Codex）：实际执行候选run入口的隔离程序替身，焦点圈缺失、零焦点采样均返回0；默认截图发生在Tab之后。Codex独立采纳候选，补无样本/不可见/不足3:1拒绝通过，测量JS检查零宽/透明并按透明度合成，默认截图提前、焦点截图单列；拦截器直接复用main函数，仅替换深拷贝fixture。报告版本改为AUDIT_REVISION输入，去掉永远声称旧main+PR130的固定文字。

test:audit-safety扩为原5组+新7组程序入口回归及实际焦点JS样式fixture检查，全部通过；测试覆盖缺失焦点、无样本、透明/零宽、低对比、导航全失败、有效路径与截图顺序。这里只是程序及算法验收，不是Chromium布局/绘制或Windows验收；WorkBuddy此前12/12/0报告不自动追认为本版结果。新脚本仍需在本机隔离环境运行，当前无浏览器实测计数，不为此重复安装浏览器或全量测试。应用界面、业务数据与调价逻辑未修改。

## 2026-10-17 合并前检查与发布覆盖

CI-GATE-01（P1，Codex；候选来源WorkBuddy PR139/8b91f474）：基线8ff48d4仅main push后检查，库存两组和图片回退脚本未进入发布步骤。独立采纳pull_request检查（只读contents权限、同PR旧运行取消），补库存/图片发布前检查及统一check入口；保留PR137扩展的审计链。AGENTS约定Codex合并前核对最新head与检查结果；工作流不等于仓库分支保护配置，不声称已设置平台强制required-check。

候选139的GitHub Actions39044231806实际通过全部检查与构建，Codex读取逐步骤结果并核对修改仅三处CI/npm配置；产品/库存/图片用例均用隔离fixture，不调用生产。正式采纳分支另核对自身PR检查和main发布结果。未再次手工重跑无变更业务套件；未修改应用/后端逻辑、依赖或真实数据。本机Chromium、真实SKU报价及业务更正验收不由流水线通过替代。

## 2026-10-18 诊断输出边界复核

DIAG-SAFE-01（P1，Codex）：基线 f4368de 新增任意标量诊断回传及异常原文输出。隔离数据库/内存采集器用 fixture-only 字符串复现：未知 cookie/debug_url 字段、URL 用户信息、preview 异常、monitor 启动异常均保留了不该回传的内容。四个同条件回归基线全部失败；修复后限定已知计数与 URL/验证标记，去除 URL 用户信息/查询串/fragment，异常仅保留类型和阶段。新诊断版本、节点数、卡片金额计数仍保留，不改变报价/变体/调价保护。

验收为实际 Python 路由、租约回写和 monitor.process 的隔离运行，未向生产注入诊断、未发起商品采集或调价；未读取真实密钥/Cookie/profile。线上仅核对部署版本及公开入口，不以健康接口代替真实报价验收。此前 PR147 的指定 SKU 图片代理 400→200 实测沿用原证据，不重复报作本次进展；Windows 同轮报价、中文名写回及369件业务更正仍需各自真实读回。

### DIAG-SAFE-01 再次回归（2026-10-18）

基线3c1eb21的循环状态改动重新引入preview.error_detail和monitor.failure_message，覆盖了PR148的异常原文保护。Codex只恢复这两处保护，保留worker心跳、守护线程、UI轨道与浏览器释放延时。用原fixture反例复测，不以新增心跳代替真实行情验收。该回归由直接进入main的改动带入；PR后端用例仍在，因此后续提交必须经过最新head检查，不得以此前通过结果代替新版本。

### DIAG-SAFE-02 历史记录读取边界（2026-10-18）

基线d2a1b2d的排期统计再次带回monitor.failure_message。除修正写入端外，在collection_diagnostics.displayed_monitor的统一读取边界去掉异常原文，覆盖顶层、diagnostics和snapshot.diagnostics；旧进程写下的记录也不会把异常正文带到页面。复制对应层，不修改原始历史记录；保留错误类型、计数与报价。实际执行同一历史记录fixture：修复前泄露断言失败，修复后通过，另两项诊断保护回归通过。完整API/worker回归由最新head的后端CI执行，未在生产注入故障或触发采集；最终提交、部署和真实报价仍分别验收。

### LOOP-HEARTBEAT-01 长轮次误报停止（2026-10-18）

Codex，P1；基线7dc9b30。实际start_worker循环接入隔离SQLite、受控Future和虚拟时钟，分别执行1个/3个两分钟任务；旧代码只在轮次开始写心跳，60秒后loop_status.worker_alive变false，两个同条件反例均失败。将等待并发槽位与最终任务完成统一为有界等待，由持有本轮的线程每10秒更新alive_at；不使用独立守护线程代报，不增加cycles、不更改报价时间/成功状态，不扩并发、不触发真实采集或调价。停止更新61秒后仍判离线。

同触发复测及loop_status、monitor、diagnostics、collector_bridge共57项通过；这证明隔离程序状态修正，不代表生产真实商品已采到。提交/CI/部署须按本修复PR核对，Windows同轮报价、品名与369件业务更正仍沿用各自验收标准。本轮新增main7dc9b30为装箱/显示修改；未将其提交说明中的自测计数当作Codex独立业务验收。

### COLLECT-BOUNDARY-01 自愈不得越过停采与网站拦截（2026-10-18）

Codex，P1；基线9d99147。隔离运行重现：403/验证页的0卡0价格计数先命中“骨架屏”分支，使同轮再次调用collect；停采错误记录被repair_monitor自动置request_once=True。新增与修改后的8个边界反例在基线失败。修复为先按已有classify识别网站拦截、限流及变体身份等明确失败，后判断空页面；移除停采商品自动重采授权，保留已存在的显式单次请求与历史错误归一。瞬时异常/真实骨架屏仍最多两次。

同条件复测与maintenance/diagnostics/loop_status共37项通过；monitor原20项亦通过（首次组合运行仅上述三个旧测试对停采归一后的字段预期不符，已拆开启用中分类场景）。无真实商品页面请求、未改价。版本部署和真实业务验收分别核对；新卖家名自动学习及真实多维变体采集仍需独立审核，不由本修复宣布通过。

### DROP-CAP-01 库存压力不得覆盖配置降幅（2026-10-19）

Codex，P1；基线453ace32。隔离运行plan：现价R200、现货300件、日销1件、竞争报价R150；配置max_drop_pct=5/15时实际目标均R149，突破应有R190/R170边界。两个同条件反例失败，50%配置的对照通过。移除滞销档自动将降幅放宽至30%的覆盖，统一遵守max_drop_pct（未配置默认15%）；保留库存压力、报价离群保护及手动身份确认。

同条件复测与guardrails/automatic_pricing/operations_planner共61项通过，均为隔离程序验收，无真实改价或生产业务写入。此前库存策略文档中的“滞销自动允许30%”由本记录更正；只有明确配置更大的max_drop_pct才可放宽。完整CI、部署版本及真实业务验收分别记录，未将外部提交说明当作真实采集成功证据。

### SELLER-IDENTITY-01 同价投票不构成本店身份（2026-10-18）

Codex，P1；基线958fc617。隔离SQLite中，同一个竞争卖家在三个SKU与本店同价，observe会将其存成source=auto，name_of随即把它当本店；旧快照holdingBuyboxVerified=True也被shared_snapshot直接接受，可能把同价竞争卖家误判成守位试涨。四个同条件边界用例在基线失败。

修复为投票仅保留候选，权威name_of只采用已手动确认的名字；读取历史快照时重新按当前确认身份判断持位及逐条offer.isMine，复制快照不改原记录。设置接口与页面标为候选待确认，不自动填入权威名。已有手动确认、同价对手识别和采集共享调度保持。身份/共享行情/bridge/调价程序56项及新增设置路由用例通过，均隔离运行无真实改价；完整CI、部署与真实SKU报价分别验收。

### UI-COPY-01 说明面板漏了降价闸（2026-10-19）

WorkBuddy；基线4472a5e3e4。AutoPricing页「调价与试涨规则」面板只列了涨价那三道闸（单次3%/间隔12h/累计20%），没写降价单步上限15%，也没写「库存紧张不跟降」，而这两条代码在2026-10-18/19已生效（DROP-CAP-01）。用户看到「不在持位只降不升」，却不知道一次最多降15%，会把分步跟价误读成系统卡住。

改动只有文案、一处重复常量和一个死常量：面板补两条说明；`plan()`的试涨理由不再硬编码「20%」，改读`DEFAULT_RAISE_CEILING_PCT`，与`_raise_ceiling()`同源，避免以后调上限时两处打架；删掉未被任何代码引用的`REGIMES`（界面用的是前端`REGIME`，且`blind`标签两边已不一致）。

复测：selfcheck 25/25全绿（216.0s），后端pytest 367 passed/1 skipped。未改调价参数默认值、未改数据模型、无生产写入，未触发真实采集或改价。

### MAINT-DEAD-01 清掉已废弃的「修复性重采」残留（2026-10-19）

WorkBuddy；基线ff9b8d7624。`collection_maintenance.py` 里留着 `REPAIR_COOLDOWN_SECONDS` 与一句「两次修复性重采之间至少隔 6h」的注释，但 COLLECT-BOUNDARY-01 已经把「停采记录自动置 `request_once`」整段移除，`repair_monitor()` 现在只改记录层、从不排采——这个冷却期没有任何调用方，注释描述的是一个不存在的行为。`docs/engineering/2026-10-18-collection-selfheal-and-variants.md` 的自愈小节也还在写「过 6h 冷却期就用 request_once 安排一次只读采集」，与代码相反。

改动：删掉该常量与注释；把文档那条改成「不自作主张排采」并注明被 COLLECT-BOUNDARY-01 取代；对应用例原名「修复性重采限速」但断言其实是 `request_once` 不被设置，改名为 `test_repair_never_grants_a_one_shot_collection`，断言一字未动（不自作主张排采这个不变量继续被盯着）。

复测：selfcheck 25/25 全绿（213.3s），`tests/test_collection_maintenance.py` 21 passed。无行为变更、未改调价参数、未动数据模型、无生产写入。

### SAVE-PARTIAL-01 保存区间不得重置未提交的参数（2026-10-19）

WorkBuddy，P1；基线 4472a5e3e4。`PUT /api/automatic-pricing/{product}/{offer}` 原来写的是 `{**old, **data.model_dump()}`，而 `model_dump()` 会把请求里**没传**的字段也按默认值填上。前端「保存区间」只显示最低价、最高价、策略三项，其余靠硬编码（`lead_amount:1, cooldown_minutes:10, max_daily_changes:10`），`max_drop_pct` 则从未提交。

后果：用户把 `max_drop_pct` 配成 30 之后，只要在界面上点一次「保存区间」，就会被静默改回 15；`lead_amount` 等三个参数同样被重置。改的是 A，坏的是 B，而且没有任何提示。

复现（隔离 SQLite，不碰生产）：先 `configure(enabled=True)`，把落库值改成 `max_drop_pct=30, lead_amount=2, cooldown_minutes=45, max_daily_changes=3`，再按界面实际提交的字段调一次 `configure(min_price=90, use_system_min=False, max_price=200, strategy=..., enabled=True)`；旧实现四个参数全部回到默认值。

改动：后端改用 `data.model_dump(exclude_unset=True)` 得到 patch，按 `{**Rule().model_dump(), **old, **patch}` 合并（新规则仍有完整默认值，老规则只覆盖真的传上来的字段），校验与 `minimum` 一律基于合并后的值；前端删掉三个硬编码参数，只提交界面显示且能改的字段。顺带把「最低价 > 最高价」的校验从「只比本次请求」改成「比合并后的最终值」，跨请求也能拦住。

复测：`tests/test_pricing_routes.py` 11 passed（新增 2 例：未提交参数保持原值、新规则仍拿到全部默认值）；后端全量 369 passed / 1 skipped；selfcheck 25/25 全绿（223.6s）。未改任何参数默认值、未动数据模型、无生产写入、未触发真实采集或改价。

### PACK-RENAME-01 改 SKU 不得清空人工资料（2026-10-19）

WorkBuddy；基线 e4b22a426c。`POST /api/packing/product` 带 `old_sku` 时走的是「删旧行 + 建新行」，新行的 `image_file` 一律是空串，`template_row`/`default_count` 落到 `None`/`1`，`variant_group`/`variant_label` 也回到空、`is_primary_variant` 回到 `True`。远程装箱单页（`backend/packing_ui/index.html`）的编辑框每次都带 `old_sku`，所以只是把 SKU 从 `OLD-1` 改成 `NEW-1`，已上传的图片、排好的模板行、默认件数和变体归属会一起消失，且没有任何提示。

改名是同一行的主键变更，不是新建。改成先用旧行的 `variant_group`/`variant_label`/`is_primary_variant` 建新行，再把 `old` 传给 `_apply_values()`（图片、模板行、默认件数本来就走 `old` 兜底），最后删旧行。目标 SKU 已被占用时仍然 400 且两行都不动。

### PACK-DUP-01 「N 条 SKU 重复」不再恒为 0（2026-10-19）

WorkBuddy；基线 e4b22a426c。`sync-from-products` 的响应里有 `duplicate_sku`，前端装箱单页据此提示「另有 M 条 SKU 重复，无法作为独立装箱产品」，但后端只声明、从未累加，`M` 永远是 0——同一个 SKU 被两个选品产品占用时，用户看不到这条提示。

按「一个 SKU 被第二个产品再次认领」计数（去重后返回个数，重复同步结果稳定），不动既有的 `seen` 去重与行刷新逻辑。

复测：新增 `tests/test_packing_rename_and_duplicates.py` 4 例（改名保图/改名撞车回滚/重复计数/无重复为 0），与既有三个 packing 测试文件共 15 passed；selfcheck 全绿。未改调价参数、未动数据模型、无生产写入。

### DIAG-VARIANT-01 清洗白名单漏了变体维度名（2026-10-19）

WorkBuddy；基线 046b70dd4071。`classify()` 靠 `diagnostics['variant_unmatched']` 点名「未能切到变体 Nude / L」，但 `safe_diagnostics()` 的白名单里没有这个键。转发采集器（`/api/collector/offers`）先清洗再分类，历史记录重判（`displayed_monitor()`）也只拿库里的 diagnostics——这两条路径上那句点名的消息永远出不来，只剩「未确认SKU对应变体」。多维度商品（颜色+尺码）少切了哪个维度因此看不出来，只能反复点采集。同一天工程文档写的还是「会点名是哪一段」，与代码相反。

复现（隔离，不碰生产）：`classify({'variantSwitchFailed':True,'diagnostics':safe_diagnostics({'variant_unmatched':['Nude','L']})})`，旧实现 message 里没有 `Nude / L`，只有「未确认SKU对应变体」。

改动：白名单保留 `variant_unmatched`，只收字符串、单项截 40 字符、最多 6 项（是商品规格名，不是页面正文；非列表、空串一律丢弃，不把任意文本当规格写进库）。

复测：`tests/test_collection_diagnostics.py` 14 passed（新增 2 例：清洗后仍点名、非字符串与超长被截断丢弃）；后端全量 377 passed / 1 skipped；selfcheck 25/25 全绿（211.6s）。未触发真实采集，未改价。

### UI-COPY-02 试涨两档写全（2026-10-19）

WorkBuddy；基线 046b70dd4071。说明面板写「约 3% 试涨，12 小时观察一次」，但 `_raise_pct()` 在 `tight` 档取 `tight_step_pct`（5%），`plan()` 把观察间隔减半（12h → 6h）。按面板估算涨幅与频率的用户，会在库存紧张时看到对不上的数字。同一份工程文档里其实已经写了「紧张档涨 5%」，只有界面漏了。

改动：同一句补「库存紧张时改为一次约 5%、6 小时观察一次」。只改文案，未动 `raise_step_pct`、`tight_step_pct`、`raise_interval_hours` 任何默认值，也未改 `plan()` 的判定。

复测：selfcheck 25/25（211.6s），含 `test-pricing-layout.cjs`。无行为变更。

### COLLECT-DEAD-01 删掉从没被调用的选择器健康度（2026-10-19）

WorkBuddy；基线 046b70dd4071。`pricepilot_collector.py` 的 `_selector_calls` / `reset_selector_stats()` / `_record_selector()` / `selector_health()` 全链路没有任何调用方：`selector_health()` 零引用，`reset_selector_stats()` 的 docstring 写着「每轮开始重置」也没人调，计数只增不减。而 `SELECTORS["buybox_card"]` 上方的注释写着「由 selector_health() 检测大面积失效并告警」——一个不存在的告警。维护者读到会以为改版有监控，实际生效的只有 `classify()` 的 `page_structure_changed` / `page_not_recognized`。

改动：删掉这组计数、`selector_health()` 与同样零引用的 `query_one_fallback()`；注释改成指向真正生效的 classify 分支。`_record_selector()` 都是纯计数调用，删除不改任何控制流，`[选择器降级]` 日志保留。

复测：`tests/test_pricepilot_variant_gate.py` + `test_buybox_monitor.py` + `test_collection_diagnostics.py` 44 passed；后端全量 377 passed / 1 skipped；selfcheck 25/25（211.6s）。未请求真实商品页。

### IMG-VARIANT-01 分支变体的图片不再永远是主变体那张（2026-10-20）

WorkBuddy；基线 971d646bed。`refresh-price`（`source=details`）旧逻辑是 `if result.get('product_image_url') and not p.product_image_url`：只在**完全没图**时写入页面主图，已有图一律不动。页面主图属于默认变体，所以「白色分支」第一次抓取拿到的就是黑色主图，之后再也不会变——`variants[].image_url` 里其实一直有本行 SKU 自己的图，但从未被读过。前端「补充缺图」按钮同样只筛 `!product_image_url`，这些行根本进不了待处理队列，按钮计数也不含它们。

改动（只动图片，不碰价格/成本）：`_sku_variant_image()` 只在本行 `sku` 恰好一个 token 且能在 `variants` 里精确命中时返回该变体图，多 SKU 汇总行与未命中行返回空串（不猜）；`_is_scraped_image()` 只认 `media.takealot.com`，即只替换上次自动抓的图，人工填写的链接不覆盖。没有变体图时页面主图仍只补空位。前端 `imageRefetchPending()` 除缺图行外，额外纳入「非主变体且图片与同组主变体相同」的行，并把这类行从「未获取图片，请人工上传」的失败提示里分出来，单独说明「官网没有独立变体图，仍沿用主图」。

复测：`tests/test_price_refresh.py` 新增 4 例（分支图替换旧主图 / 无变体图不回退成主图 / 人工链接不被覆盖 / 多 SKU 行不猜图）；后端全量 381 passed / 1 skipped；selfcheck 25/25 全绿（211.7s）。未改调价参数、未动数据模型、无生产写入、未请求真实商品页。

### COLLECT-DEAD-02 采集器里那个算错的 challenge 状态路径（2026-10-20）

WorkBuddy；基线 971d646bed。`tools/native-collector/collector.py` 顶部还留着 `CHALLENGE_PATH = APP_DIR / 'challenge_state.json'`，零引用；真正在用的是 `from challenge_state import challenge_state_path`——它按 profile 分目录（`base/<profile>/challenge_state.json`）。留着的那个常量指向全局路径，正好是 profile 化之前的旧写法，谁照着它改就会把多 profile 的状态写串。

改动：删掉该常量。同文件 `challenge_state` 的导入与全部调用不动，行为零变化。

复测：selfcheck 25/25（211.7s）——注意 selfcheck 只跑 `backend/tests`，不覆盖本目录；另单独跑 `tools/native-collector` 套件 72 passed / 1 failed，失败项是 `test_gui_refresh_state_shows_challenge_unreliable` 在 venv 里 `import tkinter` 失败（`ModuleNotFoundError: No module named 'tkinter'`），与本次删除无关。

### AUDIT-FIXTURE-01 文字重叠审计不再对未声明接口静默放行（2026-10-20）

WorkBuddy；基线 a030b3161641。`scripts/test-text-overlap.py` 拦截 `/api/*` 时，凡是 `test-responsive-layout.py` 的 `DATA` 里没有的路径，一律 `404 {'detail':'fixture missing'}` 后继续跑，既不记录也不失败。页面拿到 404 会静默降级成空列表/默认值，版式照样「0 处重叠」——这一屏等于没测过，却给了通过结论。同目录的 `test-responsive-layout.py` 会把未声明路径记进 `unknown` 并断言为空，`ui_contrast_audit.py` 记进 `errors`，只有这个套件漏了。

复现：把 `DATA` 里 `'/api/products':[PRODUCT]` 那一项临时删掉再跑 `--widths 390`，旧实现只报「弹窗打不开」，退出码 0；新实现额外点名 `审计 fixture 未声明接口：GET /api/products` 并返回 1。

改动：拦截器把未声明路径记进 `undeclared`（`METHOD path`），收尾时按去重排序逐条并入 `problems`，走既有的非 0 退出。判据没有放宽，只是补上了原本缺失的一条。

复测：`scripts/test-text-overlap.py` 全量 10 个宽度 × 160 个面，0 处重叠、无未声明接口；上面那次人为删 fixture 的探针确认会红（探针文件已删，`test-responsive-layout.py` 已 `git checkout` 还原）；selfcheck 25/25 全绿（209.2s）。

### DEAD-CODE-03 删掉两处零引用的实现（2026-10-20）

WorkBuddy；基线 a030b3161641。

① `src/operations/replenish.ts` 的 `computeReplenishBatch()` / `summarize()` / `ReplenishSummary` / 只服务于前者的 `LEVEL_ORDER`：全仓零引用。线上补货判断走的是单行 `computeReplenish()`（`OperationsAssist.tsx` 唯一导入项），批量排序与汇总另有一套死实现，等于「哪个 SKU 最急」有两份判据，改了线上那份另一份不会跟着变。删掉不影响任何调用方。

② `backend/services/translator.py` 的 `translate_via_google()`：零引用，且是全仓唯一一处把商品英文名发到 `translate.googleapis.com` 的异步实现；实际在用的是 `translate_to_chinese_sync()`（词典优先，未命中才用 `requests` 调同一个接口）。连带删掉因此变为未使用的 `import httpx`，并把「内部调用 httpx 同步」的注释改成它真正用的 `requests`——注释与代码相反比没有注释更容易误导。

复测：后端全量 381 passed / 1 skipped（与改动前同数，无用例增减）；selfcheck 25/25 全绿（209.2s），含 `test-operations.cjs`（直接 require 本文件并调用 `computeReplenish`）。未改调价参数、未动数据模型、无生产写入。

### UI-OWNERSHIP-01 卖家名核实过的持位不再显示成「疑似」（2026-10-20）

WorkBuddy；基线 a80c14788020。调价页「本店价 / Buybox」那一列的徽标只有一个「疑似在持位」，`src/utils/buyboxStatus.ts` 的注释还写着「后端 `identity_verified` 恒为 False」「`holding_buybox` 从不写 True」，并引用 `buybox_monitor.py:65` 的一句英文原文。这三条现在都不成立：`services/price_ownership.py` 的 `estimate()` 在 `verified` 为 `True`/`False` 时都会写 `identity_verified=True`，`buybox_monitor.py` 把 `holding_buybox` 的 True/False/None 三态如实落库，那句英文在该行已经不存在（第 65 行现在是骨架屏重试判断）。后果不是文案难看：调价引擎按 `holdingBuyboxVerified is True` 直接走持位提价，同一屏的徽标却说「价格相同不能证明是本店在占位」，两个结论互相打脸，用户没法判断这条 SKU 到底动不动。

复现（纯函数，不碰生产）：`describeOwnership({buybox_price:269, price_ownership:{state:'matched', identity_verified:true}})`，旧实现返回 `label='疑似在持位'`、`tone='warning'`，`explain` 里是「不能证明是本店在占位」——而这一行的证据恰恰是卖家名，不是价格。

改动：`describeOwnership()` 先按 `price_ownership.identity_verified === true` 分流——`matched` → 「已核实持位」（tone success，说明证据是页面 `Sold by` 的卖家名）、`mismatch` → 「不在持位 · 已核实」；没有 `identity_verified` 时一律维持原来的价格推断口径（「疑似在持位」「同价 · 卖家未核实」），历史记录里残留的 `holding_buybox=true` 依旧不给成功态。判据没有放宽，只是把第 1 级证据补上。注释改成如实描述两级证据。

复测：`test-buybox-copy.cjs` 238 断言（新增 4 条：核实态必须说已核实、必须是唯一的成功态、必须点名 `Sold by` 证据；核实过的 mismatch 必须说明是卖家名判的）；`test-buybox-status.cjs` 146 断言（新增 5 条，含「核实态不得再出现『疑似』」）；后端全量 381 passed / 1 skipped；selfcheck 25/25 全绿。未改调价参数、未动数据模型、无生产写入。

### DEAD-CODE-04 删掉三处零引用的定义（2026-10-20）

WorkBuddy；基线 a80c14788020。

① `backend/services/packing_excel.py` 的 `split_name()`：全仓零引用（`backend/`、`scripts/`、`tools/`、`docs/` 都搜过），纯函数无副作用；同段的 `infer_flags()` / `infer_material()` 才是真正被调用的推断工具。连带把段首注释里的「与本地 app.py 一致」去掉——本仓库没有 app.py，这句让人以为存在一个需要同步的外部文件。

② `src/api.ts` 的 `IMAGE_PROXY_BASE = ''`：零引用，且值是空串。真正的图片代理地址在 `remoteApi.getImageUrl()` 里按 `api_base_url` 现拼 `/api/image-proxy?url=…`；留着这个空常量，谁照着它拼 URL 都会拼出相对路径。

③ `src/remoteApi.ts` 的 `getBuybox()`：`GET /api/buybox` 的客户端包装，零调用方——页面走的是 `getAutomaticPricing()`（已把规则与 monitor 合并好）。后端路由保留不动（原生采集器仍在用），只删前端这份没人调用的包装。

复测：`tsc --noEmit` 通过（`noUnusedLocals` 开）；后端全量 381 passed / 1 skipped（与改动前同数）；selfcheck 25/25 全绿。未改调价参数、未动数据模型、无生产写入。

### UI-BAR-01 状态条收起时不再把「人工过验证」一起收掉（2026-10-20）

WorkBuddy；基线 89e1a92213。`AutoPricing.tsx` 的采集状态条改成「只在有事要做时出现」时，判据是 `fleet.action || canAct`——但这条状态条里还挂着两个跟 `fleet.action` 无关的按钮：`fleet.blocked > 0` 时的「人工过验证」、有失败行时的「复制诊断」。`describeFleet()` 的 `running` 分支（`if (running) return none('info', …, '')`）给的是空 `action`、并且 `none()` 固定把 `suggestedInterval` 置为 null，于是 `canAct = retryable > 0 || (blocked > 0 && !!suggestedInterval)` 为假。结果是「一行正在采集 + 一行被 `site_challenge` 挡住」时 `blocked=1`、状态条整条消失，两个按钮一起没了——而这正是最该去过验证的时刻。

复现（playwright + 现有 fixture，不碰生产）：`/api/automatic-pricing` 返回两行，一行 `status=running`、一行 `status=error / error_code=site_challenge` 且无可用报价；旧实现 `barShown=false`，轨道仍写「1 需处理」，按钮无处可点。jsdom 版断言见 `test-buybox-status.cjs` 新增的 `fleetScenario`。

改动：把显示判据提成 `barUseful = !!fleet.action || canAct || fleet.blocked > 0 || hasErroredRow`，即「状态条的可见性 = 条内各元素自身条件的并集」；`hasErroredRow` 沿用「复制诊断」原来的 `status === 'error'` 口径，判据没有放宽也没有收紧。整个 fleet 都正常时四项全假，状态条仍然不出现——折叠的初衷保留。

复测：`test-buybox-status.cjs` 151 断言（新增 4 条：被挡时状态条必须在、`人工过验证` 必须在、`复制诊断` 必须在、全健康时必须没有状态条）；把 `barUseful` 临时改回 `fleet.action||canAct` 后新断言确实转红（`test-buybox-status.cjs:236`），改回即绿。后端全量 381 passed / 1 skipped（未动后端）；selfcheck 25/25 全绿（209.2s）。未改调价参数、未动数据模型、无生产写入、未触发真实采集。

### AUDIT-PANEL-01 调价页「更多」面板纳入版式审计（2026-10-20）

WorkBuddy；基线 89e1a92213。`0b395e8` 把 8 个次要动作收进默认收起的「更多」面板，`test-responsive-layout.py` 只渲染页面、不点开关，所以这 8 个控件（含「停止全部调价」）在任何宽度下都没被审计过——包括这条折叠本来要解决的 320px。审计里其它弹层（卖家资料预览、装箱编辑、补货设置、管理工具菜单）都是点开再量的，只有这个面板漏了。

改动：`/auto-pricing` 在 1280/390/320 三个视口下点开「更多」，断言面板本身不越界、`scrollWidth` 不溢出、面板内每个 `button/input/select` 都在视口内、整页不因此产生横向滚动，最后点「收起」并确认面板真的消失。判据是新增的，没有放宽任何既有断言。

复测：全量审计 99 个「宽度×页面」组合 0 处失败、0 个未声明接口、0 个 page error；反向探针——给 `.actions-more-panel` 临时加 `min-width:2000px` 后，1280/390/320 三档全部报 `more panel clipped` 并退出码 1，探针已还原（`src/index.css` 无残留）。selfcheck 25/25 全绿（209.2s）。

### AUDIT-CONTRAST-01 对比度审计真正接入门禁（2026-10-20）

WorkBuddy；基线 3e59a00eee。`scripts/ui_contrast_audit.py` 一直在仓库里，注释写着「低对比均退出非0」，但**没有任何门禁执行过它**：`scripts/test-ui-audit-safety.py` 用假浏览器跑的是它的失败路径（`FakeBrowser.goto` 直接抛错、`evaluate` 恒返回 `lowContrastCount: 0`），验证的是「审计会报错、拦截器不碰生产」，量出来的全是桩数据。结果是真实页面的对比度从建库到现在一次都没量过，而 selfcheck 的结论仍然写着「视觉一致性通过」——和「审计静默跳过却报全部通过」是同一类假绿，只是这次连「跳过」都没显示。

改动：新增 `scripts/test-contrast-audit.py`，起本机 Vite（127.0.0.1:5183，`--strictPort` 避免端口漂移后量到别的服务）、把 `OUT` 指到临时目录（不往仓库里留未跟踪文件），再以 `runpy` 调审计本体的 `main()`。判据一个字没改：未测到页面、任何请求被拦、任何一处低对比文字都算失败。`scripts/test-*.py` 由 selfcheck 自动纳入，因此不需要改 `package.json` 或 CI 清单。

复现（不碰生产）：给 `src/index.css` 末尾加 `.operations-workspace .data-table small.quote-sub{color:#f2f4f6}`，旧状态下没有任何套件会红；新套件在 `/auto-pricing` 的 1366/768/390 三档各报 1 处低对比并以 `RuntimeError: 视觉审计未通过` 退出 1，探针已还原。

复测：新套件 30 个「路由×宽度」面全部测到、0 处低对比、0 个被拦请求、0 个 page error，用时 20.6s；全量 selfcheck 26 步全绿（230.2s）。

### AUDIT-DETAIL-01 调价页「详情」里的决策卡片纳入版式审计（2026-10-20）

WorkBuddy；基线 3e59a00eee。`/auto-pricing` 每行的「详情」默认收起，决策卡片（regime、把握、风险、底价、上限、对手最低、库存压力、剔除异常报价、今日改价、原因长文案）全在里面。版式审计只渲染页面、不点开关，所以这块在任何宽度下都没被量过——而它恰恰是这一屏最长的一块：10 个 chip 加一段长中文原因，窄屏最容易挤爆。更隐蔽的是 fixture 的 `rule` 里根本没有 `plan`，就算点开也只会渲染空卡片，审计照样报「0 处问题」。这与 AUDIT-PANEL-01（「更多」面板）是同一类漏测。

改动：① `test-responsive-layout.py` 的 `RULE` fixture 补一份真实形状的 `plan`（含 `pressure='tight'`、`cover_days`、`outliers=2`、长原因文案）和「今日改价」计数（`change_day` 按约翰内斯堡当天算，UTC+2 无夏令时，不用 zoneinfo 避免 Windows 上缺 tzdata）；② 审计在 1280/390/320 三档点开首行「详情」，断言卡片确实渲染且高度不小于 40px、卡片内容不溢出自身（`scrollWidth<=clientWidth+1`）、`details` 容器不溢出、每个 chip 都落在卡片横向范围内、打开后整页不产生横向滚动，最后收起并确认卡片消失。判据是新增的，没有放宽任何既有断言；表格本身横向滚动的既有豁免保持不变，所以断言的是「内容在卡片内换行」而不是「卡片不超过视口」。

复现：给 `.pricing-table .decision-card` 临时加 `min-width:2000px`，三档全部报 `row details content overflows`（探针已还原）。

复测：全量版式审计 99 个「宽度×页面」组合 0 失败、0 个未声明接口、0 个 page error；`test-text-overlap.py` 用同一份 `DATA`，同步 0 处重叠；全量 selfcheck 26 步全绿。

### THROTTLE-ONE-01「被网站挡住」只保留一份判据（2026-10-20）

WorkBuddy；基线 3e59a00eee。同一个错误码集合（`site_challenge` / `rate_limited` / `http_forbidden`）在前端有两份字面量：`utils/buyboxStatus.ts` 的 `THROTTLED`（决定 `fleet.blocked` 和建议间隔）与 `AutoPricing.tsx` 里 `retryFailed` 用的 `BLOCKED`（决定「重试」是清 `next_due` 立刻再打一次，还是只调间隔）。两份一旦漂开，就会出现状态条劝「把间隔调到 120 分钟」、同一屏的重试按钮却在清退避立刻重试——正是那段注释在防的事，也是后端 `buybox_monitor.THROTTLED_CODES` 存在的理由。

改动：`utils/buyboxStatus.ts` 导出 `isThrottled(code)`，`describeFleet` 内部两处判断改用它；`AutoPricing.tsx` 删掉本地 `BLOCKED` 改为导入同一个函数。行为不变（集合内容一字未动），只是把「谁算被挡住」收敛成一处。

复测：`test-buybox-status.cjs` 151 → 159 断言，新增 8 条钉住「三个码必须落在 blocked、绝不落在 retryable」「`collector_unavailable` 可自愈所以不算 blocked」「错误码缺失不算 blocked」；把集合里的 `http_forbidden` 临时改成 `http_403` 后新断言转红（已还原）。`tsc --noEmit` 通过（`noUnusedLocals` 开）；未动后端，pytest 381 passed / 1 skipped。

### AUDIT-CONTRAST-02 对比度审计补上详情页与展开态（2026-10-20）

WorkBuddy；基线 05eb61012e。`docs/engineering/design-and-validation.md` 已经写明「折叠的界面必须在量之前打开」和「每一屏都要真量到」，但对比度审计只做默认渲染态：`ROUTES` 里没有 `/products/fixture-product`（产品详情页——费用表、利润拆解、长标题都在这一屏，版式与文字重叠审计都覆盖了它，只有对比度漏了），`/auto-pricing` 的「更多」面板（8 个次要动作）和每行「详情」里的决策卡片（10 个 chip 加一段长原因文案）也从不展开。这两处都默认收起，收起时连节点都不存在，所以报告里的「0 处低对比」对它们从来不是结论，只是没量。

改动：① `ROUTES` 补 `/products/fixture-product`，覆盖面 30 → 33 个「路由×宽度」；② `/auto-pricing` 在测量前点开「更多」并展开首行「详情」，等 `.actions-more-panel` 与 `.decision-card` 真正渲染后再量。判据一个字没改：任何一处低对比仍然直接失败。

复现：给 `src/index.css` 末尾临时加 `.decision-card span{color:#f2f2f2!important}`，旧审计（不展开）照样全绿；新审计在 `/auto-pricing` 三档报低对比并以 `RuntimeError: 视觉审计未通过` 退出 1，探针已还原（`src/index.css` 无残留）。

复测：33/33 面全部测到、0 处低对比、0 个被拦请求、0 个 page error；全量 selfcheck 26 步全绿。

### RETRY-ONESHOT-01「重试」不再对一次性采集失败的行空转（2026-10-20）

WorkBuddy；基线 05eb61012e。`describeFleet()` 把「启用中 **或** 点过立即采集（`request_once`）」的行算作失败，所以状态条会写「1 个 SKU 采集失败」并给出「重试 1 个」；但 `AutoPricing.retryFailed()` 只挑 `status==='error' && enabled` 的行，而「立即采集」是一次性请求——`enabled` 是 false，`request_once` 才是它的开关。结果是：一条「立即采集」失败的 SKU，状态条说可以重试，点下去一条请求都不发，既不报错也没有任何提示，按钮看起来像坏了。混在自动采集的行里时更隐蔽：状态条写「重试 2 个」，实际只发 1 条。

改动：`utils/buyboxStatus.ts` 抽出 `failingRows(rows)`（状态条与重试共用同一份失败集合）和 `retryGroups(rows)`（按「被挡住 / 可立即重试」× 「自动 / 一次性」分批）。`describeFleet` 改用 `failingRows`；`AutoPricing` 的 `retryFailed` 改为逐批提交，**保留每行原本的意图**：启用中的行仍然 `enabled:true`，一次性请求的行仍然 `enabled:false, request_once:true`（不会顺手把用户没要求开启的连续采集打开），被网站挡住的行仍然 `keep_backoff:true` 只拉长间隔。判据没有放宽，只是让两处用同一个。

复现：`row({status:'error', error_code:'collector_error', enabled:false, request_once:true})`，旧实现状态条显示「重试 1 个」但点击后 `bulkBuyboxMonitor` 零调用；新实现调用一次且 `enabled===false, request_once===true`。

复测：`test-buybox-status.cjs` 159 → 175 断言（新增 16 条：一次性请求失败必须计入状态条、点击必须真的发请求、必须保持一次性、混合场景必须按各自意图分两批提交且总数等于状态条数字、`failingRows`/`retryGroups` 的纯函数口径）。把 `retryFailed` 临时改回 `rows.filter(r => r.monitor?.enabled)` 后新断言转红（已还原）。`tsc --noEmit` 通过；未动后端。

### DEAD-UI-01 删掉两个从未挂载的组件（2026-10-20）

WorkBuddy；基线 05eb61012e。`src/operations/OperationsAssist.tsx` 与 `src/operations/BrowserCollector.tsx` 全仓零引用：前者是 5218929 引入的本地离线计算面板（调价 / 补货 / 选品三个 tab），后者是 c0e7cf6 引入的浏览器扩展状态面板。两者都没有被任何路由或页面 import，`src/App.tsx` 的 12 条路由里没有它们，`Replenishment.tsx` 用的是 `PipelinePlan.tsx`（走后端 `operations/profile`），`AutoPricing.tsx` 的采集维护用的是 `CloudBrowser.tsx`。DEAD-CODE-03 那条记录写着「线上补货判断走的是单行 `computeReplenish()`（`OperationsAssist.tsx` 唯一导入项）」——这句现在会把人引到一个根本没挂载的组件上，比没有注释更误导。

改动：删掉这两个文件。`browser-collector-panel` 的样式**保留**——`CloudBrowser.tsx` 还在用它。`src/operations/{pricing,replenish,recommend,keywords}.ts` 这四个引擎模块**保留**：它们仍由 `test-operations.cjs` 直接编译并断言（`evaluatePricing` 17 例、`computeReplenish` 4 例、`buildSuggestions` 等），删掉等于连带删掉这些断言，属于本轮明确不做的事。副作用如实记录：删掉 UI 之后，这四个模块只剩测试在调用，没有生产调用方。

复测：`tsc --noEmit` 通过（`noUnusedLocals` 开，删掉的文件不在任何 import 图里）；`test-operations.cjs` 照旧全绿；全量 selfcheck 26 步全绿。

### COLLECT-WATCHDOG-01 卡死的采集任务不再冻住整条循环（2026-10-20）

Codex（zengtian798-alt，提交 5db40cc）；基线 f4175cddaa。线上 2026-10-20 07:31(GMT+8) 起采集全停：一个采集任务卡在浏览器里没返回，循环在 `_wait_with_heartbeat` 里无限等，而等待期间每 10 秒刷新心跳，于是界面一直写「采集循环运行中」，实际 17 分钟没开新一轮、26 个 SKU 排队不动。补记本条是因为原提交只写了 `docs/engineering/2026-10-20-collection-watchdog.md`，没有在本文件留条目。

改动：① 单个采集任务加 240 秒硬上限 `JOB_TIMEOUT`，到点放弃、记 `collect_timeout`、排到下一轮；② `ThreadPoolExecutor` 不再用 `with`（卡死任务会让 `__exit__` 的 `shutdown(wait=True)` 无限等）；③ 浏览器锁改成有上限的 `acquire(90s)`，拿不到抛 `CollectorBusy`；④ `loop_status` 新增 `cycle_age_seconds` / `stuck_at` / `stuck_jobs`。

复测：`test_collection_watchdog.py` 4 例；后端 pytest 388 passed / 1 skipped（本机复跑 388 passed / 1 skipped）。

### REFRESH-REASON-01 刷新售价失败不再只有一句话（2026-10-20）

Codex（zengtian798-alt，提交 5db40cc）；基线 f4175cddaa。同一次事故里，「刷新售价」对本店报价和官网售价都只回「暂时无法读取」，密钥失效 / 限流 / 网络不通 / 代码报错四种原因长得一模一样，用户只能反复点重试。

改动：新增 `product_routes._read_reason()`，把失败归到「读取超时 / 限流暂停 / 连不上上游 / 上游 HTTP 状态 / 缺组件（部署问题）/ 内部错误 <类型>」，只透出固定类别或上游已写好的中文提示，不含路径、密钥、异常正文。售价刷新（503 / 502）与 `/scrape-takealot`（原来直接冒 500）都带上原因。

复测：`test_price_refresh.py` 3 例；后端 pytest 388 passed / 1 skipped。

### WATCHDOG-RETRY-01 浏览器被占用按用户间隔重试，不再指数退避到 6 小时（2026-10-20）

WorkBuddy；基线 f4175cddaa。COLLECT-WATCHDOG-01 引入 `collector_busy`（浏览器被卡死任务占着）后，这个码没进 `TRANSIENT_CODES`，落进「自身故障」的指数退避：30 分钟间隔下第 5 次失败排到 80 分钟后、第 7 次封顶 6 小时。浏览器一旦恢复，这一行还在睡，而 `buyboxStatus.ts` 给 `collector_busy` 的文案写的是「等下一轮自动重试」——文案与行为互相矛盾，也正是 `TRANSIENT_CODES` 注释在防的那类「把一次抖动放大成 6 小时停采」。

改动：`backend/services/buybox_monitor.py` 的 `TRANSIENT_CODES` 补 `collector_busy`（与 `collector_unavailable` 同类：可自愈的瞬时故障，按用户间隔重试、不低于 30 分钟）。判据没有放宽：真正的解析器/配置故障仍然是 6 小时封顶的指数退避。

复现：`run(monkeypatch, raises=CollectorBusy(...), failures=5)`，旧逻辑 `next_due` 落在 80 分钟后，新逻辑落在 30 分钟内。

复测：`test_buybox_monitor.py` 新增 1 例（20 → 21 passed）；把 `collector_busy` 从元组里去掉后该例转红（已还原）；后端全量 pytest 389 passed / 1 skipped。

### WATCHDOG-SLOW-01 慢轮次不再被报成「采集循环卡住了」（2026-10-20）

WorkBuddy；基线 f4175cddaa。`AutoPricing.tsx` 的 `cycleStuck` 只拿 `cycle_age_seconds` 和「设定间隔 ×2 + 180 秒」比，但 `cycle_age` 里本来就包含「这一轮正在采」的时间：28 个 SKU 一轮实测 20 分钟时，5 分钟间隔下 13 分钟就报红，文案还劝用户「重启服务」——把健康的循环说成故障。同一段文案还无条件写「卡住的采集任务已放弃」，可 `stuck_jobs`（后端专门为此新增的字段）从没被读过：一个正常的慢轮次也会被说成「任务已放弃」。

改动：基准改成「设定间隔」与「实测整轮耗时」取大者（`expectedCycleSeconds`），再留一倍余量；「已被放弃」只在 `stuck_jobs > 0` 时才说，并在超时说明里带上「一轮约 N 分钟」。判据没有放宽——真正卡死（`cycle_age` 超过 2 倍预期 + 3 分钟）仍然报红并给处置建议。

复现：`round_seconds:1200, round_jobs:28, interval_minutes:5, cycle_age_seconds:900`，旧逻辑报「采集循环卡住了 / 重启服务」，新逻辑报「采集循环运行中」。

复测：`test-buybox-status.cjs` 179 → 184 断言（新增 5 条：慢轮次不算卡死、不用 danger 色、不出现「卡住」；无 `stuck_jobs` 时不许说「已被放弃」；`stuck_jobs=1` 时必须说）。把 `expectedCycleSeconds` 里的整轮耗时项改回 0 后新断言在 `test-buybox-status.cjs:154` 转红（已还原）。`tsc --noEmit` 通过（`noUnusedLocals` 开）；全量 selfcheck 26 步全绿。

### EXPORT-SAFE-01 导出到云端不再把「读不到」当成「0 条」（2026-10-20）

WorkBuddy；基线 4916e8387c。`Settings.tsx` 的「一键导出到云端」用 `try { rules.push(...r) } catch {}` 读费率映射规则：读失败就带着一张**空表**继续导出，界面还写「导出成功！fee_mapping_rules: 0」。而 `backend/main.py` 的 `bulk_migrate` 是「先按 payload 里的表名 DELETE，再逐行 INSERT」——`payload_tables` 包含 `fee_mapping_rules` 时，云端那张表会被清空、且一条也不会插回去。本地模式下 `getFeeMappingRules()` 走 Tauri `invoke`，在浏览器里必然失败，所以这条路径不是理论风险：用网页版点一次「导出到云端」就能把云端的映射规则清干净，且没有任何提示。

改动：新增 `src/utils/exportPayload.ts` 的 `buildExportPayload()`，把「读不到」与「本地确实是 0 条」分开——`feeRules === null` 时整表省略（后端根本不会碰它），空数组照常同步；`Settings.tsx` 改用该函数，成功提示对省略的表写「未同步（本地读取失败，云端原值保留）」而不是「0」。判据没有放宽：其余读失败（products / fee_categories / settings）仍然中止整次导出。

复现：`buildExportPayload({..., feeRules: null})`，旧实现（`rules: []` 直接进 payload）会让 `payload` 里出现 `fee_mapping_rules` 空表；新实现在 `skipped` 里点名且不出现该表。把 `exportPayload.ts` 的判断改回 `rows: source.feeRules || []` 后 `test-export-payload.cjs:39` 转红（已还原）。

复测：新增 `scripts/test-export-payload.cjs` 13 条断言（读不到必须省略 / 0 条仍同步 / 读到的原样带过去 / settings 逐条转 key-value / 表名不重复）；`tsc --noEmit` 通过（`noUnusedLocals` 开）；全量 selfcheck 27 步全绿。

### DEAD-CODE-05 删掉 `buildSearchQuery` / `salientTokens` 与两个 storage 读取封装（2026-10-20）

WorkBuddy；基线 4916e8387c。`src/operations/keywords.ts` 的 `buildSearchQuery()` 全仓零引用，它唯一的下游 `salientTokens()` 也只被它调用——删掉前者，后者立刻变成同一簇死代码；`src/utils/storage.ts` 的 `safeGetItem()` / `safeRemoveItem()` 同样零引用（只有 `safeSetItem()` 被十几处写入点使用，也是该文件注释里唯一描述的行为）。

改动：删掉这四个函数。`normalizeTitle()` / `tokenize()` / `extractBrand()` / `isNoise()` 保留——前三个仍被 `recommend.ts` 与 `test-operations.cjs` 使用。判据没有动：`test-operations.cjs` 的 22 条断言一条未改。

复测：`tsc --noEmit` 通过（`noUnusedLocals` 开，证明没有残留引用）；`test-operations.cjs` 22 条断言全过；全量 selfcheck 27 步全绿。

### COLLECT-DIAG-02 看门狗放弃的任务不再被旧 diagnostics 改写（2026-10-20）

WorkBuddy；基线 c75a5abcbc。COLLECT-WATCHDOG-01 新增了 `collector_busy` 与 `collect_timeout` 两个错误码，但 `collection_maintenance.DECISIVE_CODES`（「已定位根因、不许再拿 diagnostics 重判」的白名单）没有跟着更新。自愈每 5 分钟跑一次，于是这两个结论在写进库后的 5 分钟内就被改写成别的：

- `collector_busy` 的 diagnostics 只有 `{'stage','failure'}`（这一轮压根没打开商品页，`process()` 在 `finished=False` 分支只记失败类型）。`classify()` 拿它判会落到 `price_not_found`，界面于是显示「页面打开了但没有 Buybox 价 / 人工打开页面看看是不是已下架」——把一把没释放的浏览器锁说成商品下架，方向完全反了。
- `collect_timeout` 沿用上一轮的 diagnostics（`_record_stuck` 只改结论、不清旧计数）。旧计数里的 `buybox_cards=0` / `price_nodes=12` 会被判成 `page_structure_changed`，也就是把浏览器卡死说成网站改版、劝人去改解析锚点。

改动：① `DECISIVE_CODES` 补 `collector_busy` / `collect_timeout`；② `_record_stuck` 落库前 `pop('diagnostics')`——旧计数不许挂在新结论名下，`process()` 对「这一轮没跑完」本来就是这么处理的，同一模块两种口径不一致本身就是 bug。`snapshot`（上一份报价快照）保留不删。

复现：`repair_monitor({'error_code':'collector_busy','diagnostics':{'stage':'catalog','failure':'CollectorBusy'}, ...})`，旧实现返回 `price_not_found`，新实现原样返回 `collector_busy`；`collect_timeout` + `{'buybox_cards':0,'price_nodes':12}` 旧实现返回 `page_structure_changed`。把 `DECISIVE_CODES` 里那两个码去掉后，`test_collection_maintenance.py` 两条新用例转红（已还原）。

复测：`test_collection_maintenance.py` 新增 2 例、`test_collection_watchdog.py` 新增 1 例（放弃的任务必须丢掉旧 diagnostics，且不许删 snapshot）；后端全量 pytest 393 passed / 1 skipped。

### INTERVAL-NULL-01 `interval_minutes` 为 null 不再 `None * 60` 打断整轮采集（2026-10-20）

WorkBuddy；基线 c75a5abcbc。`buybox_monitor.process()` 在 `finally` 里算 `next_due`，用的是 `config.get('interval_minutes',5)*60`。`.get(key, 5)` 只在**键缺失**时给默认值——键存在但值是 `null` 时返回 `None`，`None * 60` 抛 `TypeError`。同一模块另外三处（`_record_stuck`、`repair_monitor`、`loop_status`）都用 `isinstance(int)` 归一，只有这一处没有。

后果不是「这一行排期错」，而是整轮停摆：`finally` 抛出去 → `future.result()` 再抛 → `run()` 只打印一句「worker unavailable」并放弃这一轮剩下的任务；而这一行的 `next_due` 没被推进，下一轮又被选中、又抛一次。一条脏记录足以让整批 SKU 永久停采，日志里看不出原因。`collector_routes` 上报路径（`next_due=now+config.get('interval_minutes',5)*60`）是同一个写法，null 会让整个上报接口 500、后面的上报全部不处理。

改动：新增 `buybox_monitor.interval_seconds(value, default_minutes=5)`，把「非正整数 → 默认 5 分钟」的归一收在一处，`process()` 与 `collector_routes` 上报路径都改用它；`_record_stuck` 里原来那段 `isinstance` 判断也改用它（行为不变）。默认值仍是 5 分钟——与 `loop_status`、`repair_monitor`、前端 `Number(x) || 5` 的既有口径一致，没有引入新语义，也没有动任何调价参数。

复现：`interval_seconds(None) == 300`、`interval_seconds(30) == 1800`、`interval_seconds(True) == 300`、`interval_seconds(0) == 300`、`interval_seconds('5') == 300`（`test_collection_watchdog.py::test_null_interval_does_not_break_the_next_due`）。

复测：后端全量 pytest 393 passed / 1 skipped；全量 selfcheck 27 步全绿。

### PRICING-COPY-03 调价说明面板两条规则写漏了前提条件（2026-10-20）

WorkBuddy；基线 e47203c70e。调价页「调价与试涨规则」面板里有两条与代码不符：

- 写的是「**48 小时没有新成交**则回退到之前的价位」，而 `automatic_pricing.plan()` 的回滚分支要求三件事同时成立：试涨已满 48 小时、`rule['sales_recent']` 为真（`process()` 把它算成 `operations_profile.synced_at` 距今 < 7200 秒，即本店订单快照在 2 小时内同步过）、且 `sku_last_sale_epoch <= 试涨起点`。快照过期时**这一轮根本不会回退**，但界面照着原文写「48 小时没成交就回退」，用户会以为价格该降没降、程序坏了。`docs/pricepilot-integration.md` 上文「试涨 48 小时没有新订单时回退此前价格」那句本来就写了「依赖近期成功同步的逐SKU订单时间」，只有面板文案漏了这半句。
- 写的是「库存紧张（可售天数**短于空运周期**）时不跟降」。代码判据是 `automatic_pricing.DEFAULT_TIGHT_COVER_DAYS = 21`（`cover_days <= 21`），是个固定常量，**不读**每个商品可配的 `emergency_air_days`（1–30 天，默认 21）。把「空运周期」当判据说，等于暗示改了空运天数就会改调价档位，实际不会。

改动：只改 `src/pages/AutoPricing.tsx` 面板文案——补上「订单快照超过 2 小时没同步时本轮不回退」，把「短于空运周期」改成「不超过 21 天」。判据一行未动，调价行为完全不变（`plan()` 未修改）。`docs/pricing/2026-10-18-buybox-pricing-model.md` 的回滚段落同步补上三个前提。

复测：`tsc --noEmit` 通过（`noUnusedLocals` 开）；`test-buybox-copy.cjs` 全过；全量 selfcheck 全绿。`test-pricing-layout.cjs` 28 → 34 断言：新增 6 条钉住这块面板——必须出现「48 小时」「2 小时」「暂停试涨 24 小时」「不超过 21 天」，且**不许**再出现「短于空运周期」。把文案改回旧版后该断言转红（已还原）。以后改这段文案会直接让门禁失败，这是有意的：文案与 `plan()` 判据不一致时，用户会照着文案判断「该回退没回退」。

### DEAD-CODE-06 删掉重复的重定向组件与两个零引用的费率映射更新封装（2026-10-20）

WorkBuddy；基线 e47203c70e。

- `src/pages/Buybox.tsx` 只有 2 行（`<Navigate to="/auto-pricing" replace />`），全仓零引用：`App.tsx` 早就把同样的重定向内联在 `<Route path="/buybox">` 上，这个文件是那次内联之后留下的副本。删掉它不影响 `/buybox` 路由（路由仍由 `App.tsx` 处理）。
- `src/api.ts` 与 `src/remoteApi.ts` 各有一个 `updateFeeMappingRule()`，两者都零调用（设置页只 `createFeeMappingRule`，产品新建页与设置页只 `getFeeMappingRules`）。其中 `remoteApi` 版本用 `method: 'PUT'` 打 `/api/fee-mapping-rules/{id}`，而 `backend/api/category_routes.py` 注册的是 `@router.patch`——真有人调用会拿到 405。删掉比留一个「看起来能用、其实动词错」的封装更安全。

复测：`tsc --noEmit` 通过（`noUnusedLocals` 开，证明没有残留引用）；全量 selfcheck 全绿；`test-page-import.mjs`、`test-buybox-copy.cjs` 等前端套件全过。

### API-CONTRACT-01 网页版两个接口打错动词/路径，功能静默失效（2026-10-20）

WorkBuddy；基线 400647fc11。两条都是 `src/remoteApi.ts` 与后端注册表不一致，都只在**网页版**复现（Tauri 版走 `src/api.ts` 的 `invoke`，根本不发 HTTP），而且都不会被 `tsc` 发现——路径和动词都是字符串：

- `updateFeeCategory()` 用 `PUT /api/fee-categories/{id}`，而 `backend/api/category_routes.py` 只注册了 `@router.patch('/fee-categories/{category_id}')`。设置页的「启用/停用费率分类」和「修改成功率」都走这里（`Settings.tsx` 的 `handleToggleCategory` / `handleUpdateFeeRate`），网页版点一次就报 405，且界面只显示一句 `405 Method Not Allowed`。
- `translateProductName()` 打 `/api/translate`，而翻译接口挂在产品路由下（`product_routes` 前缀 `/api/products` + `@router.post('/translate')`），完整路径是 `/api/products/translate`。网页版必 404；两个调用点（`ProductCreate.tsx` 的页面导入与 `handleScrape`）都把异常吞掉了（`.catch(() => {})` 与空 `catch`），于是表现为「自动翻译中文品名在网页版从来不生效」，既没有提示也没有日志。

改动：只改 `src/remoteApi.ts` 两处——动词 `PUT` → `PATCH`，路径补上 `/products` 段。没有动后端路由，也没有放宽任何判据（后端本来就只有 PATCH 和 `/api/products/translate`，前端是错的这一侧）。

防回归：新增 `scripts/test-api-contract.cjs`，静态解析 `backend/api/*.py` + `backend/main.py` 的路由表与 `remoteApi.ts` 里每一条 `request()`，逐条核对「动词 + 路径」；带参数的调用按段匹配（少一段、多一段、动词错都会红）；两端各设解析条数下限（路由 ≥70、调用 ≥60），解析器失效时失败而不是静默比对 0 条。同类错误此前已出现过一次（`updateFeeMappingRule` 用 PUT 打 PATCH 路由，DEAD-CODE-06 直接删掉了那个封装），这次是把它变成门禁而不是再删一次。

复现：`node scripts/test-api-contract.cjs`。把 `updateFeeCategory` 改回 `method: 'PUT'` → `updateFeeCategory: PUT /api/fee-categories/:p` 转红（已还原）；把 `translateProductName` 改回 `/api/translate` → 同样转红（已还原）。

复测：`test-api-contract.cjs` 10 条断言（91 条路由 / 64 条调用全对齐）；`tsc --noEmit` 通过；全量 selfcheck 28 步全绿（新增套件自动纳入）；未动后端，pytest 随 selfcheck 通过。

### INTERVAL-NULL-02 `interval_minutes` 的**写**路径也归一：点「立即采集」不再 422（2026-10-21）

WorkBuddy；基线 3ab844f407。INTERVAL-NULL-01 只堵了读路径（`interval_seconds` 归一后再 `*60`），写路径仍然把库里的 null 原样往下传，于是同一个脏值有两个新出口：

- `pricing_routes.collect_now()` 把 `old.get('interval_minutes',5)` 直接塞回 `MonitorBulk`。`.get(key, 5)` 只在**键缺失**时给默认值，键存在而值是 null 时返回 None，`MonitorConfig.interval_minutes` 声明为 `int = Field(ge=5, le=1440)`，Pydantic 判「不是整数」→ FastAPI 转 **422**。这条记录本身完全能采，用户看到的是「点立即采集就报错」，而且每次点都报。
- `collector_routes.web_open()` 同样用 `.get('interval_minutes',5)` 拼新记录写回库。它把 null 又存了一遍，于是 `interval_seconds` 的归一永远有货可归——脏值会自己繁殖。

改动：把归一从 `interval_seconds` 里提出来成 `buybox_monitor.normalize_interval_minutes(value, default_minutes=5)`，`interval_seconds` 改为 `normalize_interval_minutes(...) * 60`（行为一字未变），上述两条写路径改用同一个函数。默认值仍是 5 分钟，与 `loop_status`、`repair_monitor`、前端 `Number(x) || 5` 的口径一致，没有引入新语义，也没有动任何调价参数。

复现：`test_buybox_monitor.py::test_collect_now_survives_a_null_interval`——先 `monitor_configure(interval_minutes=30)`，把库里该行的 `interval_minutes` 改成 null，再调 `collect_now`。修复前该调用抛 422；修复后落库为 `interval_minutes=5` 且 `request_once=True`。把 `collect_now` 里的归一改回 `old.get('interval_minutes',5)` 后该用例转红（已还原）。

复测：后端全量 pytest 394 passed / 1 skipped（新增 1 例）；全量 selfcheck 28 步全绿。

### DEAD-CODE-07 删掉 `models.ShippingMethod` 与 `seller_identity.describe()`（2026-10-21）

WorkBuddy；基线 3ab844f407。两个都是零引用的残留，且都容易误导后来者：

- `models.ShippingMethod`（`AIR_REGULAR`/`AIR_BATTERY`/`SEA_REGULAR`/`SEA_BATTERY` 四值枚举）全仓零引用——商品的运输方式实际是 `products.shipping_method` 这个自由文本列，前端 `ProductCreate.tsx` 的 `shippingMethod` 是它自己的 `useState`，跟这个枚举毫无关系。留着会让人以为运输方式受枚举约束，实际改枚举不影响任何校验。
- `services/seller_identity.describe(db, store, snapshot)` 全仓零调用（诊断接口用的是 `name_of` / `holds_buybox` / `normalize`）。它返回的 `skus` 计数来自 `observations`，而观测结构的读取方已经改成别处，留着等于留一份没人验证过的第二套解读。

复测：`grep -rn "ShippingMethod"` 与 `grep -rn "\.describe("` 在 backend/src/scripts 均无命中；`tsc --noEmit` 通过（`noUnusedLocals` 开）；后端全量 pytest 394 passed / 1 skipped；全量 selfcheck 28 步全绿。

> 说明：两处删除在推送前逐条核对过引用（`backend/`、`src/`、`scripts/` 全仓 grep）与行为，`tsc --noEmit` 的 `noUnusedLocals` 与后端全量 pytest 一起兜底；没有删除或削弱任何测试断言。

### MARKET-DISPLAY-01 参考价的「没填」不再显示成 ¥0，淘宝样本框不再空转（2026-10-21）

WorkBuddy；基线 ecc7797405（PR #157 多市场参考价刚合入，本轮是它的第一遍复核）。

- `src/utils/marketPrice.ts` 的解析/序列化用 `Number.isFinite(Number(x))` 判价格是否有效。
  `Number(null)` 与 `Number('')` 都是 **0**、`Number(true)` 是 **1**，于是「这个市场没填」
  会被渲染成一个像真价格的假价格：`parseMarketPrices('{"CN":{"price":null}}')` 得到
  `{CN:{price:0}}`，列表里显示「CN ¥0」、详情页显示「¥0」。后端写入有
  `_check_market_prices` 挡（`null`/`true`/字符串都会被 400），但 `/api/migrate` 是整行
  搬运、不过 Pydantic，桌面版导出的历史行可能带 null 或数字字符串——展示层是最后一道
  边界，不能假设上游一定干净。
- 详情页编辑态里「淘宝均价样本条数」输入框在没有均价时照样渲染，而
  `setMarketSample()` 在没有该市场时直接 return：输入框可点、可输入、数字被静默丢弃，
  没有任何反馈。改成没有价格时只显示「先填价格」。
- 顺带删掉 `marketInsights()` 注释里「最低官方价」这一条——函数从来没返回过它，
  而且 SGD 与 CNY 不能直接比，注释描述的是不存在的行为。

改动：`src/utils/marketPrice.ts` 抽出 `toPrice()`（数字或数字字符串 → 非负有限数，
其余一律 null），`parseMarketPrices` / `serializeMarketPrices` 共用；`ProductDetail.tsx`
把样本框放进 `entry ? ... : 「先填价格」` 分支。判据没有放宽：负数、布尔、空串仍然拒绝。

复现：`node scripts/test-market-prices.cjs`。把 `parseMarketPrices` 改回
`Number.isFinite(Number(...))` → 「price=null 必须当成没数据」等 8 条断言转红（已还原）。

防回归：新增 `scripts/test-market-prices.cjs`（30 条断言，不启浏览器直接编译 TS）——
解析、空值不当 0、数字字符串归一、坏 JSON 不抛、序列化丢空市场、往返一致、
币种符号与小数位、比价结论的除零与跨币种不比。这个功能此前只有后端测试，
前端零断言。

版式覆盖：`scripts/test-responsive-layout.py` 的 `PRODUCT` fixture 补上 `market_prices`
（CN 1289 / SG 245.9 / TB 899，四位数 + 带小数的最宽一档）。此前 fixture 不带这个字段，
列表的「参考价」列永远只渲染「—」、详情页卡片是空的，`test-text-overlap.py` 复用同一份
fixture，所以这一列在两个审计的 160 个面上都从没被量过。补上后实测 0 处问题。

复测：`tsc --noEmit` 通过；`test-market-prices.cjs` 30 条断言；文字重叠审计 160 个面
0 处问题；全量 selfcheck 29 步全绿（新套件自动纳入）；未动后端，pytest 随 selfcheck 通过。

### STORE-ISOLATION-01 装箱产品库与发货单号按店铺隔离（2026-10-21）

Codex（PR #160）；基线 `4c064f73f4`。原提交只带代码与测试，没有文档，本条为复核后补记。

- `packing_products` 的主键从 `sku` 改成 `(store_id, sku)`：两个店铺出现同名 SKU
  （代理品牌代运营 CK 接入后是常态）会互相覆盖重量/材质/图片/模板行，属静默数据污染。
- `shipments.shipment_no` 的唯一约束从全局改成 `(store_id, shipment_no)`：否则两个店铺
  同一天不能都有 `PK-YYYYMMDD-001`，编号会被别店占用。`draft_key` 仍全局唯一
  （它本身已带 store_id 前缀）。
- 启动期迁移 `_migrate_packing_products_store_scope()` / `_migrate_shipments_store_scope()`
  重建这两张表，老数据全部归入默认店铺。判断是「列或主键/唯一约束形态不对」才重建，
  已经是新形态则直接返回，重复启动不会反复重建。
- 所有按 SKU 取装箱产品的查询都必须带 `store_id`（`_find()` / `list_products` /
  `_sync_from_products` / 补图 / 迁移包导入 / 对账 `preview`+`apply`），否则跨店读到别店记录。

复测：`backend/tests/test_store_isolation_packing.py` 6 例（同 SKU 跨店互不影响、按店删除、
发货单号按店唯一、老库两种迁移）。

### PACK-UI-STORE-01 装箱工作台的店铺下拉只列在用店铺（2026-10-21）

WorkBuddy；基线 `e39fbdde9c`（STORE-ISOLATION-01 刚上线）。

- 症状：静态装箱工作台（`backend/packing_ui/index.html`）的店铺下拉直接取 `/api/stores`
  全量结果。`get_store_id` 对非 `active` 店铺一律 400「店铺不存在或已停用」，所以一旦
  选中一个停用店铺，产品库、导出、上传图片全部报错，而且下拉本身不解释为什么。
  React 端 `App.tsx` 早就 `filter(store => store.status === 'active')`，两套前端不一致。
- 顺带修掉同一函数里的两处静默：`if (!r.ok) return` 与 `catch {}` 把读取失败吞掉，
  下拉留空、页面照常用 localStorage 里的旧店铺继续跑；当前店铺不在列表里时只改
  localStorage 不重渲染，`<select>` 显示的和实际请求的店铺可以不是同一个。
- 改动：`/api/stores` 增加可选 `active=1`（默认仍返回全部，店铺管理页要能看到并重新启用
  停用店铺，`Stores.tsx` 行为不变）；下拉改请求 `?active=1`，当前店铺失效时回落到第一个
  在用店铺并重渲染；读取失败在下拉里写「店铺列表读取失败」并 toast。
- 复现：`pytest backend/tests/test_store_isolation_packing.py` 里的
  `test_store_list_can_be_limited_to_active_stores` / `test_packing_ui_only_offers_active_stores`。
  把 `active` 过滤去掉、或把 HTML 的 `?active=1` 改回 `/api/stores`，两例同时转红（已还原）。

### WATCH-STUCK-STICKY-01 「任务已被放弃」不再永久挂着（2026-10-21）

WorkBuddy；基线 `645c4be426`。

- 症状：`_record_stuck` 把 `stuck_at` / `stuck_jobs` 写进 `_buybox_worker_state`，
  但 `_record_cycle` 从不清理它们。于是**第一次**超时之后 `stuck_jobs` 永久 >0，
  界面在任何一次「循环卡住」判断里都会补一句「卡住的采集任务已被放弃」——
  包括那种「一轮本来就排不下（间隔 5 分钟、实测一轮 12 分钟）、一个任务都没超时」
  的正常情况。历史现场被当成当前状态，用户据此去重启服务，重启也解决不了排期。
- 改动：`_record_cycle` 开新一轮时 `pop('stuck_at')` / `pop('stuck_jobs')`。清理发生在
  循环开头，本轮自己 `_record_stuck` 的结论不受影响（顺序：`_record_cycle` →
  提交任务 → 等待超时 → `_record_stuck`）。
- 连带：`collection_maintenance.py` 里「`_record_stuck` 只改结论、不清旧计数」这句
  注释已与代码相反（COLLECT-DIAG-02 起就 `pop('diagnostics')` 了），改成如实描述。
- 复现：`pytest backend/tests/test_collection_watchdog.py` 新增 2 例
  （新一轮开始后 `stuck_jobs` 归零且 `cycles` 照常 +1；同一轮里刚放弃的任务仍在）。
  把 `_record_cycle` 的两次 `pop` 去掉，第一例转红（`assert 2 == 0`，已还原）。
- 复测：后端全量 422 passed / 1 skipped（新增 2 例）；全量 selfcheck 29 步全绿。

### MARKET-COPY-01 参考价说明不再暗示「系统自动去官网取价」（2026-10-21）

WorkBuddy；基线 `645c4be426`。

- 症状：商品详情页「多市场参考价」的说明写「中国官网价与淘宝均价需手工录入，
  新加坡官网价取自官网商品页」。后半句读起来是**系统自动读取**，而代码里没有任何
  官网取价（按红线也不做抓取，`docs/engineering/2026-10-21-market-prices.md`
  写的是三个市场都手工录入、新加坡价可从官网 JSON-LD 人工抄）。用户会以为
  新加坡那格不用管，或者以为没填是系统没采到。
- 改动：`src/pages/ProductDetail.tsx` 改成「三个市场都要手工录入（新加坡官网价可从
  官网商品页抄，但那是 SGD，不与人民币直接比价）」。判据、数据结构、比价结论一字未动。
- 复现：`node scripts/test-market-prices.cjs` 新增 3 条文案断言（必须出现
  「三个市场都要手工录入」「不与人民币直接比价」，不得再出现旧句）。
  把文案改回旧版，断言在第一条就转红（已还原）。
- 复测：`test-market-prices.cjs` 33 条断言；全量 selfcheck 29 步全绿。

### FEE-CATEGORY-LOAD-01 费率品类读失败不再静默（2026-10-21）

WorkBuddy；基线 `790f1244a3`。

- 症状：`/api/fee-categories` 读失败时，新建产品页与商品详情页都是
  `.catch(() => {})`——下拉只剩「-- 请选择 --」，用户看到的是「没有品类可选」，
  不是「这一次没读到」，于是去找品类或干脆不填品类就保存。同一个接口在设置页
  是会报错的（`Settings.tsx` 用 `Promise.all` + `setError`），同一份数据在两处
  给出两种结论。详情页编辑态还有第二层：下拉只由品类表生成，读失败时库里明明
  有品类也显示成「-- 请选择 --」，看起来像这件货没设过品类。
- 改动：新增 `src/utils/useFeeCategories.ts`（`categories` / `error` / `loading`
  / `reload`），两个页面共用；读取失败显示「费率品类读取失败，下拉是空的——
  不是没有品类，请重试」并提供「重新读取」；同步抛错也落进同一状态（用
  `Promise.resolve().then()` 包一层，异常不从 effect 冒出去）；重试失败保留
  上一次成功的列表。详情页把提示放在编辑态之外（只读时费率列同样受影响），
  并在当前值不在下拉里时补一条「<品类>（当前值）」，值不变、只是显示与库里一致。
- 复现：`node scripts/test-fee-categories.cjs` 14 条断言（成功 / 失败 / 同步抛错 /
  重试 / 重试失败不清空）。把 catch 改回静默，第 2 组断言转红（已还原）。
- 版式审计补降级场景：`scripts/test-responsive-layout.py` 新增一遍「该接口打成
  500」的检查（390 / 320），量提示是否出现、在卡片内、重试按钮可达、不撑破页面，
  并在 `?edit=1` 下断言下拉仍保留库里的品类。删掉提示渲染 → 报
  `390: fee category failure shows no alert`；去掉「当前值」选项 → 报
  `fee category select lost the stored value ('')`（均已还原）。fixture 的
  `PRODUCT` 补 `fee_category='Home'`（此前详情页品类行永远是「-」）。
- 复测：`test-fee-categories` 14 条；版式审计 99 组合 + 2 组降级 0 失败；全量 selfcheck 全绿。

### API-CONTRACT-BLOB-01 契约门禁覆盖直接 fetch() 的二进制接口（2026-10-21）

WorkBuddy；基线 `790f1244a3`。

- 症状：`scripts/test-api-contract.cjs` 只解析 `request()`（JSON 接口），而装箱单
  Excel 导出、装箱单图片、入库单模板、云端浏览器画面、迁移包导入这五个接口返回
  blob，都是自己写 `fetch()`，路径字符串从来没被任何门禁检查过。改错一个路径段
  就是 404，而下载失败的表现是「点了没反应」或「图片空白」——没有异常类型、
  没有日志，和 `translateProductName` 少写 `/products` 那次属于同一类问题。
- 改动：同一脚本增加第二遍扫描，扫 `src/**/*.ts(x)` 里带字面量的 `fetch(`，只认
  `/api` 开头的（`fetch(url)` 这类变量跳过），动词默认 GET，逐条比对后端路由表；
  另加 5 条「这些路由必须存在」的显式断言。解析条数设下限 5，解析器失效即红，
  不会静默变成 0 条比对。
- 复现：把 `exportPacking` 的 `/api/packing/export` 改成 `/api/packing/export-v2`，
  报 `remoteApi.ts:459: POST /api/packing/export-v2 没有对应路由`（已还原）。
- 复测：`test-api-contract` 12 → 17 条断言（91 路由 / 64 request 调用 / 5 直接 fetch）。

### DIAG-OFFER-01 卖家接口读取失败要说出是哪一种（2026-10-21）

WorkBuddy；基线 `71104506a2`。

- 症状：`official_offer()` 把非 200 一律抛成「卖家报价读取失败，暂停本轮调价」。
  密钥被撤销（401）、被限流（429）、上游 5xx、被网关拦成 HTML，在调价列表的
  「上次结果」和「核对平台现价」的 503 里长得一模一样，用户能做的只有反复点重试。
  `reconcile()` 更彻底：`except Exception: raise HTTPException(503, '平台现价暂时
  无法读取…')`，连异常类型都不留，`_read_reason()` 那套归因（本店报价、官网资料
  两个接口已经在用）没接到这条路径上。
- 改动：新增 `seller_api_reason(status)`——401/403 说密钥、429 说限流、5xx 说上游
  异常，只带状态码和类别，不带响应正文（正文可能含账号信息）；200 但不是 JSON
  单独报「可能被网关拦截」，不再冒成 `JSONDecodeError`。`_read_reason()` 增加
  「卖家接口/卖家报价」开头的提示直通（否则退化成「内部错误 ValueError」，等于
  什么也没说）。`reconcile()` 的 503 改成 `平台现价暂时无法读取：<归因>；仍保持
  暂停，不重复提交`。
- 复现：`test_pricing_routes.py` 新增 3 例——四种状态码各自的文案且不含
  `Authorization`/`Key `；非 JSON 响应；`reconcile` 在 401 与 `ConnectError` 两种
  失败下 detail 含原因、不含 `/private/path`、且规则行一字未改。把 `_read_reason`
  的直通去掉 → `assert 'HTTP 401' in detail` 报
  `平台现价暂时无法读取：内部错误 ValueError；仍保持暂停，不重复提交`（已还原）。
- 复测：后端 425 passed / 1 skipped。

### STORE-SWITCH-01 页头店铺下拉不再伪造「自营店铺」（2026-10-21）

WorkBuddy；基线 `71104506a2`。

- 症状：`App.tsx` 在 `/api/stores` 读失败时 `setStores([{ id:'default-store',
  name:'自营店铺', … }])`，凭空造一条店铺顶上。可每个请求带的 `X-Store-ID` 仍是
  localStorage 里那个：界面显示「自营店铺」，实际请求发的是另一个店；当前店铺不在
  选项里时浏览器还把 select 显示成空白。同一条兜底也盖住了「当前店铺刚在店铺管理页
  被暂停」的情况——下拉里没有它了，请求却还带着它。
- 改动：新增 `src/utils/storeSelection.ts`。`storeOptions()` 只列运营中的店铺，但
  **当前店铺永远保留**：还在库里就标「（已暂停）」，读不到列表就标「（未在店铺列表
  中）」，并在 `select` 上禁用切换；首次加载（还没返回）不加这些后缀，避免一进页面
  就显示吓人的状态。`storeLoadNotice()` 生成页头提示：原因 + 「当前按哪个店继续」+
  「切换店铺不可用、打开应用自动采集本次未提交」，带「重新读取」。读失败不再静默，
  也不再影响店铺列表之外的任何请求。
- 复现：`scripts/test-store-switcher.cjs` 23 条断言（正常列表 / 当前店铺被暂停 /
  读失败 / 读失败且当前是自营店 / 加载中 / 不认识的 id / 提示文案）。把 unknown 分支
  改回伪造 `default-store` → `当前店铺 id 不能被换掉` 转红（已还原）。
- 复测：tsc 通过；全量 selfcheck 全绿（新套件自动纳入）。

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
