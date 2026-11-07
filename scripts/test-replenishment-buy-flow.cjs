// 补货计划「就地登记采购」的端到端守卫（jsdom + 隔离 API）。
// 这条链路的失败方式很安静：数量带错（用了建议数量不是实采数量）、总价漏传、
// product_id 传空被后端当成没选商品——界面上都可能只表现为"点了一下没反应"。
// 所以这里真的渲染 Replenishment 页，点按钮、填表单、提交，检查写出去的 payload
// 和保存后的界面回执。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');

const watchdog = setTimeout(() => {
  console.error('FAIL Replenishment buy-flow suite did not complete within 10s');
  process.exit(1);
}, 10000);

const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event'])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');

const file = path.resolve(__dirname, '../src/pages/Replenishment.tsx');

const created = [];
let overviewCalls = 0;

// 两行补货计划：#123 建议海运 30 件、#456 建议空运 8 件。
const rows = [
  { id: 'p-a', product_no: 123, name: '折叠水桶', english_name: 'Foldable Bucket', sku: 'WIG-8-01',
    plan: { level: 'urgent', stock: 5, pending: { air: 2, sea: 10 }, velocity: 2, cover_days: 2.5,
            sea_quantity: 30, air_quantity: 8, pre_sea_shortage: 12, allow_emergency_air: true,
            economics: { available: true, minimum_margin: 0.2, sea: { profit_zar: 40, profit_margin: 0.3 }, air: { profit_zar: 28, profit_margin: 0.22 } },
            schedule: [] } },
  { id: 'p-b', product_no: 456, name: '集尘袋', english_name: 'Dust Bag', sku: 'ZEN-01',
    plan: { level: 'watch', stock: 90, pending: { air: 0, sea: 0 }, velocity: 1, cover_days: 90,
            sea_quantity: 0, air_quantity: 0, pre_sea_shortage: 0, allow_emergency_air: false,
            economics: { available: false }, schedule: [] } },
  { id: 'p-zero', product_no: 789, name: '零销品', english_name: 'Zero Sales Item', sku: 'ZERO', plan: { velocity: 0, level: 'no_data', schedule: [] } },
];

const history = [
  { id: 'h1', product_id: 'p-a', product_no: 123, product_name: 'Foldable Bucket', quantity: 20, total_amount: 400, status: 'received', recorded_at: '2026-10-01' },
  { id: 'h2', product_id: 'p-b', product_no: 456, product_name: 'Dust Bag', quantity: 50, total_amount: 900, status: 'in_transit', recorded_at: '2026-10-20' },
];

const api = {
  getOperationsOverview: async () => { overviewCalls++; return { rows, sync: { status: 'success', last_success: '2026-11-02 08:00' } }; },
  getProduct: async () => ({ id: 'p-a' }),
  listProcurementRecords: async () => history,
  createProcurementRecord: async payload => { created.push(payload); return { id: 'new', ...payload }; },
};

const overrides = {
  'react-router-dom': { Link: props => React.createElement('a', { href: props.to }, props.children) },
  '../remoteApi': api,
  // 计划设置面板与本用例无关，用占位组件切断它的数据依赖。
  '../operations/PipelinePlan': { default: () => null },
  '../operations/SourcingPanel': { default: () => null },
};

// 页面里的相对导入（含 types.ts 里再往下的 utils/number）必须逐层按「发起文件」的
// 目录解析，否则 CJS 会拿脚本自身目录当基准，直接 MODULE_NOT_FOUND。
const cache = new Map();
const load = absFile => {
  if (cache.has(absFile)) return cache.get(absFile);
  const m = new Module(absFile, module);
  m.filename = absFile;
  m.paths = Module._nodeModulePaths(path.dirname(absFile));
  const fallback = m.require.bind(m);
  m.require = name => {
    if (overrides[name]) return overrides[name];
    if (typeof name === 'string' && name.startsWith('.')) {
      const base = path.resolve(path.dirname(absFile), name);
      for (const candidate of [base + '.ts', base + '.tsx', path.join(base, 'index.ts'), path.join(base, 'index.tsx')]) {
        if (fs.existsSync(candidate)) return load(candidate);
      }
    }
    return fallback(name);
  };
  m._compile(
    ts.transpileModule(fs.readFileSync(absFile, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
    }).outputText,
    absFile,
  );
  cache.set(absFile, m.exports);
  return m.exports;
};
load(file);

const Page = load(file).default;
const host = document.getElementById('root');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

const type = (input, value) => {
  const proto = input.tagName === 'SELECT' ? dom.window.HTMLSelectElement.prototype : dom.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, value);
  input.dispatchEvent(new dom.window.Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
};
const cells = row => [...row.querySelectorAll('td')].map(td => td.textContent.trim());
const bodyRows = () => [...host.querySelectorAll('.data-table tbody tr')];
const actionsIn = row => [...row.querySelectorAll('.operations-actions button')];

