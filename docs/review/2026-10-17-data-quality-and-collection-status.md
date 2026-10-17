# 数据质量排查与采集排队状态核查

- 日期：2026-10-17 17:00–17:10 (+08:00)
- 对应 Codex 任务：`6231755050`（中文品名全库排查）、`6231775937`（同任务补充）、`6231895715`（SKU 9902348624058 排队状态投诉）
- 性质：**只读核查报告**。未修改任何数据、未触发采集、未读传密钥/Cookie。

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

### 2.1 口径

`chinese_product_name` 为中文字段。判定"异常"的标准：

- **G1** 空值；
- **G2** 该字段被规格/尺码/颜色词占据（多行短词、无汉字）；
- **G3** 该字段被英文短词或英文品名占据（无汉字、非规格形态）；
- **G4** 同一记录挂多个 SKU（变体），但 `product_name` 只描述其中一个变体（尾部带 `- 颜色/款式`）；
- **G5** 含汉字但 ASCII 字母占比 > 45%（中英混排，**需人工确认，不建议自动改**）。

### 2.2 目标 SKU 精查（Codex 要求先查）

| 字段 | 值 |
| --- | --- |
| `sku` | `9902416416912` + `9902416416929`（同链接双变体） |
| `chinese_product_name` | **`Gold\nSilver`** ← 中文字段被颜色词占据 |
| `product_name` | `Elegant Vintage Women's 18K Gold Plated Necklace - Gold` ← 只描述 Gold 变体 |
| `link_status` / `selection_status` | 已购买 / 合格选品 |
| `note` | `45+5/2mm，银色这个链接 https://www.takealot.com/silver-stainless-steel-snake-chain-necklace/PLID95234630` |
| `updated_at` | 2026-10-10T05:17:26 |

**本品同时命中 G2 与 G4 两类**：前端展示中文品名时直接渲染 `Gold\nSilver`，用户看到的就是英文；且 Silver 变体的英文名缺失（takealot 上实际为 `...Necklace - Silver`）。

**建议值（需人工确认，不自动写入）**：中文品名可补为「复古优雅女士18K镀金项链」，变体后缀分别为「-金色」「-银色」。依据：`product_name` 语义 + `note` 中已确认的"银色"表述 + 规格 45+5/2mm。**这是依据既有字段的补全，不是无依据机翻。**

### 2.3 全库统计

| 类别 | 条数 | 性质 |
| --- | --- | --- |
| 总数 | 377 | — |
| G1 中文品名空值 | **1** | 明确问题 |
| G2 中文字段被规格/尺码/颜色词占据 | **16** | 明确问题 |
| G3 中文字段被英文短词/英文品名占据 | **9** | 明确问题 |
| G4 多 SKU 但英文名只描述单变体 | **10** | 明确问题 |
| **明确问题合计** | **36** | |
| G5 中英混排（字母占比 > 45%） | 38 | **需人工确认，不建议自动改** |

**疑似总数：明确 36 条 + 待人工确认 38 条 = 74 条。**

### 2.4 明确问题清单（36 条）

**G1 中文品名空值（1）**

| sku | 英文品名 |
| --- | --- |
| `9902647071249` | 5-Pack Zenty Non-Woven Dust Bags Compatible with Karcher MV1 WD1 WD1S … |

**G2 中文字段被规格/尺码/颜色词占据（16）**

| sku | 当前中文品名字段值 |
| --- | --- |
| `9902421650912` `9902421650929` | `64GB` / `128GB` |
| `9902421396858` `9902421396841` | `2TB` / `1TB` |
| `9902421394144` `9902421394199` | `1TB` / `2TB` |
| `9902419581525` `9902419581549` | `Cats` / `Dogs` |
| `9902407959329` `9902407959336` | `L` / `XL` |
| `9902411076715` `9902411076722` | `S` / `M` |
| `9902388559280` `9902388559297` | `M` / `L` |
| **`9902416416912` `9902416416929`** | **`Gold` / `Silver`** ← 目标 SKU |
| `9902382588781` `9902411418980` | `Wife` / `Daughter` |
| `9902368142464` `9902368142471` | `S·M` / `L·XL` |
| `9902368131628` `9902368131635` | `7.0·cm` / `6.0·cm` |
| `9902367061100` `9902367061117` `9902367061124` | `S` / `M` / `L` |
| `9902348624539` `9902348624522` `9902348624546` `9902348624553` | `M` / `S` / `L` / `XL` |
| `9902348622542` `9902348622535` `9902348622726` | `XL` / `L` / `M` |
| `9902348622450` `9902348622238` `9902348622467` | `M` / `XL` / `L` |
| `9902348621859` `9902348621866` `9902348622108` | `M` / `L` / `XL` |
| `9902363666941` `9902409597604` | `S` / `M` |

**G3 中文字段被英文短词/英文品名占据（9）**

| sku | 当前值 | 判定依据 |
| --- | --- | --- |
| `9902390746609` `9902390747033` | `3·Pack` / `1·Pack` | 包装数 |
| `9902387513665` `9902387513726` | `34·x·50'` / `28·x·44'` | 尺寸 |
| `9902348623181` `9902348623174` `9902348623198` `9902409541669` | `XL` / `L` / `3XL` / `2XL` | 尺码 |
| `9902348622573` `9902348622153` `9902348622160` | `M` / `2XL` / `3XL` | 尺码 |
| （空 SKU） | `FRIYAY - …` | 英文品名残段 |
| （空 SKU） | `Petsound …` | 英文品名残段 |
| `9902380607071` `9902380602298` | `黑色·daisy` / `Color·daisy` | 中文+英文款式混排但无主体品名 |
| `9902427894600` `9902427894815` | `cream·colored` / `黑色` | 同上 |
| `9902409492312` `9902367071710` | `米色·Rainbow` / `Watercolour·Rainbow` | 同上 |

