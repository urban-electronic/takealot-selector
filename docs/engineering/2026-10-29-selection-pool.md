# 选品池（Selection Pool）：完整交接

日期：2026-10-29
状态：已上线（GitHub Pages 前端 + Railway 后端），可继续迭代
当前 `main` 上的相关提交：`a01ef68`（合并上线）→ `6eefa19` / `2267480`（导航与窄屏版式）→ `0d5f58b`（类目下拉 + 主图诊断）→ `bbab0a8`（采购价派生）→ `e45ad1d`（429 节流）→ `5694253`（收尾文案）

这份文档写给接手的人/agent：先看「它是什么、边界在哪」，再看「数据与接口」，最后看「已知缺口」。所有行号以本文写作时的 `main` 为准。

---

## 1. 它解决什么问题

原来的流程是「在别处筛选 → 到新建产品页一条条录」。筛选和记录是断开的：候选商品没有落脚点，核算数据（重尺、采购价、利润率）在录之前根本不存在，只能凭感觉。

选品池把**筛选和建档接成一条流水线**：

```
上传表  →  抓主图/类目/价格  →  填重尺和采购价  →  算出利润率  →  勾选达标的  →  一键进产品列表
 import       enrich              PATCH/bulk          _calc         select-qualified   promote
```

### 不做什么（重要边界）

- **不写 `products`**。草稿留在 `selection_items`。`products` 是库存、打包、采购三块的锚点（`inventory_routes` 查库存只按 `store_id + is_archived` 过滤，不看选品状态；打包有 32 处引用、采购 10 处），草稿进去会立刻出现在库存和装箱单里被当成真货。而且一轮 1800 条绝大多数会被淘汰，删掉还会在 `product_no` 上留空洞。
- **不做实时比价/自动选品**。没有「自动挑出值得做的商品」这种能力，判断仍然靠人，选品池只负责把数据凑齐、把利润算准。
- **不用类目默认值冒充重尺**。拿不到就留空、标「缺重尺」，绝不让运费按猜测值算。
- **不绕过 Takealot 的站点校验**。公开接口是正常请求，被 Cloudflare 挡就如实报错（见 §8）。

---

## 2. 文件地图

| 位置 | 行数 | 作用 |
|---|---|---|
| `backend/models.py:418` | ~105 | `SelectionItem` 表定义（字段含义写在类的 docstring 里） |
| `backend/api/selection_routes.py` | 1209 | 全部 13 个端点 + 核算/回填/分档逻辑 |
| `backend/services/selection_import.py` | 264 | Excel/CSV 读表、列名猜测、数字解析 |
| `backend/services/takealot_public_api.py` | 415 | 公开接口抓图/类目/价格/重尺，含节流与重试 |
| `backend/services/takealot_catalogue_api.py` | — | 卖家目录接口探测（竞品重尺，见 §5.2） |
| `backend/services/fee_category_matcher.py` | — | 前台类目路径 → Fee 类目推断 |
| `backend/services/product_calculator.py:35` | ~280 | `calculate_all()`，与产品详情页共用同一套公式 |
| `src/pages/Selection.tsx` | 816 | 页面本体（卡片、工具栏、批量操作、诊断面板） |
| `src/remoteApi.ts:837-935` | ~100 | 13 个 API 封装 |
| `src/types.ts:429-566` | ~140 | `SelectionItem` / `ImportResult` / `EnrichResult` / `Summary` / `Batch` / `Patch` / `PromoteResult` |
| `src/index.css:3167-3670` | ~500 | 全部样式（`.selection-*`），含 480px / 700px 断点 |
| `src/App.tsx:11,25,189` | — | 懒加载 + 导航项 + 路由 |
| `backend/tests/test_selection_routes.py` | 763 | 40 个用例 |
| `backend/tests/test_takealot_public_api.py` | 370 | 49 个用例（含节流/重试/预算） |
| `scripts/test-responsive-layout.py:162-212` | — | `/selection` 的版式夹具（`_selection_item` 工厂 + 三条 mock） |

---

## 3. 数据模型

`selection_items` 一张表，**没有单独的批次表**——批次是 `batch_id` 字段上的分组，`batch_name` 冗余存在每行上，`/batches` 端点用 `GROUP BY` 现算。这样整批删除就是一条 `DELETE`，不需要维护两张表的一致性。

### 分组与身份

