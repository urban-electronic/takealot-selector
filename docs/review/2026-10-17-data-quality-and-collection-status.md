# 数据质量排查与采集排队状态核查（v2 修正版）

- 日期：2026-10-17 17:00–17:10 (+08:00) 初版；v2 于 2026-10-17 19:xx 按 Codex 评论 `6232385441` 修正
- 对应 Codex 任务：`6231755050`（中文品名全库排查）、`6231775937`（同任务补充）、`6231895715`（SKU 9902348624058 排队状态投诉）、`6232385441`（v1 修正要求）
- 性质：**只读核查报告**。未修改任何数据、未触发采集、未读传密钥/Cookie。

---

## 0. v2 相对 v1 的修正（对应评论 6232385441 四条）

1. **计数改为 product_id 级去重**：v1 的"1+16+9+10=36 条唯一问题 / 74 个商品"把类内命中数与商品记录数混为一谈，且 G2 表实际列了 17 行。v2 全部按商品记录（product_id）计数，并区分类间交集；同一记录同时命中多类的只算一条需关注记录。
2. **区分"合法型号标签"与"真实错误"**：v1 把 `64GB/128GB`、`S/M`、`34·x·50'`、`7.0·cm`、`3·Pack` 等一律算问题。按项目既有"SKU 顺序对应中文规格名"语义，这些是**合法变体标签**，v2 单列 H2 不计问题；**不给任何规格值补假中文**。
3. **排队结论加上限**：v1"19 分钟两点均成功 ⇒ 与 5 分钟间隔吻合"的推断不成立。v2 明确：`last_attempt_at` 只证明该时刻有一次成功轮次，不能倒推历史排队原因；`request_once=false` 只代表核查时点的状态；`own_price_asof` 是本店价时点而非 Buybox 成功；没有同轮快照时间绑定，不把单字段当新报价证据。
4. **`holdingBuybox=true` 降级为待核实**：该值来自解析器对同价的推定，不等于卖家身份已核实。v2 明确需要 `source`/`last_success_at`/`quote_time_source` 与快照同轮时间绑定才能下结论，本轮**未**提供这些字段，故不给"自持已核实"结论。

---

## 1. 环境与版本证据

| 项 | 值 |
| --- | --- |
| 后端 | `https://takealot-selector-production.up.railway.app` |
| openapi | 77 条路径，`info.version = 1.0.0`（静态值，不含 commit） |
| 版本下界 | openapi 已含 `/api/inventory/reconciliation/{preview,apply}`（PR115 引入）⇒ 运行版本 **≥ main `73cacad`** |
| 商品总数 | **377** 条 |
| 无 commit 级版本端点 | `/version`、`/health`、`/api/health`、`/api/version`、`/system/version` 均 404，无法给出精确 commit，只能给上述下界 |

复现命令（全部只读）：

```bash
curl -s -H "X-API-Key: <key>" -H "X-Store-ID: default-store" \
  https://takealot-selector-production.up.railway.app/api/products?limit=1000&offset=0
curl -s -H "X-API-Key: <key>" -H "X-Store-ID: default-store" \
  https://takealot-selector-production.up.railway.app/api/buybox
curl -s -H "X-Collector-Key: <key>" -H "X-Collector-Client: wb-audit" \
  https://takealot-selector-production.up.railway.app/api/collector/status
```

---

## 2. 任务 A：中文品名全库排查

### 2.1 口径（v2 五分类）

以 `chinese_product_name`（中文字段）为对象，按商品记录（product_id）判定：

| 类 | 定义 | 性质 |
| --- | --- | --- |
| **H0** | 中文字段为空值 | 确定问题 |
| **H1** | 无汉字，且**不是**合法型号/规格标签（颜色、品类、受众等英文词，或英文品名残段） | **确定纯英文误填** |
| **H2** | 无汉字，但全部行都是规格/尺码/容量/包装数/尺寸 token（`64GB`、`S/M`、`34·x·50'`、`3·Pack` 等） | **合法变体标签，非问题** |
| **H3** | 含汉字但 ASCII 字母占比 > 45%，或中英混排无主体品名 | 需业务确认 |
| **H4** | 多 SKU 记录，父级 `product_name` 尾部只描述一个变体（`- Gold` 形态） | **不单独证明错配**，需业务确认 |

> H2 的判定依据是项目既有语义"SKU 顺序对应中文规格名"：中文字段按 SKU 顺序逐个标注变体规格是正常业务写法。H4 同理：单一父级英文标题覆盖多变体是该产品线的常见形态，只有叠加其他证据（如中文名与英文名变体数不一致且可对照）才构成错配；**本轮不把 H4 单列为错误**。

