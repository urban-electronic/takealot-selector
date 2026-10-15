// Executes the actual page against isolated API fixtures; no production calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid' });
for (const name of ['window','document','navigator','HTMLElement','Node','Event'])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const file = path.resolve(__dirname, '../src/pages/AutoPricing.tsx');
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
let fixture = [];
let writes = 0;
const originalRequire = compiled.require.bind(compiled);
compiled.require = name => name === '../remoteApi' ? new Proxy({
  getAutomaticPricing: async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' }),
}, { get: (target, key) => target[key] || (() => { writes++; throw Error('Unexpected mutation'); }) }) : originalRequire(name);
compiled._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText, file);
const Page = compiled.exports.default;
const host = document.getElementById('root');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const row = monitor => ({ product_id: 'p', offer_id: 'o', sku: 'test-sku', name: 'Test product', price: 269,
  stock: 11, group: '未分类', url: 'https://example.invalid/product', suggested_min: 170,
  rule: { enabled: false, use_system_min: true }, monitor });
async function scenario(monitor, inspect) {
  fixture = [row(monitor)];
  const root = createRoot(host);
  try {
    await act(async () => root.render(React.createElement(Page)));
    inspect(host.querySelector('tbody tr'));
  } finally { act(() => root.unmount()); }
}
(async () => {
  const known = { buybox_price: 269, price_matches_buybox: true, holding_buybox: null,
    last_success_at: '2026-10-15T01:58:09Z', source: 'cloud_browser',
    snapshot: { buyboxSeller: 'Urban Electronics' } };
  await scenario({ ...known, status: 'queued' }, tr => {
    check(tr.textContent.includes('R 269'), 'queued job retains last valid quote');
    check(tr.textContent.includes('卖家：Urban Electronics'), 'actual quoted seller is visible');
    check(tr.textContent.includes('同价 · 卖家未核实'), 'unknown identity is explicitly distinguished from collection');
    check(!tr.textContent.includes('同价 · 待确认'), 'ambiguous badge removed');
    check(tr.textContent.includes('报价来源：后台采集'), 'quote origin is visible');
    check(tr.querySelector('.pricing-result').textContent.includes('采集：排队中'), 'task result does not say fresh success');
    check(tr.querySelector('.pricing-result').textContent.includes('调价：未启动'), 'pricing state has its own label');
    check(tr.querySelector('.collection-status-cell').textContent.includes('最近成功'), 'timestamp meaning is explicit');
  });
  await scenario({ ...known, status: 'error', error: '网站验证阻挡', error_code: 'site_challenge' }, tr => {
    check(tr.querySelector('.pricing-result').textContent.includes('采集：本轮失败'), 'failure not hidden by pricing state');
    check(tr.textContent.includes('旧报价'), 'failure retains stale quote warning');
  });
  await scenario({ ...known, status: 'success', source: 'local_collector', holding_buybox: true }, tr => {
    check(tr.textContent.includes('报价来源：本机采集'), 'native quote source preserved');
    check(tr.querySelectorAll('td')[2].textContent.includes('持有'), 'verified holding remains visible');
  });
  await scenario({ status: 'queued' }, tr => {
    check(tr.querySelectorAll('td')[2].textContent.includes('未采集'), 'empty quote does not show success');
    check(tr.querySelectorAll('td')[2].textContent.includes('卖家：未读取'), 'missing seller is not invented');
  });
  await scenario({ ...known, status: 'success', source: undefined, snapshot: {} }, tr => {
    check(tr.textContent.includes('报价来源：未记录'), 'legacy source remains unknown');
    check(tr.textContent.includes('卖家：未读取'), 'legacy seller remains unknown');
  });
  check(writes === 0, 'rendering never triggers a mutation');
  console.log(`PASS ${checks} Buybox page assertions (jsdom, isolated API)`);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => dom.window.close());