| 字段 | 说明 |
|---|---|
| `batch_id` | `YYYYmmddHHMMSS-<6位随机>`，一次上传一个 |
| `batch_name` | 用户填的批次名，空则用文件名 |
| `source_row` | 原始 Excel 行号（`index + 2`，方便回表核对） |
| `plid` / `tsin` | 两个身份锚点，至少有一个 |
| `already_in_products` | **导入时**算一次，之后不刷新（见 §9.1） |
| `promoted_product_id` | 指向 `products.id`；非空即「已建产品」，卡片锁定不可编辑 |

### 三类输入

1. **只有链接**（竞品店铺导出）→ `takealot_url`，PLID 从链接正则取
2. **只有 TSIN**（Takealot 销售报表）→ `tsin`，enrich 时用卖家 API 反查 PLID
3. **链接 + 销售数据**（自己整理的表）→ 全字段都有

### 状态字段

| 字段 | 取值 | 谁写 |
|---|---|---|
| `status` | 待核算 / 合格 / 不合格 / 已建产品 | `_refresh_status()`，每次 `_calc` 后 |
| `enrich_status` | `""` / `ok` / `failed` | `_apply_public()` |
| `dimensions_source` | `product_list` / `seller_api` / `catalogue_api` / `public_api` / `manual` | 各回填步骤；一旦是 `manual` 就不再被网络值覆盖 |
| `tier` | S / A / B / C / 空 | 导入时按帕累托算，之后不重算 |

`status` 与 `Product.selection_status` 用词接近但含义更粗——这是有意的：选品池只关心「能不能建」，不关心上架后的运营状态。

---

## 4. 接口清单

全部挂在 `/api/selection`（`backend/main.py:145`）。所有端点都经过 `get_store_id` 做店铺隔离（读 `X-Store-ID` 头，缺省 `default-store`），跨店铺查不到数据。

| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| POST | `/import` | multipart: `file`, `batch_name` | 读表 → 聚合 → 落库。返回合并前后行数、重复数、识别到的列、未识别的列 |
| POST | `/enrich` | `{batch_id?, ids?, limit≤800, refresh}` | 抓资料。**分块**：一次最多 `ENRICH_CHUNK=400`，无访问拒绝/限流时，`remaining≠0` 由前端接着调 |
| GET | `/items` | `batch_id, status, keyword, tier, only_selected, hide_duplicates, sort, limit≤300, offset` | 列表。`sort` 支持 `tier`(默认) / `sales` / `profit` / `price` |
| GET | `/batches` | — | 批次列表（含 count、最早 created_at） |
| GET | `/summary` | `batch_id?` | 汇总计数：total/pending/qualified/rejected/promoted/selected/duplicates/with_image/with_dimensions/no_plid |
| PATCH | `/items/{id}` | `ItemPatch` | 单条改。改重尺会把 `dimensions_source` 锁成 `manual` |
| POST | `/items/bulk` | `{ids≤2000, patch}` | 批量改同一组字段 |
| POST | `/items/select-qualified` | `{batch_id?, min_margin=0.15, only_new=true}` | 一条 UPDATE 把达标的整批勾上（不翻页，避免漏） |
| POST | `/items/promote` | `{ids≤500}` | 复制成 `Product`，回写 `promoted_product_id` |
| DELETE | `/items` | `{ids≤500}` | 删记录 |
| DELETE | `/batches/{batch_id}` | — | 整批删，**保留已建产品的记录** |
| GET | `/diag/public` | `plid` | 让**服务器**自己打一次公开接口，原样返回结果（排 429/403 用） |
| GET | `/diag/catalogue` | `plid, save=true` | 探测卖家目录接口的「端点 × 鉴权位置」组合 |

`query_items` / `summarize` 被拆成独立函数（不直接在路由函数里写逻辑），因为 `Query(None)` 的默认值对象本身是真值——被内部直接调用时 `if batch_id:` 永远成立，会静默加一个 `batch_id IS NULL` 过滤，返回空列表。

---

## 5. 处理链路（每步的真实优先级）

### 5.1 导入（`import_table`）