**G4 多 SKU 但英文名只描述单变体（10）**

| sku | 中文品名字段值 | 英文品名（只描述一个变体） |
| --- | --- | --- |
| `9902439062288` `9902348621606` | `白色` / `黑色` | Laptop Stand … -**Large** |
| `9902391031254` `9902391031247` | `白色` / `黑色` | Smart WiFi Touch Switch 2 Gang … -**White** |
| `9902390918969` `9902390918976` | `黑色` / `白色` | Smart Wall Socket With USB & Type C -**Black** |
| `9902348625178` …6 个 SKU | `黑色·XL` 等 6 组 | Front Zipper Sports Bra … -**Bl…** |
| `9902348621729` …5 个 SKU | `棕色` `绿色` `蓝色` `白色` `黑色` | Camping … Cap Hat -**Green** |
| `9902348621521` `9902348624287` | `棕色` / `粉色` | Bible Study Journal Notebook … -**Brown** |
| `9902428296038` `9902428296267` | `棕色` / `黑色` | Luxurious PU Leather Travel … -**Brown** |
| `9902390273754` …4 个 SKU | `黑色` `灰色` `宝蓝色` `藏蓝色` | Plush Ladies … Fleece Gown -**black** |
| `9902409489169` `9902348624492` | `黑色` / `蓝色` | Keyboard Case Compatible With iPad … |
| `9902348623136` `9902387531072` | `黑色` / `深绿色` | Leather Large Laptop Briefcase Bag … -**Black** |

### 2.5 已排除的误报（重要）

**88 条 `sku` 为空的记录，全部 `link_status = 未购买`（100%）**，其中合格选品 68、不建议选品 15、数据待补充 5。这是选品池商品尚未采购的正常状态，**不是数据错误，不计入问题清单**。若按"空 SKU 即异常"机械筛查，会凭空多出 88 条误报。

### 2.6 G5 待人工确认（38 条，抽样）

含汉字但字母占比 > 45%，多为「品牌名/型号 + 中文主体」，多数**可能是合理的业务写法**，不建议批量改。抽样：

| sku | 当前值 | 字母占比 |
| --- | --- | --- |
| `9902348624058` | `6pcs tapo垃圾袋` | 58% |
| `9902591488780` | `Zenty 高压电子` | 50% |
| `9902512417554` | `Zenty防尘袋5件装` | 45% |
| `9902430413706` | `LED灯灯` | 60% |
| `9902407958575` | `USBHDMI集线` | 78% |
| `9902359788893` | `MOVA E20PLUS垃圾袋3个装` | 50% |

其中 `LED灯灯`（语义重复）、`架子tapo支架`、`缠绕支架tapo`、`dock插头` 等属**主体缺失/语义重复**，可优先人工复核；`Zenty 追踪器`、`tapo 垃圾袋` 等品牌+主体写法可保留。

### 2.7 回读证据

- 数据来源文件：`prod_full.json`（`GET /api/products?limit=1000&offset=0`，377 条）
- 分类脚本：`name_audit.py`（纯本地正则分类，无网络）
- 分类结果 ID 清单：`name_audit_ids.json`、`name_groups.json`（仅 `product_id`）
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

### 3.2 本轮报价回传结果

`last_attempt_at`（16:58:55）**确实取得了本轮报价**，不是旧成功：

- `http_status = 200`，`page_title` 正常，`block_reason = null`
- `buyboxPrice = 269.0`，`buyboxSeller = Urban Electronics`（自持），`holdingBuybox = true`
- `myPrice = 269`，`myStock = 11`，`stockStatus = In stock`
- `offers` 共 3 条：自己 2 条（buybox 269 / fastest 269）+ **1 条非本人竞品**（`isMine: false`，`best_price` **268**，`status = backorder`，`bestPricePreorder = true`，配送 6–11 Nov）

### 3.3 结论

**没有卡死，链路正常运转。**

- 采集间隔 5 分钟，`next_due` 17:03:49，是正常的周期轮询窗口；
- `request_once = false`、`web_requested = false` ⇒ **没有待处理的"立即采集"请求**，用户看到的"排队"只是下一轮周期等待，不是卡死；
- 16:39:53 与 16:58:55 两次相隔约 19 分钟均成功，与 5 分钟间隔吻合（中间轮次可能因分段调度未全部记录到本字段）；
- 用户 16:42 截图时的"最近成功 16:41:33"与当前链路无矛盾——**单次截图时刻的排队状态不能证明卡死**，与 Codex 判断一致。

**心跳说明**：`GET /api/collector/status` 仅返回 `{"ready": true, "store": "default-store", "mode": "quote_only"}`，**不包含心跳时间戳**。因此无法提供"采集器心跳时间"这一项，只能以每个 offer 的 `last_attempt_at` 作为回传证据。若需要真正的心跳监控，需后端在该端点补充 `last_seen_at`。

**竞品提示（不影响本次结论）**：该 SKU 有一条竞品 `best_price = 268`（backorder，非现货）。按 `decide()` 的口径，非现货竞品不触发跟价，因此当前维持 269 是正确的。

---

## 4. 红线

- 未修改任何商品数据、未改售价、未开启真实调价、未创建采购。
- 未触发采集：未调用 `POST /api/buybox/page-open`、未调用 `GET /api/collector/products`（不领取租约）。
- 未读取、传输或打印任何密钥 / Cookie。
- 所有数据经 `GET` 只读取得。中文品名的"建议值"仅为供人工确认的参考，**未写入任何字段**。