### 2.2 目标 SKU 精查（Codex 要求先查）

| 字段 | 值 |
| --- | --- |
| `sku` | `9902416416912` + `9902416416929`（同链接双变体，同一 `product_id` `fdfb7368-9018-48d7-8e59-3028232a8d24`） |
| `chinese_product_name` | **`Gold\nSilver`** ← 中文字段被英文颜色词占据 |
| `product_name` | `Elegant Vintage Women's 18K Gold Plated Necklace - Gold` ← 父级标题只描述 Gold 变体 |
| `link_status` / `selection_status` | 已购买 / 合格选品 |
| `note` | `45+5/2mm，银色这个链接 https://www.takealot.com/silver-stainless-steel-snake-chain-necklace/PLID95234630` |
| `updated_at` | 2026-10-10T05:17:26 |

**命中类别：H1 ∩ H4（v1 称"G2+G4"，现按新口径：H1 确定误填成立；H4 父级单变体描述成立但不单独构成错配证据）**。前端展示中文品名时直接渲染 `Gold\nSilver`，用户看到的就是英文。

**建议值（需人工确认，不自动写入）**：中文品名可补为「复古优雅女士18K镀金项链」，变体后缀分别为「-金色」「-银色」。依据：`product_name` 语义 + `note` 中已确认的"银色"表述 + 规格 45+5/2mm。**这是依据既有字段的补全，不是无依据机翻。** 注意：Gold/Silver 两个 SKU 共用同一商品行，修改时必须按 SKU 顺序保留金银区分，不能用一个中文名覆盖整行导致 Silver 错配（Codex 评论 `6231834199` 同旨）。

### 2.3 全库统计（product_id 级去重）

| 类 | 商品记录数 | SKU 数 | 性质 |
| --- | --- | --- | --- |
| 总数 | **377** | — | — |
| H0 中文字段空值 | **1** | 1 | 确定问题 |
| H1 确定纯英文误填 | **6** | 7 | 确定问题 |
| H2 合法型号标签 | 19 | 47 | **非问题**（仅记录） |
| H3 中英混排/主体缺失 | 38 | — | 需业务确认 |
| H4 父级单变体描述 | 14 | 35 | 需业务确认 |
| **需关注并集（H0∪H1∪H3∪H4）** | **55** | — | H2 不计入 |

类间交集（同一记录可命中多类，并集已去重）：

- H1 ∩ H4 = **1**（即目标 SKU `9902416416912/9902416416929`）
- H3 ∩ H4 = 3
- 其余交集为 0

**明确表述**：确定问题是 **H0 1 条 + H1 6 条 = 7 条商品记录**（不是 v1 的 36 条）；需业务确认 55−7 = 48 条记录（H3/H4 及与 H1 的交集已去重）；H2 19 条是合法写法，**不计入任何问题数**。v1 的"36 条唯一问题 / 74 个商品"作废。

### 2.4 H1 确定纯英文误填清单（6 条商品记录 / 7 个 SKU）

| sku | 当前中文品名字段值 | 英文品名（节选） |
| --- | --- | --- |
| `9902416416912` `9902416416929` | `Gold` / `Silver` | Elegant Vintage Women's 18K Gold Plated Necklace - Gold ← **目标 SKU** |
| `9902419581525` `9902419581549` | `Cats` / `Dogs` | Bead Box-3D Beaded Animal DIY Kit … |
| `9902382588781` `9902411418980` | `Wife` / `Daughter` | To My Wife Crown Pendant Necklace With Gift Box … |
| `9902426493170` | `AcrylGel Complete Po…` | AcrylGel Complete Polygel Kit with Nail Lamp - Set |
| （空 SKU，link_status=未购买） | `FRIYAY - …` | FRIYAY - Copper Infused Arthritis Gloves … |
| （空 SKU，link_status=未购买） | `Petsound …` | Petsound Ball (M-11cm or L-14cm) … |

后两条为选品池商品（空 SKU=未采购）的英文品名残段落入中文字段，属于确定误填但**优先级低于已购商品**。

### 2.5 H2 合法型号标签清单（19 条商品记录 / 47 个 SKU，**非问题**）

按 Codex 要求单列，证明这些值**不是**数据错误，禁止给它们补假中文：

