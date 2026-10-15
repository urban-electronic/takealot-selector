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
