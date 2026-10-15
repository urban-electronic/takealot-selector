# 23:33复核：自主采集候选明确失败，未部署

正式版基准fd061b22（PR101）。WorkBuddy在已关闭PR94继续旧分支，最新2be9efde7c0851d14f89e76b6a74cae0d07e1847；前一说明689db4d声称缺库存导致领0已修。候选包含未采纳的b005旧修改，不能整包覆盖main。

## 实际执行结果

全部使用本环境隔离SQLite/函数替身，无Windows操作、无真实浏览器访问Takealot、无密钥/profile/生产写入。CLI仅AST提取main，系统动作全部替身，未运行候选stealth或计划任务。

| 触发条件 | 预期 | 实际 | 结论 |
| --- | --- | --- | --- |
| run_background.main，argv=start | 参数解析后调用启动动作 | ArgumentError: conflicting subparser:start，动作0次 | 失败；start/install仍重复add_parser |
| 两个无monitor目录SKU，库存分别5和0；local_collector、Autonomous=1 | 保留库存过滤和原授权任务范围 | 实际返回one/5、two/0，零库存也领走 | 失败；不能靠扩大全目录范围证明原领0根因 |
| 上述首任务offers buyboxPrice100 | successful=1且首次成功标记存在 | successful1，但LAST_SUCCESS_KEY行数0 | 失败；update仍无insert |
| 读取候选成功记录 | 保留现网来源及推定字段 | source=local_collector，last_attempt_source=None、price_ownership=None | 回退PR91/101；未修复 |

静态复核：候选collector.py移除了main的variantControls→diagnostics.variant_controls透传；仍有stealth/去自动化参数、登录检测过宽与count>0即collecting_ok等旧问题。没有执行这些分支来规避验证。

候选新测试只是证明“自主领取未配置SKU”和“缺库存也排队”的新行为；不能证明符合原任务边界，也不是Windows正式GUI同轮验收。

## 归因校正与交接

已在PR94评论6195960947退回具体复现。要求从实时main独立PR，不覆盖旧整文件，不安装新守护，不以重装解决。Mac报告已PR100归档；变体门控已上线；价格推定已PR101上线，不能以旧分支回退后称缺口未补。

- 库存缺失可能影响其他商品，但20:44只读catalog中两个目标SKU库存6/11且已queued/request_once/next_due0，不支持其唯一根因判断。
- 网页入口/api/buybox/page-open与扩展/api/collector/web-open不同；native回传不会自动经过云process重新读取库存。
- 要求Windows提供正在运行版本、已有日志、products返回数量、开始/取消/release/异常、offers successful及网页同轮结果；不得打印token/请求头或重复请求已确认验证页。
- 如补row=None容错，先证明现有opt-in路径的真实触发条件，作为最小修复，不绑定全目录自主模式。保留暂停/库存/租约/来源/价格核验。

## 运行验收分别记录

1. **正式版隔离回归通过**：245后端无跳过、Chromium99窗口、Buybox32和图片26断言。最初测试Chromium SIGSEGV导致244+1skip及窗口启动失败，恢复独立测试浏览器后已实际重跑，不把缺失算通过。
2. **部署检查通过**：Pages38755380701、Railway fd061b22成功；控制台部署05c298a1-7549-415d-b443-17ecdb2a27aa、实例ae12ca8f在线。公开前端200、未认证collector/status401另记PR。
3. **最后真实尝试失败，当前无新执行报价**：15:37:53Z（北京23:37:53）只读29个monitor，最新有效成功仍09:05:42Z。吸尘袋最后成功04:54:58Z/R269，最后尝试08:36:01Z为site_challenge；相机最后成功09:03:31Z/R1999，最后尝试09:05:47Z为site_challenge。两者当前queued/request_once=true、enabled=false、price_ownership为空。
4. **本机同轮验收尚未完成执行**：本轮没有新attempt/quote；只有local_collector心跳15:37:46Z。不能将此刻queued称为刚执行失败，也不能称成功；网站验证失败是明确的历史最后尝试结果。WorkBuddy未提供新的Windows运行证据，所缺的是完整领取→执行→回传→网页时序，不是权限再次确认。

本轮仅更新审核交接文档，未采纳候选、未改业务代码或真实售价；不触发生产采集来刷新时间，不把心跳当采集成功。后续报告明确使用“通过 / 失败 / 未执行及缺少证据”，避免“未验收成功”含糊表述。
