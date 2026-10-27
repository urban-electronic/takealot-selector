// 用真实 ProductList 组件跑一遍「同步卖家后台」：预览 → 确认 → 刷新普通列表。
// 不连后端、不写任何业务数据，seller 资料全是本地 fixture。
//
// 这组断言对应的是用户明确提的那个要求：同步只改商品主档，展示仍是原来那张表，
// 不再另开一套核对卡片、TSIN 输入框或独立结果页。
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const ts = require('typescript'), { JSDOM } = require('jsdom');
const watchdog = setTimeout(() => { console.error('FAIL seller sync suite did not complete within 10s'); process.exit(1); }, 10000);
const dom = new JSDOM('<header class="app-header"></header><div id="root"></div>', { url: 'https://example.invalid/products' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'localStorage']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.alert = () => {};
const React = require('react'), { act } = React, { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');

let listCalls = 0, listFailure = false;
const api = { getProducts: async () => { listCalls += 1; if (listFailure) throw Error('fixture list offline'); return []; }, getImageUrl: x => x, updateProduct: () => { throw Error('sync suite must not write'); } };
let source = 'remote', previewCalls = 0, appliedTokens = [], pendingPreview = [];
const remote = {
  previewSellerSync: () => { previewCalls += 1; return new Promise((resolve, reject) => pendingPreview.push({ resolve, reject })); },
  applySellerSync: token => { appliedTokens.push(token); return Promise.resolve({ ...PLAN, applied: { created: 1, updated: 1, renamed: 1, moved: { inventory_adjustments: 2 } } }); },
  getStoreStorageKey: x => x, readStoreStorage: x => localStorage.getItem(x),
  getActiveStoreId: () => 'default-store', getStores: () => Promise.resolve([]),
};
function localModule(name, fromFile) { if (typeof name !== 'string' || !name.startsWith('.')) return null; for (const ext of ['.ts', '.tsx']) { const target = path.resolve(path.dirname(fromFile), name + ext); if (!fs.existsSync(target)) continue; const m = new Module(target, module); m.filename = target; m.paths = Module._nodeModulePaths(path.dirname(target)); const orig = m.require.bind(m); m.require = n => localModule(n, target) || orig(n); m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target); return m.exports; } return null; }
function compile(file, mocks = {}) { const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file)); const orig = m.require.bind(m); m.require = n => n in mocks ? mocks[n] : (localModule(n, file) || orig(n)); m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, file); return m.exports; }
const types = compile(path.resolve(__dirname, '../src/types.ts'));
const page = compile(path.resolve(__dirname, '../src/pages/ProductList.tsx'), { '../DataSourceContext': { useApi: () => api, useDataSource: () => ({ dataSource: source }) }, '../api': { openUrl: () => {} }, '../remoteApi': remote, '../types': types, '../utils/imageFallback': { ProductImage: () => null } }).default;

const PLAN = {
  stats: { seller_offer_count: 3, seller_group_count: 3, local_product_count: 2, update_count: 1, create_count: 1, unchanged_count: 0, skip_count: 1, rename_count: 1 },
  updates: [{ plid: '104036518', product_id: 'p1', product_no: 1, name: 'Fixture', matched_by: 'plid', warnings: [], changes: [{ field: 'sku', label: 'SKU', value: 'W-S-12-04', current: '9902357746192' }] }],
  creates: [{ plid: '104410843', sku: 'W-S-12-02', tsin: '104410843', product_name: 'Fixture New', product_image_url: '', takealot_url: 'https://www.takealot.com/x/PLID104410843', actual_sale_price_zar: 299 }],
  skipped: [{ plid: '96097290', skus: ['9901', '9902'], reason: '多规格无法自动对应，请人工核对' }],
  token: 'fixture-token', truncated: false,
};

let root = createRoot(document.getElementById('root'));
const button = s => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === s);
const click = b => b.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
const body = () => document.body.textContent;

(async () => {
  const render = () => root.render(React.createElement(MemoryRouter, { initialEntries: ['/products?custom_sku=1'] }, React.createElement(page)));
  await act(async () => render());

  // 1. 筛空不再是「核对卡片」，就是普通空态 + 一个同步入口。
  assert.equal(previewCalls, 0, '进入筛空状态不应自动拉卖家资料（一次读取最多 5 页，很贵）');
  assert.match(body(), /当前没有匹配的自建 SKU/);
  assert.equal(document.querySelector('.seller-coverage-empty'), null, '不许再渲染独立的核对卡片');
  assert.equal(document.querySelector('#coverage-tsin'), null, '不许再放单独的 TSIN 核对输入框');
  const entry = button('同步卖家后台');
  assert.ok(entry, '空态里要有同步入口');
  assert.equal(entry.disabled, false);

  // 2. 预览：列出将更新 / 新增 / 跳过，跳过要带原因。
  await act(async () => click(entry));
  assert.equal(previewCalls, 1, '点一次只读一次卖家资料');
  await act(async () => pendingPreview.shift().resolve(PLAN));
  const dialog = document.querySelector('[aria-label="同步卖家后台预览"]');
  assert.ok(dialog, '预览要弹窗');
  assert.match(dialog.textContent, /将更新 1 个、新增 1 个，跳过 1 个/);
  assert.match(dialog.textContent, /多规格无法自动对应/, '跳过项必须连原因一起列出来');
  assert.match(dialog.textContent, /9902357746192/, '更正 SKU 要显示旧值');
  assert.match(dialog.textContent, /采购成本、库存、利润不会被写入/, '必须说清哪些字段不会被写');

  // 3. 确认同步：带预览 token，完成后刷新普通列表并给出结果。
  const before = listCalls;
  await act(async () => click([...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('确认同步'))));
  assert.deepEqual(appliedTokens, ['fixture-token'], '写入必须带预览 token');
  assert.equal(listCalls, before + 1, '同步完要刷新现有列表');
  assert.equal(document.querySelector('[aria-label="同步卖家后台预览"]'), null, '成功后关闭弹窗');
  assert.match(body(), /同步完成：新增 1 个商品，更新 1 个商品/);
  assert.match(body(), /采购成本、库存和利润没有被写入/);

  // 4. 本地数据源不显示同步入口（卖家资料只有远程库有）。
  source = 'local';
  await act(async () => render());
  assert.equal(button('同步卖家后台'), undefined, '本地数据源不应出现同步入口');
  source = 'remote';
  await act(async () => render());
  assert.ok(button('同步卖家后台'), '切回远程后入口要回来');

  // 5. 列表读失败 ≠ 筛空：不能把「读不到」说成「没有商品」。
  // 换一个根节点重挂：只改 fixture 不会重跑列表请求（依赖数组没变）。
  await act(async () => root.unmount());
  listFailure = true;
  root = createRoot(document.getElementById('root'));
  await act(async () => render());
  assert.match(body(), /商品列表读取失败/);
  assert.equal(body().includes('暂无产品记录'), false, '读失败不能显示成空列表');
  listFailure = false;
  await act(async () => click(button('重新读取列表')));
  assert.equal(body().includes('商品列表读取失败'), false, '重试成功要清掉错误');
  assert.match(body(), /当前没有匹配的自建 SKU/);

  await act(async () => root.unmount());
  console.log('PASS seller sync page: plain empty state, one preview read, token-bound apply, list refresh, source isolation, read failure != empty; zero business writes');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
