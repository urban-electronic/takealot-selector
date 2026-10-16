# PR #103 · WB-103-R6/R7 候选状态（采集器分支 fix/native-collector-loop-status）

> 本文件对应 Codex 在 PR #103 的 WB-103-R6（issue id=6201798704）与 WB-103-R7（issue id=6202899377，接续 6202919562）评审。
> 仅采集器 / GUI 范围；未触碰 backend/，未改售价 / 调价 / 采购 / SKU 校验，未读传密钥 Cookie。

## R6 要求与处理

| # | Codex 要求 | 本次处理 | 状态 |
|---|-----------|----------|------|
| 1 | 拦截暂停跨重启保存 / 明确人工恢复 | 新增 `tools/native-collector/challenge_state.py`：每轮把被验证页/403 拦截的 SKU 计数原子写盘；`run()` 启动时 `load_challenge_state` 读回（暂停跨重启保存）；新增恢复标志 `clear_challenge.flag`，运行中的采集器在下一轮顶部 `consume_recovery_flag` 清空暂停（显式人工恢复） | 已修 |
| 2 | GUI 启动方生成 run_id 并传入子进程，不从首条残留文件学习 | `gui.py start()` 生成 `run_id=uuid4().hex[:12]`，立即 `self._last_run=run_id` 并 `environment['PP_COLLECTOR_RUN_ID']=run_id`；`collector.py` 改用 `os.environ.get('PP_COLLECTOR_RUN_ID') or 本机生成`，首次读取即以已知 run_id 校验 | 已修 |
| 3 | 补重启 / 同 PID 旧状态 / 首次读取接入测试；恢复后再次挑战只暂停、其他任务继续 | `tests/test_collector_flow.py` 新增：跨重启持久化、缺失/损坏降级、非 int 剔除、clear 恢复、恢复标志清空、已知 run_id 首次读取信任、同 PID 旧 run_id 判陈旧、恢复后仅被拦 SKU 暂停且其他继续、持久化暂停跨轮直接生效 | 已修（单测级） |
| 4 | 真实 Windows 仍无登录会话，模板不是采集结果；不得重复访问已确认挑战页补验收 | 遵守：全部以单测（FakeApi/FakePage/FakeContext）验证，未对 Takealot 真实站点发起任何访问；未用证据模板充当采集结果 | 已遵守 |
| 5 | 更新候选并保留 PR105/107，不覆盖回退 | 本候选文档更新；改动仅限 native-collector（采集器/GUI）与 tests，未涉及 PR105/107 的桥接脚本与 backend，未回退 | 已遵守 |

## WB-103-R7 要求与处理（issue id=6202899377，接续 id=6202919562）

> R7 为 Codex 在隔离环境（假 Playwright/Api、无站点/凭据）补测发现的问题；全部以单测（替身）修复与覆盖，未对 Takealot 发起真实访问。

