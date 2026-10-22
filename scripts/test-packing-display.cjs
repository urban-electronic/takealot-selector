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
if (!dom.window.URL.revokeObjectURL) dom.window.URL.revokeObjectURL = () => {};

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');
const file = path.resolve(__dirname, '../src/pages/Packing.tsx');

let catalog = [];
let packingReads = 0;
const writes = [];
const exported = [];
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
  exportPacking: async (payload) => { exported.push(payload); return { ok: true }; },
  getPackingCartons: async () => ({ date: '', total_cartons: 0, cartons: [] }),
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
  row({ sku: '114', name_zh: '露营速干遮阳帽', name_en: 'Camping Hat', default_count: 12 }),
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

  // ---- 箱数 / 每箱件数的默认值：界面上写多少，导出就必须是多少 ----------------
  // 输入框里「箱数」默认显示 1、「每箱件数」的 placeholder 写着「默认 12」，小计也按 12 算；
  // 但导出曾经要求**手填过**才算有效（`parseInt(lines[sku].count)` 为 NaN 就整行跳过）。
  // 于是「勾一个产品、什么都不改、点导出」会报「请填写有效的箱数和每箱件数」——
  // 让用户去填那两个看起来已经填好的框。
  await act(async () => { rowOf('114').querySelector('input[type=checkbox]').click(); });
  await act(async () => { await Promise.resolve(); });
  const pickedRow = [...host.querySelectorAll('.packing-selected-item')][0];
  check(!!pickedRow, 'selecting a product adds a row to the packing list');
  check(pickedRow.querySelector('.packing-line-total').textContent.includes('12 件'),
    `the row subtotal uses the default pieces per carton, got ${pickedRow.querySelector('.packing-line-total').textContent}`);
  const headTotal = host.querySelector('.packing-summary-banner small').textContent;
  check(headTotal.includes('1 箱') && headTotal.includes('12 件'),
    `the list header must agree with the row (1 carton, 12 pieces), got ${headTotal}`);
  check(host.querySelector('.packing-export-card').textContent.includes('将导出 1 行'),
    'the export hint counts the rows that will actually be exported');

  // 逐箱清单：箱内数量必须是默认件数，不能是空（空会印成「×0」）。
  // 以前要先点「按 SKU 展开」——那个按钮已经删掉了：勾选之后箱子就按
  // 「箱数 × 每箱件数」自动长出来，展开完再手录一遍数量是重复劳动。
  // 所以这里不再点任何按钮，直接看自动生成的结果。
  check(host.querySelectorAll('.packing-carton-item').length === 1,
    `selecting one SKU must auto-generate exactly one carton, got ${host.querySelectorAll('.packing-carton-item').length}`);
  const qtyInput = host.querySelector('.packing-carton-line input[type=number]');
  check(qtyInput && qtyInput.value === '12',
    `the carton line must carry the default pieces (12), got ${qtyInput && qtyInput.value}`);
  check(!host.querySelector('.packing-carton-warn'), 'a default-filled carton list must not be flagged as mismatched');

  // ---- 弹层：声明了 aria-modal 就得能被 Esc 关掉、关掉前背后不滚动 -------------
  // 产品列表、补货页的弹层都是这么做的，装箱页三个弹层一个都没做：
  // 键盘用户关不掉，窄屏上手指划的其实是背后的装箱表。
  const queryBtn = [...host.querySelectorAll('button')].find(b => b.textContent.trim() === '查箱子');
  check(!!queryBtn, 'the packing page has a 「查箱子」 entry point');
  await act(async () => { queryBtn.click(); await Promise.resolve(); });
  check(!!host.querySelector('.carton-query-modal'), '「查箱子」 opens a dialog');
  check(document.body.style.overflow === 'hidden', 'an aria-modal dialog must lock background scrolling');
  await act(async () => { document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' })); });
  check(!host.querySelector('.carton-query-modal'), 'Escape closes 「查箱子」（键盘用户没有别的关闭方式）');
  check(document.body.style.overflow !== 'hidden', 'closing the dialog restores background scrolling');

  const editBtn = [...rowOf('114').querySelectorAll('button')].find(b => b.textContent.trim() === '编辑');
  await act(async () => { editBtn.click(); await Promise.resolve(); });
  check(!!host.querySelector('[aria-label="编辑装箱产品"]'), '「编辑」 opens the edit dialog');
  check(document.body.style.overflow === 'hidden', 'the edit dialog locks background scrolling too');
  await act(async () => { document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' })); });
  check(!host.querySelector('[aria-label="编辑装箱产品"]'), 'Escape closes the edit dialog');
  check(document.body.style.overflow !== 'hidden', 'closing the edit dialog restores scrolling');

  // 导出 payload：默认值必须原样进 items 与 cartons（后端拿它生成装箱单与箱唛）
  const exportBtn = host.querySelector('.packing-export-btn');

  // 运输方式必选：头程印错一行，货代就可能按错的渠道走。空着点导出必须被拦住，
  // 不能悄悄默认一个值印在单子上。
  await act(async () => { exportBtn.click(); await Promise.resolve(); await Promise.resolve(); });
  check(exported.length === 0, `export must be blocked without a shipping method, got ${exported.length} call(s)`);
  const blocked = host.querySelector('.alert-error');
  check(blocked && blocked.textContent.includes('运输方式'),
    `the blocked export must say why, got ${blocked && blocked.textContent}`);

  // 模板 G2 是「头程（空/海）」，只能是下拉里的两项之一，不能再是自由文本。
  const shippingSelect = host.querySelector('.packing-export-grid select');
  check(!!shippingSelect, 'the shipping method is a dropdown, not a free-text field');
  check([...shippingSelect.options].map(o => o.value).filter(Boolean).join(',') === '空运,海运',
    `the dropdown offers 空运/海运 only, got ${[...shippingSelect.options].map(o => o.value).join(',')}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype, 'value').set.call(shippingSelect, '海运');
    shippingSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  });

  await act(async () => { exportBtn.click(); await Promise.resolve(); await Promise.resolve(); });
  check(exported.length === 1, `export must be called once, got ${exported.length}`);
  check(exported[0].shipping === '海运', `the chosen shipping method must reach the backend, got ${exported[0].shipping}`);
  const payload = exported[0] || {};
  check(JSON.stringify(payload.items) === JSON.stringify([{ sku: '114', cartons: '1', count: '12' }]),
    `export items must carry cartons=1 / count=12, got ${JSON.stringify(payload.items)}`);
  check(JSON.stringify(payload.cartons) === JSON.stringify([{ box_no: 'C01', note: '', items: [{ sku: '114', quantity: '12' }] }]),
    `export cartons must carry quantity=12, got ${JSON.stringify(payload.cartons)}`);

  check(writes.length === 0, 'rendering and reloading never writes to the backend');
  await act(async () => root.unmount());
  console.log(`PASS ${checks} packing display assertions (jsdom, isolated API)`);
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
