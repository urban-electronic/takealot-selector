// 补货计划「序号列 + 就地登记采购」的守卫。
// 这两条链路的失败方式都很安静：序号列漏了只是少一列；采购少传 product_id 会被后端
// 当成「请选择产品」400，或者写到别的商品名下，界面上看起来却"保存成功"。
// 所以这里把 UI 结构、写接口的字段、跳转参数、以及后端接收模型逐环钉住。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// 工作区可能被 core.autocrlf 检出成 CRLF，统一归一化后再匹配。
const read = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8').replace(/\r\n/g, '\n');
const page = read('src/pages/Replenishment.tsx');
const procurement = read('src/pages/Procurement.tsx');
const routes = read('backend/api/procurement_routes.py');
const opsRoutes = read('backend/api/operations_routes.py');
const css = read('src/index.css');

const checks = [
  // --- 序号列：补货计划要和「产品列表」用同一个编号对账 ---
  [page, /<th className="operations-no">#<\/th>/, '两种视图的表头都要有 # 序号列'],
  [page, /<td className="operations-no">\{r\.product_no \?\? '—'\}<\/td>/, '序号单元格要取 product_no，缺失时显示占位符'],
  [opsRoutes, /product_no=p\.product_no/, 'overview 必须把 product_no 带出来，否则前端无号可显示'],
  [css, /\.data-table \.operations-no \{/, '序号列要有独立样式（窄列、等宽数字）'],
  [css, /th:nth-child\(2\)[\s\S]{0,200}left: 44px;/, '窄屏把商品列钉在序号列右侧，不能盖住序号'],

  // --- 就地登记采购：写入的是采购记录，不是第二套账 ---
  [page, /createProcurementRecord/, '要调用采购记录写入接口'],
  [page, /product_id: buy\.row\.id/, '必须带 product_id，后端只认它反查商品'],
  [page, /quantity: qty/, '要写采购数量'],
  [page, /total_amount: amount/, '要写采购总价'],
  [page, /status: buy\.status/, '要写到货状态'],
  [page, /recorded_at: buy\.date/, '要写采购日期，否则记录一律落到今天'],
  [routes, /class ProcurementRecordCreate[\s\S]{0,400}total_amount: float/, '后端接收模型要含 total_amount，字段名不能靠猜'],
];

// 前端提交前必须自己拦一次，否则后端 400 只能看到英文 detail。
assert.match(page, /采购数量必须是大于 0 的整数/, '数量非法要给出可读提示');
assert.match(page, /采购总价请填写有效金额/, '总价非法要给出可读提示');
// 写完要刷新 overview：在途/可售天数按新采购重算，形成闭环。
assert.match(page, /await refresh\(\);/, '保存成功后要刷新补货概览');
// 成功后给一条回执，并能跳到采购记录核对。
assert.match(page, /已同步到采购记录/, '成功提示要说明数据去向');
assert.match(page, /to=\{`\/procurement\?product_no=\$\{notice\.no\}`\}/, '回执要能带序号跳到采购记录');
assert.match(procurement, /useSearchParams/, '采购记录页要能读 URL 参数');
assert.match(procurement, /searchParams\.get\('product_no'\)/, '要按 product_no 初始化过滤条件');
// 历史采购要按 product_id 过滤，避免把同序号不同商品的记录混进来。
assert.match(page, /r\.product_id === row\.id/, '历史采购按 product_id 匹配');
// status 只是标记，不会自动加库存——文案不能暗示"入库即计入可用库存"。
assert.match(page, /已到仓（需在商品设置里更新现货）/, '到货状态文案不能承诺自动改库存');
assert.match(opsRoutes, /purchases_for\(db, store, p\.id\)/, '补货计划要读采购记录，否则登记完看不见');

let count = 0;
for (const [target, rule, message] of checks) {
  if (typeof target === 'string') {
    assert.match(target, rule, message);
  } else {
    assert.ok(rule.test(target), message);
  }
  count += 1;
}
console.log(`${count + 12} replenishment-procurement assertions passed`);
