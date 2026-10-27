# 补货侧栏加「采购货源」（2026-10-27）

## 要解决的问题

补货计划页看到某商品要补 N 件，下一步就是去 1688 找货、下单。原来这条路是断的：

- 侧栏（`商品计划设置`）只有计划参数，没有货源信息；
- 采购链接存在 `products.purchase_url` 里，只能在产品详情页看和改；
- 没存过链接的商品，运营得自己复制中文品名去 1688 搜。

全库 379 条商品里 375 条已经存了 1688 链接（`https://detail.1688.com/offer/...`），
但补货时看不到——这才是真正的浪费：数据早就有，只是不在需要它的地方。

## 做了什么

侧栏顶部新增一块「采购货源」（`src/operations/SourcingPanel.tsx`），排在计划参数之前：

1. **打开采购链接**：`purchase_url` 有值就显示成主按钮，标题带平台名（`打开采购链接 · 1688`）。
2. **搜索关键词**：默认取中文品名，可改。
3. **平台找货**：1688 / 淘宝 / 阿里巴巴国际站 / 拼多多 四个搜索链接，新标签打开。
4. **保存采购链接**：就地粘贴、保存、清除，不用跳详情页。

链接规则全部抽在 `src/operations/sourcing.ts`（纯函数，可测）：

| 输入 | 关键词 | 说明 |
| --- | --- | --- |
| `chinese_product_name = '温度控制器'` | 温度控制器 | 单行中文名才是"商品叫什么" |
| `chinese_product_name = '棕色\n卡其色\n浅蓝色'` | **空** | 多行是变体标签，见下 |
| 中文名空、有 `product_name` | 英文标题 | 界面提示"英文在 1688 上搜不准" |
| 都没有、有 `sku` | SKU | 最后兜底 |
| 全空 | 空 | 不生成任何平台按钮 |

## 两个判断，值得单独说

### 1. 多分支商品不给默认关键词

`chinese_product_name` 在多分支商品里是**变体标签**（`棕色\n卡其色\n浅蓝色`），
不是品名——这个口径在 VAR-SETTLE-01 排查时已经确认过（变体名来自分支标签，不是商品名后缀）。

所以拿第一行"棕色"去 1688 搜，会返回一堆绿植和服饰。这比"搜不到"更糟：用户会以为
自己搜到了候选货源。**宁可留空 + 明确提示"这个商品只有变体标签，先填完整品名"，
也不要给一个看起来能用、实际一定搜错的词。** 48 条商品属于这一类。

关键词为空时四个平台按钮不渲染（`sourcingLinks('')` 返回空数组），
用户填上品名后按钮出现。

### 2. 保存链接会顺手清空人工成本覆盖值（差点踩到）

`PATCH /api/products/{id}` 里有一条分支：

```python
metadata_fields = {'sku', 'chinese_product_name', ..., 'market_prices'}
if not set(update_data).issubset(metadata_fields):
    for mf in manual_fields:
        setattr(p, mf, None)      # 清空全部 manual_*
    _apply_calculated_fields(db, p)
```

`purchase_url` 原本**不在**白名单里。侧栏的"保存链接"只发 `purchase_url` 一个字段，
于是每保存一次链接，就会把运营手工填的成本覆盖值（`manual_total_cost_zar` 等）全部抹掉
再重算——而界面上价格还是那个价格，只有利润换成了重算值，几乎不可能被发现。

修法：把 `purchase_url` 加进 `metadata_fields`（它是纯链接，本来就不参与成本计算）。

守卫：`backend/tests/test_metadata_updates.py` 按**白名单逐字段**验一遍
（不是只验 `purchase_url`），断言 `manual_total_cost_zar` / `total_cost_zar` /
`profit_zar` / `actual_sale_price_zar` / `purchase_cost_cny` 都不变，外加"清空链接"一例。
反向探针：把 `purchase_url` 从白名单删掉 → 正好这 2 例转红，其余 7 例仍绿。

## 界面细节

- 采购链接是又长又不换行的 URL。第一版把 URL 原样放在按钮旁边，
  `.sourcing-panel` 的 grid 列宽是 `auto`，被这一串顶开，整张卡横向溢出到侧栏外——
  **是截图发现的，不是想出来的**。修法是 `grid-template-columns: minmax(0, 1fr)`
  + 子项 `min-width: 0`，并把重复的 URL 行去掉（下面的输入框已经显示同一个值）。
  验收时用 `panel.scrollWidth === panel.clientWidth` 判有没有溢出。
- 只用 `ui-tokens.css` 里的令牌（字号 ≥11px、控件高度 ≥24px、文字色都带实测对比度），
  `check-visual-consistency.cjs` 16 组约束全过。
- `<a>` 套 `.btn` 会带下划线，`.sourcing-link` 补 `text-decoration: none` 与 `min-height`。

## 守卫清单

| 守卫 | 钉住什么 |
| --- | --- |
| `scripts/test-sourcing-links.cjs` | 平台清单（删一个就红）、关键词来源优先级、多分支不给关键词、空白折叠、空关键词不生成链接、`javascript:`/`data:`/裸域名一律拒、平台名识别、四个平台域名不串台 |
| `backend/tests/test_metadata_updates.py` | 只改元数据字段不得动人工成本与重算结果 |
| 截图验收（临时脚本，未入库） | 卡片不横向溢出；已存链接 / 只有变体标签 两种状态的实际排版 |

反向探针各做过一次：去掉 `encodeURIComponent` → "关键词没有编码"转红；
去掉 protocol 校验 → "必须拒绝 javascript:" 转红。

## 故意没做的事

- **不做自动搜索/比价**：只拼搜索 URL，不抓 1688 结果。抓第三方平台列表页要处理
  登录、验证码、反爬，属于明确不碰的范围（见 AGENTS.md 红线）。
- **不在侧栏改中文品名**：品名会影响变体匹配与装箱单，改它属于产品资料编辑，
  仍留在产品详情页。
- **不自动判断"哪个 1688 链接才是这个 SKU"**：一个商品可能对应多个货源，
  现在一个商品存一个链接，是既定口径；要多货源得先定数据结构。
