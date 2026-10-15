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

## 回归测试（WorkBuddy 本机执行，非 Codex 运行）

- `tools/native-collector/tests/`（pytest）：**39 passed**（R2–R6 既有 + R7 新增 11 项：损坏抛错、负计数过滤、save 成功/父路径为文件失败/目标为目录失败、consume 清空失败返回 False、consume 全成功返回 True、profile 隔离标志、write+consume 按 profile、write 失败返回 None、default_path 一致性、run 级取消持久化）。
- `tools/test_codex_bridge.py` + `tools/_bridge_mp_worker.py`（R5 既有）：**6 passed**。
- `py_compile` 校验：`collector.py` / `gui.py` / `status_view.py` / `challenge_state.py` 均通过。

## 未验证（须用户环境确认）

- **真实 Windows 同轮采集**：本环境无已登录 Takealot 会话，无法实跑采集器对接真实站点；R7 仅以单测（替身）覆盖逻辑，未做端到端采集验收。
- **GUI 真实 Tk 点击流程**：`gui.recover_challenges` / `start` 改动未在本环境用真实 Tk 界面跑通（测试 venv 无 tkinter，按项目约定 GUI 逻辑抽至无 Tk 模块单测）；恢复回调的**实际写盘/反馈**以 `write_recovery_flag` 替身测试覆盖，未做真实窗口点击。
- **恢复标志跨 profile 端到端**：按 profile 哈希隔离以单测验证，未在真实多 profile 运行态实测。
- **真实 Windows 路径一致性**：`default_path` 依赖 `LOCALAPPDATA`/`HOME`，在 Linux/macOS 容器与本机 Windows 行为一致（绝对路径、稳定），但现网部署仅在 Windows，未跨机实测。
- **现网包下发**：ZIP 已重新生成并含新模块，但用户侧实际下载/解压/运行以新包为准，需用户环境确认。

## 红线声明

未修改 backend/；未改售价、未开调价、未创采购、未动 SKU 身份校验；未读取或上传密钥 / Cookie；未自行合并 main；未 force push；未做任何指纹规避 / 反爬绕过。
