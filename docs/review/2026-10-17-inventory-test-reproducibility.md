# 2026-10-17 库存交互检查可复现性修复

基准 main `184702666ba0a34862cb633a5f40be979ebf190a`。WorkBuddy PR113/8da6e2 提供了额外边界交互脚本及 jsdom 开发依赖建议；Codex 独立接续，不覆盖其候选分支。

## 实际失败与修复

上轮已在真实 Inventory.tsx 上执行候选脚本，报 `Cannot find module '../operations/ShipmentReconciliation'`。PR115新增组件后，CommonJS测试编译器没有该TSX组件的替身，测试尚未挂载页面就退出。补充与main基础脚本相同的组件替身，仅隔离与库存调整无关的装箱补登组件，不替换被测Inventory页面。

基础脚本与候选边界脚本都依赖jsdom，但main未声明该开发依赖。采纳PR113建议，将jsdom列入devDependencies并同步package-lock.json，新增统一入口 `npm run test:inventory`。开发依赖固定26.1.0，沿用项目CI的Node20；检查lockfile中原有包版本无变化。修正候选输出中固定写“Windows native”的字样，避免把Codex当前Linux运行误报为Windows本机实测。

## 可执行验收

环境：Codex Linux，Node24.19.0，jsdom26.1.0。新建独立空目录，仅复制package.json、package-lock.json、两个测试脚本与真实Inventory.tsx，没有复用项目node_modules。

1. `npm ci --ignore-scripts --no-audit --no-fund`：成功安装114个包。
2. `npm run test:inventory`：基础10项断言与边界交互检查全部通过。实际执行小数/零/负数/非数字/空输入拒绝、延迟响应下连续点击仅一个请求、提交锁定、失败保留草稿与解锁重试、成功清空并刷新。
3. 额外使用Node20.20.2执行相同两个脚本，均通过，与现有CI的Node20运行时一致。
4. 同批库存/海外在途/装箱导出/补货后端38项再次通过，包括隔离数据库HTTP预览→应用→回读与幂等、撤回、跨店/过期预览拒绝。
5. 生产前端HTTP200，openapi包含两条reconciliation路由，未认证库存接口401。属于可访问性和鉴权烟雾，不是生产业务通过。

未使用真实库存调整测试，未创建采购、触发采集或开启调价。真实装箱更正仍因当前浏览器认证缺失而未提交；真实目标机报价回传仍缺新同轮证据。WorkBuddy未更新PR114，前轮畸形ID导致合法租约未释放与入口提示失真继续保留失败，不重复认定修复。PR113的独立窗口检查脚本本轮未采纳，不把其401空表窗口报告追认为带数据业务验收。
