# 2026-10-15 18:34 Codex 运行验收与候选审核

基准 main a4445c356047cfa85069ad5bacf15c2a85834e40；WorkBuddy候选061148c543，仍位于已关闭PR94旧基准分支，未合并。

## 实际运行结果

| 项目 | 环境/复现 | 预期与实际 | 结论 |
| --- | --- | --- | --- |
| 老库存重复导出 | 最新main路由，内存SQLite，隔离文件生成；record_shipment=False，同草稿及新草稿重复 | 路由允许，但旧test_export_only仍要求clearance记录及第二草稿409，实际NoResultFound | 过期测试已修；业务行为不变 |
| 历史清理标记 | 同环境预置old_inventory_clearance_used=1再导出 | 仍200，不读库存、不写发货/库存调整/操作记录、不改变历史标记 | 通过 |
| 后端回归 | 同步最新main变化文件，仅更新上述过期测试 | 229通过、1跳过（缺Chromium）；不把跳过计通过 | 通过/限制已记 |
| Buybox页面 | 实际组件+jsdom、隔离API | 17断言通过 | 非真实Windows端到端 |
| 前端构建 | tsc/Vite | 通过 | 不等同业务验收 |
| 上线烟雾 | 实际公开站点GET、未认证collector/status | 200/401，既有main Pages attempt2及Railway均success | 通过 |

未创建生产订单、导出业务单或触发采集、改价。文件生成在替身中隔离，不能据此声称第三方ERP实导入通过。

## 候选会话失效检测：未采纳

AST提取候选真实is_auth_expired/round_once函数，以假页面/API执行，不联网、不访问profile或凭据。
- 正常商品URL参数含?return=/login、无登录表单，错误返回True。
- 正常商品标题Sign In Visitor Book - Takealot.com、无登录表单，错误返回True。
- 真实/account/login正例True；普通商品False。需受信域名/路径及可见主表单的明确证据，不能仅标题、导航入口判断会话失效。
- 四任务中第0项触发auth_expired；第2/3项获得信号量后仍调用scrape，彼时auth_event已经True。需阻止未开始任务、释放租约。
- 静态发现GUI AUTH_EXPIRED红字被紧随普通SKU日志改黑覆盖；需保持终止原因并测试真实poll队列。
- 300秒退避不等于停止已确认验证路径；网站验证与登录过期应分开。不得采用候选讨论中的headless指纹规避。
- 上轮variant_controls、GUI保护、守护单例/停止/永久错误重试整改仍待；不采纳旧分支整包。

已在PR94评论6190136045提供复现、修改要求及验收标准；PR87继续作为正式协作入口。要求下一份候选基于实时main新PR，补GUI同轮及WB-BUYBOX-IDENTITY-01证据。交接不算完成。

## 只读生产复核

Railway当前部署be33b0e8-3e0c-46a7-99d4-2c48bdbc931a，实例217dba56；只查询两SKU监控白名单字段，rollback/close。
- 吸尘袋9902348624058：当前error/site_challenge；最后尝试08:36:01Z（北京时间16:36），HTTP200/Just a moment/0卡。旧R269来自04:54:58Z（12:54）cloud_browser。
- 相机9902348621330：当前error/site_challenge；最后成功09:03:31Z（17:03）R1999/cloud_browser；最后尝试09:05:47Z（17:05）local_collector，HTTP200/Just a moment/0卡。
- 两项holding_buybox=null、enabled=false/request_once=false。来源与失败尝试分离正确保留；这些记录不能证明本机稳定成功或GUI按钮闭环。
- 比上轮新增的明确事实为相机本机路径失败记录；未重新访问Takealot，不把HTTP200当报价成功，不把旧价当新价。

未验证：用户Windows实际GUI同轮回传/网页、卖家身份、原Mac追踪、真实浏览器DOM跳过项、实际第三方ERP导入。缺本机权限的验证继续由WorkBuddy提供脱敏证据，Codex审核后再决定发布。
