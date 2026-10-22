# 产品标签 PDF（EAN-13）：两份生成器、同一套版式

日期：2026-10-22
基线：`d4101cabf3`（功能由并行提交带入，本文件补上它缺失的设计说明；同日又加了一份
浏览器内生成器 `src/utils/labelPdf.ts`，见下）

## 为什么要两份生成器

后端 `POST /api/products/labels` 出标签（`backend/services/label_pdf.py`），
前端又在 `src/utils/labelPdf.ts` 里零依赖手写了一份 PDF + ZIP。原因不是重复劳动：
Railway 部署长期停在 `8f3af26cc3db`，标签是「打印前必须拿到手」的东西，不该被后端
部署卡住，所以网页上的「下载标签」直接走浏览器内生成。后端接口保留给 API 调用方与
A4 拼版（浏览器版只出单个 40×30 的 PDF/zip）。

代价是同一套版式有两份实现，**参数必须逐字对齐**（两边都从官方 product_labels.pdf
量出来）：Helvetica 8pt、产品名 3 行在上、EAN-13 横排、条宽 0.33mm、条高 7.6mm、
13 位数字按 1-6-6 分组、条码左侧 M / P 标记、整块内容 34.3 × 21.8mm。改一边不改另一边，
同一个 SKU 在网页和接口里会印出两张不一样的标签。

## 版式常量（单位 pt，原点在内容左下角）

| 常量 | 值 | 说明 |
| --- | --- | --- |
| `CONTENT_W` / `CONTENT_H` | 97.24 / 61.90 | 整块内容 34.3 × 21.8mm |
| `NAME_SIZE` | 8.0 | 全篇唯一字号 |
| `NAME_BASELINES` | 53.3 / 45.3 / 37.3 | 产品名 3 行，从上到下 |
| `NAME_MAX_WIDTH` | `CONTENT_W + 0.5` = 97.74 | 折行宽度（见 LABEL-WRAP-02） |
| `BAR_X` / `BAR_TOP` | 8.42 / 31.64 | 条码左边界 / 顶部（数据条与护线顶对齐） |
| `BAR_WIDTH` | 0.935 | 0.33mm，EAN-13 标准条宽（95 模块铺满 88.825） |
| `BAR_HEIGHT` | 21.581 | 7.61mm 数据条高 |
| `GUARD_HEIGHT` | 31.181 | 护线高，比数据条长 9.6pt（见 LABEL-GUARD-01） |
| `DIGITS_BASELINE` | 2.37 | 13 位数字基线 |
| `MARK_M/P_BASELINE` | 23.13 / 14.63 | 条码左侧 M / P |
| `DIGIT_X` | 0.93 / 17.52 / 61.49 | 1-6-6 分组数字的起点 |

上表是**逐条量官方 PDF 的原始绘图指令**得到的（不是按截图估的），改动任何一项都要重跑
`label_probe/compare_same_sku.py` 那种逐条比对，否则「看起来差不多」会悄悄退化成「不一样」。

尺寸取舍写在 `barcode_width_mm()` 与对应测试里：40×30 能 1:1 放下官方内容，条宽保持
0.33mm；30×40 竖版要缩到 0.78 倍，条宽跌到 0.258mm，**低于 EAN-13 的 0.264mm 下限**。
所以默认 40×30，30×40 只留给「一定要竖版」的人。

`grid_shape()` 里的 `+0.5` 容差不是随手写的：40mm × 5 正好等于可用宽度，浮点除法会
算成 4.9998，不加容差会掉成 4 列。

## 条码不合法的 SKU 一律不出

`is_ean13()` 要求 13 位、全数字、校验位正确。印一张扫不出来的标签是废纸，所以跳过并
在响应头里报数（`X-Label-Count` / `X-Label-Skipped`）；前端版本把跳过的 SKU 与原因
放进 `LabelBundle.skipped`。

多规格产品的 `products.sku` 是「空格/逗号拼起来的一整串」，`split_skus()` 先拆开再逐个
校验；列表页勾选拿到的是**单个 SKU**，所以接口先用 `like` 粗筛再按拆分结果精确比对
（整串精确 `in_` 匹配永远匹配不上，那是这个功能刚上线时点下载直接 404 的原因）。

## 三处修正（LABEL-ERR-01 / LABEL-SKIP-01 / LABEL-WRAP-01）

### LABEL-ERR-01 生成失败不再整页替换

详情页 `error` 是「加载失败」状态，渲染时整页被换成一行红字（`if (error) return …`）。
下载标签的失败分支原本写 `setError(...)`，于是生成失败之后，用户正在编辑的整张表单连同
草稿一起消失——与 DETAIL-SAVE-ERR-01 同一类错，只是换了入口。改用独立的 `labelError`，
渲染成页面顶部一条行内提示：失败可重试，草稿不动。

