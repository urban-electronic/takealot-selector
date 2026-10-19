# 采集失败的自愈与多维变体选择（2026-10-18）

## 现场

作业队列条上有 3 条红色徽标，「实际调价」区块整块变灰不可用。逐条查下来是
**三个完全不同的原因**，用一个降级通道解决不了：

| SKU | 当时 error_code | 真实原因 |
| --- | --- | --- |
| 9902348622221 | `price_not_found` | diagnostics 里 `page_title` 是 `Just a moment...`（网站验证页）。旧版本代码把它写成「没找到 Buybox 价」；而且该行 `enabled=False`，`process()` 第一行就 return，**永远不会被重试**，错误徽标一直挂着 |
| 9902391031247 | `collector_unavailable` | 浏览器抛 `RuntimeError`，但库里还有 5 分钟前的有效价 450 |
| 9902415247821 | `variant_unmatched` | 商品名 `... - Nude - L`，旧代码只取最后一段 `L`；页面是颜色 + 尺码两维，停在 `Select an option`，Buybox 卡文本只有 5 个字符，根本没有报价 |

## 三条处理原则

1. **不伪造**：没有报价就不显示报价，采不到就说采不到。
2. **不永久卡死**：任何记录都不能因为一次失败就再也排不上队。
3. **不把可自愈的抖动当故障**：浏览器抖动、网站验证是瞬时的，等下一轮即可。

## 改动

### 1. 多维变体选择（`services/pricepilot_collector.py`）

- `variant_labels_from_name()`：取名称**全部**尾段（`Nude`、`L`），不再只取最后一段。
- `apply_variant_label()`：逐维度应用，三种控件都试——原生 `select` → 标签相同的
  可点控件 → 下拉触发器点开再从弹出列表里选。
- 每个维度都以**选中态**验证；下拉额外认「触发器回显」（选完把值写回
  `Select an option` → `L`）。不用价格变化当证据：选全之前本页压根没有价格。
- **严格口径**：每一个变体段都必须确认选中。少确认一个，页面给的就是默认变体的
  价，拿它调价等于采错价。`diagnostics.variant_unmatched` 会点名是哪一段。
- 去掉了原来的 URL 参数直达路径：多维度下一次只能表达一个维度，且无法逐个验证。

### 2. 记录自愈（`services/collection_maintenance.py`，新增）

每 5 分钟随采集循环跑一次，只改记录、不碰价格：

- **错误码重判**：原结论不是「已定位根因」时，用 diagnostics 重判一次
  （验证页不再显示成「没找到 Buybox 价」）。`price_not_found` 故意不在
  `DECISIVE_CODES` 里——它恰恰是最需要重判的兜底码。
- **next_due 卡死**：超过 `last_attempt + 6h + 1h` 的拉回按间隔排期。
- **停采归一**：已停采却留着 error 的记录改成 `paused`，真实原因存进
  `last_error_code` / `last_error`，信息不丢也不再报红。
- **不自作主张排采**：自愈只改记录层，不设 `request_once`、不改 `enabled`。
  本节初稿曾有一条「过 6h 冷却期就用 `request_once` 安排一次修复性重采」，
  已由 `COLLECT-BOUNDARY-01` 移除——停采是用户的决定，自愈不能自己把采集
  重新打开（配套的 `REPAIR_COOLDOWN_SECONDS` 死常量已在 2026-10-19 清掉）。
  用户显式留下的单次请求照旧保留。

### 3. 同轮内重试（`services/buybox_monitor.py`）

`_collect_with_retry()`：一次没跑成就同轮再试一次（`COLLECT_ATTEMPTS=2`）。
只对可自愈的失败重试：

- 抛异常（浏览器抖动）
- 一个元素都没认出来（落地页还没渲染成商品页）
- Buybox 卡在但卡里几乎没文本（骨架屏）

**不重试** `variant_unmatched`：不是抖动，重试只是白打一次请求。

### 4. 界面口径（`src/utils/buyboxStatus.ts`、`src/pages/AutoPricing.tsx`）

新增 `hasUsableQuote(monitor)`：有没有一份还能用的报价（有正数 `buybox_price`
且有可解析的 `last_success_at`）。据此把「失败」拆成两件事：

| 情况 | 轨道格 | 行状态 | 页头 |
| --- | --- | --- | --- |
| 有报价，这一轮没刷新 | 黄（`is-warn`） | 待刷新 · <原因> | `N 个待刷新`，tone-warning |
| 从没采到过报价 | 红（`is-bad`） | 采集失败 · <原因> | `N 个 SKU 采集失败`，tone-danger |
| 已停采 | 灰（`is-off`） | 已停采 | 不计入需处理 |

「需处理」只算真的没有报价的行——这一轮没刷新的不需要用户动手。
不报红不等于瞒着：原因照样写在行里。

## 验证

部署 `ccd88548` 后在线上逐条触发采集：

| SKU | 结果 |
| --- | --- |
| 9902415247821 | `success`，`variant_applied: ["Nude","L"]`，buybox 138，卡文本 5 → 43 字符 |
| 9902391031247 | `success`，`variant_applied: ["Black"]`，buybox 450，3 个 offer |
| 9902348622221 | `success`（自愈的修复性重采直接采到，`http_status: 200`——验证是瞬时的） |
| 9902390746609 | `success`，`variant_applied: ["3 Pack"]`，buybox 273 |

全量：`status=error` 从 3 条降到 **0 条**，27 条 success。界面
`ok=27 bad=0 warn=0`，页头「27 已采到 · 0 需处理」。

## 测试

- `backend/tests/test_pricepilot_multi_variant.py`（新增 5 例）：双维度全选、
  缺尺码不采信、单维度页满足不了双维度 SKU、副标题尾段保守失败。
- `backend/tests/test_collection_maintenance.py`（新增 14 例）：直接照抄线上
  3 条记录的字段形状；含重试/不重试的对照。
- `scripts/test-buybox-status.cjs`：141 条断言，含「有价待刷新」与
  「无价才报红」的对照。
- `npm run check` 25 步全绿（107s）。

## 边界（不变）

只做选品/市场数据采集；不读取或传输任何凭据；**不做反风控绕过**——
不解验证码、不伪装指纹、不换 IP。网站验证只等它自己解除，解除不了就如实记录。
