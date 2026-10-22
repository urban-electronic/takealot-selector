# 领先差额按价格缩放 + 价格战熔断 + 采配送时效（2026-10-23）

叠在 `codex/pricing-target-reachability`（PR #173）之上：那一版先把「抢不起的低价
不该钉死整条 SKU」修好了，这一版接着修「抢的方式」。

## 为什么动这三刀

盘下来最要紧的三个缺陷，都不改决策骨架，都是「参数/证据」层面的错：

**一、领先差额写死 R1，和价格无关。** R150 的货上 R1 是 0.67%，够用；
R3000 的货上 R1 是 0.03%，对手点一个 R1 就抹平，我们的下一次反应又是一个 R1。
双方各让 R2，位置回到原样，唯一的结果是价格单向下行——这不是竞争，
是双方一起替对方做价格发现。

**二、连续跟降没有任何刹车。** 引擎每一轮都独立求「这一轮的最优价」，
从不看「我上一轮已经降了、位置还是没拿到」。对手陪着降的时候，
单轮最优解就是继续降，一直降到利润底价为止，而位置还在对方手里。
让利换不来持位的时候，继续让是单向亏损。

**三、`leadtimeDays` 三处全是 `None`。** 采集器把卡片文案读进来了
（`deliveryLabel`），但没有解析成天数。于是「时效位」和「价格位」在引擎眼里
完全一样——它在「最快配送」卡位上靠降价去抢一个由配送速度决定的展示位。

## 改动

### 1. 领先差额按价格缩放（`services/automatic_pricing.py`）

```python
DEFAULT_LEAD_PCT = 0.5     # 差额占现价的比例
LEAD_CAP = 50              # 绝对上限，缩放不等于大额让利

def _lead_amount(rule, current):
    base = rule.get('lead_amount') or 1          # 人工填的更大值仍是下限
    pct = rule.get('lead_pct', DEFAULT_LEAD_PCT) # 填 0 = 退回旧的永远 R1
    return max(base, min(round(current * pct / 100), LEAD_CAP))
```

R150 → R1，R1000 → R5，R3000 → R15，R20000 以上封在 R50。
差额的作用是给对手设一道**跟价门槛**：想抢回去必须先让出成比例的钱。

三处调用统一走它：`_winnable()`、守位提价（`climb`）、抢位降价（`compete`）。
顺带把「算差额」从三处收敛成一处——原来是 `_winnable()` 里算一遍、
调用方再算一遍，两边各自读 `rule['lead_amount']`，改一个漏一个就会不一致。

`_winnable()` 的返回值也跟着改了：从「能抢的池」变成 `(leader, target)`。
旧写法把「抢不起」和「没有竞品」压成同一个空列表，于是两种完全不同的处境
共用一句话。现在 `target` 可能低于底价，由调用方用 `_unreachable_reason()`
说清是哪种：

- 领先差额把价格压过了底价 → 该调差额或底价，不是等行情；
- 现货最低价本身就在底价之下 → 这个位置确实抢不起；
- 池里没有现货竞品 → 没有对手可抢。

### 2. 价格战熔断

跟降之后有两种结果：拿到位置（说明让利有效），或者位置还在别人手里。
后者连续发生就是价格战。规则状态里加三个字段：

| 字段 | 含义 |
| --- | --- |
| `drop_streak` | 连续跟降次数 |
| `drop_streak_from` | 这段连降的起点价，用来算累计让利 |
| `war_until` | 熔断截止时间 |
| `war_count` | 历史上熔断过几次（观察用） |

两个条件任一满足即熔断，默认停手 6 小时：

```python
if streak >= war_drop_streak (4) or (streak >= 2 and drop_pct >= war_drop_total_pct (8)):
```

- 次数条件拦「对手每轮都跟」；
- 比例条件拦「次数少但每刀都深」。

计数在 `process()` 里维护，**不在 `plan()` 里**——`plan()` 是纯函数，
决策不该顺手改状态：

- 改价成功且方向向下 → `drop_streak += 1`，`drop_streak_from` 记第一次的价；
- 改价成功且方向向上 → 归零；
- 采集到 `holdingBuyboxVerified is True` → 归零（跟降有效，位置拿到了）；
- `war_until` 到期 → 归零并 `war_count += 1`。

最后一条容易漏：到期不清零的话，下一轮 `_war()` 立刻再次触发，
规则会永远停在「熔断中」——看着像保护，实际是把这条 SKU 冻死。

熔断只停「跟降」这一个动作。**持位提价和缺货试涨照常**，那两种不是在打价格战。
所以判断放在抢位分支之前、守位与试涨分支之后。

### 3. 采配送时效（`services/pricepilot_collector.py`）

```python
parse_leadtime('Get it today')              # 0
parse_leadtime('Get it tomorrow')           # 1
parse_leadtime('Delivery in 2-4 days')      # 4  ← 区间取上界
parse_leadtime('Ships in 3 - 5 working days')  # 5
parse_leadtime('Delivery 23 Oct - 28 Oct')  # 到 28 Oct 的天数
parse_leadtime('In stock')                  # None ← 读不出来就不猜
```

区间一律取上界：宁可认为对手比实际慢，也不要以为自己已经比对手快。
日期跨年（12 月看到「1 月 4 日」）按明年算。

三处 `leadtimeDays: None` 换成真值，另外新增 `buyboxLeadtime`（Buybox 主卡的时效，
同价多卡时取最短的那张）。决策卡片上多两个数：**对手时效**和**领先差额**。

## 验证

- `backend/tests/test_automatic_pricing.py`：熔断 6 例（未触发/次数触发/比例触发/
  只停降不停涨/端到端落库/到期重开），加连降计数落库。
- `backend/tests/test_pricing_guardrails.py`：新增 `test_lead_amount_scales_with_price`。
  两个旧断言随行为一起改（`1199 → 1190`、`909 → 905`），都是「固定 R1」的直写值。
- `backend/tests/test_delivery_leadtime.py`：13 个解析用例，含跨年。
- 后端全量 `pytest`：572 passed / 1 skipped。

## 没做的，和为什么

**`leadtimeDays` 目前只采集和展示，不参与决策。** 要拿它做判断，还缺一个数：
**本店自己的配送时效**。卖家接口返回的 `offer` 里没有这一项，卡片上也没有
（持位时 Buybox 卡显示的是本店的承诺，但拿不到「我们承诺几天」这个字段）。
缺了「我方时效」就无法回答「这个时效位我到底抢不抢得到」，硬写一个推断规则
只会变成新的误判。先让数据攒起来。

**顺带发现一个更大的问题，但没动：** `takealot_buybox` 策略按
`fastest` → `best_price` 的顺序找可降价的卡位。可 Buybox 归属在采集器里是
**现货卡中最低价**（`buybox = min(in_stock_prices)`），不是「最快」。
所以当 Fastest 卡 R500、Best Price 卡（现货）R450 时，Buybox 属于 R450，
把价格降到 R499 去压 Fastest 卡，一分钱都换不来位置。

要改它就得回答一个业务问题：**是守着「最快」这张卡卖高价，还是压到最低价去抢
Buybox？** 这是毛利和转化之间的取舍，不是代码对错，所以留给人决定。

**还没解决的（上一轮列的）**：没有竞品价格时间序列（分不出限时券和永久降价）、
没有转化闭环（引擎只会贴最低价）、没有对手反应模型、冷却与日限额仍是全局常数。
