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

