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

### Rolling sea-first calendar
The planner now defaults to a 120-day departure outlook, a 30-day review/departure interval and 60 days of sea coverage plus existing safety days. It calculates each subsequent sea batch against existing stock, confirmed arrivals and earlier suggested batches; it never repeats the initial air bridge in later sea batches. Procurement preparation days are separate from transport lead times. Every batch carries purchase, departure, arrival and target coverage dates. Future batches are conditional estimates, not placed orders; all purchase reminders still use current unmet air/sea requirements. Short observations and stale snapshots are marked provisional. Profit gates apply to future batches as well. Estimated transport savings are not a guarantee of net profit and exclude unknown storage/capital costs. The main page opens on the dated calendar with compact product rows, filters, CSV and pagination; SKU repricing remains explicitly opt-in.