### LABEL-SKIP-01 被跳过的 SKU 要说出来，成功不许报成错误

勾 5 个 SKU、其中 2 个不是合法 EAN-13 时只会出 3 张标签。第一版界面一个字都不说，
用户以为标签丢了；补上提示后又被写进 `setError`——那是页面顶部「加载失败」的红色告警，
把一次成功的下载报成错误。现在走独立的 `labelNote`（信息条）：
「已生成 3 张标签；另有 2 个 SKU 不是合法 EAN-13，已跳过」。一个都不合法才走错误提示。

### LABEL-WRAP-01 只在真丢词时加省略号

`wrapName()`（前端）与 `_wrap()`（后端）原来的判据是「凑满 3 行就在末行补 `...`」，
于是「正好折成 3 行、第三行又刚好填满」的产品名会被截掉最后一个词——名字本来完整，
印出来却带省略号。40×30 的标签内容宽只有 34.3mm、8pt，长名字很容易正好占满 3 行。
两边都改成用 `dropped` 标记区分「被截断」与「刚好填满」。

## 并行重写丢掉的三样东西（LABEL-WRAP-GUARD-01 / LABEL-COUNT-01 / LABEL-404-01）

2026-10-22 晚的并行提交把后端标签测试改成用 `tests/pdfmeta.py` 量页面尺寸（去掉
pymupdf 运行依赖，这一步是对的），但整文件重写时漏掉了两条折行用例；同一轮里前端
按钮的数量口径与 404 归因也没跟上。三处都在下面补回。

### LABEL-WRAP-GUARD-01 折行用例被整文件重写冲掉

`test_wrap_keeps_full_last_line` / `test_wrap_marks_dropped_words` 在重写后消失，
`_wrap` 也不再被 import——实现里的 `dropped` 判据于是没有后端用例守着（前端
`test-label-pdf.cjs` 那条「7 个词放得下，不许丢词」只覆盖 `wrapName()`）。原样补回，
断言一字未改。

### LABEL-COUNT-01 列表页按钮的数字改成「真正能出标签的数量」

按钮原来写 `selectedSkus.size`（勾选总数，含不合法的 SKU），而 `utils/ean.ts` 的头注释
明说「前端先筛一遍是为了按钮上能显示真实数量」；详情页的 `labelCount` 一直是合法数。
勾 3 个只有 1 个合法时，按钮写「(3)」、实际下到 1 张，看着像文件丢了。改成
`Array.from(selectedSkus).filter(isEan13).length`，一个都不合法时禁用并在 title 里说明。

### LABEL-404-01 「产品查不到」不再报成「产品没有 SKU」

`download_labels` 在 `products` 为空时一律回「选中的产品都没有 SKU，生成不了条码」。
可列表页的勾选是持久化在 localStorage 里的，产品被删掉之后再点下载走的就是这条路：
提示让人去补 SKU，实际该做的是刷新列表重新勾。拆成 404「没有找到对应的产品（可能已被
删除，或不属于当前店铺）」与 400「选中的产品没有可用的 SKU（SKU 为空或已被修改）」；
顺带修掉 `items` 为空时 400 文案拼出空括号（「没有可生成的标签（）」）的问题。

## 和官方下载文件「裁剪后一模一样」（LABEL-GUARD-01 / LABEL-WRAP-02 / LABEL-BASE-01）

要求原话是「产品标签确认是要跟原下载文件裁剪后一模一样的，你要确保这些」。
「确保」不能靠肉眼，做法是把官方 `product_labels.pdf` 的**原始绘图指令**（pymupdf
`page.get_texttrace()` + `page.get_drawings()`）全部 dump 出来，再拿同一个 SKU、同一个
产品名逐条比：**文字 17 段、条码矩形 30 个，x/y/宽/高全部一致**（残差 ≤0.04pt，是官方
自己写 PDF 时的舍入）。三个差异是这样找出来的：

### LABEL-GUARD-01 护线比数据条长 9.6pt

`createBarcodeDrawing('EAN13', …)` 出来的条码是**等高**的，而官方版式的六根护线
（起始 `101`、中间 `01010`、终止 `101`）比数据条长 9.6pt（31.181 vs 21.581），一直伸到
13 位数字那一行。少了它，标签整体会矮一截，视觉上一眼能看出「这不是官方的」。
现在两边都自己编码（`ean13Modules()` / `ean13_modules()`）再画矩形，护线模块下标写死在
`GUARD_MODULES = {0, 2, 46, 48, 92, 94}`（95 个模块里的位置）。**合并连续黑模块时护线与
数据条不能合并**——高度不同，合并了就等于把护线砍成数据条高。

