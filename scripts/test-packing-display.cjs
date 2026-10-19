// 装箱单展示规则：变体中文名不重复、不出现「·」、非中文要标出来；
// 产品列表改完名字要能即时反映到装箱单（catalog:changed）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const watchdog = setTimeout(() => { console.error('FAIL packing suite did not complete within 10s'); process.exit(1); }, 10000);
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid/packing' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'localStorage'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
if (!dom.window.URL.createObjectURL) dom.window.URL.createObjectURL = () => 'blob:stub';

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');
const file = path.resolve(__dirname, '../src/pages/Packing.tsx');

let catalog = [];
let packingReads = 0;
const writes = [];
const api = {
  getProducts: async () => [],
  getImageUrl: (x) => x,
  getProduct: async () => ({}),
};
const remote = {
  getPackingProducts: async () => { packingReads += 1; return catalog.map(r => ({ ...r })); },
  getInventory: async () => [],
  syncPackingProductsFromCatalog: async () => ({ ok: true, total_products: 3, packing_products: catalog.length, imported: 0, skipped_no_sku: 0, duplicate_sku: 0 }),
  upsertPackingProduct: async () => { writes.push('upsert'); return { ok: true }; },
  deletePackingProduct: async () => { writes.push('delete'); return { ok: true }; },
  uploadPackingImage: async () => { writes.push('upload'); return { ok: true }; },
  importFromProducts: async () => ({ ok: true, imported: 0, skipped: 0, failed: [] }),
  syncPackingImagesFromProducts: async () => ({ ok: true, updated: 0, total_pending: 0, failed: [] }),
  syncPackingVariantsFromTakealot: async () => ({ ok: true, groups_checked: 0, matched: 0, images: 0, unresolved: [] }),
  exportPacking: async () => ({ ok: true }),
  fetchPackingImageBlob: async () => { throw new Error('no image in fixture'); },
  getShipments: async () => [],
  exportInboundTemplate: async () => ({ ok: true }),
  getStoreStorageKey: (k) => k,
  readStoreStorage: () => null,
};
function localModule(name, fromFile) {
  if (typeof name !== 'string' || !name.startsWith('.')) return null;
  for (const ext of ['.ts', '.tsx']) {
    const target = path.resolve(path.dirname(fromFile), name + ext);
    if (!fs.existsSync(target)) continue;
    const m = new Module(target, module);
    m.filename = target;
    m.paths = Module._nodeModulePaths(path.dirname(target));
    m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target);
    return m.exports;
  }
  return null;
}
function compile(target, mocks = {}) {
  const m = new Module(target, module);
  m.filename = target;
  m.paths = Module._nodeModulePaths(path.dirname(target));
  const orig = m.require.bind(m);
  m.require = n => n in mocks ? mocks[n] : (localModule(n, target) || orig(n));
  m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target);
  return m.exports;
}
const types = compile(path.resolve(__dirname, '../src/types.ts'));
const Page = compile(file, {
  '../DataSourceContext': { useApi: () => api, useDataSource: () => ({ dataSource: 'remote' }) },
  '../remoteApi': remote,
  '../types': types,
}).default;

const row = (over) => ({
  sku: '9900000000001', name_zh: '', name_en: '', unit: '个', weight: '', material: '',
  brand: '', battery: '', electric: '', magnetic: '', template_row: null, default_count: 1,
  image_file: '', ...over,
});
catalog = [
  // 多规格：中文名是定语，不能和 SKU 列里的规格框重复显示；中间点要归一化
  row({ sku: '111', name_zh: '7.0·cm', name_en: 'Dog Shoes - 6.0 cm', variant_group: 'g1', variant_label: '7.0·cm', is_primary_variant: true }),
  row({ sku: '112', name_zh: '6.0·cm', name_en: 'Dog Shoes - 6.0 cm', variant_group: 'g1', variant_label: '6.0·cm', is_primary_variant: false }),
  // 非中文定语要能被标出来
  row({ sku: '113', name_zh: 'Color·daisy', name_en: 'Steering Cover - daisy', variant_group: 'g2', variant_label: 'Color·daisy', is_primary_variant: true }),
  // 单规格：中文名就是全名，不该被当成定语截断
  row({ sku: '114', name_zh: '露营速干遮阳帽', name_en: 'Camping Hat' }),
];

