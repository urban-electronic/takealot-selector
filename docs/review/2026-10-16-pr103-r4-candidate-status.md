# PR #103 — 候选实现状态（R4，2026-10-16）

按 Codex WB-103-R3 / 6199526037 要求，分别记录**已修 / 仍失败 / 未实测**。
本环境无已登录 Takealot 会话、无法访问站点，真实同轮采集一律标记为「未实测」。

## 已修（fixed）
- **采集器领取改造（接续 PR105 e4da21d）**：移除旧候选里同步阻塞事件循环的
  `claim_done.wait` 线程版 + 释放双兜底吞异常；改用 PR105 的 `asyncio.shield`
  + `claim_batch` / `_finish_pending`。取消时异步等真实响应再 `release` 迟到租约，
  **不阻塞事件循环、不用 30s 截止、不吞领取错误**。
- **桥接丢更新修复**：`save_state` 增加跨进程互斥锁（msvcrt/ fcntl）+ `atomic_state_update`
  （读-改-写串行化），杜绝并发 ACK/fetch/reply 交错丢更新。
- **自检不再回退游标**：`state_rw_probe` 改用**完全独立的临时态文件**往返，真实协作游标
  绝不被读写，旧实现「并发 ACK 期间自检把游标回退」的缺陷已消除。
- **新增回归测试** `tools/test_codex_bridge.py`：并发写不丢更新 + 自检不回退游标。

## 仍待修 / 未实测（failed / untested）
- **真实 Windows 同轮采集**：无已登录持久会话、本环境无法访问 Takealot 站点，未实测。
  按方案 B 模板（`2026-10-16-pr103-r3-windows-evidence-template.md`）待用户环境执行验证。
- **首拦截进程内暂停**：Codex 已确认「进程内暂停测试已过」；但**重启清空**、
  **GUI 从首条文件学习 run_id** 仍待修，按 Codex 要求作为**分开的最小提交**处理，本轮未做。
- **main 同款风险**：采集器分支范围，未改 main。

## 测试
- `tools/native-collector/tests/test_collector_flow.py`：全绿；原 `test_claim_timeout_*` 改为
  `test_late_claim_after_cancel_releases_lease`，验证 shield 迟到释放语义。
- `tools/test_codex_bridge.py`：并发写不丢更新 + 自检不回退游标（离线，不触网络/凭据）。

## 红线（全程未触碰）
仅行情采集；未改售价/未开调价/未创采购/未动 SKU 身份校验/未读传密钥 Cookie；
未改 backend/；未重新引入「自主全目录采集」与「最近成功 120s 接管」；未合并 main、未 force push。

---

## WB-103-R5 复核（2026-10-16，WorkBuddy 执行，非 Codex 运行）

> 说明：Codex 本轮（6200797492）明确——其执行环境 unavailable，**未独立运行本机 17+ 桥接 2
> 测试**，仅做静态对照。下方「测试」为 **WorkBuddy 在本机执行**的结果，不得写成「Codex 运行通过」。

### Codex 静态审查结论（已采纳）
- 确认 `claim_batch` / `_finish_pending` 已采纳 PR105 shield 语义，旧同步 `wait`/30s 截止段已移除；
  「API 就地整合」= 该段移植，**不等于分支已 rebase**。
- 澄清：`backend/tests` 是测试而非禁止修改的业务边界，可正常保留/补测；但本分支红线仍为
  **不推 backend 业务代码整包**（测试文件保留不动）。

### 本轮新增修复（WB-103-R5 第 1–3 条）
1. **锁失败 fail closed**：`atomic_state_update` 在跨进程文件锁获取失败 / 两平台锁后端均不可用
   时，**返回 False 且不写文件、绝不谎称串行化成功**；`_acquire_lock` 改用非阻塞锁 + 有限重试实现
   超时，避免 LK_LOCK 永久阻塞事件循环。
2. **probe 独立化**：`state_rw_probe` 改用独立 `StateStore`（临时态文件 + 独立锁），**不再重新赋值
   全局 `STATE_FILE`**；同进程并发 ACK 写真实游标与 probe 写临时态互不干扰，真实游标不丢更新。
3. **跨进程多进程测试**：新增 `tools/_bridge_mp_worker.py` + `test_cross_process_atomic_no_lost_updates`
   （multiprocessing **spawn**，Windows 默认），验证 4 进程各 +25 次最终 count==100，不丢更新；
   另补 `test_atomic_fail_closed_*`、`test_probe_acks_interleave_no_lost_cursor`（Event 门闩交错）。

### 测试（WorkBuddy 本机执行，来源已记录）
- `tools/native-collector/tests/test_collector_flow.py`：**17 passed**（含 PR105 门闩语义：
  `test_late_claim_after_cancel_releases_lease`、`test_cancelled_round_releases_all_leases`，及
  status_view/GUI 纯函数 8 项：陈旧时间戳/轮次倒退/pid 不符/run_id 不符/非法 round/未来时间戳等）。
- `tools/test_codex_bridge.py`：**6 passed**（原 2 + 新增 4：fail-closed ×2、probe/ACK 交错、跨进程）。

### 未执行限制（仍保持，未冒充通过）
- **真实 Windows 同轮采集**：无已登录持久会话、本环境无法访问 Takealot，未实测；B 模板是
  「准备好的方案」而非「已跑成功」。**未验证**。
- **重启清空 / GUI 从首条文件学习 run_id**：仍待修，作为分开的最小提交，本轮未做（第 4 条保留项）。
- **main PR105 的 4 项门闩测试**：属 main/PR105 分支、依赖后端测试环境，本采集器分支未含、本机
  未执行；本机执行的是采集器分支内等价的门闩语义用例（见上）。
- 未操作已确认挑战页、未取出/打印凭据；未合并 main、未 force push。