### LABEL-WRAP-02 折行宽度是 97.74，不是 88.82

折行宽度原来写 `CONTENT_W - BAR_X`（88.82，即条码左边界到右边的宽度），官方第 2 行
`Timer Energy Metering DIN` 实测 **97.35pt**，比内容宽 97.24 还多 0.11 也照样排在同一行。
用 88.82 会提前折行，`DIN` 被挤到第三行，三行的断词位置就跟官方不一样。
改成 `CONTENT_W + 0.5`（0.5pt 容差）后 17 段文字 0 差异。
`test-label-ean.cjs` 里两边的 `NAME_MAX_WIDTH` 都钉了「必须是 `CONTENT_W + 0.5`」。

### LABEL-BASE-01 文字基线整体低 0.4pt

产品名基线官方是 53.3 / 45.3 / 37.3，原来写的 52.9 / 44.9 / 36.9 整体低 0.4pt；
M / P 与 13 位数字同理（23.13 / 14.63 / 2.37）。单看一张看不出来，叠在官方图上就是
整块文字往下压了一点。

### 像素比对 7% 差异是假的，别拿它当「不一样」

按 300dpi 渲染两块内容做像素差，平均差 6.70/255、差异像素 7%，看着像完全没对上。
真相是渲染器 clip 的亚像素相位 + 抗锯齿：`get_pixmap(clip=…)` 会把裁剪框取整，
再叠上 ±1px 的相位差。做 ±2px 偏移搜索，最佳 Δ=(1,0)。**判断标准只能是矢量级的
坐标/宽度**，像素差只用来发现「大块缺失」（比如少了护线）。

### 扫码余量

`label_probe/pixel_check.py` 在 203 / 300 / 400 / 600 / 1200 dpi 下逐个解码，全部读对。
之前为了「避免 300/400dpi 出灰边」把条码起点对齐到 0.5pt 网格，那会把整块条码平移
0.08pt（官方 x 就是 8.42），与「一模一样」冲突；实测五种 dpi 都不需要这个取整，已去掉。

## 文件名 = SKU + 中文简写（LABEL-FILENAME-01）

打印软件和资源管理器里，`9902348623136.pdf` 这种名字**光看分不出是哪个品**——用户要求
「标签的名字就叫 SKU 加中文合理简写，便于区分」。现在一个 SKU 一个文件，形如
`9902348623136_皮革黑色包.pdf`：

- 中文简写取自 `chinese_product_name`，与 SKU 一一对应（多规格按 `variantLabels()` 切）；
- **单 SKU 时整串都是它的中文名**，不能走 `variantLabels()`——那个按空格切，
  `6pcs tapo垃圾袋` 会被拆成两个、长度对不上就退化成空（这坑踩过）；
- 规格名占位值（`主商品` / `规格待识别` / `规格待确认` / `历史分支`）不进文件名，
  那是「没对上真实颜色尺码」的标记，写进文件名反而误导；
- 中文不进 PDF 内容流（只进文件名），PDF 仍是纯 ASCII，xref 偏移不受影响；
- zip 条目的通用标志位 bit 11 已置位，文件名按 UTF-8 存，解压不乱码。

## 代理店才有参考价，自营店没有（AGENCY-STORE-01）

自营是我们自己从 Takealot 选品、1688 采购，**没有官方售价**这套概念；多市场参考价
（新加坡官网价 / 国内原价 / 淘宝到手价）只对代理品（CK 这类）有意义。判断口径按用户
口述：**店名里写「代理」的才是代理店**（如「代理CK店铺」），没写的是自营。

`src/utils/agencyStore.ts` 只做一件事：`isAgencyStoreName()` → `/代理/.test(name)`。
`ProductList` 与 `ProductDetail` 各自读一次当前店铺，自营时隐藏：

- 列表页「参考价（R）」列、「参考价」按钮、批量录入面板、列设置里的勾选项
- 详情页「多市场参考价」卡片

**店铺列表读不到时按代理处理**（保持原界面），宁可多显示也不要误伤已有功能。
`test-responsive-layout.py` 的 fixture 店名因此改成「代理CK店铺」——否则参考价整块被
收起，审计找不到「参考价」按钮会超时。

## 两份生成器的一致性由测试守着（LABEL-PARITY-01）