const host = document.getElementById('root');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const root = createRoot(host);
(async () => {
  await act(async () => root.render(React.createElement(MemoryRouter, { initialEntries: ['/packing'] }, React.createElement(Page))));
  await act(async () => { await Promise.resolve(); });

  const rowOf = (sku) => [...host.querySelectorAll('tbody tr')].find(tr => tr.textContent.includes(sku));
  const textOf = (sku) => rowOf(sku).textContent.replace(/\s+/g, ' ');

  // 折叠态：每个规格组只出主款一行，单规格商品各占一行。
  check(host.querySelectorAll('tbody tr').length === 3, 'primary variants render one row per group, single products render their own row');
  check(!rowOf('112'), 'branch rows stay collapsed until expanded');

  const primary = rowOf('111');
  check(textOf('111').includes('7.0 cm'), 'the middle dot in a legacy label is normalised to a space');
  check(!textOf('111').includes('7.0·cm'), 'the raw legacy label never reaches the screen');
  check((textOf('111').match(/7\.0 cm/g) || []).length === 1, 'the variant label is printed once, not duplicated in the SKU cell');
  check(!primary.querySelector('.packing-spec-box'), 'the duplicate spec chip in the SKU column is gone');

  // 展开后分支行各显示自己的定语
  await act(async () => { primary.querySelector('.packing-expand-link').click(); });
  check(!!rowOf('112'), 'expanding a group reveals its branch rows');
  check(textOf('112').includes('6.0 cm'), 'branch labels show their own qualifier');

  const search = host.querySelector('.packing-search');
  const setSearch = async value => act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(search, value);
    search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
  await setSearch('111');
  check(!!rowOf('112'), 'searching the primary SKU preserves branches of an expanded group');
  await act(async () => rowOf('111').querySelector('.packing-expand-link').click());
  check(!rowOf('112'), 'searching a primary SKU still allows collapse');
  await act(async () => rowOf('111').querySelector('.packing-expand-link').click());
  check(!!rowOf('112'), 'expanding after searching the primary SKU reveals the branch');
  await act(async () => rowOf('111').querySelector('.packing-expand-link').click());
  await setSearch('112');
  check(!!rowOf('112'), 'searching a branch SKU reveals it even when its group is collapsed');
  check(!rowOf('114'), 'search excludes unrelated products');
  await setSearch('');

  const flagged = rowOf('113');
  check(!!flagged.querySelector('.packing-name-flag'), 'a non-Chinese variant label is flagged for correction');
  check(flagged.querySelector('.packing-name-flag').title.includes('产品列表'), 'the flag points at where to fix it');

  const single = rowOf('114');
  check(textOf('114').includes('露营速干遮阳帽'), 'a single-variant product keeps its full Chinese name');
  check(!single.querySelector('.packing-name-flag'), 'a Chinese name is never flagged');

  // 属性行只报「带电/含磁」这类正向信息，不再写「带电 否」占一整行。
  check(!primary.querySelector('.packing-attr-line').textContent.includes('否'), 'negative electrical flags are not printed as noise');

  // 产品列表改完名字 → 装箱单要跟着变，不能等整页刷新。
  const before = packingReads;
  catalog = catalog.map(r => r.sku === '114' ? { ...r, name_zh: '改过的中文名' } : r);
  await act(async () => { window.dispatchEvent(new dom.window.CustomEvent('catalog:changed')); });
  await act(async () => { await Promise.resolve(); });
  check(packingReads > before, 'a catalog change triggers a packing reload');
  check(host.textContent.includes('改过的中文名'), 'the edited Chinese name shows up in the packing list');

  check(writes.length === 0, 'rendering and reloading never writes to the backend');
  await act(async () => root.unmount());
  console.log(`PASS ${checks} packing display assertions (jsdom, isolated API)`);
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