1. 读表：`.xlsx/.xlsm` 用 openpyxl，`.csv/.txt` 用 csv（自动嗅探 `,;\t`，按 `utf-8-sig → utf-8 → gb18030 → latin-1` 逐个试编码）
2. **选 sheet 靠打分**，不取第一个：`_score_sheet = 身份字段数×1e6 + 字段数×1e3 + 行数`。实测销售报表 sheet 顺序是 `['Sheet3','Sheet4','Sheet1']`，真数据在最后的 Sheet1（617 行 vs 13,984 行）
3. 列名按别名表猜（`FIELD_ALIASES`），中英文都认。顺序即优先级——`review_count` 必须排在 `rating_value` 之前，否则「评分数量」会被「评分」抢走
4. **按 `plid → tsin → 标题(小写)` 的优先级聚合**。销售报表一行一个订单行，不聚合 13,984 行就是 13,984 张卡片。聚合时 `sales_qty / net_sales_zar / gross_sales_zar` **累加**，其余字段取第一个非空
5. 批量查 `products.plid` 标出 `already_in_products`（每 500 个一批）
6. `_assign_tiers()` 按累计净销售额占比分档：≤50% S、≤80% A、≤95% B、其余 C。**没有销售数据就不分档**（竞品表硬套会让所有商品都变 S）

数字解析（`to_number`）是这块最容易错的地方：`"R 1,234.56"`、`"1 234,56"`、`"1.234,56"`、`"R325 (含税)"` 都要能吃。逗号既可能是千分位也可能是小数点，**不能一律删掉**——`"1 234,56"` 删逗号会变成 123456，价格错 100 倍。

### 5.2 抓资料（`enrich`）——四层回退

按优先级依次尝试，前一层填到的字段不会被后一层覆盖：

| 顺序 | 来源 | 能拿到什么 | 条件 |
|---|---|---|---|
| 1 | **产品主档** `_copy_from_product_list` | 名字/图/价格/类目/重尺 | 按 `plid` 或 `tsin` 匹配 `products`。只填空着的字段，不覆盖报表带来的数据 |
| 2 | **卖家 API** `/v1/offers` | 重尺（cm/g → mm/kg）、TSIN→PLID 反查 | 只对本店 offer 有效，拿不到竞品 |
| 3 | **卖家目录接口** `_fill_catalogue_dims` | 竞品重尺 | 只在 `/diag/catalogue` 探测成功并保存了 winner 之后才生效 |
| 4 | **公开接口** `pub.fetch_many` | 主图/图库/类目树/售价/评分/重尺(约10%) | 并发上限 4，受 `_Pacer` 节流 |

**为什么第 1 层是主档**：销售报表只有 TSIN，而那些 TSIN 对应的商品本来就在产品列表里、重尺是之前人工填过的。走网络既慢又可能把人工值冲掉。

**哪些条目会打网络**：`refresh=false` 时只挑「缺主图 或 缺价格 或 缺费率类目」的。刻意不要求 `takealot_category_path`——`Product` 主档本来就不存前台类目路径（存的是确认过的 `fee_category`），拿它当必要条件会让自家商品永远重抓。

**分块与推迟**：`ENRICH_CHUNK=400`，按45秒和当前间隔**估算**单次发送量（`pub.fetch_budget(45)`；并非强制总deadline）。正常节奏（间隔 0.2s）能抓 225 个；被限流放慢到 2s 就只剩 22 个。抓不完的记进 `deferred_plids`，**原样保留**——不记 failed、不参与本轮计算、仍在 `remaining` 里，没有访问拒绝/限流时下一轮自动接着抓；`public_access_blocked=true` 时停止后续轮次。这是刻意的：`enrich` 是末尾一次 `commit()`，请求被代理掐掉就整块白丢。

### 5.3 人工补数据（`PATCH` / `bulk`）

`_apply_patch()` 做三件事：
1. 有重尺字段被改 → `dimensions_source = "manual"`（从此网络值不再覆盖）
2. 写字段 → `_calc()` → `_refresh_status()`

**采购价的派生**（`_derive_purchase_cost`，放在 `_calc` 最前面，所以导入/单条改/批量改/enrich/promote 全部同一口径）：

```
供货单价有值  →  采购价 = 供货单价 × 采购数量（四舍五入 2 位）
供货单价留空  →  采购价照旧手填（不清空）
清空单价      →  保留上一次算出的总价，回到手填模式
```

### 5.4 核算公式（`product_calculator.calculate_all`）

与产品详情页**共用同一套**，不要在选品池里另写一份。