| # | Codex 要求 | 本次处理 | 状态 |
|---|-----------|----------|------|
| 1 | 真实 run 取消/异常后拦截暂停漏存：保存仅在 `await cycle` 正常返回之后，用户暂停/取消或后续异常会跳过保存，重启重访已确认拦页 | `collector.run` 将 `save_challenge_state` 移入 cycle 的 `finally`，**正常/异常/取消三种路径都落盘**；新增确定性 run 级测试 `test_run_persists_challenge_state_on_cancel`（替身 playwright/api/parser，round_once 首次确认即取消，断言状态文件已落盘） | 已修（单测级） |
| 2 | `load` 损坏 JSON 直接 `{}` 冒充无暂停；`save` 父路径是文件返回 `None` 无提示；写盘失败/损坏不能冒充无暂停；负计数未过滤 | `load_challenge_state` 损坏/格式非法抛 `ChallengeStateError`（缺失仍安全返回 `{}`）；`save_challenge_state` 返回 `bool`，父路径是文件返回 `False`，绝不冒充成功；`load` 过滤负计数；`collector` 启动捕获 `ChallengeStateError` 保留内存暂停并给**受控提示（不输出路径/敏感内容）**，写盘失败同样给受控警告并保留内存暂停 | 已修（单测级） |
| 3 | `recover_challenges` 吞全部异常仍显示“已请求恢复”；`consume_recovery_flag` 删除失败仍返回 `True` | `consume_recovery_flag` 仅当“标志删除 + 状态清空都成功”才返回 `True`，任一失败返回 `False` 并保持暂停；`gui.recover_challenges` 据 `write_recovery_flag` 实际结果反馈，失败给受控错误并保持暂停；恢复标志**按 profile 哈希隔离**，避免一键误放开其他 profile | 已修（单测级） |
| 4 | 新模块 `status_view`/`challenge_state` 必须纳入打包脚本与 ZIP，现网包未包含；补 GUI start/recover 替身测试与跨平台路径一致性 | `scripts/package-native-collector.py` ZIP 列表加入 `status_view.py` / `challenge_state.py` 并重新生成 `public/downloads/takealot-native-collector.zip`（已验证含 9 个采集器文件）；补 `write_recovery_flag`/`consume_recovery_flag` 替身测试、`recovery_flag_path` 按 profile 隔离且稳定、`default_path` 绝对且稳定等一致性测试 | 已修（单测级） |

## WB-103-R8 要求与处理（issue id=6204519284，接续 id=6204526846）

> R8 为 Codex 在隔离环境（假 Playwright/Api、无站点/凭据）补测发现的 4 项运行级失败；全部以单测（替身）修复与覆盖，未对 Takealot 发起真实访问，未用证据模板充当采集结果。

| # | Codex 复现 / 要求 | 本次处理 | 状态 |
|---|------------------|----------|------|
| 1 | 损坏状态仍实际放行并销毁证据：预写损坏文件，启动打印人工核验后仍 `round_once(challenged={})` 领取/scrape，正常结束把磁盘覆盖成 `{}`；`challenged_unreliable` 只赋值从未使用 | `collector.run` 启动读盘损坏置 `challenged_unreliable=True`（保留内存暂停 + 受控提示，不输出路径/敏感内容）；循环顶部：不可信状态**禁止领取/访问**（不调用 products/scrape），每轮重试重读，人工修复或删除原文件后即恢复采集；`finally` 保存仅在 `not challenged_unreliable` 时执行，**绝不覆盖损坏原文件**，直到显式人工恢复。新增 run 级测试 `test_run_refuses_collection_when_state_corrupt`（损坏不调用 products、不擦原文件） | 已修（单测级） |
| 2 | profile 仅恢复标志隔离，state 仍全局 `challenge_state.json`：存 B 暂停后 `write_recovery_flag('A')` + `consume A` 返回 True，读 B 暂停已 `{}`；A 可清除 B；不同 profile 单例锁也允许并发写同一固定 tmp/覆盖状态 | 新增 `challenge_state_path(profile)` 按 profile 路径哈希隔离状态文件（与恢复标志共用同一 slug/base），`load/save/clear/consume` 支持 `profile` 参数；A 的恢复只清 A 的状态文件，不碰 B；新增 `test_challenge_state_path_is_profile_scoped_and_deterministic` / `test_two_profiles_same_sku_independent_pause` / `test_profile_a_recovery_does_not_clear_profile_b` / `test_profile_concurrent_save_no_lost_pause` | 已修（单测级） |
| 3 | `consume` 先删状态再删标志：标志删除失败返回 False，内存保留但磁盘丢暂停，重启仍放行 | `consume_recovery_flag` 改为**保守顺序**：先删恢复标志，再清空持久化暂停；标志删除失败直接返回 False 且磁盘状态原样不动（不丢暂停）；清空失败也返回 False 但磁盘状态仍在，重启仍读回暂停（不丢暂停），仅本次恢复未生效可重试。新增 `test_consume_recovery_flag_false_when_flag_unlink_fails`（标志删除失败不得清空持久化暂停） | 已修（单测级） |
| 4 | 注释「首次确认即保存」实际仍只轮次 finally 保存；进程突然退出在 finally 前会丢本轮暂停 | `round_once` 新增 `lock`/`persist` 参数；worker 首次确认拦截即在锁内**即时持久化**（不等整批 600s 结束）；`collector.run` 传入 `asyncio.Lock()` 与 `persist_challenge` 回调（不可信时跳过）；新增 `test_round_once_persists_on_first_intercept`（首次确认即 persist）+ run 级 `test_run_persists_during_round_not_only_on_exit`（即时落盘不依赖 finally）；`test_run_persists_challenge_state_on_cancel` 同步适配 profile 隔离路径 | 已修（单测级） |

