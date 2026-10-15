# 原 Mac 运行时追踪：Buybox 采集调用链与迁移差异

- 日期：2026-10-15（UTC）
- 执行端：原 PricePilot 实际运行的 Mac（`daiguangxuedeMacBook-Air.local`，Darwin 25.6.0 arm64）
- 目的：用原环境的可复核证据，解释原程序采集链路，并与 `takealot-selector` main 逐项比对
- 范围：**只读**。未重启/停止生产服务，未抢占单例锁，未启动第二个生产采集器，未改价，未写入生产库
- 本文不含密码、API Key、Cookie、登录态、完整环境变量与完整请求头

---

## 0. 本机能力实测（不虚构）

| 能力 | 结果 |
|---|---|
| 执行 Mac 本机终端命令 | ✅ 已实测（`whoami` / `hostname` / `uname` 均返回本机值，非云端工作区） |
| 读取原 PricePilot 源码目录 | ✅ `pricepilot/server/src`、`pricepilot/web/src`、`TakealotScraper` 均可读 |
| 读取采集器日志与指标 | ✅ `TakealotScraper/collector.log`、`collector_metrics.jsonl` |
| 访问本机服务 | ✅ `:3000/api/health` → 200（db/redis/workerLock 全 ok）；`:5173` → 200 |
| 读取 launchd 服务定义 | ✅ 已提取（已脱敏，仅保留程序路径/工作目录/开关，环境变量值全部省略） |
| **枚举进程（`ps` / `pgrep`）** | ❌ **不可用**。`ps` 返回 `operation not permitted`（系统层拒绝，非沙箱），`pgrep` 空 |
| **枚举监听端口（`lsof` / `netstat`）** | ❌ **不可用**（同上） |
| **`launchctl list`** | ❌ 返回空 |
| GitHub 仓库读写 | ✅ REST API 实测通过：身份 `urban-electronic`，对 `takealot-selector` 具 `push/admin` |

进程信息的替代证据：单例锁文件 `.collector.lock`（PID=69429）、服务 HTTP 端点存活、日志活跃时间、launchd 服务定义。
**因此下表中 PID/启动时间一栏，除采集器外均无法给出**，不以推测填充。

---

## 1. 实际拓扑表

| 角色 | 程序文件路径 | 启动者 | 通道 | 读 / 写 |
|---|---|---|---|---|
| 后端 API | `pricepilot/server/dist/index.js`（node） | launchd `com.pricepilot.server` | `127.0.0.1:3000` | 读 PG、写 PG、写 Redis（BullMQ 队列） |
| 调价 worker | `pricepilot/server/dist/worker.js` | launchd `com.pricepilot.worker` | 消费队列 | 读 PG/Redis，写 PG |
| 网页前端 | `pricepilot/web/node_modules/vite/bin/vite.js --port 5173` | launchd `com.pricepilot.web` | `127.0.0.1:5173` | 反代 `/api` → 3000 |
| **采集器** | `TakealotVenv/venv/bin/python collector.py --interval 600` | launchd `com.pricepilot.collector`（PID 69429，来自锁文件） | HTTP → `127.0.0.1:3000` | 读 `/api/collector/products`，写 `/api/collector/offers` |
| Chromium（采集浏览器子进程） | Playwright `launch_persistent_context(user_data_dir=browser_profile)` | 由 `collector.py` 启动 | 出站 HTTPS | 读 takealot.com 公开商品页 |
| PostgreSQL | 版本 PG16（`pricepilot` 库） | **未在 LaunchAgents 中找到** | 5432 | 由后端/worker 读写 |
| Redis | `pricepilot-dev/redis-7.2.4/src/redis-server redis.conf` | launchd `com.pricepilot.redis` | 6379 | 队列 + worker 单实例锁 |
| 外网隧道 | `bin/pricepilot-tunnel.sh`（cloudflared） | launchd `com.pricepilot.tunnel` | 公网 | 反向代理（**当前处于失败重连状态，非本轮目标**） |
| Tailscale | `bin/tailscaled --tun=userspace-networking` | launchd `com.pricepilot.tailscaled` | — | — |
| 防休眠 | `/usr/bin/caffeinate -i -m -s` | launchd `com.pricepilot.keepawake` | — | — |

源码版本（三个仓库，工作区均无本地修改）：