「参数必须逐字对齐」原来只是一句话。两份实现分别在 `src/` 和 `backend/`，谁改了其中
一份都不会有东西报错——表现只是「打印出来对不齐 / 网页和接口出的标签不一样」，
而这两件事都不会在自检里显形。现在 `scripts/test-label-ean.cjs` 直接把两份源码里的
版式常量读出来逐项比数值：`CONTENT_W/H`、`NAME_SIZE`、`NAME_BASELINES`、
`DIGITS_BASELINE`、`BAR_X`、`BAR_TOP`、`BAR_WIDTH`、`BAR_HEIGHT`、`GUARD_HEIGHT`、
`MARK_X`、`MARK_M/P_BASELINE`、`DIGIT_X`、`PAD`，以及 `LABEL_SIZES` 与默认尺寸。
常量对齐了但调用处各换一个数同样会印错，所以两边的折行调用
（`NAME_SIZE, NAME_MAX_WIDTH, 3 行`）与缩放公式
（`(尺寸 - 2*PAD) / CONTENT_W`）也一起钉住。

**已知且故意的差异：字符集。** 后端 `_latin1()` 保留 Latin-1 重音字符（`Café` 印成
`Café`），前端 `asciiOnly()` 把它们换成 `?`（`Caf?`）。不是漏改：前端这份是零依赖手写
PDF，xref 偏移靠「字符数 == 字节数」算，而 `TextEncoder` 会把 `é` 编成两个字节，整份
文件的偏移全错。要跟前端对齐，得把字节写入换成 latin1 编码器，属于另一件事。别为了
「看起来一致」把 `asciiOnly()` 改成保留重音——`test-label-pdf.cjs` 有断言挡着。

## 列表页下载标签时产品名不能只看当前筛选（LABEL-NAME-01）

勾选存在 localStorage 里、不随筛选变化，而 `products` 只是**当前筛选结果**。下载标签时
按 SKU 反查产品名原来是在 `products` 里现扫：勾 3 个、再按关键词筛出 1 个，剩下 2 个
查不到名字，`buildLabelBundle()` 就退回用 SKU 当产品名——印出来是
`CK2-90151648-1` 而不是商品名，而且看不出哪里错。改成把见过的 SKU→品名累积在
`labelNames` ref 里（新值覆盖、空名不覆盖），筛选只影响展示，不影响已见过的名字。

同一处提示条补上 `role="status"`：同页另外三条信息条（任务提示、批量录入提示、
刷新详情）都有，只有「已生成 3 张、跳过 2 个」这条没有，读屏用户点完下载听不到任何反馈。

## 验证

- `backend/tests/test_label_pdf.py` 25 例（含 2 例折行边界）、`test_label_routes.py` 10 例
- `scripts/test-label-pdf.cjs`：前端生成器（EAN 编码结构、页面尺寸、copies、跳过、
  确定性、折行不丢词、**六根护线 31.181 / 数据条 21.581 / 三行基线 53.3·45.3·37.3 /
  M·P 23.13·14.63 / 数字 2.37**、文件名带中文简写与占位值回退、zip 条目 UTF-8 标志位）
  —— 135 条检查
- `scripts/test-label-ean.cjs`：三份 EAN 实现的判据一致 + 详情页/列表页的错误与跳过口径
  + 列表页按钮数量按合法 EAN-13 计 + 两份生成器的版式常量逐项对齐（含 `BAR_TOP` /
  `GUARD_HEIGHT` / `NAME_MAX_WIDTH`）+ 产品名走累积缓存 —— 101 项检查
- 与官方文件的逐条比对：`label_probe/compare_same_sku.py`（文字 17 段、条码 30 个矩形，
  残差 ≤0.04pt）、`label_probe/compare_frontend.py`（前端 PDF + zip 文件名）、
  `label_probe/pixel_check.py`（203/300/400/600/1200 dpi 解码）
- 反向探针：折行改回「凑满 3 行就截」→ `test_wrap_keeps_full_last_line`（pytest）与
  `test-label-pdf.cjs`「7 个词放得下，不许丢词」双红；详情页改回 `setError`、
  列表页跳过改回 `setError` → `test-label-ean.cjs` 各自转红；按钮数字改回
  `selectedSkus.size` → `test-label-ean.cjs` 转红；404 文案改回「都没有 SKU」→
  `test_missing_product_is_not_reported_as_missing_sku` 转红；
  `labelPdf.ts` 的 `BAR_WIDTH` 改成 0.93 → 「BAR_WIDTH 两份生成器不一致」转红；
  产品名改回现扫 `products` → 「产品名要从 labelNames.current 取」转红；
  去掉 `role="status"` → 对应断言转红；护线合并进数据条 → 「护线必须正好 6 根」转红；
  折行宽度改回 `CONTENT_W - BAR_X` → 「NAME_MAX_WIDTH 必须是 CONTENT_W + 0.5」转红
  （均已还原）
