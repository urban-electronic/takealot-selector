# 变体切换的「落定延迟」与线上稳定性巡检（2026-10-27）

## 现象

线上 28 条启用监控里，同时有 4 条报 `variant_unmatched`：

```
9902380618800  未能切到变体 Silver
9902416416929  未能切到变体 Silver
9902428508865  未能切到变体 Khaki
9902419581525  未能切到变体 Cats
```

但这 4 条的 `failures` **全是 1**，`last_success_at` 都在几分钟前（03:05–03:08）。
再隔几分钟复查，其中一条自己恢复了，另一条又新失败——是在
「成功 → 失败 → 成功」之间来回跳，不是永久故障。

## 根因

`apply_variant_label()` 点完变体控件后**固定等 700ms** 就查一次选中态：

```python
await el.click()
await page.wait_for_timeout(700)
if await _selected_for_label(page, label):
    return True, "click"
```

Takealot 切变体会重新请求报价再重绘。700ms 时 DOM 常常还没更新，于是：

- 门控判定「没切到」→ `variantSwitchFailed` → 本轮不采信报价（**这个方向是对的**，
  宁可少采一次，也不能拿默认变体的价去调价）；
- 但下一轮页面快一点就成功了，记录在成功与失败之间反复横跳，界面上一条红杠
  一闪一闪，用户以为系统坏了。

诊断数据能证明不是配置问题：`diagnostics.variant_sample` 里页面确实有同名按钮
（`{"tag":"BUTTON","text":"Grey"}`），`variant_controls` 也不是 0。

## 修法

新增 `_wait_selected()`：**判据不变**（还是 `_selected_for_label`），只把「等 700ms
查一次」改成「每 300ms 查一次，最多等 4 秒」。另外点击前先
`scroll_into_view_if_needed()`——元素被吸顶头部盖住时 `click()` 会抛
`ElementClickIntercepted`，原代码在 `except` 里静默跳过，界面上只剩一句
「未能切到变体」，看不出是点不进去还是点了没反应。

**没有**放宽判据。把「点过就算选中」写进去能让失败率归零，但那样切不到变体时
会放行默认变体的报价，等于拿错的价去调价——比多等几秒严重得多。

## 守卫

`backend/tests/test_pricepilot_multi_variant.py` 新增两例：

| 用例 | 钉住什么 |
| --- | --- |
| `test_variant_click_that_settles_late_is_still_confirmed` | 页面 1.5 秒才重绘完，仍要判成功（旧实现 700ms 就判，必红） |
| `test_variant_that_never_settles_is_still_rejected` | 等再久也不生效的，必须照样判失败并拒绝采信报价 |

反向探针实测：把 `_wait_selected` 换回固定 700ms，第一条转红、其余 6 条仍绿，
说明守卫精确锁定了这一个缺陷，不是靠放宽断言凑绿。

## 顺带：稳定性巡检脚本

`scripts/stability-check.py` 把「现在是否正常」固化成可重复执行的检查，
供定时任务使用。它刻意**只判当前状态**，不重跑历史修复的验收（gzip 字节数、
SQL 条数那些属于一次性证据，写在各自的 engineering 记录里）。

检查项：

| 编号 | 判据 |
| --- | --- |
| S1 | `cycle_age_seconds` 不超过间隔的两倍（心跳只证明线程在等，不证明循环在转） |
| S2 | `stuck_jobs == 0`，且采集线程心跳在 |
| S3 | 采集失败率不超过 25% |
| S4 | 卖家同步 3 小时内成功过 |
| S5 | 采集器 `ready=true` |
| S6 | 6 个主接口全 200，单个不超过 12s |
| S7 | 每条失败都有 `error_code`（说不出原因的失败，用户只能反复点重试） |

三条避免「巡检变成噪音」的设计：

1. **只报变化**：失败 SKU 写进状态文件，连续失败的归入「仍未恢复」不再逐条报，
   恢复时也说一声。
2. **24 小时失败史**：间歇性失败会在「失败→成功→失败」之间跳，只比上一轮快照的话，
   同一条商品隔一轮就报一次「新增失败」。24 小时内报过的算已知。
3. **偶发 5xx 重试**：Railway 部署切换时会短暂 502，实测抓到过一次、紧接着连测
   5 次全 200。不重试的话每天误报几次「站点不可达」，报几次就没人看了。

「通过」也不会说成一句干净话：循环正常但有 3 条商品采不到时输出
`巡检 通过（3 条待处理）`，避免用户看到「通过」以为全都好了。

## 一个已知的、故意没做的事

商品页 404（`9902446813330` Zenty SSD，重试 67 次）已停采：`page_title` 是
`404, Page Not found`，商品确实下架了。**没有**把「确定性失败」的退避拉长到
6 小时以上——那会撞上 `collection_maintenance.repair_monitor` 的第 ② 条自愈规则
（`next_due > last_attempt + 6h + 1h` 一律拉回一个间隔），等于每 5 分钟撤销一次，
不改自愈的话改了也没用。