| 仓库 | HEAD | 说明 |
|---|---|---|
| `pricepilot` | `1d4d51f` | 后端 + 前端；`dist` 构建于 2026-10-11 15:44，无 `.ts` 比 `dist` 新 |
| `TakealotScraper` | `bc9b9bb` | `collector.py` 1050 行 |
| `takealot-bridge` | `4be9e9d` | 选品数据桥接 |

采集器运行环境：Python 3.13.14，`playwright 1.61.0`，`playwright-stealth 2.0.3`。

---

## 2. 调用链追踪（文件 : 函数 : 行号）

标注：**A**=代码明确实现，**B**=本轮实际观察，**C**=未验证推测。

### 2.1 任务选择与调度

| 步骤 | 实现 | 标注 |
|---|---|---|
| 商品名单来源 | `collector.py:417 fetch_products()` → `GET /api/collector/products` | A |
| 服务端过滤 | `server/src/routes/collector.ts:74-116`；`where url != null` **且**（`all=1` 时不过滤，否则 `autoPricingEnabled: true`） | A |
| 本轮实际名单 | 27 个（`autoPricingEnabled=true` 且 `url` 非空）；库内 785 个商品中 758 个 `auto=false` 不在默认名单 | B |
| 单轮上限 | 无（`pick_batch` 默认 `shards=1` 不分批） | A |
| 并发 | `run_once_async:914` `asyncio.Semaphore(3)` | A |
| 失败重试 | **单商品无重试**；`err` 时 `return None`，不上报（`collector.py:936-943`） | A |
| 整轮超时 | `asyncio.wait_for(..., 600)` → `os._exit(1)`；`start_watchdog(900)` | A |
| 单例锁 | `acquire_singleton_lock()`（`:85`），`flock` 于 `.collector.lock`，**保护整个进程，不保护单个任务** | A |
| 任务占用 / 租约 | **没有**。无 claim、无 lease、无心跳。进程中断即丢，下一轮重新拉取名单 | A |
| 轮次间隔 | `--interval 600`（10 分钟） | A+B |

**与迁移侧的关键差异**：迁移侧 `/products` 用 `CLAIM_PREFIX` 租约（uuid token 作为 `productId`）、`recover_expired` 回收、`/release` 释放、`connected()` 心跳仲裁云端 worker、单次上限 20。**原程序完全没有这套机制。**

### 2.2 商品身份与变体

| 步骤 | 实现 | 标注 |
|---|---|---|
| SKU/TSIN | 服务端返回 `sku`，来自 `Product.sku` | A |
| offer id | `platformProductId`，形如 `offer:234862133`；**原程序把它直接作为 `productId` 回传** | A |
| PLID | 只在 `Product.url` 中（`.../x/PLID97512496`），采集器不做解析 | A |
| 访问 URL | 直接用 `item["url"]`，不做构造 | A |
| 是否跳转 | **会跳转**：本轮隔离探测中 `/x/PLID97512496` → `https://www.takealot.com/4k-thump-action-camera-.../PLID97512496`（slug 形式，PLID 不变） | B |
| 变体后缀判定 | `collector.py:474-476`：`name` 中含 `" - "` 时取最后一段作后缀 | A |
| 主商品区 vs 推荐卡区分 | **没有**。等待选择器用 `SELECTORS["variant"] = [class*="variant"], [data-ref*="variant"], [class*="swatch"]`（`:287-291`），属宽选择器 | A |
| 探测失败 vs 确定无变体 | **不区分**。没有 `page_variant_controls` / `_CORE_BLOCK_JS` / `_CARD_SCOPE_JS`（实测 0 处） | A |
| 拒绝采信 | 变体切换失败 → `result["variantSwitchFailed"] = True`（`:605`），**但仍返回读到的 buybox**；由后端决定 | A |
| 后端处置 | `collector.ts:285-301`：落库 + 写 `lastPricingResult="skip: 变体切换失败…"` + `continue`，**不触发调价、不写 `lastPricingAt`** | A |

### 2.3 浏览器与配送上下文

`collector.py:897-912`（A）：

- `launch_persistent_context(user_data_dir=browser_profile, headless=True)`
- `args`: `--disable-blink-features=AutomationControlled`、`--no-sandbox`、`--disable-features=TranslateUI`、`--lang=en-ZA`
- `user_agent`：**显式覆盖**（Windows Chrome 131 字符串，`:41`）
- `locale="en-ZA"`、`timezone_id="Africa/Johannesburg"`
- `permissions=["geolocation"]`、`geolocation={"latitude": -26.2041, "longitude": 28.0473}`（约翰内斯堡）
- 每个 page 额外注入 stealth JS（`:890-894`，隐藏 `navigator.webdriver`、伪造 `plugins`、注入 `window.chrome`），并尝试 `playwright_stealth.Stealth()`（`:834-837`）