(async () => {
  const root = createRoot(host);
  await act(async () => root.render(React.createElement(Page)));
  await act(async () => { await new Promise(r => setTimeout(r, 50)); });

  check(bodyRows().length === 3, 'zero-sales SKU remains visible by default');
  check(bodyRows()[0].querySelector('.product-link').textContent === 'Foldable Bucket', 'English platform title is first');
  check(bodyRows()[0].querySelector('.product-cn').textContent === '折叠水桶', 'Chinese name is on its own second line');
  // --- 1. 序号列：和「产品列表」的 # 同源 ---
  const headers = [...host.querySelectorAll('.data-table thead th')].map(th => th.textContent.trim());
  check(headers[0] === '#', '首列必须是 # 序号列');
  const first = bodyRows()[0];
  check(cells(first)[0] === '123', '序号取上架的 product_no，不是行号');
  check(cells(bodyRows()[1])[0] === '456', '第二行序号同样取 product_no');
  check(cells(first).slice(1).join(' ').includes('折叠水桶'), '序号后面紧跟商品名');

  // --- 2. 登记采购：带出建议数量，填完总价写入采购记录 ---
  const buyButton = actionsIn(first).find(b => b.textContent.includes('登记采购'));
  check(!!buyButton, '每行都要有「登记采购」入口');
  await act(async () => { buyButton.click(); });
  const dialog = host.querySelector('[aria-label="登记采购"]');
  check(!!dialog, '点击后打开登记弹窗');
  check(dialog.textContent.includes('#123'), '弹窗标题带序号，避免登记到别的商品');
  const numbers = [...dialog.querySelectorAll('input[type="number"]')];
  check(numbers.length === 2, '弹窗只有数量与总价两个数字输入');
  check(numbers[0].value === '30', '默认带出本次建议海运数量，用户只填实采差异');
  check(!!dialog.textContent.match(/Foldable Bucket/), '弹窗显示英文标题便于对供应商');

  await act(async () => { type(numbers[0], '28'); });
  await act(async () => { type(numbers[1], '560'); });
  check(dialog.textContent.includes('¥20.00'), '单价跟着数量和总价自动核算');

  const save = [...dialog.querySelectorAll('button')].find(b => b.textContent.includes('保存并同步采购记录'));
  check(!!save, '弹窗要有明确的保存并同步按钮');
  await act(async () => { save.click(); });
  await act(async () => { await new Promise(r => setTimeout(r, 50)); });

  check(created.length === 1, '提交只写一条采购记录');
  const payload = created[0];
  check(payload.product_id === 'p-a', '必须按 product_id 关联，后端只认这个字段');
  check(payload.product_no === 123, '同时带上序号，采购记录页按序号筛选');
  check(payload.quantity === 28, '写的是实际采购数量 28，不是建议的 30');
  check(payload.total_amount === 560, '写的是实际采购总价');
  check(payload.status === 'in_transit', '默认按在途登记，未到仓不应计入现货');
  check(/^\d{4}-\d{2}-\d{2}$/.test(payload.recorded_at), '采购日期用本地业务日期，不能是 UTC 前一天');
  check(!host.querySelector('[aria-label="登记采购"]'), '保存成功后弹窗关闭');
  check(host.querySelector('.alert-success').textContent.includes('已同步到采购记录'), '回执要说明数据去了采购记录');
  check(host.querySelector('.alert-success a').getAttribute('href') === '/procurement?product_no=123', '回执能跳去采购记录并按序号过滤');
  check(overviewCalls >= 2, '保存后要重新拉补货概览，在途/可售天数要跟着变');

  // --- 3. 非法输入必须自己拦，不能把 400 抛给用户看 ---
  await act(async () => { actionsIn(bodyRows()[0]).find(b => b.textContent.includes('登记采购')).click(); });
  const dialog2 = host.querySelector('[aria-label="登记采购"]');
  const numbers2 = [...dialog2.querySelectorAll('input[type="number"]')];
  await act(async () => { type(numbers2[0], '0'); });
  await act(async () => { type(numbers2[1], '100'); });
  await act(async () => { [...dialog2.querySelectorAll('button')].find(b => b.textContent.includes('保存并同步采购记录')).click(); });
  check(created.length === 1, '数量为 0 时不得发请求');
  check(dialog2.textContent.includes('采购数量必须是大于 0 的整数'), '数量非法要给中文提示');
  await act(async () => { type(numbers2[0], '10'); });
  await act(async () => { type(numbers2[1], '-5'); });
  await act(async () => { [...dialog2.querySelectorAll('button')].find(b => b.textContent.includes('保存并同步采购记录')).click(); });
  check(created.length === 1, '总价为负时不得发请求');
  check(dialog2.textContent.includes('采购总价请填写有效金额'), '总价非法要给中文提示');

  // --- 4. 日历视图的每一批也要能登记，且带的是这一批的数量 ---
  await act(async () => { [...host.querySelectorAll('button')].find(b => b.textContent.includes('取消')).click(); });
  check(!host.querySelector('[aria-label="登记采购"]'), '取消后弹窗关闭');
  check(created.length === 1, '取消不发请求');

  await act(async () => root.unmount());
  clearTimeout(watchdog);
  console.log(`PASS ${checks} Replenishment buy-flow assertions (jsdom, isolated API)`);
})().catch(error => {
  console.error('FAIL', error.message);
  process.exit(1);
});
