# PR #103 · WB-103-R6 候选状态（采集器分支 fix/native-collector-loop-status）

> 本文件对应 Codex 在 PR #103 的 WB-103-R6 评审（issue id=6201798704）。
> 仅采集器 / GUI 范围；未触碰 backend/，未改售价 / 调价 / 采购 / SKU 校验，未读传密钥 Cookie。

## R6 要求与处理

| # | Codex 要求 | 本次处理 | 状态 |
|---|-----------|----------|------|
| 1 | 拦截暂停跨重启保存 / 明确人工恢复 | 新增 `tools/native-collector/challenge_state.py`：每轮把被验证页/403 拦截的 SKU 计数原子写盘；`run()` 启动时 `load_challenge_state` 读回（暂停跨重启保存）；新增恢复标志 `clear_challenge.flag`，运行中的采集器在下一轮顶部 `consume_recovery_flag` 清空暂停（显式人工恢复） | 已修 |
| 2 | GUI 启动方生成 run_id 并传入子进程，不从首条残留文件学习 | `gui.py start()` 生成 `run_id=uuid4().hex[:12]`，立即 `self._last_run=run_id` 并 `environment['PP_COLLECTOR_RUN_ID']=run_id`；`collector.py` 改用 `os.environ.get('PP_COLLECTOR_RUN_ID') or 本机生成`，首次读取即以已知 run_id 校验 | 已修 |
| 3 | 补重启 / 同 PID 旧状态 / 首次读取接入测试；恢复后再次挑战只暂停、其他任务继续 | `tests/test_collector_flow.py` 新增：跨重启持久化、缺失/损坏降级、非 int 剔除、clear 恢复、恢复标志清空、已知 run_id 首次读取信任、同 PID 旧 run_id 判陈旧、恢复后仅被拦 SKU 暂停且其他继续、持久化暂停跨轮直接生效 | 已修（单测级） |
| 4 | 真实 Windows 仍无登录会话，模板不是采集结果；不得重复访问已确认挑战页补验收 | 遵守：全部以单测（FakeApi/FakePage/FakeContext）验证，未对 Takealot 真实站点发起任何访问；未用证据模板充当采集结果 | 已遵守 |
| 5 | 更新候选并保留 PR105/107，不覆盖回退 | 本候选文档更新；改动仅限 native-collector（采集器/GUI）与 tests，未涉及 PR105/107 的桥接脚本与 backend，未回退 | 已遵守 |

## 回归测试（WorkBuddy 本机执行，非 Codex 运行）

- `tools/native-collector/tests/`（pytest）：全部通过（含 R2–R5 既有 + R6 新增）。
- `tools/test_codex_bridge.py` + `tools/_bridge_mp_worker.py`（R5 既有）：全部通过。
- `py_compile` 校验：`collector.py` / `gui.py` / `status_view.py` / `challenge_state.py` 均通过。

## 未验证（须用户环境确认）

- **真实 Windows 同轮采集**：本环境无已登录 Takealot 会话，无法实跑采集器对接真实站点；R6 仅以单测覆盖逻辑，未做端到端采集验收。
- **GUI 真实点击流程**：`gui.py` 改动（run_id 绑定、恢复按钮）未在本环境用真实 Tk 界面跑通，仅做 `py_compile` 与逻辑核对。
- **恢复标志端到端**：运行中的采集器消费 `clear_challenge.flag` 的时序以单测间接覆盖，未在真实运行态实测。

## 红线声明

未修改 backend/；未改售价、未开调价、未创采购、未动 SKU 身份校验；未读取或上传密钥 / Cookie；未自行合并 main；未 force push；未做任何指纹规避 / 反爬绕过。
