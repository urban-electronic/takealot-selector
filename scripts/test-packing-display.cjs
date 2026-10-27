// 装箱单展示规则：变体中文名不重复、不出现「·」、非中文要标出来；
// 产品列表改完名字要能即时反映到装箱单（catalog:changed）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
// Reverse probe: restore the old refresh-failure behavior in memory, never on disk.
if (!process.env.PACKING_REFRESH_REVERSE) {
  const { spawnSync } = require('node:child_process');
  const probe = spawnSync(process.execPath, [__filename], {
    env: { ...process.env, PACKING_REFRESH_REVERSE: '1' },
    encoding: 'utf8', timeout: 10000,
  });
  assert.equal(probe.status, 1, 'refresh reverse probe must fail');
  assert.match(probe.stdout + probe.stderr,
    /successful mutation feedback survives a separate list refresh failure/,
    'reverse probe must fail at the refresh assertion, not fixture setup');
  console.log('PASS refresh reverse probe: old load failure clears confirmed success');
}
const watchdog = setTimeout(() => { console.error('FAIL packing suite did not complete within 10s'); process.exit(1); }, 10000);
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid/packing' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'localStorage'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.URL = dom.window.URL;
document.addEventListener('click', e => { if (e.target.closest?.('a[download]')) e.preventDefault(); });
if (!dom.window.URL.createObjectURL) dom.window.URL.createObjectURL = () => 'blob:stub';
if (!dom.window.URL.revokeObjectURL) dom.window.URL.revokeObjectURL = () => {};

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');
const file = path.resolve(__dirname, '../src/pages/Packing.tsx');

