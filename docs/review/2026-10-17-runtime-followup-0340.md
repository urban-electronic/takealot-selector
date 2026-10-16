# 2026-10-17 03:40 运行复核

基准 main `73cacadd5b757b6b7728f1f807e9e8849d1024e4`（PR115）。代码、部署、运行回归与真实业务验收分别记录。

| 项目 | 复现 / 环境与预期 | 实际证据 | 结论 |
| --- | --- | --- | --- |
| PR115 后端接续 | 读取生产 openapi，预期包含同步预览与提交路由 | 两条 reconciliation 路由已存在，独立印证 WorkBuddy R27 的公开探测；不据此认定业务写入成功 | 路由上线通过，实际 commit 未由部署控制台核对 |
| 库存 / 海外在途规则 | 隔离数据库 HTTP 预览→提交→读回；重复提交不重复记账，撤回恢复国内扣库且移除对应在途；过期预览、跨店拒绝 | 本轮重新执行 reconciliation、packing export、operations planner 共38项通过；Inventory真实React组件10交互断言通过 | 程序回归通过 |
| 真实装箱更正 | 生产库存页，预期读取旧单后提交已授权批次并回读 | 当前浏览器显示“无效或缺失 API Key”，旧会话未保留；本轮未请求/读取凭据，未提交更正 | 仍阻塞；默认0不得当真实库存 |
| PR114 候选 | 独立提取 head91985e2 的collector及flow测试，假API/页面，无真实站点请求 | 64项flow测试通过；额外API返回合法lease与数组productId混合时，round_once在leased集合构造处TypeError，release=[] | 仍失败，退回PR114评论6223336210 |
| PR114 用户提示 | API.call提示应经过run后保持可操作且脱敏 | run仍把RuntimeError统一写为连接拒绝或超时；当前新增测试只调用API.call | 入口验收不足，要求受控类别与实际run回归 |
| PR113 脚本接续 | head8da6e2 edge脚本在最新main运行 | 实际报MODULE_NOT_FOUND：../operations/ShipmentReconciliation；新增jsdom未同步已有package-lock | 仍失败，退回PR113评论6223337091 |
| Windows窗口报告 | 来源为WorkBuddy R24；本机无后端会话 | 11路由×3窗口只读空表检查，不能覆盖带真实数据的编辑区 | 保留外部来源和未验证边界 |
| 真实采集闭环 | 目标机正确SKU/变体本轮报价回传网页 | 未收到新同轮证据；R26/R27仅status/products探针，无offers回传 | 未验证，不恢复调价 |

## 协作与状态纪律

已在PR103评论6223345095归档：生产products会授予租约，不是纯只读健康检查。WorkBuddy报告26个租约曾被领取但未归还，是实际队列阻塞事件；不能把TTL回收当清理机制，也不能由此推断所有历史零任务均源于竞争。要求停止生产循环/并发products健康压测，健康只读status；正常领取在finally仅归还本人未上报租约，不释放别人的任务。

X-Collector-Client是客户端标识；持有效密钥的任意标识被接受，本身不证明越权，不据此仓促改变鉴权。没有读取密钥、Cookie或profile，没有采集确认被拦商品，没有创建测试采购、价格回写或生产库存测试。

当前认证限制已交接目标机恢复既有授权会话，不要求在PR贴凭据；既定业务更正仍由Codex统一执行，交接不算完成。后续先核对认证及真实旧单状态，避免重复提交上一轮可能已完成的批次。
