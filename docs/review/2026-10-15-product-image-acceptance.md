# 图片占位状态管理验收

来源：WorkBuddy `review/2026-10-15-round6-f2f3-image-offload` 的前端改动（`3157c54`，后续文档提交 `ccee448`）。
Codex 从 main `4936083` 只提取6个 src 文件，不合并旧候选的后端文件，保留 PR89 的运行保护。

## 修复及验证

- 移除图片错误回调中直接替换 React 管理 DOM 的做法，改为 `ProductImage` 状态组件。
- 源地址改变时清除旧失败状态；相同坏地址不因父组件重渲染反复请求。
- 库存、产品列表/分支、创建预览及详情统一状态占位与懒加载；装箱保留原状态机，补图像错误状态。
- Codex 独立执行 `scripts/test-product-image.cjs`：26项断言通过，覆盖失败、换URL、重复失败、卸载/重建及StrictMode，零React警告。
- Codex `npm run build` 通过。WorkBuddy另报告其隔离后端和真实Chromium页面20/20回归通过，此项来源为对方报告，并非Codex直接操作用户Windows。

组件测试使用 jsdom，不加载网络图片，不代表真实CDN已恢复或正式采集已验收。
测试不增加应用依赖，可用独立临时测试目录安装 `jsdom@26.1.0`，以 `NODE_PATH=<该目录>/node_modules node scripts/test-product-image.cjs` 执行；仓库已安装依赖中提供 React、ReactDOM 和 TypeScript。

## 仍未验收

- CDN图片访问恢复；占位只保证失败如实呈现，不能宣称恢复图片。
- 正式GUI → 生产后端 → 线上网页同轮采集。WorkBuddy已确认现有Windows GUI配置存在，应校验程序版本并正常复用，而不是索取或解密凭据。
- 网页打开自动采集一轮为用户要求的保留功能；单SKU限制仅用于受控验收，不应删除正常调度。

不触发真实调价、采购或额外采集；未读取密钥、Cookie或profile。
