# 调价列表逐条查档案：一次轮询 339 条 SQL（2026-10-27）

## 现象

调价页（`/auto-pricing`）每 30 秒自动刷新一次列表（`useVisibleInterval(…, 30000)`），
每次请求 `GET /api/automatic-pricing`。本地库 329 个商品，单次请求打了 **339 条 SQL**，
其中 **329 条是同一条语句**：

```
SELECT … FROM system_settings WHERE system_settings."key" = ? LIMIT ? OFFSET ?
```

参数是 `operations_profile:default-store:<商品id>`——一个商品一条。

## 证据

用 SQLAlchemy 的 `before_cursor_execute` 钩子统计（本地库副本，329 商品）：

| 指标 | 修复前 |
| --- | --- |
| 单次请求 SQL 条数 | 339（其中按商品取档案 329、前缀查询 4） |
| 其中 SQL 执行耗时 | 13.1ms |
| 单次请求总耗时 | 56.3ms |

查询数正好等于商品数，说明它随数据量线性增长：商品涨到 3000 个，这个接口每 30 秒
就是 3000 条查询，页面越用越慢，且和用户是否在看这一行无关。

## 根因

`api/pricing_routes.py` 的 `listing()` 在商品循环里逐条查档案：

```python
for p in db.query(Product).filter(...).all():
    setting = db.query(SystemSettings).filter(SystemSettings.key == f'operations_profile:{store}:{p.id}').first()
    profile = json.loads(setting.value) if setting else {}
```

同一个文件里，规则、分组、监控三份数据早就是「前缀查一次 + 字典查」的写法，只有档案
这一份漏在循环里。`services/buybox_monitor.py` 里也已有正确写法（`profiles={r.key: …}`）。

## 改法

| 位置 | 改动 |
| --- | --- |
| `api/pricing_routes.py` | 循环前一次前缀查询取全店档案，`profiles.get(p.id, {})` 取用；前缀走常量 |
| `api/operations_routes.py` | 新增 `PROFILE_PREFIX` 常量，`key()` / `profiles()` 改用它。前缀字符串原来在两个 API 文件里各写一份，改一边另一边会静默读不到档案（列表变成没有报价、整页空行） |
| `tests/test_pricing_routes.py` | 新增守卫：3 个商品与 60 个商品各跑一次，断言 SQL 条数不增长 |

常量只覆盖了两个 API 文件；`settings_routes` 的前缀白名单和
`automatic_pricing` / `buybox_monitor` / `seller_operations_sync` 里按商品取档案那几处
仍是字面量——它们不在本次缺陷的路径上，改动会扩大范围，已在常量处注明。

## 验证

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| SQL 条数 | 339 | **11** |
| 单次请求总耗时 | 56.3ms | **15.8ms** |
| 响应体大小 | 491 B | 491 B（一致） |

- **输出等价**：用一份带档案的库（5 个商品，含一条只有 `offer_ids` 的老数据、一条带规则和
  分组）分别跑旧实现和新实现，`listing()` 返回的 JSON 完全一致（5 行，`p0` 仍走
  `offer_ids` 回退分支，price 120 / stock 7）。
- **反向探针**：把循环里的逐条查询改回去，新守卫立刻转红——
  `SQL 条数随商品数增长：3 个商品 12 条，60 个商品 69 条`。恢复后 15 例通过。
- 调价相关套件 57 例通过（`test_pricing_routes` / `test_buybox_monitor` / `test_collector_bridge`）。

## 同类的下一处

补货页的两个接口是同一个病，但走的是另一个 helper（`seller_operations_sync.eligible()`
逐商品查采购记录）：

| 接口 | 修复前 SQL 条数 | 单次耗时 |
| --- | --- | --- |
| `GET /api/operations/overview` | 318 | 66.1ms |
| `GET /api/operations/alerts` | 317 | 63.5ms |

已按「一个 PR 一个缺陷」单独处理，见 [补货列表逐商品查资格](2026-10-27-eligible-n1.md)
（318 → 6、317 → 5）。
