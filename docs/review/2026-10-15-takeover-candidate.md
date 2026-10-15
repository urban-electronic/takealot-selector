# 2026-10-15 19:40：接管候选b005f783a5独立运行复核

当前正式版main f72d63f7df846b95993e587a3154237b857fd7eb。WorkBuddy继续在已关闭PR94旧基准分支提交，最新为b005f783a5；不是已部署的功能。

## 结论与交接

候选未采纳，正式采集/调度/窗口代码未修改。评论6191231160（PR94）逐项退回，评论6191257199（PR87）同步正式协作入口。下一次须从实时main新PR交付，不覆盖已上线来源/变体/GUI保护；删除规避参数，先修启动、状态、来源与租约证明，再交本机GUI同轮及WB-BUYBOX-IDENTITY-01证据。

## 独立执行证据

均在隔离环境执行。CLI仅AST提取main函数，注入动作替身；后端直接加载候选模块，复用现有测试SQLite上下文、mock HTTP/采集函数。没有启动候选浏览器、计划任务或后台守护，没有读取DPAPI/密钥/profile，没有访问Takealot或生产API写入。

| 项目 | 复现条件 | 预期 | 实际 | 结论 |
| --- | --- | --- | --- | --- |
| 守护CLI | argv=run_background.py start，launch/install等动作替身 | 能完成参数解析 | ArgumentError: conflicting subparser: start；动作调用0 | 未解决；重复add_parser(start/install) |
| 成功标记首次写入 | 内存SQLite无LAST_SUCCESS_KEY；实际products→offers，buyboxPrice99 | successful=1且首次标记落库 | processed1/successful1，但标记行数0、local_last_success0.0 | 未解决；update缺少insert |
| 本机来源 | 复用test_client_origin_is_not_conflated_with_native_gui，本机成功 | source及last_attempt_source均local_collector | KeyError(last_attempt_source) | 候选回退 |
| 扩展来源 | 同测试、browser_extension | source及尝试来源保留扩展 | AssertionError | 候选错误标成本机 |
| 云端成功来源 | 复用test_buybox_monitor.run，初始本机来源，模拟云端实际函数成功 | cloud_browser/cloud_completion且尝试来源cloud_browser | success但source仍local_collector，clock仍collector_timestamp，尝试来源None | 候选回退 |

现有正式版上述来源回归仍通过。不能整包合并候选旧后端。成功标记应在同一有效回传事务中可靠写入，不能通过手工补生产记录掩盖首次写入失败。

## 设计/静态风险（不冒充生产实测）

- 120秒缺成功不等于进程/租约死锁：系统允许5分钟间隔、无到期任务、首次180秒页面任务，扩展也使用独立90秒心跳。该条件与client、租约和在途执行无绑定；健康空闲也会失去退避。缺少实际排队/领取/执行/暂停/断连时序，不能宣称唯一根因已证实。
- 全局LAST_SUCCESS还混合接收来源；即便补insert，也不能替代各客户端健康与租约。
- 新status分支按count>0写collecting_ok，非整轮challenge的全失败可能同样写OK；每轮重置state，last_success_ts不持久，状态文件还缺原子写、实例/档案绑定，可能读到旧auth_expired。
- 上轮登录误判、待执行任务在auth_event后仍开始、GUI红字被覆盖和守护单例/日志/停止问题，未有合格回归证据。
- 新增stealth/去自动化特征参数超出既有明确边界：不得执行，不因默认关闭称可接受；要求移除，不再次尝试已确认验证路径。保留本人正常会话与手动登录流程，网站验证不等同登录过期。

## 本轮正式版运行验收

- 原生/GUI/监控/桥接51测试通过。
- 实际Buybox页面组件17断言通过。
- 真实Chromium、隔离API，11页×9窗口99次运行检查通过，无pageerror/整页横向溢出/未声明API。数据替身验证不是生产交易或本机采集验收。
- Pages38727344605成功，Railway提交状态成功，公开前端GET200、未认证collector/status401。
- Railway控制台当前部署1fcc5cad-7319-4abb-a1ec-878c25df4395/实例62b856ca显示“This session is active in another tab”；未抢占另一个会话，故本轮没有新的生产监控读数。不把17:03云端成功/17:05本机验证页等旧证据改称本轮新证据。
- 本机GUI→正确SKU/变体报价→successful→网页同轮，以及卖家身份/原Mac追踪仍未验证。WorkBuddy未交付对应证据；交接不等于解决。

本轮只更新审核交接文档，不为保持活动改动正式业务代码。