| sku | 当前值 | 英文品名（节选） |
| --- | --- | --- |
| `9902421650912` `9902421650929` | `64GB` / `128GB` | Type C USB Flash Drive 32GB/64GB/128G … |
| `9902421396858` `9902421396841` | `2TB` / `1TB` | 2TB USB Metal Flash Drive … |
| `9902421394144` `9902421394199` | `1TB` / `2TB` | 2TB USB 3.0 Pendrive … |
| `9902407959329` `9902407959336` | `L` / `XL` | Pet Adjustable Waterproof Non-Slip Paw … |
| `9902411076715` `9902411076722` | `S` / `M` | Pet Harness Service Dog & Lead - Black - M |
| `9902388559280` `9902388559297` | `M` / `L` | Car Window Shade Sock … |
| `9902368142464` `9902368142471` | `S·M` / `L·XL` | 3 Pairs Ankle Swelling Relief … |
| `9902367061100` `9902367061117` `9902367061124` | `S` / `M` / `L` | Elegant Push Up and Lace-Up Front Lingerie Dress … |
| `9902348624539` `9902348624522` `9902348624546` `9902348624553` | `M` / `S` / `L` / `XL` | Women's Fashionable & Elegant Floral-Print Tight S … |
| `9902348622542` `9902348622535` `9902348622726` | `XL` / `L` / `M` | Mens Workout Compression Set … |
| `9902348622450` `9902348622238` `9902348622467` | `M` / `XL` / `L` | Men's Workout Compression Set … |
| `9902348621859` `9902348621866` `9902348622108` | `M` / `L` / `XL` | Gym Gloves Heavyweight Sports … |
| `9902363666941` `9902409597604` | `S` / `M` | 3 Pieces Dog Diapers Female - S |
| `9902387527372` `9902387527808` | `L` / `XL` | Motorcycle Gloves -Winter/Waterproof … |
| `9902390746609` `9902390747033` | `3·Pack` / `1·Pack` | 16A Wi-Fi Mini DIY Smart Switch … |
| `9902387513665` `9902387513726` | `34·x·50'` / `28·x·44'` | Men's 3 Piece - Notched Lapel … |
| `9902368131628` `9902368131635` | `7.0·cm` / `6.0·cm` | Voyager Pets - Dog Shoes - 6.0 cm |
| `9902348623181` `9902348623174` `9902348623198` `9902409541669` | `XL` / `L` / `3XL` / `2XL` | Women Slimming Waist Trainer Belt … |
| `9902348622573` `9902348622153` `9902348622160` | `M` / `2XL` / `3XL` | 25 Steel Boned Latex Waist Trainer … |

### 2.6 H3 / H4 需业务确认（不做批量修改）

**H3 中英混排/主体缺失（38 条）**：含汉字但字母占比 > 45%，多为「品牌名/型号 + 中文主体」。多数**可能是合理业务写法**，需人工逐条确认；其中 `LED灯灯`（语义重复）、`架子tapo支架`、`缠绕支架tapo`、`dock插头` 等属主体缺失/语义重复，可优先人工复核；`Zenty 追踪器`、`tapo 垃圾袋` 等品牌+主体写法可保留。抽样：

| sku | 当前值 | 字母占比 |
| --- | --- | --- |
| `9902348624058` | `6pcs tapo垃圾袋` | 58% |
| `9902591488780` | `Zenty 高压电子` | 50% |
| `9902512417554` | `Zenty防尘袋5件装` | 45% |
| `9902430413706` | `LED灯灯` | 60% |
| `9902407958575` | `USBHDMI集线` | 78% |
| `9902359788893` | `MOVA E20PLUS垃圾袋3个装` | 50% |

**H4 父级单变体描述（14 条）**：多 SKU 记录 + 父级英文标题尾部只描述一个变体。按 Codex 口径**不单独证明 SKU 映射错误**，仅列出待业务确认；如确认错配再按既有映射格式修正。典型：`9902391031254/9902391031247`（`白色`/`黑色` ← Smart WiFi Touch Switch 2 Gang … -**White**）、`9902348621729` …5 个 SKU（`棕色`/`绿色`/`蓝色`/`白色`/`黑色` ← Camping … Cap Hat -**Green**）。

### 2.7 已排除的误报（重要）

**88 条 `sku` 为空的记录，全部 `link_status = 未购买`（100%）**，其中合格选品 68、不建议选品 15、数据待补充 5。这是选品池商品尚未采购的正常状态，**不是数据错误，不计入问题清单**。若按"空 SKU 即异常"机械筛查，会凭空多出 88 条误报。

### 2.8 回读证据

- 数据来源文件：`prod_full.json`（`GET /api/products?limit=1000&offset=0`，377 条）
- v1 分类脚本：`name_audit.py`（口径作废，保留备查）
- **v2 分类脚本：`name_audit_v2.py`**（纯本地正则分类，无网络；H0–H4 口径见 2.1）
- v2 分类结果 ID 清单：`name_audit_v2_ids.json`（各类 `product_id` 集合，交集计数由脚本直接输出）
- 回读方式：任一 SKU 用 `GET /api/products?search=<sku>` 或重新全量拉取后比对 `chinese_product_name` 当前值即可验证本报告结论

