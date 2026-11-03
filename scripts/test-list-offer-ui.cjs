// 用真实 ProductList 组件跑一遍「上架」：单条按钮、勾选批量、RRP 1.5 倍口径。
// 不连后端：remoteApi.listProductOffer 是本地桩，断言的是前端传下去的 payload
// 与回执文案，真正的写接口口径由 backend/tests/test_list_offer.py 兜住。
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const ts = require('typescript'), { JSDOM } = require('jsdom');
const watchdog = setTimeout(() => { console.error('FAIL list offer suite did not complete within 10s'); process.exit(1); }, 10000);
const dom = new JSDOM('<header class="app-header"></header><div id="root"></div>', { url: 'https://example.invalid/products' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'localStorage']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.alert = () => {};
const React = require('react'), { act } = React, { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');

const PRODUCTS = [
  { id: 'p1', product_no: 617, product_name: 'Fixture A', sku: '9900000000011', actual_sale_price_zar: 200, link_status: '未购买', variants: null },
  // 一个产品挂两个 SKU：勾选是 SKU 粒度，批量必须按产品去重，否则第二条必然 409。
  { id: 'p2', product_no: 618, product_name: 'Fixture B', sku: '9900000000022\n9900000000033', actual_sale_price_zar: 350, link_status: '未购买', variants: null },
];

const listCalls = [], listFailures = [];
const api = {
  getProducts: async () => PRODUCTS,
  getImageUrl: x => x,
  updateProduct: () => { throw Error('list offer suite must not write products'); },
  listProductOffer: (id, options) => {
    listCalls.push({ id, options });
    if (listFailures.includes(id)) return Promise.reject(Error('本店已在变体 990... 有报价'));
    return Promise.resolve({ ok: true, product_id: id, sku: '9902739445897', offer_id: 'offer-1', status: 'Not Buyable', selling_price: 200, rrp: 300, variant: { barcode: '6009512345678' } });
  },
};
const source = 'remote';
const remote = { getStoreStorageKey: x => x, readStoreStorage: x => localStorage.getItem(x), getActiveStoreId: () => 'default-store', getStores: () => Promise.resolve([]), listProductOffer: api.listProductOffer };

function localModule(name, fromFile) { if (typeof name !== 'string' || !name.startsWith('.')) return null; for (const ext of ['.ts', '.tsx']) { const target = path.resolve(path.dirname(fromFile), name + ext); if (!fs.existsSync(target)) continue; const m = new Module(target, module); m.filename = target; m.paths = Module._nodeModulePaths(path.dirname(target)); const orig = m.require.bind(m); m.require = n => localModule(n, target) || orig(n); m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target); return m.exports; } return null; }
function compile(file, mocks = {}) { const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file)); const orig = m.require.bind(m); m.require = n => n in mocks ? mocks[n] : (localModule(n, file) || orig(n)); m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, file); return m.exports; }
const types = compile(path.resolve(__dirname, '../src/types.ts'));
const page = compile(path.resolve(__dirname, '../src/pages/ProductList.tsx'), { '../DataSourceContext': { useApi: () => api, useDataSource: () => ({ dataSource: source }) }, '../api': { openUrl: () => {} }, '../remoteApi': remote, '../types': types, '../utils/imageFallback': { ProductImage: () => null } }).default;

const root = createRoot(document.getElementById('root'));
const button = s => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === s);
const buttonStarting = s => [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(s));
const click = b => b.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
const body = () => document.body.textContent;

(async () => {
  const render = () => root.render(React.createElement(MemoryRouter, { initialEntries: ['/products'] }, React.createElement(page)));
  await act(async () => render());
  await act(async () => { await new Promise(r => setTimeout(r, 0)); });

  // 1. 每行都有单条「上架」，未勾选时批量按钮禁用且不显示数量。
  assert.ok(document.querySelectorAll('td button').length > 0);
  const rowListButton = [...document.querySelectorAll('td button')].find(b => b.textContent.trim() === '上架');
  assert.ok(rowListButton, '每行都要有单条上架按钮');
  assert.equal(rowListButton.disabled, false);
  const batchIdle = buttonStarting('批量上架');
  assert.ok(batchIdle, '工具栏要有批量上架入口');
  assert.equal(batchIdle.disabled, true, '没勾选时批量上架不可点');

  // 2. 单条上架：立即提交，倍数走 1.5，回执带平台分配的 SKU。
  await act(async () => { click(rowListButton); await new Promise(r => setTimeout(r, 0)); });
  assert.equal(listCalls.length, 1, '点一下只提交一次');
  assert.equal(listCalls[0].id, 'p1');
  assert.equal(listCalls[0].options.rrp_multiplier, 1.5, '前端传下去的倍数必须是 1.5，不是 1.2');
  assert.match(body(), /#617 已上架/, '单条要留在 banner 里给回执');
  assert.match(body(), /SKU 9902739445897/);

  // 3. 勾选全部：两个产品共 3 个 SKU，批量按钮按产品去重后应是 2。
  const selectAll = document.querySelector('input[title="全选当前页"]');
  assert.ok(selectAll, '表头要有全选');
  await act(async () => { click(selectAll); });
  const batch = buttonStarting('批量上架');
  assert.match(batch.textContent, /批量上架 \(2\)/, '一个产品的多个 SKU 只算一次上架');

  // 4. 批量先确认再提交：确认框要说清 RRP 倍数与不预售，取消不发请求。
  const before = listCalls.length;
  await act(async () => click(batch));
  const dialog = document.querySelector('[aria-label="批量上架确认"]');
  assert.ok(dialog, '批量上架要弹确认');
  assert.match(dialog.textContent, /售价 ×1\.5/, '确认框要写明 RRP 口径');
  assert.match(dialog.textContent, /不预售/);
  assert.equal(listCalls.length, before, '弹窗阶段不能已经发请求');
  await act(async () => click([...dialog.querySelectorAll('button')].find(b => b.textContent.trim() === '取消')));
  assert.equal(document.querySelector('[aria-label="批量上架确认"]'), null);
  assert.equal(listCalls.length, before, '取消后一条都不能发');

  // 5. 确认后逐条提交，其中一条失败也要继续跑完并点名。
  listFailures.push('p2');
  await act(async () => click(buttonStarting('批量上架')));
  await act(async () => click([...document.querySelector('[aria-label="批量上架确认"]').querySelectorAll('button')].find(b => b.textContent.trim() === '确认上架')));
  await act(async () => { await new Promise(r => setTimeout(r, 0)); });
  assert.equal(listCalls.length, before + 2, '两个产品各提交一次');
  assert.deepEqual(listCalls.slice(before).map(c => c.id), ['p1', 'p2'], '按列表顺序逐条提交');
  assert.ok(listCalls.slice(before).every(c => c.options.rrp_multiplier === 1.5));
  assert.match(body(), /批量上架完成：成功 1 个，失败 1 个/, '失败不能被吞掉');
  assert.match(body(), /有报价/, '失败要带平台给的原因');

  clearTimeout(watchdog);
  console.log('PASS list offer ui');
  process.exit(0);
})().catch(e => { clearTimeout(watchdog); console.error('FAIL', e); process.exit(1); });