```
体积(cbm)        = 长mm × 宽mm × 高mm / 1e9
体积重(kg)       = cbm × 1000 / 6            # 即 长×宽×高(cm)/6000
计费重(kg)       = max(实重, 体积重)
国内前置仓(¥)    = max(5 × 计费重, 1)
单件综合成本(¥)  = (采购价 + 国内运费) / 采购数量 + 包装费 + 国内前置仓
国际头程(¥)      = 空运普货 79×计费重 | 空运带电 89×计费重
                   海运普货 1500×cbm | 海运带电 2100×cbm
到仓成本(ZAR)    = (单件综合成本 + 国际头程) / 汇率
海外仓操作(ZAR)  = (0.75 + 0.70 + 2.00 + 2.00) / 汇率
Success Fee(ZAR) = 类目费率 × 售价
官方费用(ZAR)    = Success Fee + Fulfillment Fee（按类目，缺省 42.00）
总成本(ZAR)      = 到仓成本 + 海外仓操作 + 官方费用
利润(ZAR)        = 售价 - 总成本
利润率           = 1 - 总成本 / 售价
```

- 汇率从 `SystemSettings.cny_per_zar` 读，缺省 `0.41`
- 任一环缺失，后面全部 `None`，界面显示「待核算」——不需要自己判完整性
- 合格线 `QUALIFY_MARGIN = 0.15`（用户确认按 15%）

### 5.5 一键新建产品（`promote`）

只复制「核算需要的东西」，**不复制销售数据**（那是竞品/历史数据，不该进主档）。跳过三种情况：

1. `promoted_product_id` 已存在 → 「已建过产品」
2. `already_in_products && plid` → 「产品列表里已有同一 PLID」
3. 既无 `product_name` 又无 `plid` → 无法建产品

`fee_category_confirmed` 直接置 `true`——用户点了「新建」就视为认可自动匹配的类目，之后在详情页仍可改。`product_no` 取当前最大值 +1。

---

## 6. 前端结构

`src/pages/Selection.tsx` 一个文件，三个组件：

- `NumberCell` — 数值输入。**本地保留字符串，失焦或回车才提交**，避免每敲一个字符发一次请求
- `MarginBadge` — 利润率徽标（已建产品 / 待补数据 / ≥15% / <15%）
- `SelectionCard` — 一张卡片，顺序：勾选框+档位+标记 → 主图 → 标题 → TSIN/PLID/评价 → 售价 → 类目 → **8 个数值字段** → 物流方式+类目下拉 → 结果四行 → 页脚（重尺来源 / 前台 / 货源 / 查看产品）

页面自上而下：标题+4 个工具栏按钮 → 提示条 → 上传区（含诊断面板）→ 汇总条 → 筛选工具栏 → 批量操作条 → 卡片网格 → 分页。

### 四个工具栏按钮

| 按钮 | 行为 |
|---|---|
| 测试主图接口 | 拿列表第一个有 PLID 的样本，让**服务器**打一次公开接口，展开面板显示接口结果/warnings/商品名/图 URL/前台类目/售价 |
| 探测竞品重尺接口 | 逐个试「端点 × 鉴权位置」，成功后保存 winner，之后 enrich 自动用 |
| 重抓全部资料 | `handleEnrich(true)`：`refresh=true` 只作用于**第一轮**，后续轮次只补没抓过的 |
| 一键新建产品（N） | 只处理本页勾选且未建过的 |

### 抓取循环（`handleEnrich`）

最多20轮，每轮调一次 `/enrich`。收到 `public_access_blocked=true` 即停止，保留实际 `remaining` 和已有数据，不能通过后续批次继续请求；无访问限制且 `remaining` 不为0时才继续。成功/失败按每轮 `ok/failed` 累计；处理上限仍有剩余时显示待处理数量，不报完成。

### 卡片里的三个下拉/派生

- 类目：**下拉选全部 Fee 类目**（带成功率），不再手打。库里存的值不在类目表中时补一条「（当前值）」，否则下拉会显示成「未匹配」
- 采购价：有供货单价时**置灰**并标 `= 单价×数量`，前端也算一份只为立刻显示，不用等往返
- 批量操作只作用于**当前页**（`items` 数组），不是全库

---

## 7. 测试与验证

```bash
# 后端（最常用）
cd backend && python3 -m pytest tests/test_selection_routes.py -q          # 40 个
cd backend && python3 -m pytest tests/test_takealot_public_api.py -q       # 49 个
cd backend && python3 -m pytest tests -q                                   # 886 个全量

# 前端
npx tsc --noEmit
npm run check -- --quick        # tsc + 视觉一致性 + 全部 scripts/test-*.cjs|mjs

# 版式审计（需要 playwright + chromium，CI 里必跑）
TMPDIR="$PWD/.tmp-audit" python3 scripts/test-responsive-layout.py         # 108 项
TMPDIR="$PWD/.tmp-audit" python3 scripts/test-text-overlap.py              # 170 个面
```

