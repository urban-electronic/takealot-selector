// Executes the actual page against isolated API fixtures; no production calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const watchdog = setTimeout(() => { console.error('FAIL Buybox suite did not complete within 10s'); process.exit(1); }, 10000);
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
let readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' });
let pauseFixture;
const bulkCalls = [];
// 未显式 mock 的相对导入按真实 .ts/.tsx 编译，避免新增一个模块就让整组测试失败。
const localModule = name => {
  if (typeof name !== 'string' || !name.startsWith('.')) return null;
  for (const ext of ['.ts', '.tsx']) {
    const target = path.resolve(path.dirname(file), name + ext);
    if (!fs.existsSync(target)) continue;
    const m = new Module(target, module);
    m.filename = target;
    m.paths = Module._nodeModulePaths(path.dirname(target));
    m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target);
    return m.exports;
  }
  return null;
};
const originalRequire = compiled.require.bind(compiled);
compiled.require = name => name === '../remoteApi' ? new Proxy({
  getAutomaticPricing: () => readPricing(),
  pauseAutomaticPricing: (...args) => pauseFixture ? pauseFixture(...args) : Promise.reject(Error('Unexpected pause')),
  bulkBuyboxMonitor: (...args) => { bulkCalls.push(args[0]); return Promise.resolve({ updated: 1 }); },
}, { get: (target, key) => target[key] || (() => { writes++; throw Error('Unexpected mutation'); }) }) : (localModule(name) || originalRequire(name));
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
    check([...tr.querySelectorAll('.row-details')].every(d=>!d.open), 'verbose explanations are collapsed by default');
    check(tr.querySelector('.product-link').title==='Test product', 'full English name remains available on hover');
    check(tr.textContent.includes('测试商品'), 'Chinese name is displayed separately');
    check(!tr.querySelector('.product-english-name'), 'English title is not repeated as its own line');
    check(tr.textContent.includes('R 269'), 'queued job retains last valid quote');
    check(tr.textContent.includes('页面卖家：Urban Electronics'), 'actual quoted seller is visible');
    check(tr.textContent.includes('同价 · 卖家未核实'), 'equal price is never presented as verified holding');
    check(!tr.textContent.includes('同价 · 待确认'), 'ambiguous badge removed');
    check(tr.textContent.includes('报价来源：后台采集器'), 'quote origin is visible');
    check(tr.querySelector('.collection-status-cell').textContent.includes('排队中'), 'collection state is stated in its own cell');
    check(tr.querySelectorAll('td')[4].textContent.includes('调价：关闭'), 'pricing state has its own label');
    check(tr.querySelector('.collection-status-cell').textContent.includes('最近成功'), 'timestamp meaning is explicit');
  });
  await scenario({ ...known, status: 'error', error: '网站验证阻挡', error_code: 'site_challenge' }, tr => {
    check(tr.querySelector('.collection-status-cell').textContent.includes('采集失败'), 'failure not hidden by pricing state');
    check(tr.textContent.includes('旧报价'), 'failure retains stale quote warning');
  });
  // 采集失败的原因有一半藏在页面里。诊断数字必须能在行内看到，否则用户只能反复点采集。
  await scenario({ ...known, status: 'error', error: '页面已读取，但未找到有效Buybox价格', error_code: 'price_not_found',
    diagnostics: { http_status: 200, page_title: 'Product page', buybox_cards: 0, price_nodes: 3 } }, tr => {
    const diag = tr.querySelector('.diag-line');
    check(!!diag, 'diagnostics are surfaced inside the failing row');
    check(diag.textContent.includes('Buybox 区块 0 个'), 'buybox block count is shown');
    check(diag.textContent.includes('价格节点 3 个'), 'price node count is shown');
    check(diag.textContent.includes('HTTP 200'), 'http status is shown');
  });
  await scenario({ ...known, status: 'error', error_code: 'page_structure_changed' }, tr => {
    check(tr.textContent.includes('页面结构变了'), 'structural failure is named instead of "no buybox price"');
    check(!tr.textContent.includes('页面打开了但没有 Buybox 价'), 'structural failure is not lumped into the ambiguous copy');
    const copy = [...host.querySelectorAll('.collection-bar-actions button')].find(b => b.textContent === '复制诊断');
    check(!!copy, 'a failing fleet offers a one-click diagnostics export');
  });
  await scenario({ ...known, status: 'success', source: 'local_collector', holding_buybox: true }, tr => {
    check(tr.textContent.includes('报价来源：本机采集器'), 'native quote source preserved');
    // 历史 holding_buybox=true 只代表当时同价，不是卖家身份证据，不能显示成"已确认持有"。
    check(tr.querySelectorAll('td')[2].textContent.includes('卖家未核实'), 'legacy holding flag is not verified identity');
    check(!tr.querySelectorAll('td')[2].textContent.includes('已确认'), 'legacy holding flag never claims confirmation');
  });
  await scenario({ status: 'queued' }, tr => {
    check(tr.querySelectorAll('td')[2].textContent.includes('未采集'), 'empty quote does not show success');
    check(tr.querySelectorAll('td')[2].textContent.includes('页面卖家：未读取'), 'missing seller is not invented');
  });
  for (const [state,label] of Object.entries({matched:'疑似在持位',mismatch_pending:'疑似在持位 · 复核中',mismatch:'不在持位',checking:'正在核对本店价',verification_failed:'本店价核对失败'})) {
    await scenario({ ...known, status: 'success', price_ownership: {state,own_price:269,own_price_source:'official_live',own_price_asof:'2026-10-15T01:58:10Z',identity_verified:false} }, tr => {
      check(tr.textContent.includes(label), `price estimate ${state} is explicit`);
      check(tr.textContent.includes('对照本店价：R 269 · 实时API'), 'comparison source is visible');
      const pill = tr.querySelectorAll('td')[2].querySelector('.status-pill');
      check((pill.title||'').length > 10, `price estimate ${state} explains itself on hover`);
      // 同价是最容易被误读成"已持有"的状态，单独钉死这条限定。
      if (state === 'matched') check(pill.title.includes('不能证明是本店在占位'), 'equal price is never verified identity');
    });
  }
  await scenario({ ...known, status: 'success', source: undefined, snapshot: {} }, tr => {
    check(tr.textContent.includes('报价来源：未记录'), 'legacy source remains unknown');
    check(tr.textContent.includes('卖家：未读取'), 'legacy seller remains unknown');
  });
  await scenario({ ...known, status: 'queued', enabled:true, next_due:Date.now()/1000+300 }, tr => {
    check(tr.textContent.includes('等待下轮'), 'future scheduled work is not shown as waiting for a worker');
    check(tr.textContent.includes('到点自动开始'), 'scheduled waiting has a reason');
    check(tr.textContent.includes('上一次成功采到的报价，不是本轮结果'), 'retained quote is not a new collection result');
    check([...tr.querySelectorAll('button')].find(b=>b.textContent==='立即采集').disabled, 'queued work cannot be repeatedly submitted');
  });
  await scenario({ ...known, status:'queued', next_due:0 }, tr => {
    check(tr.textContent.includes('已排队，后台马上执行'), 'due queue explains what it is waiting for');
    check(!tr.textContent.includes('正在采这一轮'), 'queue never implies success');
    check(!tr.textContent.includes('离线'), 'queue alone does not prove an offline collector');
  });
  await scenario({ ...known, status:'paused', enabled:false }, tr => {
    check(tr.querySelector('.collection-status-cell').textContent.includes('已停采'), 'paused work is not labelled manual collection');
  });
  await scenario({status:'success'}, tr => {
    check(tr.querySelector('.product-link').textContent==='中文名待核对', 'English never fills the Chinese display');
    check(tr.textContent.includes('中文字段为非中文，请修正'), 'invalid Chinese field is actionable');
    check(tr.querySelector('.product-link').title==='Test product', 'original English name is preserved on the link');
  }, {chinese_name:null,name_status:'invalid'});
  await scenario({status:'success'}, tr => {
    check(tr.textContent.includes('SKU与中文名对应不明确'), 'ambiguous variant mapping is visible');
  }, {chinese_name:null,name_status:'ambiguous'});
  // 信息密度与一键重试：列数必须收敛，重试必须真的打到接口上。
  {
    fixture = [row({ status: 'error', error_code: 'site_challenge', enabled: true,
      last_success_at: new Date(Date.now() - 30 * 60000).toISOString(), next_due: Date.now() / 1000 + 28800 })];
    readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' });
    const dense = createRoot(host);
    try {
      await act(async () => dense.render(React.createElement(Page)));
      check(host.querySelectorAll('thead th').length === 5, 'table collapses to five columns');
      const bar = host.querySelector('.collection-bar');
      check(!!bar && bar.querySelectorAll('li').length === 0, 'header status is one line, not a list');
      check(bar.textContent.includes('网站要求人工验证'), 'header names the cause in plain language');
      // 被网站挡住时只改间隔，不立刻再试一次——否则页面正文劝「别反复点」，
      // 按钮自己却在点。
      const widen = [...bar.querySelectorAll('button')].find(b => b.textContent.includes('改为 120 分钟'));
      check(!!widen, 'a widen-interval action is offered for blocked rows');
      check(!bar.textContent.includes('并重试'), 'blocked rows are not retried immediately');
      await act(async () => widen.click());
      check(bulkCalls.length === 1, 'widening the interval calls the bulk endpoint once');
      check(bulkCalls[0].enabled === true, 'widening keeps collection enabled');
      check(bulkCalls[0].interval_minutes === 120, 'throttled rows widen the interval instead of hammering');
      check(bulkCalls[0].keep_backoff === true, 'blocked rows keep their existing backoff');
      check(bulkCalls[0].items.length === 1, 'only the stuck rows are targeted');
    } finally { act(() => dense.unmount()); }
  }
  {
    // 自身故障（解析器/网络）才是可以立刻重试的。
    bulkCalls.length = 0;
    fixture = [row({ status: 'error', error_code: 'collector_error', enabled: true,
      last_success_at: new Date(Date.now() - 30 * 60000).toISOString(), next_due: Date.now() / 1000 + 1800 })];
    readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' });
    const fault = createRoot(host);
    try {
      await act(async () => fault.render(React.createElement(Page)));
      const retry = [...host.querySelector('.collection-bar').querySelectorAll('button')].find(b => b.textContent.includes('重试'));
      check(!!retry && retry.textContent.includes('重试 1 个'), 'self-inflicted faults offer an immediate retry');
      await act(async () => retry.click());
      check(bulkCalls.length === 1 && !bulkCalls[0].keep_backoff, 'immediate retry clears the backoff');
      check(bulkCalls[0].interval_minutes === 5, 'immediate retry does not silently widen the interval');
    } finally { act(() => fault.unmount()); }
  }
  {
    // 接口返回形状不对时保留上一次的有效列表并明确报错。整表清空比显示旧数据更危险：
    // 用户会以为 SKU 都没了，然后重新开一遍采集。
    const good = [row({ status: 'success', enabled: true, last_success_at: new Date().toISOString() })];
    readPricing = async () => ({ rows: good, configured: true, collector_source: 'cloud_browser' });
    const box = createRoot(host);
    try {
      await act(async () => box.render(React.createElement(Page)));
      check(host.querySelectorAll('tbody tr').length === 1, 'a valid load renders its row');
      readPricing = async () => ({ configured: true });
      const refreshBtn = [...host.querySelectorAll('button')].find(b => b.textContent === '刷新列表');
      await act(async () => { refreshBtn.click(); });
      check(host.querySelectorAll('tbody tr').length === 1, 'a malformed refresh keeps the previous rows');
      check((host.querySelector('[role="alert"]')?.textContent || '').includes('保留'), 'a malformed refresh says the old list is kept');
      check(!host.textContent.includes('暂无本店有库存的SKU'), 'a malformed refresh never claims the store has no stock');
    } finally { act(() => box.unmount()); }
  }
  check(writes === 0, 'rendering never triggers a mutation');
  const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no; }); return { promise, resolve, reject }; };
  const payload = (price, enabled = false) => ({ rows: [{...row(known), price, rule:{enabled,use_system_min:true}}], configured:true });
  const nativeSetInterval = globalThis.setInterval;
  const nativeClearInterval = globalThis.clearInterval;
  const timers = new Map(); let timerId = 0;
  globalThis.setInterval = fn => { timers.set(++timerId,fn); return timerId; };
  globalThis.clearInterval = id => timers.delete(id);
  const click = text => [...host.querySelectorAll('button')].find(b=>b.textContent===text).click();
  let root;
  try {
    let calls = 0;
    const first = deferred();
    readPricing = () => { calls++; return first.promise; };
    root = createRoot(host);
    await act(async () => root.render(React.createElement(Page)));
    await act(async () => { click('刷新列表'); [...timers.values()][0](); });
    check(calls===1, 'manual refresh and polling share the pending read');
    check(host.querySelector('.dashboard-heading button').getAttribute('aria-busy')==='true', 'refresh exposes its pending state');
    await act(async () => first.resolve(payload(269,true)));
    check(!host.querySelector('.dashboard-heading button').disabled, 'refresh becomes available when read completes');

    const old = deferred(), current = deferred();
    readPricing = () => { calls++; return old.promise; };
    await act(async () => { [...timers.values()][0](); });
    let pauses = 0;
    pauseFixture = async () => { pauses++; readPricing = () => { calls++; return current.promise; }; };
    await act(async () => click('暂停调价'));
    check(pauses===1 && calls===3, 'post-mutation refresh starts a new read instead of reusing the pre-pause snapshot');
    await act(async () => current.resolve(payload(270,false)));
    await act(async () => old.resolve(payload(269,true)));
    check(host.querySelector('.own-price').textContent==='R 270', 'late pre-pause response cannot overwrite fresh data');
    check(host.textContent.includes('调价：关闭'), 'late pre-pause response cannot restore the running badge');

    const staleError = deferred(), afterPause = deferred();
    readPricing = async () => payload(270,true);
    await act(async () => click('刷新列表'));
    readPricing = () => staleError.promise;
    await act(async () => { [...timers.values()][0](); });
    pauseFixture = async () => { readPricing = () => afterPause.promise; };
    await act(async () => click('暂停调价'));
    await act(async () => afterPause.resolve(payload(272,false)));
    await act(async () => staleError.reject(Error('obsolete polling failure')));
    check(!host.querySelector('[role="alert"]'), 'obsolete request failure cannot replace a newer successful result');

    readPricing = async () => { throw Error('current network failure'); };
    await act(async () => click('刷新列表'));
    check(host.querySelector('[role="alert"]').textContent==='current network failure', 'current failure remains visible');
    check(host.querySelector('.own-price').textContent==='R 272', 'failed refresh preserves the last loaded data');
    check(!host.querySelector('.dashboard-heading button').disabled, 'failed refresh releases its busy state');
    readPricing = async () => payload(273,false);
    await act(async () => click('刷新列表'));
    check(!host.querySelector('[role="alert"]') && host.querySelector('.own-price').textContent==='R 273', 'retry updates data and clears the resolved read error');

    const lateError = deferred();
    readPricing = () => lateError.promise;
    await act(async () => { [...timers.values()][0](); });
    // Unmount with a pending read. No production API or pricing writes exist here.
    act(() => root.unmount()); root = null;
    readPricing = async () => payload(271,false);
    root = createRoot(host);
    await act(async () => root.render(React.createElement(Page)));
    await act(async () => lateError.reject(Error('obsolete network error')));
    check(!host.querySelector('[role="alert"]'), 'unmounted request errors cannot affect a new page');
    check(host.querySelector('.own-price').textContent==='R 271', 'remounted page keeps its own current response');
  } finally {
    if (root) act(() => root.unmount());
    check(timers.size===0, 'all polling timers are cleared on unmount');
    globalThis.setInterval = nativeSetInterval;
    globalThis.clearInterval = nativeClearInterval;
  }
  console.log(`PASS ${checks} Buybox page assertions (jsdom, isolated API)`);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
