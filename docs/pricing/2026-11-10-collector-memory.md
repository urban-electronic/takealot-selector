# 采集浏览器内存收敛（2026-11-10）

## 背景

Railway 账单周期第 7 天已用 $5.14（Hobby 额度 $5）。30 分钟降频上线后 CPU 降 74%、
出网流量降 75%，但**内存只降 26%，仍占花费 54%**（约 1.4 GB 常驻基线，限额 8 GB）。

优化前的线上实测（近 6 小时 `MEMORY_USAGE_GB`）：

| 指标 | 值 |
| --- | --- |
| min | 0.545 GB |
| avg | 1.329 GB |
| p90 | 1.537 GB |
| max | 1.775 GB |
| CPU avg | 0.204 |

一轮 40 条买盒采集耗时 14:47:50 → 15:05:38 共约 1068 秒（17.8 分钟），40 条全部
`success`；采样按「每 1–2 分钟若干条」推进——说明 `ThreadPoolExecutor(max_workers=2)`
里那把全局 `_lock` 本来就把两个 worker 串成了一个。

## 改了什么

### 1. 并发 2 → 1（`backend/services/buybox_monitor.py`）

`COLLECT_WORKERS`（默认 1，`BUYBOX_COLLECT_WORKERS` 可回退为 2）。

第二个 worker 没有带来过吞吐：浏览器锁本身就是全局串行的。但它让「卡死任务」和
「下一个正常任务」在池里并存，卡死的那份渲染进程会一直挂到 `JOB_TIMEOUT` 之外。
并发 1 之后，任一时刻最多只有一份 Chromium 基线。

### 2. 浏览器启动参数瘦身 + 资源拦截（`backend/services/quote_browser.py`）

`LEAN_BROWSER_ARGS`：原有四条沙箱/共享内存/GPU 参数保持不变，追加

- `--disable-extensions --disable-default-apps --disable-component-update --disable-sync
  --disable-background-networking --no-default-browser-check --metrics-recording-only
  --mute-audio --disable-breakpad`
- `--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints`
- `--js-flags=--max-old-space-size=256`：采集只读 DOM，不需要 Chromium 默认的 V8 老生代预算
- `--renderer-process-limit=2`：站内广告/统计 iframe 不再各起一个渲染进程

`BLOCKED_RESOURCE_TYPES = {image, media, font}`：`collect()` 传 `block_media=True`，
命中即 `abort`。买盒判定读的是 DOM 文本与 `data-ref` 锚点，图片/字体对价格零贡献，
却在渲染进程里各占一份解码缓冲。交互式看页面（cloud_browser 截图给用户设提货点）
不传这个参数，看到的仍是完整页面（`BUYBOX_BLOCK_MEDIA=0` 可全局关掉）。

### 3. 生命周期收尾核账（`_reap_profile`）

`context.close()` 之后再看一眼进程表：`playwright.stop()` 完成 3 秒内 `/proc` 里仍有
指向该 profile 的进程，就按 `--user-data-dir` 强杀整棵进程树。这条专治「close 超时/
静默失败留下的 1GB 常驻」，顺带避免下一次采集因为 profile 被占而启动失败。

### 4. 空闲自动回收（`_idle_guard_loop`）

守护线程每 30 秒看一次：浏览器锁空闲、且距上次释放已超过 `IDLE_CLOSE_SECONDS`
（默认 300 秒，`BUYBOX_BROWSER_IDLE_SECONDS` 可调），就把该 profile 的残留浏览器清掉。
没有任务时不再留一份 Chromium 挂着。

### 5. headless 开关（不改默认行为）

`browser_headless(display)` + `BUYBOX_BROWSER_HEADLESS`：

- `auto`（默认）：有 Xvfb/图形会话就有头，没有就无头——与改动前完全一致；
- `1`：强制无头，并**跳过拉起 Xvfb**（那台虚拟显示器本身也占几十 MB）；
- `0`：强制有头。

无头比有头少掉合成器与软件栅格那一整块，是收内存最大的单项；但它同时会改变浏览器
指纹，可能触发 Takealot 反爬。因此做成环境变量开关：先按原形态上线观察，需要时改
环境变量试跑，成功率掉了就改回来，不必回滚代码。

## 没动的东西

- `MIN_INTERVAL_MINUTES=30` 与 `effective_interval_minutes()`（30 分钟下限）；
- 解析逻辑、买盒判定、自动调价策略、表结构、接口契约。

## 回滚

```bash
git revert <本次提交>            # 或只用环境变量回退单项
# 单独回退并发：Railway 变量 BUYBOX_COLLECT_WORKERS=2
# 单独关掉资源拦截：BUYBOX_BLOCK_MEDIA=0
# 单独关掉空闲回收：BUYBOX_BROWSER_IDLE_SECONDS=86400
```

## 验证

- 后端 pytest 1401 passed（含新增 `backend/tests/test_browser_memory_policy.py` 14 条）；
- 前端 `node scripts/selfcheck.cjs --quick` 49 步通过；
- 线上内存与单轮耗时见提交后 20–30 分钟的观察记录。