40 个后端用例钉的是「错了会真亏钱」的点：聚合、单位换算（cm/g → mm/kg）、重尺不被覆盖、重复新建、15% 合格线、采购价派生、429 推迟语义。`TestDiagPublic` 钉住「成功/失败都要把原因原样带回去」。

`test-responsive-layout.py` 里 `/selection` 有专门的夹具（`_selection_item()` 是**全套字段**的默认值工厂，约 50 个 key，后端加字段不用回来补夹具；另加两条真实形状的草稿：一条长英文名+完整重尺+已算利润率，一条缺 PLID+重尺待填）。空列表也能跑通，但那样卡片区/汇总条/分页全是空态，等于没测。9 个视口 × 12 个页面 = 108 项检查。

---

## 8. 部署与线上

| 链路 | 触发 | 关键点 |
|---|---|---|
| 前端 | push `main` → `.github/workflows/deploy.yml` | `build` job 跑 `npm run check -- --quick` + `GH_PAGES=true npm run build` + `cp dist/index.html dist/404.html`；`deploy` job `needs: build` |
| 后端 | Railway 跟随 `main` 自动重建 | `Dockerfile` 两阶段：node 构建前端 → `playwright/python:v1.47.0-jammy` 运行时。**不要设 `GH_PAGES`**（会把 base 改成子路径），要设 `VITE_API_SAME_ORIGIN=1` |
| 版式审计 | 独立 job `Backend + layout tests` | `node scripts/selfcheck.cjs --python-only --require-python`。**`deploy` 不依赖它**，所以它红不挡发布 |

线上地址：
- 前端 `https://urban-electronic.github.io/takealot-selector/`
- 后端 `https://takealot-selector-production.up.railway.app`（`/` 返回 `{"build":"<commit sha>"}`，可用来确认是否已重建；其余接口全 401，需要 API key）

前端请求经 `src/remoteApi.ts` 的 `request()` 统一带上 `X-API-Key` 和 `X-Store-ID`。

### 建表 / 迁移

`main.py:184` 的 `Base.metadata.create_all()` 会自动建 `selection_items`，**新表不需要迁移**。但 `create_all` 不改已有表——**如果以后给 `selection_items` 加列，必须同时往 `_ensure_columns()`（`main.py:349`）里加一条 `ALTER TABLE`**，否则线上老库会缺列报错。

---

## 9. 已知缺口（按建议优先级）

### 9.1 `already_in_products` 不刷新 → promote 可能重复建产品 ⚠️

只在导入时算一次，之后不更新。而且**只按 PLID 判**（`already = bool(group["plid"]) and group["plid"] in existing_plids`）——销售报表里只有 TSIN 的那批，导入时**永远不会**被标成重复，哪怕商品早就在产品列表里。

`promote` 只信这个标记，**不实时查 `products.plid`**。所以两种情况下会重复建：

- 导入之后又在产品列表里手工建了同 PLID
- 只有 TSIN 的报表行（标记永远是 False）

修法：`promote` 里按 `item.plid`（或 `item.tsin`）实时查一次 `products`（`store_id + plid`），命中就跳过；导入时也把 TSIN 纳入重复判断。

### 9.2 竞品重尺覆盖率低（最大体验缺口）

公开接口里 `Assembled Dimensions` 实测约 10%、`Weight` 约 2%，格式还乱（`"22 x 7.2 x 17 cm"`、`"M-L"`）。所以竞品卡片大面积「缺重尺，算不出运费」，必须人工填。

`/diag/catalogue` 已经能探测「Search Takealot's Catalogue」那套接口，但**鉴权方案未确认**（`/v1/catalogue` 系列返回 401，路径存在、只差鉴权，且和 `/v1/offers` 不是同一套）。这是目前最有价值的一条待打通路径：通了就能自动补竞品重尺。

### 9.3 公开接口访问受限（业务接入未验证）