持久会话：`user_data_dir` 复用登录态；首次需人工登录。
**是否验证页面实际配送区域**：不验证 DOM 上的配送地址；仅解析页面文案（`"fastest delivery"` / `"get it today"` / `"ships in"` 等，`:248-259`）。
**会话过期/验证页**：**无挑战页识别**（实测 0 处 `just a moment` / `CHALLENGE_TITLES` / `wait_initial_document`）；只有标题兜底启发式（`"online shopping"` / `"leading online store"` → `blocked_or_load_failed`，`:750-765`）。

### 2.4 页面就绪与报价解析

| 步骤 | 实现 | 行号 | 标注 |
|---|---|---|---|
| 导航 | `page.goto(url, wait_until="domcontentloaded", timeout=45000)` | 457 | A |
| **HTTP 状态记录** | **不记录**：`resp` 被赋值但从未使用 `resp.status`（实测 0 处） | 457 | A |
| 就绪等待 | `wait_for_selector(buybox_card + variant 宽选择器, timeout=12000)` | 462-464 | A |
| buybox 卡选择器 | `[data-ref="buybox-offer"]`（**单选择器，不降级**；注释说明宁可采不到也不采错） | 272-276 | A |
| 价格解析 | `price_of()`（`:390`）+ `parse_price()`（`:117`） | A |
| 稳定性双读 | `_read_buybox_cards()` 读 2 次（间隔 1.2–2.0s），不一致再读第 3 次取多数；3 次互异 → `unstable_price` 放弃 | 694-722 | A |
| 现货映射 | `map_stock()`（`:212`）/ `map_card_stock()`（`:230`）；buybox 有价即视为 `in_stock` | 741-744 | A |
| 多卡处理 | `_read_buybox_cards` 返回 `fastest` / `best_price` / `cards`，分卡上报 `offerType` | 628-692, 798-817 | A |
| 验证页误判 | **可能**。无挑战页标题识别，若挑战页渲染出 buybox 卡则无法区分（C，未实测到该情形） | — | C |

### 2.5 一致性与身份判断

- **存在双读**（上表 694-722），间隔 1.2–2.0s，第三次 1.2–2.0s 后。A
- 不一致处理：取多数派；无多数 → `unstable_price` 放弃本轮。A
- `isMine` / `holdingBuybox`：`abs(buybox_price - my_price) < 0.01`（`:777`），`my_price` 来自服务端 DB 价。逐卡独立判定（`:804`、`:814`），不复用 buybox 的 `is_mine`。A
- 卖家 ID/名称：`extract_seller_name()`（`:127`）只取名称，不参与身份判定；**可靠判据只有价格比对**（代码注释明确说明前台不再暴露卖家名）。A
- 库存/预售/配送：`map_card_stock` 决定报价卡是否计入"现货最低价"。A
- 服务端二次仲裁：`pricing/holding.ts:46 resolveHolding()` —— 采集器判持有时信任不推翻；判未持有时用 Seller API 权威价复核；无权威价时按"页面无其他卖家且非缺货"兜底；信息不足则**保留旧值不翻转**；"持有→失去"需连续 2 轮确认。A

### 2.6 回传、入库与失败保留

- 成功判据：`err is None and buyboxPrice is not None`（`:944-946`）。A
- 计数：`ok` / `404` / `error`；404 与 error **均不上报**（`return None`），避免污染后端状态。A
- 回传：整批 `POST /api/collector/offers`，网络错误重试 3 次（`:989-998`）。A
- 更新字段：`buyboxPrice`、`holdingBuybox`、`stockStatus/stockMapped`、`offers`、`offersFetchedAt`（`collector.ts:245,261`）。A
- **失败保留旧价/旧成功时间**：是。失败项不上报 → `buyboxPrice`、`offersFetchedAt` 均不刷新。A
- **是否存在"只刷新时间、没取得新报价"的路径**：未发现（`offersFetchedAt` 只在成功上报时写）。A
- 暂停/中断恢复：无租约，进程退出后无在途任务；下一轮重新拉取。A

---

## 3. 本轮真实观察（2026-10-15，UTC）

轮次：**13:55:46 开始 → 13:57:19 结束**，结果 `ok=27 404=0 error=0`。

