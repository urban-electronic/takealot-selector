# 视觉审计阻塞直接修复

基线main d3fd260；原脚本来源WorkBuddy PR120/afd72fc，Codex独立修复，不覆盖候选分支，不采纳尚未验收的视觉样式。

已复现：30个goto全部抛错时原main正常返回TOTAL0/0；只拦page-open，packing sync写入可漏过。修复后仅允许本机HTTP前端，复用既有responsive-layout隔离fixture；context级拦截所有页面请求，已声明的GET/HEAD返回fixture，两条页面初始化POST也仅返回fixture，未知API/其他写方法/外部源全部abort并记录失败。无需Key，不注入或读取生产凭据。

每个页面必须出现预期标题，数据页面还必须出现TEST-001并加载完成，测量结构有效后才计入measured。expected/measured/errors和blocked_requests明确输出；缺页、请求违规或低对比退出非0，不能用零问题掩盖零测量。默认态测量不等于全部交互态/真实业务验收。

运行验收：stdlib unittest直接执行真实audit.main的全30页失败路径，确认RuntimeError与measured0/errors30；执行真实context拦截函数10种方法/路径（含packing查询参数、未知写/读、外部URL）及生产base拒绝，3项测试组通过。测试不需要浏览器或网络。已接入npm test:audit-safety和Pages构建前检查。

仍未验证：真实Chromium下全页面渲染、焦点和同窗口名称几何位置，继续由WorkBuddy在隔离fixture中提供证据；本次未连接生产、不采集、不改商品数据或售价。原PR120整包仍待其他范围审查，不以脚本修复宣称视觉全过。