裸403只能证明访问被拒，429表示限流，不能据此宣称验证码或出口配额根因已证实。
使用真实应用标识，不模拟浏览器UA、客户端提示、指纹或切换出口规避验证。
401/403/429均立即停止自动请求、排队任务和后续前端轮次；已发出的单次请求可结束。
暂停中的节流等待和暂时性故障退避也响应停止信号。仅暂时性5xx/网络故障有有限重试，
Retry-After读取不区分大小写，等待有上限。

保留 `_Pacer` 主动节流、最多4个工作线程及分块推迟逻辑。429可降低未来人工操作的节奏，
但不能用于继续本次被限流的读取。接入条件未改变时，不重复点诊断或重抓已确认受限页面。
没有通过真实公共接口恢复验收；隔离测试、发布成功不能替代真实SKU资料回传。
45秒仅为发送量估算，整批强制deadline仍是单独未解决项。

### 9.4 大批量时体验受限

- 每页 24 张卡片，1800 条要翻 75 页，**没有表格视图**。加一个紧凑表格视图（列：图/名/PLID/售价/利润率/状态）会显著改善
- 批量操作（批量设物流、取消勾选）只作用于**当前页**，跨页批量只能靠「按 15% 勾选达标商品」那一个入口
- 没有导出：算完的一批不能导成 Excel

### 9.5 数据回填的小口子

- `tier` 只在导入时算，之后改数据不重算
- `chinese_product_name` 只能靠表里带或手填，没有翻译流程
- `purchase_url`（货源链接）完全手填，没有自动找 1688。详情页已有 `external_offers`（站外货源登记 + 关键词匹配），可以接进选品池卡片
- `_apply_public` 会用公开接口的价格**覆盖**表里带的价格（`if record.get("actual_sale_price_zar") is not None`）。如果认为表里的价格更可信，这里要改成「只在为空时填」
- `enrich` 的 `refresh=true` 只在第一轮生效（前端 `refresh: refreshAll && round === 0`）

---

## 10. 改动时的红线

这些都是踩过的坑，改之前先看一眼：

1. **`.selection-card` 必须有 `min-width: 0`**。grid item 默认 `min-width: auto`，min-content 会被 `nowrap` 的长英文标题撑到 390px，320px 视口直接横向溢出
2. **`minmax(min(320px, 100%), 1fr)` 是无效值**，整条声明被丢弃。要写 `minmax(320px, 1fr)` 然后在网格项上压 `min-width`
3. **卡片里不要放 `nowrap` 的长文本**，也别用 `.visually-hidden` 装长标题——版式审计会判「文字被裁且无省略号」。无障碍名直接挂 `aria-label`
4. **`useFeeCategories(source)` 的 effect 依赖 `source` 对象本身**，必须传模块级常量 `FEE_CATEGORY_SOURCE`，否则每次渲染重读一遍
5. **新增导航项要同步 `scripts/test-responsive-layout.py:218` 的 `EXPECTED_NAV`**，否则 7 项变 8 项时审计会红（原来是写死 `links.count()!=6`，已经改成比对标签清单）
6. **不要给 `enrich` 的 `fetch_many` 传 `workers=8`**。`workers` 现在只是并发上限，写死 8 会把 `fetch_many` 的默认值 4 抵消掉，机房 IP 上就是整片 429
7. **`enrich` 里被推迟的记录必须 `continue`**。照常走一遍会被 `_apply_public` 记成 failed，`enrich_status` 一旦变成 failed 就不在 `remaining` 里，下一轮再也不会自动重试
8. **`processed` 要扣掉推迟数**（`handled` 逐条计数，不要用 `len(targets) - len(集合)`——同一 PLID 可能对应多条记录，集合长度会偏小）
9. **不要新增一套核算公式**。`calculate_all()` 是和产品详情页共用的，两边分叉会导致同一个商品在两个页面显示不同利润
10. **测试里 `pub.fetch_many` 的 lambda 默认值写的是 `workers=8`**（历史遗留）。调用处传 `workers=4`，所以 keyword 覆盖了默认值，暂时没问题；但改函数签名时要一起改

---

## 11. 快速上手

```bash
# 后端
cd backend && uvicorn main:app --reload --port 8000

# 前端（另开一个终端）
npm run dev
```

打开 `/selection`，传一份只有「产品链接」的 Excel 就能跑通全流程：PLID 从链接取，主图和类目走公开接口，重尺和采购价手填，利润率自动算出来。

想验证抓取是否正常，点「测试主图接口」——它会显示服务器侧的真实结果，需区分本地和服务器网络条件；接入条件未改变时，不重复测试已确认受限页面。