| 步骤 | 时间（UTC） | 记录 |
|---|---|---|
| 任务选择 | 13:55:46 | 日志 `拉取商品列表: 27 个 (all=False, limit=None, concurrency=3, shards=1)` |
| 页面请求 | 未记录 | 采集器不打印请求时间 |
| 页面就绪 | 未记录 | 无单独日志 |
| 身份/变体确认 | 13:56:40 等 | `变体切换: 后缀='3 Pack' before=168.0 after=None 方式=失败(仍为默认变体)` |
| 报价读取 | 13:56:20 | metrics：`sku=9902348624058 ok=true buybox=269.0 fastest=269.0 best=268.0 preorder=true ms=4896` |
| 一致性检查 | 未记录 | 双读不产生日志（仅失败时打印 `buybox 价格不稳定`） |
| 回传 | 13:57:19 | `上报完成: processed=28 computeEnqueued=26 skipped=[]` |
| 入库 | 13:57:19.637 | `Product.offersFetchedAt` 更新 |

吸尘袋 **SKU 9902348624058 / PLID100687738** 本轮结果：

- 标题：`6PCS Vacuum Cleaner Bags compatible with RV 20/30 Max Plus`
- 我方价 269.00；读到 **buybox = R269.00**；`holdingBuybox = true`
- 入库报价：`buybox 269 in_stock isMine=true`（Urban Electronics）、`fastest 269 in_stock isMine=true`、`best_price 268 backorder isMine=false`
- 配送文案：`fastest delivery … get it tomorrow, 7am - 7pm`、`best price … delivery 5 nov - 8 nov`、`In stock`
- **HTTP 状态：未记录**（原程序不记录，不从"读到价格"反推 200）
- 变体控件数：未采集该字段（原程序无 `variantControls`）

同轮变体切换失败样本 **SKU 9902390746609**（`16A Wi-Fi Mini DIY Smart Switch … - 3 Pack`）：
`我方价 273`，切换失败后**仍上报** `buybox=168`（默认变体价格），`holdingBuybox` 由服务端翻转保护暂留 `true`（采集器侧 `isMine=false`）。
**这说明原程序在变体切换失败时确实会回传一个可能属于错误变体的价格，靠后端拦截调价。**

相机 **SKU 9902348621330 / PLID97512496**：**本轮不在采集名单**（`autoPricingEnabled=false`，`offersFetchedAt` 为空，`collector_metrics.jsonl` 中 0 条记录 —— 即本机从未采集过该商品）。按约定，未自行将其加入生产任务。

---

## 4. 隔离探测：原程序对 PLID97512496 的真实行为

因相机不在生产名单，改用隔离方式验证（**不触碰生产 profile、不抢锁、不上报、不写库**）：

- 诊断目录 `/tmp/pp_diag`
- 浏览器 profile 用 **APFS 克隆副本**（`cp -Rc`），生产 `browser_profile` 未被改动（生产锁 PID 仍为 69429，日志仍停在 `休眠 600s`）
- 直接调用 `/tmp/pp_diag/collector_probe.py` 的 `scrape_product()`，入参为相机商品

结果（2026-10-15 约 14:00 UTC）：

| 项 | 值 |
|---|---|
| 请求 URL | `https://www.takealot.com/x/PLID97512496` |
| 最终 URL | `https://www.takealot.com/4k-thump-action-camera-professional-package-for-motor-biking-sky/PLID97512496`（slug 跳转，**PLID 一致**） |
| HTTP 状态 | **200**（document 响应） |
| 页面标题 | `4K Thump action camera - professional package for motor, biking, skydiving \| Shop Today. Get it Tomorrow! \| takealot.com` |
| 宽选择器 variant 命中数 | **16** |
| "More Colours" teaser 命中数 | **8** |
| buybox 卡数（`[data-ref="buybox-offer"]`） | **1** |
| 解析器返回 | `buyboxPrice=1999.0`、`buyboxSeller=Urban Electronics`、`holdingBuybox=true`、`stockMapped=in_stock`、offers 2 条（buybox 1999 / best_price 1999） |
| **`variantSwitchFailed`** | **true** |

结论：

1. 迁移侧在 `pricepilot_collector.py:411-442` 注释中记录的「PLID97512496 推荐卡内有 11-12 个 More Colours teaser、主商品区实为 0 个变体控件」——**宽选择器过宽这一点在原环境得到印证**（本环境实测 16 个 variant 命中 / 8 个 More Colours，而真实 buybox 卡只有 1 个）。
2. **原程序对该商品会置 `variantSwitchFailed=true`**，即：如果相机进入原 Mac 的生产名单，后端会落库但**跳过调价**。迁移侧新增的 `page_variant_controls` 门控正是把这个"误判为需切换变体"修掉，使该 SKU 能被正常采信。
3. 两条路径最终读到的是同一个价格 1999，但**采信状态不同**（原：冻结；迁移（修复后）：有效）。

