# 「证据不足」不是没证据，是缺一个卖家名（2026-10-18）

## 现场

调价页整列 SKU 的决策徽标都是「证据不足」，一个价都不动。用户的原话是
「我要实现调价啊，你不能显示证据不足啊」。

线上实测 `9902416416929`：

```
price 197 | stock 2 | floor 138
rule.plan = {"regime":"blind","target":null,
             "reason":"与Buybox同价但卖家身份未核实，保持现价",
             "sole_seller":true}
snapshot  = {"buyboxPrice":197.0,"buyboxSeller":"Urban Electronics",
             "holdingBuybox":true,"myPrice":197,
             "offers":[{"seller":"Urban Electronics","price":197.0,"isMine":true}, ...]}
```

卖家名有了、`sole_seller` 是 true、本店价和 Buybox 价一致——证据一点都不缺，
是判断逻辑把路堵死了。

## 根因

`plan()` 里那一行：

```python
holding = price_matches and snapshot['holdingBuyboxVerified'] is True
```

`holdingBuyboxVerified` **从来没有被赋过值**（恒为 `None`）。所以只要本店价
恰好等于 Buybox 价，`holding` 必然是 `False`，然后落到 `regime='blind'`。
换句话说：**价格越对，越调不动。**

而 `holdingBuyboxVerified` 之所以一直空着，是因为不敢简单拆掉这道闸。实测反例：
SKU `9902388556722` / `9902388559808` 的 Buybox 卖家是 `ValuezyVAT Registered`，
价格与本店同为 106 / 158。**同价 ≠ 本店持位**——把同价当持位，就会在对手压着
的位置上提价，直接丢 Buybox。

## 解法：用卖家名判持位，三态而不是两态

### 1. 本店卖家名从哪来（`services/seller_identity.py`）

- **手填优先**（`source='manual'`），自动学习永不覆盖；
- 否则从采集快照里**投票学**：只在「Buybox 有卖家名 + Buybox 价 == 本店价 + 有货」
  时计数，按**不同 SKU 数**（不是次数）取众数，要求至少 3 个 SKU 且占比 ≥ 60%。
  用不同 SKU 数是为了防止一个 SKU 反复采集把票刷满。
- `seed_from_monitors()`：启动时从库里已有的成功快照补学一次，不用等下一轮采集。

线上 29 条快照里 `Urban Electronics` 出现在 19 个 SKU 上，`ValuezyVAT Registered`
只有 2 个——投票结果没有悬念。

### 2. 采集器给出三态（`services/pricepilot_collector.py`）

`holdingBuyboxVerified`：`True` 本店持位 / `False` 对手持位 / `None` 不知道本店名。

```python
if my_seller_name and buybox_seller:
    seller_match = _norm_label(buybox_seller) == _norm_label(my_seller_name)
    holding_verified = True if seller_match else False
    is_mine = seller_match
else:
    holding_verified = None          # 没有卖家名可对，退回价格推断
    is_mine = price_matches
```

`isMine` 跟着一起改：逐卡判定改成「有卖家名就按名字判，没名字才按价格判」。
原来纯按价格判，会把对手的卡标成 `isMine=True`，`live_count` 因此虚高。

### 3. `plan()` 只在真的不知道时才停下（`services/automatic_pricing.py`）

| 核实结果 | 同价 | 决策 |
| --- | --- | --- |
| `True` | 是 | 本店持位 → `scarcity`（独家）/ `climb`（守位提价） |
| `False` | 是 | 对手同价压着 → `compete` 抢位让利 |
| `None` | 是 | 只有这一种才 `blind`，文案改成「到设置里填一次卖家名即可自动核实」 |

### 4. 持位推断也认三态（`services/price_ownership.py`）

`estimate(..., verified=)`：只在「同价」时才用卖家身份仲裁。`verified is True`
直接判 `holding=True`，`verified is False` 直接判 `mismatch`，`None` 退回原来的
价格推断。**不同价的情况不需要卖家名**——价格本身已经说明问题，多引入一个
可能学错的信号没有收益。

## 界面

- 调价页徽标 `blind`：「证据不足」→「待核实卖家名」。前者读起来像这条 SKU 废了，
  后者是一个可执行的动作。
- 「本店价 / Buybox」列的持位徽标分两级证据（2026-10-20 补，`UI-OWNERSHIP-01`）：
  `identity_verified === true` 时说「已核实持位」/「不在持位 · 已核实」，证据是页面
  `Sold by` 的卖家名；只有价格推断（`identity_verified` 缺失）时才说「疑似在持位」
  「同价 · 卖家未核实」。本节早先的注释写「后端 `identity_verified` 恒为 False」，
  那是第 4 节上线前的口径——现在 `estimate()` 会在 `verified` 为 True/False 时写
  `identity_verified=True`，界面再按旧的单级口径显示，就会和引擎「按持位提价」的
  结论打架。
- 设置页新增「本店卖家名」卡片：显示当前值、来源（手动指定 / 自动学到）、
  以及采集里出现最多的前 5 个卖家名和各自的 SKU 数——让人能判断学得准不准。
- 接口：`GET/PUT /api/integrations/takealot-seller/identity`。

## 顺手修掉的两处

- `test_collection_maintenance.py` 五个桩还是 2 参数，`_collect_with_retry` 加了
  卖家名后全部失败 → 补 `seller_name=None`。
- `scripts/test-responsive-layout.py` 的 fixture 漏声明新接口，页面会 404 后静默
  降级 → 补进 `DATA`。审计要求「前端调用的每个接口都必须在 fixture 里声明」，
  这条规矩是对的，不该绕过。
- 设置卡片里两个 `<b>` 在 320px 换行后 `getBoundingClientRect` 返回两行并集矩形，
  被判成重叠 65×16px（假阳性）。没有削弱审计，改的是一段里只留一处加粗。

## 验证

- `pytest`：340 passed / 1 skipped（含新增 `test_seller_identity.py` 15 例）。
- `selfcheck.cjs`：25 步全绿。
- 版式审计 99 个面、文字重叠审计 96 个面，0 问题。

## 边界

只读商品页上的公开文字（`Sold by X`、价格、库存）。不碰账号凭据，不做任何
反风控绕过——卖家名是页面上本来就写给买家看的信息。
