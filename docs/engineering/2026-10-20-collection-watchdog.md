# 采集看门狗：卡死的任务不能冻住整条循环（2026-10-20）

## 事故现场

07:31(GMT+8) 起，线上采集全停。用户看到的是「刷新售价 → 本店报价暂时无法读取」，
调价页写着「采集循环运行中」，但价格和报价都停在原地。

接口读数（`GET /api/automatic-pricing` → `loop`）：

```json
{"worker_alive": true, "cycles": 1995,
 "last_cycle_at": "2026-10-19T23:31:23Z",
 "due_now": 27, "running": 26, "success": 1, "error": 1}
```

`worker_alive: true` 但 `last_cycle_at` 停在 17 分钟前。这两个数放在一起就是
矛盾——而界面只看了前一个。

## 根因：等待期间还在刷新心跳

`buybox_monitor.run()` 用两个 worker 跑采集，等结果走的是
`_wait_with_heartbeat()`：

```python
while True:
    done, remaining = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
    ... value['alive_at'] = time.time()   # 每 10 秒刷一次心跳
    if done: return done, remaining       # 没有 done 就永远转下去
```

一个任务卡在浏览器里（`quote_context` 里 `_lock` 被它占着、页面调用不返回），
future 永远不完成，这个 while 就永远不退出。更糟的是它在**等待期间**刷新
`alive_at`，于是：

- `loop_status.worker_alive` 恒为 `true` → 界面永远「运行中」；
- `_maybe_repair()` 写在循环开头，循环不转 → 记录永远不自愈；
- 后面排队的任务都卡在 `_lock.acquire()` 上，采集整条停摆。

`ThreadPoolExecutor` 用的是 `with`，退出时 `shutdown(wait=True)` 还会再等一次，
即使把 while 改掉，卡点也只是从 `wait` 挪到 `__exit__`。

## 改法

| 位置 | 改动 |
| --- | --- |
| `_wait_with_heartbeat` | 记下每个 future 的开始时间，超过 `JOB_TIMEOUT`(240s) 就取消、放弃、返回，不再等 |
| `_record_stuck` | 把被放弃的任务写成 `status=error / error_code=collect_timeout`，`next_due` 排到下一轮，并在 `_buybox_worker_state` 里记 `stuck_at` / `stuck_jobs` |
| `run()` | `ThreadPoolExecutor` 改成显式 `shutdown(wait=False)` |
| `quote_browser.quote_context` | 锁改成 `acquire(timeout=LOCK_WAIT_SECONDS=90)`，拿不到抛 `CollectorBusy`；只有真拿到了才 release |
| `process()` | `CollectorBusy` 记成 `collector_busy`，不再混进 `collector_unavailable`（那条会劝用户「点立即采集重试」，而重试只会继续等同一把锁）。`collector_busy` 与 `collector_unavailable` 同属 `TRANSIENT_CODES`：按用户间隔重试，不走指数退避（WATCHDOG-RETRY-01） |
| `loop_status` | 新增 `cycle_age_seconds` / `stuck_at` / `stuck_jobs` |
| `AutoPricing.tsx` | 心跳新鲜但 `cycle_age > max(间隔, 实测整轮耗时)×2+180s` 时显示「采集循环卡住了」，danger 色；「任务已被放弃」只在 `stuck_jobs>0` 时说（WATCHDOG-SLOW-01） |

判断「卡住」不能用 `worker_alive`，只能用**上一轮到现在多久**。这是这次事故
最贵的一课：心跳证明的是「线程还在」，不是「循环在转」。

## 顺带修的：失败原因说不出来

同一时刻「刷新售价」对本店报价和官网售价都只回一句
「暂时无法读取」。而这四个分支在代码里长得完全一样：

```python
except Exception:
    raise HTTPException(503, '本店报价暂时无法读取，请稍后重试；原售价保留')
```

密钥失效(401)、被限流(429)、网络不通、代码报错，用户看到的字一模一样，
只能反复点重试。`/api/products/scrape-takealot` 更糟：抓取层抛异常会一路冒到
全局处理器变成 500「服务器内部错误」。

新增 `_read_reason(error)`，只做**归因**，不透出路径/密钥/异常正文：

- `_load_offers` 已写好的中文原因（限流冷却、格式异常、超出读取上限）原样透出；
- `httpx` 的 `ConnectError` → 「连不上上游接口（网络不可达或 DNS 失败）」；
- `HTTPStatusError` → 「上游接口返回 HTTP 4xx/5xx」；
- `ModuleNotFoundError` → 「运行环境缺少抓取组件（部署问题，不是商品问题）」；
- 其它 → 「内部错误 <类型名>」。

## 验证

- `pytest` 388 passed / 1 skipped；新增 `test_collection_watchdog.py` 4 例
  （卡死任务被放弃并记账 / 正常任务不被误判 / 浏览器忙立即报错而不是排队 /
  `cycle_age` 与 `stuck_jobs` 读数）。
- `test_price_refresh.py` +3 例：限流原因透出、官网异常带类型名、`_read_reason` 映射。
- `test-buybox-status.cjs` 179 断言（+4：卡住文案、danger 色、卡了多久、该怎么办）。
- `tsc --noEmit` + `vite build` 通过。

## 仍然存在的边界

`_record_stuck` 只能「放弃等待」，不能真正杀掉卡住的浏览器线程。如果 Chromium
长期挂死，任务线程会一直占着一个 worker 槽（另一个仍可干活），彻底恢复要重启
服务。要做无重启恢复，得把采集放进子进程（可以 kill），那是另一个量级的改动。