---

## 5. 与迁移程序 main（`709be2a`）逐项比较

| # | 步骤 | 原 Mac 实现 | 迁移实现 | 实际行为差异 | 证据 | 是否足以造成故障 | 最小修复/验证方式 |
|---|---|---|---|---|---|---|---|
| 1 | 任务选择 | `collector.ts:81`：默认只采 `autoPricingEnabled=true`，无上限、无租约 | `collector_routes.py:91-111`：`catalog()` 遍历 + `enabled/request_once` + `next_due` + **单次上限 20** + uuid 租约 | 原程序"开关即名单"；迁移"调度器按需发放" | 本机 27/785；迁移 `if len(items)>=20: break` | 否（设计差异） | 无需修；但 Windows 端"领 0 任务"应先查 `enabled` 是否开启、`request_once` 是否已被上一轮消费 |
| 2 | 任务占用 | **无租约、无心跳** | `CLAIM_PREFIX` 租约 + `HEARTBEAT` 心跳 + `/release` + `recover_expired` | 迁移更严格；原程序进程中断即丢任务 | `collector.py:85`；`collector_routes.py:19,55-64,199-215` | 否 | — |
| 3 | productId 语义 | `platformProductId`（业务 ID） | uuid 租约 token | 迁移侧不可用 productId 反查商品 | `collector.ts:105`；`collector_routes.py:108` | 否（已知约束） | 维持现状，勿改回业务 ID |
| 4 | 浏览器 UA | **显式覆盖** Windows Chrome 131 | `tools/native-collector/collector.py:162-164` **无 UA 覆盖** | 迁移侧用 Playwright 默认 UA | `collector.py:41,909` vs `collector.py:162` | **待裁定** | 见 §6 合规边界 |
| 5 | 反自动化参数 | `--disable-blink-features=AutomationControlled`、`--lang=en-ZA` | 仅 `--no-sandbox`（native）；`quote_browser.py` 亦无 | 迁移侧缺 2 个参数 | `collector.py:900-905` vs `collector.py:163` | **待裁定** | 同上 |
| 6 | stealth 注入 | init script 隐藏 `webdriver`+伪造 `plugins`+`window.chrome`，并 `playwright_stealth` | **无** | 迁移侧无 stealth | `collector.py:833-837,890-894` vs native 全文 0 处 | **待裁定** | 同上 |
| 7 | geolocation | `permissions=["geolocation"]` + JHB 坐标 | **无**（仅 locale/timezone） | 迁移侧无地理坐标 | `collector.py:910-911` vs `collector.py:163` | **待裁定** | 同上 |
| 8 | HTTP 状态 | **不记录**（`resp` 未用） | 记录 `diagnostics.http_status`（native `:94,110`）；`classify()` 按 401/403/429/5xx 分类 | 原程序无法回答"HTTP 多少"；迁移可 | 实测原程序 0 处 `resp.status` | 否（可观测性差异） | 若需在原环境取证，只能用诊断脚本旁路监听 `page.on("response")` |
| 9 | 挑战页识别 | **无**（仅标题启发式） | `wait_initial_document()` + `CHALLENGE_TITLES` → `site_challenge` | 迁移更严格 | 原程序 0 处 `just a moment` | 否 | — |
| 10 | 变体门控 | **无**：宽选择器 `[class*="variant"]` 等待 | `_CORE_BLOCK_JS` + `_CARD_SCOPE_JS` + `main_variant_elements` + `page_variant_controls` | 原程序无法区分主商品区与推荐卡 | 原程序 0 处 `_CORE_BLOCK_JS`；本环境实测宽选择器命中 16 | **是（对多变体商品）** | 迁移侧修复方向正确，建议保留 |
| 11 | 变体失败 | 置 `variantSwitchFailed=true`，**仍回传价格**；后端跳过调价但落库 | `valid=False` → `status='error'`，不写 `buybox_price`（`collector_routes.py:171,187-189`） | 原：网页可见"读到价但冻结"；迁移：仅显示错误 | `collector.py:605` + `collector.ts:285-301` vs `collector_routes.py:171` | 可能是"网页验收不通过"的观感成因之一 | 建议迁移侧在 `error` 分支保留 `snapshot` 供展示（不影响 `valid` 判定） |
| 12 | 双读稳定性 | 2 次 + 必要时第 3 次，`unstable_price` 放弃 | **完全一致**（`:669-691`） | 无差异 | 两侧代码同构 | 否 | — |
| 13 | holding 判定（采集器侧） | `abs(buybox-myPrice)<0.01` → 上报 true/false | 两侧算法**一致**；但 native `:101` **强制 `holdingBuybox=False`** | 迁移侧永不采信采集器持有结论 | `collector.py:777` vs native `:101` | 否（有意设计） | 已知约束，勿改 |
| 14 | holding 落库 | `resolveHolding()` 权威价复核 + 2 轮翻转保护 | `holding_buybox=None`（`collector_routes.py:183`）；云端 `buybox_monitor.py:66` 亦只写 `False/None` | 原程序会写 `true`；迁移不写 | `holding.ts:46-86` vs `collector_routes.py:183` | 否（有意设计，身份不采信） | 已知约束 |
| 15 | `collectedAt` | **不上报**（0 处） | native `:99` 上报；服务端 `observation_time()` 据此判时间来源 | 迁移侧有时间来源判定；原程序只能用回传时间 | 实测原程序 0 处 | 否 | — |
| 16 | 并发 | 3 | native 2（`round_once:125`） | 差异小 | — | 否 | — |