let catalog = [];
let packingReads = 0;
let packingReadFails = false;
let imageSyncFails = false;
// 「从选品库导入」弹层读的就是 api.getProducts。默认空，用例里按需改：
// 读失败 / 有候选产品 / 导入结果的三档计数都要能分别构造出来。
let importCatalog = [];
let importReadFails = false;
let importResult = { ok: true, imported: 0, skipped: 0, failed: [], image_failed: [] };
let importThrows = false;
const writes = [];
const exported = [];
const api = {
  getProducts: async () => {
    if (importReadFails) throw new Error('HTTP 500');
    return importCatalog;
  },
  getImageUrl: (x) => x,
  getProduct: async () => ({}),
};
const remote = {
  getPackingProducts: async () => { packingReads += 1; if (packingReadFails) throw new Error('列表读取失败'); return catalog.map(r => ({ ...r })); },
  getInventory: async () => [],
  syncPackingProductsFromCatalog: async () => ({ ok: true, total_products: 3, packing_products: catalog.length, imported: 0, skipped_no_sku: 0, duplicate_sku: 0 }),
  upsertPackingProduct: async () => { writes.push('upsert'); return { ok: true }; },
  deletePackingProduct: async () => { writes.push('delete'); return { ok: true }; },
  uploadPackingImage: async () => { writes.push('upload'); return { ok: true }; },
  importFromProducts: async () => {
    if (importThrows) throw new Error('网络中断');
    return importResult;
  },
  syncPackingImagesFromProducts: async () => { if (imageSyncFails) throw new Error('匹配接口 500'); return { ok: true, updated: 0, total_pending: 0, failed: [] }; },
  syncPackingVariantsFromTakealot: async () => ({ ok: true, groups_checked: 0, matched: 0, images: 0, unresolved: [] }),
  // 真实实现返回的是 Blob（remoteApi.exportPacking 走 response.blob()）。
  // 桩原来回 `{ok:true}`，页面 `URL.createObjectURL(blob)` 于是抛
  // 「obj argument must be an instance of Blob」——导出成功那条路径**从来没跑通过**，
  // 只是没人断言横幅，所以一直看不出来。
  exportPacking: async (payload) => { exported.push(payload); return new Blob(['fixture']); },
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
  let source = fs.readFileSync(target, 'utf8');
  if (target === file && process.env.PACKING_REFRESH_REVERSE) {
    const before = 'setError(`加载装箱单库失败：${reasonOf(e)}`);';
    assert.ok(source.includes(before), 'reverse probe target must exist');
    source = source.replace(before, 'fail(`加载装箱单库失败：${reasonOf(e)}`);');
  }
  m._compile(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target);
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

  // ---- 从选品库导入：读失败 ≠ 产品库是空的；失败提示要写在弹层里 ----------------
  // 两个真实的坑：
  //   a) 读 /api/products 失败时 catch 里 setImportResults([])，弹层照样显示
  //      「未找到匹配的选品产品」——用户以为选品库空了，转去别处找产品；
  //   b) 导入失败写的是页面顶部的 error，而弹层还开着，那条提示在遮罩后面，
  //      用户看到的就是「点了没反应」。
  const importBtn = [...host.querySelectorAll('button')].find(b => b.textContent.trim() === '新增产品');
  check(!!importBtn, 'the packing page has a 「新增产品」 entry point');
  const openImport = async () => {
    await act(async () => { importBtn.click(); await Promise.resolve(); await Promise.resolve(); });
    return host.querySelector('[aria-label="从选品库导入"]');
  };
  const escKey = async () => act(async () => {
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  });

  importReadFails = true;
  let imp = await openImport();
  check(!!imp, '「新增产品」 opens the import dialog');
  check(!!imp && imp.textContent.includes('选品库读取失败'),
    `a failed read must be named, got ${imp && imp.textContent.slice(0, 80)}`);
  check(!!imp && !imp.textContent.includes('未找到匹配的选品产品'),
    'a failed read must not be presented as an empty catalog');
  check(!!imp && [...imp.querySelectorAll('button')].some(b => b.textContent.trim() === '重试'),
    'a failed read offers a retry');
  check(document.body.style.overflow === 'hidden', 'the import dialog locks background scrolling');
  await escKey();
  check(!host.querySelector('[aria-label="从选品库导入"]'), 'Escape closes the import dialog');
  check(document.body.style.overflow !== 'hidden', 'closing the import dialog restores scrolling');

  importReadFails = false;
  importCatalog = [{ id: 'np1', sku: 'NEW-1', product_name: 'New bag', chinese_product_name: '新包', fee_category: '包袋', shipping_method: '' }];
  imp = await openImport();
  check(!!imp && imp.textContent.includes('NEW-1'), 'the dialog lists catalog candidates');
  const candidate = imp && [...imp.querySelectorAll('input[type=checkbox]')][0];
  check(!!candidate && !candidate.disabled, 'a candidate with an unused SKU is selectable');
  await act(async () => { candidate.click(); await Promise.resolve(); });

  // 导入失败：弹层不关，错误必须出现在弹层内部
  importThrows = true;
  const importSubmit = [...imp.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('导入到装箱单'));
  await act(async () => { importSubmit.click(); await Promise.resolve(); await Promise.resolve(); });
  imp = host.querySelector('[aria-label="从选品库导入"]');
  check(!!imp, 'a failed import keeps the dialog open so the selection is not lost');
  const inlineError = imp && imp.querySelector('.alert-error');
  check(!!inlineError && inlineError.textContent.includes('导入失败'),
    `the import error must be shown inside the dialog, got ${inlineError && inlineError.textContent}`);
  check(!!inlineError && inlineError.textContent.includes('网络中断'),
    'the import error must carry the reason');

  // 导入成功但部分产品缺图 / 部分没导进来：三档计数要分开说，不能混成「失败 N 条」
  importThrows = false;
  importResult = {
    ok: true, imported: 2, skipped: 1,
    failed: [{ sku: 'EMPTY', reason: 'SKU 为空，无法作为装箱单主键' }],
    image_failed: [{ sku: 'NEW-1', reason: '图片抓取失败：CDN 403' }],
  };
  await act(async () => { importSubmit.click(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
  check(!host.querySelector('[aria-label="从选品库导入"]'), 'a successful import closes the dialog');
  const done = host.textContent.replace(/\s+/g, ' ');
  check(done.includes('已导入但图片没抓到') && done.includes('NEW-1'),
    'an image failure must be described as "already imported", naming the SKU');
  check(done.includes('没有导入') && done.includes('EMPTY'),
    'a real import failure must be described separately, naming the SKU');
  check(!/失败 1 条/.test(done), 'the old catch-all 「失败 N 条」 wording must be gone');

  // 导出 payload：默认值必须原样进 items 与 cartons（后端拿它生成装箱单与箱唛）
  const exportBtn = host.querySelector('.packing-export-btn');

  // 运输方式必选：头程印错一行，货代就可能按错的渠道走。空着点导出必须被拦住，
  // 不能悄悄默认一个值印在单子上。
  await act(async () => { exportBtn.click(); await Promise.resolve(); await Promise.resolve(); });
  check(exported.length === 0, `export must be blocked without a shipping method, got ${exported.length} call(s)`);
  const blocked = host.querySelector('.alert-error');
  check(blocked && blocked.textContent.includes('运输方式'),
    `the blocked export must say why, got ${blocked && blocked.textContent}`);
  // 同一次操作的成败不能并排挂着（BANNER-EXCLUSIVE-02）：上一步导入成功留下的
  // 绿条必须被这次「运输方式没选」的红条顶掉，否则屏幕上同时写着「导入完成」
  // 和「请选择运输方式」，用户看不出这次到底成没成。
  check(host.querySelectorAll('.alert-success').length === 0,
    'a blocked export must clear the previous success banner');

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
  check(host.querySelectorAll('.alert-error').length === 0,
    'a successful export must clear the previous error banner');
  check(host.querySelectorAll('.alert-success').length === 1,
    `a successful export leaves exactly one success banner, got ${host.querySelectorAll('.alert-success').length}`);

  const syncImages = [...host.querySelectorAll('button')].find(b => b.textContent === '按SKU匹配选品库图片');
  check(!!host.querySelector('.alert-success'), 'the fixture must reach actual export success, not just submit its payload');
  imageSyncFails = true;
  await act(async () => { syncImages.click(); await Promise.resolve(); await Promise.resolve(); });
  check(!host.querySelector('.alert-success'), 'a later action failure removes the previous export success');
  check(host.querySelector('.alert-error')?.textContent.includes('匹配接口 500'), 'the current action failure remains visible');
  check(![...host.querySelectorAll('.alert button')].some(b => b.textContent === '导出 OMS 入仓模板'), 'the previous success action expires with its message');
  imageSyncFails = false;
  packingReadFails = true;
  await act(async () => { syncImages.click(); await Promise.resolve(); await Promise.resolve(); });
  check(!!host.querySelector('.alert-success'), 'successful mutation feedback survives a separate list refresh failure');
  check(host.querySelector('.alert-error')?.textContent.includes('列表读取失败'), 'list refresh failure is not erased by the successful action');
  packingReadFails = false;

  check(writes.length === 0, 'rendering and reloading never writes to the backend');
  await act(async () => root.unmount());

  // 「一键导入」（URL 带 ?import= 且该 SKU 不在装箱单库时出现）打的是**同一个**导入接口，
  // 却自己拼了一句「新增 N 条，跳过 M 条，失败 K 条」：「已导入但缺图」这一档被整个吞掉，
  // 用户不知道要去补图；真失败也不点名。同一个接口在同一页给出两套说法，必须共用一套。
  const host2 = document.createElement('div');
  document.body.appendChild(host2);
  const root2 = createRoot(host2);
  importCatalog = [{ id: 'p9', sku: 'MISSING-9', product_name: 'Fixture', chinese_product_name: '夹具' }];
  importResult = {
    ok: true, imported: 1, skipped: 0,
    failed: [{ sku: '', reason: 'SKU 为空，无法作为装箱单主键' }],
    image_failed: [{ sku: 'MISSING-9', reason: '图片抓取失败：CDN 403' }],
  };
  await act(async () => root2.render(React.createElement(MemoryRouter, { initialEntries: ['/packing?import=MISSING-9'] }, React.createElement(Page))));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  const oneClick = [...host2.querySelectorAll('button')].find(b => b.textContent.trim() === '一键导入');
  check(!!oneClick, 'a SKU coming from the URL but absent from the packing library offers 一键导入');
  await act(async () => { oneClick.click(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
  const oneClickText = host2.textContent.replace(/\s+/g, ' ');
  check(oneClickText.includes('已导入但图片没抓到') && oneClickText.includes('MISSING-9'),
    `the one-click import must report an image failure as "already imported", got ${oneClickText.slice(0, 160)}`);
  check(oneClickText.includes('没有导入') && oneClickText.includes('SKU 为空'),
    'the one-click import must describe real failures separately and name the reason');
  check(!/失败 1 条/.test(oneClickText), 'the one-click import must not fall back to the old 「失败 N 条」 wording');
  await act(async () => root2.unmount());

  // ---- 源码守卫：动作反馈互斥；列表读取失败单独保留；跳过行是提醒 -------------
  // 行为断言只覆盖了「导入成功 → 导出被拦」与「导出成功」两条路径；剩下 8 个
  // 处理函数（保存/删除/上传/移除图片/图片匹配/规格核对…）都是同一类写法，
  // 靠这条守卫防止有人再退回 setError()/setMsg() 直写。
  const packingSource = fs.readFileSync(file, 'utf8');
  check(packingSource.includes('const notice = ') && packingSource.includes('const fail = '),
    '装箱页必须提供 notice()/fail()：一个动作只留一条横幅');
  const bannerWrites = packingSource.match(/(?:setMsg|setError)\((?!'')/g) || [];
  check(bannerWrites.length === 3 && packingSource.includes('setError(`加载装箱单库失败：${reasonOf(e)}`)'),
    `只允许 notice()/fail() 和独立列表读取错误写横幅，实际 ${bannerWrites.length} 处`);
  // 「部分勾选行被跳过」是提醒、不是失败：原来写进 error，紧接着导出开始时的
  // setError('') 在同一个同步流程里就把它抹掉，用户从来没看见过这句。
  check(!/setError\(cartonsManual/.test(packingSource),
    '跳过行的提醒不能写进 error（会被导出前的 setError(\'\') 立刻抹掉）');
  check(packingSource.includes('skippedNote'),
    '跳过说明必须并进导出成功提示，否则用户永远看不到');

  console.log(`PASS ${checks} packing display assertions (jsdom, isolated API)`);
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