> 红线：未触碰 backend/，未改售价/调价/采购/SKU 校验，未读传密钥 Cookie；未做任何指纹规避/反爬绕过；未自行合并 main；未 force push。

## WB-103-R9 要求与处理（issue id=6205300420，接续 id=6205307546）

> R9 为 Codex 在隔离环境（假 Playwright/Api、无站点/凭据）给出的 4 项最小修复；全部以单测（替身）修复与覆盖，未对 Takealot 发起真实访问，未用证据模板充当采集结果。仅提交本最小修复，未引入新功能。

| # | Codex 要求 | 本次处理 | 状态 |
|---|-----------|----------|------|
| 1 | GUI 恢复按钮对损坏状态无效：`challenged_unreliable` 分支在 `consume_recovery_flag` 之前 `continue`/`break`，显式恢复标志永远不消费 | `collector.run` 循环顶部把 `consume_recovery_flag` 提前到「不可信状态」判断**之前**：即使处于损坏/不可信态，明确人工恢复协议仍被消费（删除损坏文件 + 恢复标志），清空暂停并恢复采集；恢复成功才置 `challenged_unreliable=False` 并继续。新增 run 级测试 `test_run_recovers_from_corrupt_state_via_gui_flag`（损坏文件 + GUI 恢复标志 → 标志被消费、损坏内容不再残留、恢复后恢复领取） | 已修（单测级） |
| 2 | `persist_challenge` 忽略 `save_challenge_state` 返回 False，worker 又吞异常，直到 finally 才警告；首次落盘失败未确认却继续整轮，进程在 finally 前退出仍丢暂停 | `persist_challenge` 改为返回 `bool` 且落盘失败**抛受控 `ChallengeStateError`**（不再吞掉）；worker 不再包裹 `except Exception: pass`，失败直接上抛；run 循环新增 `except ChallengeStateError`：立即进入「持久化不可信」，停止新增领取/访问、保留内存暂停、准确提示「需人工核验」；`finally` 整批保存加 `not persist_unreliable['v']` 守卫避免重复失败。新增 run 级测试 `test_run_stops_claims_when_first_persist_fails`（monkeypatch 写盘返回 False → 状态置 `challenge_state_unreliable`、不生成有效暂停文件） | 已修（单测级） |
| 3 | GUI 状态映射未加入 `challenge_state_unreliable`，当前显示「启动中」而非「需人工恢复」；旧日志「暂停持续到进程结束」与跨重启持久化矛盾 | `status_view.STATE_VIEW` 新增 `challenge_state_unreliable → ('拦截状态不可信，需人工核验', '#b00000')`；`classify_state` 据此显示「需人工恢复」而非默认「启动中」；`collector` 拦截提示文案改为「已持久化保存，重启后仍保持暂停直至显式人工恢复」，与跨重启持久化一致。新增 `test_status_view_maps_challenge_state_unreliable`（映射非启动中蓝）+ `test_gui_refresh_state_shows_challenge_unreliable`（真实 `refresh_state` 调用，tkinter 可用时跑、本环境无 Tk 跳过） | 已修（单测级 / GUI 受 Tk 限制跳过） |
| 4 | 只提交最小修复，不再引入新功能；收到后可做最终独立集成/打包/回归 | 改动仅限 `collector.py` / `status_view.py` / `tests/test_collector_flow.py` / 候选文档；未改 backend/、未回退、未引入新功能 | 已遵守 |

