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
const row = monitor => ({ product_id: 'p', offer_id: 'o', sku: 'test-sku', name: 'Test product', chinese_name:'测试商品', name_status:'ok', price: 269,
  stock: 11, group: '未分类', url: 'https://example.invalid/product', suggested_min: 170,
  rule: { enabled: false, use_system_min: true }, monitor });
async function scenario(monitor, inspect, overrides = {}) {
  fixture = [{...row(monitor), ...overrides}];
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
    check(tr.textContent.includes('测试商品'), 'Chinese name is displayed separately');
    check(tr.textContent.includes('英文名：Test product'), 'English title remains labelled');
    check(tr.textContent.includes('R 269'), 'queued job retains last valid quote');
    check(tr.textContent.includes('卖家：Urban Electronics'), 'actual quoted seller is visible');
    check(tr.textContent.includes('历史同价 · 推定持有'), 'historical comparison is inferred rather than freshly verified');
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
  for (const [state,label] of Object.entries({matched:'同价 · 推定持有',mismatch_pending:'推定持有 · 失去复核1/2',mismatch:'异价 · 推定未持有',checking:'本店售价待复核',verification_failed:'本店售价复核未完成'})) {
    await scenario({ ...known, status: 'success', price_ownership: {state,own_price:269,own_price_source:'official_live',own_price_asof:'2026-10-15T01:58:10Z',identity_verified:false} }, tr => {
      check(tr.textContent.includes(label), `price estimate ${state} is explicit`);
      check(tr.textContent.includes('对照本店价：R 269 · 实时API'), 'comparison source is visible');
      check(tr.querySelectorAll('td')[2].querySelector('.status-pill').title.includes('不证明卖家身份'), 'estimate is not verified identity');
    });
  }
  await scenario({ ...known, status: 'success', source: undefined, snapshot: {} }, tr => {
    check(tr.textContent.includes('报价来源：未记录'), 'legacy source remains unknown');
    check(tr.textContent.includes('卖家：未读取'), 'legacy seller remains unknown');
  });
  await scenario({ ...known, status: 'queued', enabled:true, next_due:Date.now()/1000+300 }, tr => {
    check(tr.textContent.includes('等待下轮'), 'future scheduled work is not shown as waiting for a worker');
    check(tr.textContent.includes('尚未到计划时间'), 'scheduled waiting has a reason');
    check(tr.textContent.includes('上次有效报价 · 非本轮结果'), 'retained quote is not a new collection result');
    check([...tr.querySelectorAll('button')].find(b=>b.textContent==='立即采集').disabled, 'queued work cannot be repeatedly submitted');
  });
  await scenario({ ...known, status:'queued', next_due:0 }, tr => {
    check(tr.textContent.includes('等待采集器领取'), 'due queue explains what it is waiting for');
    check(tr.textContent.includes('尚未收到本轮完成结果'), 'queue never implies success');
    check(!tr.textContent.includes('离线'), 'queue alone does not prove an offline collector');
  });
  await scenario({ ...known, status:'paused', enabled:false }, tr => {
    check(tr.querySelector('.collection-status-cell').textContent.includes('已停采'), 'paused work is not labelled manual collection');
  });
  await scenario({status:'success'}, tr => {
    check(tr.querySelector('.product-link').textContent==='中文名待核对', 'English never fills the Chinese display');
    check(tr.textContent.includes('中文字段为非中文，请修正'), 'invalid Chinese field is actionable');
    check(tr.textContent.includes('英文名：Test product'), 'original English name is preserved');
  }, {chinese_name:null,name_status:'invalid'});
  await scenario({status:'success'}, tr => {
    check(tr.textContent.includes('SKU与中文名对应不明确'), 'ambiguous variant mapping is visible');
  }, {chinese_name:null,name_status:'ambiguous'});
  check(writes === 0, 'rendering never triggers a mutation');
  console.log(`PASS ${checks} Buybox page assertions (jsdom, isolated API)`);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => dom.window.close());