---

## 3. 任务 B：SKU 9902348624058 排队状态核查

用户截图显示"最近成功 2026-10-17 16:41:33"，拍摄约 16:42，质疑排队卡死。

### 3.1 实测值（2026-10-17 17:00 +08:00）

| 字段 | 值 |
| --- | --- |
| `sku` / `offer_id` | `9902348624058` / `234862405` |
| `product_id` | `0dd0d693-5e85-4035-b3be-f9c781c80245` |
| `enabled` | **true** |
| `interval_minutes` | **5** |
| `request_once` | **false** |
| `web_requested` | **false** |
| `status` | **success** |
| `failures` | **0** |
| `last_attempt_at` | `2026-10-17T08:58:55Z` = **北京 16:58:55** |
| `next_due` | `1792227829` = **北京 17:03:49** |
| `segment` | 第1段 |
| `own_price_asof` | `2026-10-17T16:39:53+08:00` |

### 3.2 观测到的字段值（不等同于业务结论）

`last_attempt_at`（16:58:55）对应的记录字段值：

- `http_status = 200`，`page_title` 正常，`block_reason = null`
- `buyboxPrice = 269.0`，`buyboxSeller = Urban Electronics`，`holdingBuybox = true`
- `myPrice = 269`，`myStock = 11`，`stockStatus = In stock`
- `offers` 共 3 条：自己 2 条（buybox 269 / fastest 269）+ **1 条非本人竞品**（`isMine: false`，`best_price` **268**，`status = backorder`，`bestPricePreorder = true`，配送 6–11 Nov）

**重要限定（对应修正第 3、4 条）**：

- `holdingBuybox=true` 与 `buyboxSeller = Urban Electronics` 是解析器按同价格推定的结果，**不等于卖家身份已核实**；本轮未取得 `source`/`last_success_at`/`quote_time_source`，也没有把该轮响应与同一快照时间绑定的证据，因此**不给出"自持已核实"结论**。
- `own_price_asof = 16:39:53` 是**本店价格时点**，不是另一次 Buybox 采集成功，不能与 16:58:55 并列当作"两次均成功"。
- 上述字段来自 17:00 的只读拉取，与用户 16:42 截图**不是同一时刻的快照**；没有同轮时间绑定，不把 `last_attempt_at` 单独当作"截图时刻的新报价证据"。

### 3.3 结论（限定口径）

**没有证据表明卡死，但也不能用本轮数据证明历史排队原因。**

- 核查时点（17:00）该 offer `enabled=true`、`status=success`、`failures=0`，`next_due=17:03:49`，链路在正常周期窗口内；
- `request_once=false`、`web_requested=false` **只代表核查时点**没有待处理的"立即采集"请求——用户 16:42 截图时是否有点击触发的排队，**无法用当前字段值倒推**；
- 用户截图时刻（16:41:33 的"最近成功"）与本轮核查（16:58:55 的成功记录）相差约 17 分钟，两个时间点各自成立，**互不矛盾，也互不证明**；"19 分钟两点 ⇒ 5 分钟稳定周期"的推断**不成立**，v1 该句作废；
- 若用户仍质疑 16:42 前后排队，需要后端提供该时间窗内的领取/回传日志（领取时刻、client、结果）才能定论，本轮数据不足。

**心跳说明**：`GET /api/collector/status` 仅返回 `{"ready": true, "store": "default-store", "mode": "quote_only"}`，**不包含心跳时间戳**。因此无法提供"采集器心跳时间"这一项，只能以每个 offer 的 `last_attempt_at` 作为回传证据。若需要真正的心跳监控，需后端在该端点补充 `last_seen_at`。

**竞品提示（不影响本次结论）**：该 SKU 记录中有一条竞品 `best_price = 268`（`status=backorder`，非现货）。按 `decide()` 的口径，非现货竞品不触发跟价，因此当前维持 269 是正确的——此为字段值推断，非本轮实测调价。

---

## 4. 红线

- 未修改任何商品数据、未改售价、未开启真实调价、未创建采购。
- 未触发采集：未调用 `POST /api/buybox/page-open`、未调用 `GET /api/collector/products`（不领取租约）。
- 未读取、传输或打印任何密钥 / Cookie。
- 所有数据经 `GET` 只读取得。中文品名的"建议值"仅为供人工确认的参考，**未写入任何字段**。
- H2 合法型号标签**不补假中文**；H4 不单独认定为错配；疑似项不猜测、不批量修改。