> 验收补充（id=6205307546）：main `032f56df` 未变，Pages/Railway 成功；本轮正式版 native/GUI 再次通过；候选 `b73ca22` 的 46 测试通过且 R7/R8 已修复确认；R9 新增两项实际失败（显式恢复不消费、persist False 漏判）已分开记录并补最小修复。

## 回归测试（WorkBuddy 本机执行，非 Codex 运行）

- `tools/native-collector/tests/`（pytest）：**49 passed + 1 skipped**（R2–R8 既有 46 + R9 新增/调整 3 项：损坏状态经 GUI 恢复标志消费恢复、首次 persist 落盘失败即停、状态映射识别不可信；GUI 真实 `refresh_state` 受 Tk 限制跳过）。
- `tools/test_codex_bridge.py` + `tools/_bridge_mp_worker.py`（R5 既有）：**6 passed**。
- `py_compile` 校验：`collector.py` / `gui.py` / `status_view.py` / `challenge_state.py` 均通过。

## 未验证（须用户环境确认）

- **真实 Windows 同轮采集**：本环境无已登录 Takealot 会话，无法实跑采集器对接真实站点；R7 仅以单测（替身）覆盖逻辑，未做端到端采集验收。
- **GUI 真实 Tk 点击流程**：`gui.recover_challenges` / `start` 改动未在本环境用真实 Tk 界面跑通（测试 venv 无 tkinter，按项目约定 GUI 逻辑抽至无 Tk 模块单测）；恢复回调的**实际写盘/反馈**以 `write_recovery_flag` 替身测试覆盖，未做真实窗口点击。
- **恢复标志跨 profile 端到端**：按 profile 哈希隔离以单测验证，未在真实多 profile 运行态实测。
- **真实 Windows 路径一致性**：`default_path` 依赖 `LOCALAPPDATA`/`HOME`，在 Linux/macOS 容器与本机 Windows 行为一致（绝对路径、稳定），但现网部署仅在 Windows，未跨机实测。
- **现网包下发**：ZIP 已重新生成并含新模块，但用户侧实际下载/解压/运行以新包为准，需用户环境确认。
- **损坏状态端到端人工恢复（R8-1）**：单测覆盖「损坏不领取/不擦原文件 + 每轮重读恢复」，但真实场景「人工修复或删除损坏文件后采集器自动恢复采集」未在真实运行态实测。
- **profile 隔离跨 profile 真实运行（R8-2）**：按 profile 哈希隔离状态/标志以单测覆盖，未在两 profile 同时运行的真实态实测「A 恢复不影响 B、并发保存互不覆盖」。
- **即时持久化崩溃恢复（R8-4）**：首次确认即写盘的语义以单测覆盖，但「进程在整批 600s 结束前被强杀、重启读回暂停」未做真实强杀验收。
- **损坏态经 GUI 按钮恢复的真实运行态（R9-1）**：单测覆盖「预写损坏文件 + 写入恢复标志 → 标志被消费、损坏内容不再残留、恢复后恢复领取」；但未在真实 Tk 窗口点击「恢复被拦截商品」+ 真实损坏文件恢复路径实测。
- **持久化写盘失败的真实磁盘故障（R9-2）**：以 monkeypatch `save_challenge_state` 返回 False 覆盖「首次 persist 失败即停、状态置不可信、不生成有效暂停文件」；未做真实磁盘满/权限故障端到端验收。
- **GUI 状态映射真实显示（R9-3）**：`status_view` 映射以单测验证；`gui.refresh_state` 真实调用受本环境无 Tk 限制跳过，需用户环境在真实窗口确认「不可信 → 需人工恢复（红色）」显示与恢复按钮反馈。

## 红线声明

未修改 backend/；未改售价、未开调价、未创采购、未动 SKU 身份校验；未读取或上传密钥 / Cookie；未自行合并 main；未 force push；未做任何指纹规避 / 反爬绕过。