---

## 6. 合规边界说明（重要）

第 4–7 项（UA / `--disable-blink-features` / stealth / geolocation）属于**浏览器指纹与访问特征**范畴。
本次授权明确要求：不修改指纹、代理、网络出口或安全机制来规避网站验证。
因此：

- **本端不实施**这 4 项的"补齐"修改，也不建议把它当作 Windows 采集失败的首选解释；
- 仅作为「原程序有、迁移程序无」的**事实差异**记录，交由云端主负责人裁定是否、以及如何对齐；
- 若裁定需要对齐，建议先由 Windows 端在**单 SKU、低频**条件下验证，并观察验证页命中率，而不是直接全量放开。

---

## 7. 未创建候选修复分支的原因

按"有明确代码缺陷才做最小修复"的要求复核后：

- 迁移侧与主流程相关的 12/13/14/16 项属于**有意设计**或**两侧一致**，不是缺陷；
- 第 10/11 项差异已由迁移侧现网代码覆盖（门控已加），本端证据**支持保留现网实现**；
- 第 4–7 项落在合规边界内，不由本端修改；
- 未发现可由本端证据证实的迁移侧代码缺陷。

因此本次**未创建候选修复分支**，以免用无证据的改动覆盖 main。
若主负责人认为第 11 项（错误分支保留快照用于展示）值得改，本端可基于最新 main 建分支补一个只影响展示、不影响 `valid` 判定的最小改动。

---

## 8. 待验证项（不含推测结论）

1. **Windows 端领取 0 任务的具体成因**：需在生产配置上确认目标 SKU 的 monitor `enabled` 是否为真、`request_once` 是否已被上一轮消费、以及 `operations_profile.offers[].stock` 是否为 0/缺失 —— `collector_routes.py:128` 的 `/web-open` 会跳过 `stock<=0` 或非数值的 SKU，若 stock 缺失则网页点击永远排不上队。
2. **原 Mac 各进程 PID/启动时间**：本机 `ps` 被系统拒绝，需具备进程枚举权限的环境补采。
3. **挑战页在原环境的误判率**：原程序无挑战页识别，本次未实测到挑战页，不做结论。
4. **相机进入生产名单后的表现**：本端未开启其自动调价（需用户明确授权），当前仅有隔离探测结果。

---

## 9. 需要 Windows 端 WorkBuddy 执行的步骤与验收标准

1. 在网页对目标 SKU（相机 9902348621330）确认：常态采集开关状态、operations_profile 中该 offer 的 `stock` 是否为有效正数。**验收**：`/api/collector/products` 返回该 SKU 的租约任务（1 条）。
2. 运行 GUI「采集一轮」。**验收**：日志出现 `SKU 9902348621330: Buybox R 1999 已回传`，且 `successful=1`。
3. 网页同轮核对：显示 buybox R1999、来源标记为 `local_collector`、**不是** `cloud_browser`。**验收**：`last_attempt_source == 'local_collector'` 且 `source == 'local_collector'`。
4. 若仍 0 任务：贴出 `/api/collector/status` 返回与 GUI 日志首行，交由本端比对心跳与 `connected()` 状态。
