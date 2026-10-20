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
// jsdom 不实现滚动。点 SKU 会滚到表格那一行，这里补个空实现，免得异常噪声盖住断言。
dom.window.Element.prototype.scrollIntoView = function () {};
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const file = path.resolve(__dirname, '../src/pages/AutoPricing.tsx');
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
let fixture = [];
let writes = 0;
let loopFixture = { worker_alive: true, cycles: 42, enabled: 1, success: 1, error: 0,
  running: 0, interval_minutes: 5, next_due_in: 130, paused_by: null };
let readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser', loop: loopFixture });
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
    await inspect(host.querySelector('tbody tr'));
  } finally { act(() => root.unmount()); }
}
// 多行场景：状态条与轨道是按整个 fleet 判断的，单行 fixture 覆盖不到
// 「一行在跑、另一行被网站挡住」这种组合。
async function fleetScenario(rowsFixture, inspect) {
  fixture = rowsFixture;
  const root = createRoot(host);
  try {
    await act(async () => root.render(React.createElement(Page)));
    await inspect();
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
    // 绝对时间戳是证据，已收进行尾的统一详情面板；采集格里留的是「几分钟前」。
    // 要守的是「这个时间点说明它是什么时间」，不是它落在哪一格。
    check(tr.textContent.includes('最近成功'), 'timestamp meaning is explicit');
  });
  // 采集循环轨道：一个格子一个 SKU，颜色即状态。运营要回答的是"自动化还在转吗、
  // 哪几个没在采"，不是"总共多少条"，所以这里断言的是格子数与状态词，不是数字。
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(!!strip, 'collection loop strip is rendered above the table');
    check(strip.textContent.includes('采集循环运行中'), 'loop health is stated in words, not just a colour');
    check(strip.textContent.includes('每 5 分钟一轮'), 'cycle interval is stated');
    check(strip.querySelectorAll('.loop-cell').length === 1, 'one track cell per SKU');
    check(!!strip.querySelector('.loop-cell.is-ok'), 'a collected SKU shows as done');
    check(strip.textContent.includes('下一轮'), 'next cycle is announced');
    check(strip.textContent.includes('约 2 分钟后'), 'countdown is human readable');
  });
  // worker 死掉时必须直说，否则用户会以为"只是还没到时间"而一直等。
  // 这一条故意不带任何报价：从没采到过才算真的失败（红色）。
  loopFixture = { ...loopFixture, worker_alive: false };
  await scenario({ ...known, buybox_price: undefined, last_success_at: undefined,
    status: 'error', error_code: 'collector_unavailable',
    error: '采集器不可用（浏览器或接口未就绪）；未改价' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('采集循环已停止'), 'a dead worker is reported instead of hidden');
    check(strip.classList.contains('tone-danger'), 'a dead worker uses the danger tone');
    check(!!strip.querySelector('.loop-cell.is-bad'), 'the failing SKU is marked on the track');
  });
  loopFixture = { ...loopFixture, worker_alive: true, paused_by: 'local_collector' };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('本机采集器接管中'), 'handover to a local collector is not reported as a fault');
    check(!strip.classList.contains('tone-danger'), 'handover is not styled as an error');
  });
  loopFixture = { ...loopFixture, paused_by: null };
  // 心跳新鲜 ≠ 循环在转。任务卡死时 alive_at 每 10 秒刷新一次，界面永远显示
  // 「运行中」——线上 2026-10-20 就是这么停了 17 分钟没人发现。只有「上一轮
  // 到现在多久」能揭穿它，所以这条必须单独断言。
  loopFixture = { ...loopFixture, next_due_in: 60, due_now: 0, interval_minutes: 5, cycle_age_seconds: 900 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('采集循环卡住了'), 'a frozen cycle is called out even while the heartbeat is fresh');
    check(strip.classList.contains('tone-danger'), 'a frozen cycle uses the danger tone');
    check(strip.textContent.includes('15 分钟没开新一轮'), 'how long it has been stuck is stated, not just "stuck"');
    check(strip.textContent.includes('重启服务'), 'the copy says what to do about a wedged browser');
    // 后端没报过 stuck_jobs，就不能声称「任务已被放弃」——超时可能是正常的慢轮次，
    // 也可能是一个还没到 240 秒上限的任务。这句话是有证据才说的。
    check(!strip.textContent.includes('已被放弃'), 'abandonment is only claimed when a job really was abandoned');
  });
  // 但「已放弃」有依据时必须说出来：用户要知道是采集环境出过问题。
  loopFixture = { ...loopFixture, stuck_jobs: 1 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('已被放弃'), 'a real abandonment is reported as such');
  });
  loopFixture = { ...loopFixture, stuck_jobs: 0 };
  // 慢轮次不是卡死：28 个 SKU 一轮实测 20 分钟，15 分钟没开新一轮完全正常。
  // 只拿设定间隔（5 分钟）比，会把健康的循环报成红的、还劝用户重启服务。
  loopFixture = { ...loopFixture, interval_minutes: 5, round_seconds: 1200, round_jobs: 28,
    enabled: 28, cycle_age_seconds: 900 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('采集循环运行中'), 'a slow round is not mistaken for a frozen cycle');
    check(!strip.classList.contains('tone-danger'), 'a slow round does not use the danger tone');
    check(!strip.textContent.includes('卡住'), 'a slow round is never described as stuck');
  });
  loopFixture = { ...loopFixture, round_seconds: undefined, round_jobs: undefined, enabled: 1 };
  loopFixture = { ...loopFixture, cycle_age_seconds: 30 };
  // 逾期必须说逾期。一直显示"马上开始"会把"循环跟不上"藏起来，
  // 而这恰恰是最需要人介入的时刻。
  loopFixture = { ...loopFixture, next_due_in: -400, due_now: 3 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('已到期 7 分钟'), 'an overdue cycle is reported instead of masked as starting soon');
    check(strip.textContent.includes('3 个在排队'), 'overdue says how many are actually waiting');
    check(!strip.textContent.includes('马上开始'), 'overdue is never described as "starting right away"');
  });
  // 延迟的真正原因要能看出来：一轮实测耗时比设定间隔长时，必须明说排期排不下。
  loopFixture = { ...loopFixture, next_due_in: -400, due_now: 3, interval_minutes: 5, round_seconds: 492, round_jobs: 28 };
  // 文案从「一轮实测 8.2 分钟（4 个 SKU，每个…）」收成一句结论，完整推演挪进 title。
  // 要求没变：仍然必须说清 ①实测多久 ②为什么追不上 ③该怎么改。
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('整轮约 8.2 分钟'), 'the measured round duration is stated');
    check(strip.textContent.includes('已超过 5 分钟间隔'), 'the cause of the backlog is named, not just the symptom');
    check(strip.textContent.includes('调到 10 分钟以上'), 'the fix is an actionable interval suggestion');
    check(!!strip.querySelector('.loop-capacity').getAttribute('title'), 'the full derivation stays available on hover');
  });
  loopFixture = { ...loopFixture, round_seconds: 60, round_jobs: 4 };
  await scenario({ ...known, status: 'success' }, () => {
    check(!host.querySelector('.loop-capacity'), 'a round that fits the interval produces no capacity warning');
  });
  // 上一轮可能只轮到 4 个 SKU。拿这 4 个的耗时当整轮会得出「排得下」的假结论，
  // 而线上真实的 28 个在采、每个 10.4 秒，整轮已经快占满 5 分钟间隔了。
  loopFixture = { ...loopFixture, next_due_in: -38, due_now: 4, interval_minutes: 5,
    round_seconds: 41.7, round_jobs: 4, seconds_per_sku: 10.4, enabled: 28 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('整轮约 4.9 分钟'), 'a partial round is extrapolated to the whole fleet');
    check(strip.textContent.includes('接近 5 分钟间隔'), 'a round that barely fits explains the chronic lateness');
    check(!!strip.querySelector('.loop-capacity.is-tight'), 'the tight-fit note is shown, not the over-capacity one');
    check(!strip.textContent.includes('已超过'), 'a round that fits is not described as over capacity');
  });
  loopFixture = { ...loopFixture, enabled: 1, next_due_in: 130 };
  loopFixture = { ...loopFixture, round_seconds: null, round_jobs: null, seconds_per_sku: null };
  // 荒谬的延迟不许原样印出来。线上出现过「已延迟 2987107 分钟」——
  // 那是后端把 next_due=0 当时间戳算出来的，精确到分钟是假精确。
  loopFixture = { ...loopFixture, next_due_in: -1792265800 };
  await scenario({ ...known, status: 'success' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(strip.textContent.includes('排期已过期 1 小时以上'), 'an absurd delay is capped instead of printed verbatim');
    check(!/\d{5,}/.test(strip.textContent), 'no five-digit minute count ever reaches the strip');
  });
  loopFixture = { ...loopFixture, next_due_in: 130 };
  // 轨道只给颜色，用户没法知道红的是哪个 SKU。这里必须点名，并且能一键筛出来。
  // enabled:true 是线上真实行必带的字段；缺了它这条就退化成「已关闭的 SKU」，
  // 而「已关闭的 SKU 上残留的旧错误」本来就不该算进需处理——那是另一条规则。
  // 同时不带报价：只有「从没采到过价」才该被点名并画红。
  await scenario({ ...known, buybox_price: undefined, last_success_at: undefined,
    status: 'error', enabled: true, error: '网站验证阻挡', error_code: 'site_challenge' }, async tr => {
    const strip = host.querySelector('.loop-strip');
    const named = strip.querySelector('.loop-failing button');
    check(!!named && named.textContent === 'test-sku', 'the failing cell is named by SKU, not just coloured red');
    check(strip.querySelectorAll('.loop-cell.is-bad').length === 1, 'a SKU with no quote at all is the red cell');
    check(!!strip.querySelector('.loop-cell.is-bad .loop-cell-hit'), 'the red cell itself is clickable');
    check(!strip.querySelector('.loop-cell.is-ok .loop-cell-hit'), 'healthy cells are not extra tab stops');
    // 点名要能直接跳到那一行，否则还是得自己去 279 行里找。
    await act(async () => named.click());
    check(host.querySelector('input[aria-label="搜索调价商品"]').value === 'test-sku', 'clicking the name filters the table to that SKU');
  });
  // 2026-10-18 线上现场：浏览器抖了一下（RuntimeError），但库里还有 5 分钟前的有效价。
  // 这种情况不是「没有行情」，是「这一轮没刷新」——画黄色、不进需处理，
  // 否则用户看到一片红会以为采集全废了。
  await scenario({ ...known, status: 'error', enabled: true,
    error: '采集器不可用（浏览器或接口未就绪）；未改价', error_code: 'collector_unavailable' }, tr => {
    const strip = host.querySelector('.loop-strip');
    check(!!strip.querySelector('.loop-cell.is-warn'), 'a stale-but-usable quote is marked yellow, not red');
    check(!strip.querySelector('.loop-cell.is-bad'), 'a stale-but-usable quote is never drawn as a hard failure');
    check(!strip.querySelector('.loop-failing'), 'a stale-but-usable quote is not listed as work to do');
    const bar = host.querySelector('.collection-bar');
    check(bar.textContent.includes('待刷新'), 'the fleet summary says "待刷新" instead of "采集失败"');
    check(!bar.classList.contains('tone-danger'), 'a stale-but-usable quote does not paint the header red');
    check(tr.querySelector('.collection-status-cell').textContent.includes('待刷新'), 'the row says the quote is pending refresh');
    // 不报红不等于瞒着：原因仍然写在行里。
    check(tr.textContent.includes('采集器没起来'), 'the real reason is still stated in the row');
    check(tr.textContent.includes('的报价'), 'the row still shows which quote is being kept');
  });
  // 已经停采的 SKU 身上留着上次的报错，不算「需处理」。否则上面那个数字会永久虚高，
  // 而且点进去发现是个自己关掉的 SKU，等于把人支使了一趟。
  await scenario({ ...known, status: 'error', enabled: false, error: '网站验证阻挡', error_code: 'site_challenge' }, () => {
    const strip = host.querySelector('.loop-strip');
    check(!strip.querySelector('.loop-failing'), 'a stopped SKU keeps its red cell but is not counted as work to do');
    check(!!strip.querySelector('.loop-cell.is-off'), 'a stopped SKU is drawn as not collecting');
  });
  await scenario({ ...known, status: 'error', enabled: true, error: '网站验证阻挡', error_code: 'site_challenge' }, tr => {
    check(tr.querySelector('.collection-status-cell').textContent.includes('待刷新'), 'a failed round with a usable quote is reported as pending refresh');
    check(tr.textContent.includes('网站要求人工验证'), 'the reason is still named');
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
  // 2026-10-20 回归：状态条改成「只在有事要做时出现」时，判据只看了 fleet.action||canAct。
  // describeFleet 的 running 分支给的是空 action、也不带 suggestedInterval，
  // 于是「一行在跑 + 一行被网站验证挡住」时 canAct 为假、blocked 却是 1，
  // 整条状态条连「人工过验证」「复制诊断」一起消失——而这一刻正是最需要去过验证的。
  await fleetScenario([
    { ...row({ ...known, status: 'running', enabled: true, error_code: null, error: null,
      buybox_price: undefined, last_success_at: undefined }), product_id: 'p-run', offer_id: 'o-run', sku: 'run-sku' },
    { ...row({ ...known, status: 'error', enabled: true, error_code: 'site_challenge', error: '网站验证阻挡',
      buybox_price: undefined, last_success_at: undefined }), product_id: 'p-block', offer_id: 'o-block', sku: 'block-sku' },
  ], () => {
    const bar = host.querySelector('.collection-bar');
    check(!!bar, 'a blocked SKU keeps the action bar even while another SKU is being collected');
    check(!![...bar.querySelectorAll('button')].find(b => b.textContent === '人工过验证'),
      'the manual verification entry stays reachable while a round is running');
    check(!![...bar.querySelectorAll('button')].find(b => b.textContent === '复制诊断'),
      'the diagnostics export stays reachable while a round is running');
  });
  // 反过来也要钉住：整个 fleet 都正常时这条不该出现——那正是把它折叠掉的理由。
  await fleetScenario([row({ ...known, status: 'success', enabled: true })], () => {
    check(!host.querySelector('.collection-bar'), 'an all-healthy fleet shows no action bar at all');
  });
  // 采集器自己没起来时，不能显示成「页面打开了但没有 Buybox 价」——那是把基础设施
  // 故障说成页面问题，2026-10-17 就因此查错了方向。
  await scenario({ ...known, status: 'error', error: '采集器不可用（浏览器或接口未就绪）；未改价', error_code: 'collector_unavailable' }, tr => {
    check(tr.textContent.includes('采集器没起来'), 'collector outage is named as an infrastructure failure');
    check(!tr.textContent.includes('页面打开了但没有 Buybox 价'), 'collector outage is not blamed on the product page');
  });
  await scenario({ ...known, status: 'success', source: 'local_collector', holding_buybox: true }, tr => {
    check(tr.textContent.includes('报价来源：本机采集器'), 'native quote source preserved');
    // 历史 holding_buybox=true 只代表当时同价，不是卖家身份证据，不能显示成"已确认持有"。
    check(tr.querySelectorAll('td')[2].textContent.includes('卖家未核实'), 'legacy holding flag is not verified identity');
    check(!tr.querySelectorAll('td')[2].textContent.includes('已确认'), 'legacy holding flag never claims confirmation');
  });
  await scenario({ status: 'queued' }, tr => {
    check(tr.querySelectorAll('td')[2].textContent.includes('未采集'), 'empty quote does not show success');
    // 「页面卖家」是证据，已随判断依据一起收进行尾的统一详情面板，所以按整行断言。
    // 这条要守的是「没采到就说未读取、不许编一个卖家名」，与它落在哪一格无关。
    check(tr.textContent.includes('页面卖家：未读取'), 'missing seller is not invented');
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
  // 反过来也要钉住：采集器按卖家名比对过（identity_verified=true）时，不能再写"疑似"。
  // 后端 price_ownership.estimate() 在 verified 为 True/False 时都会写 identity_verified=True，
  // 调价引擎也据此按持位在涨价；徽标若仍说"价格相同不能证明"，同一屏上两个结论互相打脸。
  await scenario({ ...known, status: 'success', price_ownership: {state:'matched',own_price:269,own_price_source:'official_live',own_price_asof:'2026-10-15T01:58:10Z',identity_verified:true} }, tr => {
    const pill = tr.querySelectorAll('td')[2].querySelector('.status-pill');
    check(pill.textContent.includes('已核实持位'), 'seller-name verified holding is not downgraded to a price guess');
    check(!pill.textContent.includes('疑似'), 'verified holding is never called suspected');
    check(pill.title.includes('Sold by'), 'verified holding names the evidence it rests on');
  });
  await scenario({ ...known, status: 'success', price_ownership: {state:'mismatch',own_price:269,own_price_source:'official_live',own_price_asof:'2026-10-15T01:58:10Z',identity_verified:true} }, tr => {
    const pill = tr.querySelectorAll('td')[2].querySelector('.status-pill');
    check(pill.textContent.includes('已核实'), 'seller-name verified mismatch says it was verified, not inferred');
    check(pill.title.includes('别家卖家'), 'verified mismatch explains it is the seller name, not the price');
  });
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
  // ---- 去冗余 / 变体无定语 / 决策卡片 ----
  await scenario({ ...known, status: 'success' }, tr => {
    const text = [...tr.querySelectorAll('td')].map(td => td.textContent).join('|');
    // 同一个金额在一行里只出现一次。原来本店价、Buybox 列、判断依据各印一遍。
    check((text.match(/R 269/g) || []).length === 1, 'the same price is printed once, not three times');
    check(tr.querySelectorAll('.row-details').length === 1, 'one detail panel per row instead of two');
  });

  await scenario({ ...known, status: 'success' }, tr => {
    check(!tr.querySelector('.variant-toggle'), 'single-variant product has no variant control');
  }, { variant: { base: '', count: 1, siblings: [] } });

  const variants = { base: '女士运动套装', count: 3, siblings: [
    { sku: 'test-sku', label: '黑色·S', price: 269, stock: 1 },
    { sku: 'b', label: '粉色·L', price: 269, stock: 2 },
    { sku: 'c', label: '红色·XL', price: 275, stock: 3 }] };
  await scenario({ ...known, status: 'success' }, tr => {
    check(tr.querySelector('.product-link').textContent === '女士运动套装', 'collapsed row shows the base name without qualifiers');
    check(!tr.textContent.includes('粉色·L'), 'qualifiers stay hidden until the variant list is expanded');
    check(tr.querySelector('.variant-toggle').textContent.includes('3'), 'collapsed state shows only the variant count');
  }, { variant: variants });

  await scenario({ ...known, status: 'success' }, async tr => {
    await act(async () => tr.querySelector('.variant-toggle').click());
    check(tr.textContent.includes('粉色·L') && tr.textContent.includes('红色·XL'), 'expanding reveals the qualifiers (colour / size)');
    check(!!tr.querySelector('.variant-list li.is-current'), 'the variant this row belongs to is marked');
  }, { variant: variants });

  await scenario({ ...known, status: 'success' }, tr => {
    const card = tr.querySelector('.decision-card');
    check(!!card && card.textContent.includes('抢位让利'), 'the pricing direction is named, not implied');
    check(card.textContent.includes('R 170') && card.textContent.includes('R 263'), 'floor and cheapest rival are on the card');
    check(card.textContent.includes('R 262'), 'the planned target is on the card');
  }, { rule: { enabled: true, use_system_min: true, plan: { regime: 'compete', target: 262,
    reason: '未持有 Buybox，降到 R 262 抢位', confidence: 'medium', risk: 'medium',
    floor: 170, ceiling: null, cheapest: 263, gap_pct: 2.6 } } });

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
    // 「立即采集」失败的行 enabled 是 false、request_once 才是它的开关。
    // 状态条把它算进失败数（所以写着「重试 1 个」），重试按钮却只挑 enabled 的行，
    // 于是点下去一条请求都不发，而且没有任何提示——按钮看起来像坏了。
    bulkCalls.length = 0;
    fixture = [row({ status: 'error', error_code: 'collector_error', enabled: false, request_once: true,
      last_success_at: new Date(Date.now() - 30 * 60000).toISOString(), next_due: Date.now() / 1000 + 1800 })];
    readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' });
    const once = createRoot(host);
    try {
      await act(async () => once.render(React.createElement(Page)));
      const retry = [...host.querySelector('.collection-bar').querySelectorAll('button')].find(b => b.textContent.includes('重试'));
      check(!!retry && retry.textContent.includes('重试 1 个'), 'a failed one-shot request is counted as retryable in the bar');
      await act(async () => retry.click());
      check(bulkCalls.length === 1, 'retrying a failed one-shot request actually calls the endpoint');
      check(bulkCalls[0].enabled === false, 'retrying a one-shot request must not switch on continuous collection');
      check(bulkCalls[0].request_once === true, 'retrying a one-shot request stays a one-shot request');
      check(bulkCalls[0].items.length === 1, 'the one-shot row is the one that gets retried');
    } finally { act(() => once.unmount()); }
  }
  {
    // 混合场景：状态条上的数字必须等于真正被重试的行数，否则用户点下去只会觉得「少了一条」。
    bulkCalls.length = 0;
    fixture = [
      row({ status: 'error', error_code: 'collector_error', enabled: true,
        last_success_at: new Date(Date.now() - 30 * 60000).toISOString(), next_due: Date.now() / 1000 + 1800 }),
      { ...row({ status: 'error', error_code: 'collector_error', enabled: false, request_once: true,
        last_success_at: new Date(Date.now() - 30 * 60000).toISOString(), next_due: Date.now() / 1000 + 1800 }),
        product_id: 'p2', offer_id: 'o2' },
    ];
    readPricing = async () => ({ rows: fixture, configured: true, collector_source: 'cloud_browser' });
    const mixed = createRoot(host);
    try {
      await act(async () => mixed.render(React.createElement(Page)));
      const retry = [...host.querySelector('.collection-bar').querySelectorAll('button')].find(b => b.textContent.includes('重试'));
      check(!!retry && retry.textContent.includes('重试 2 个'), 'both auto and one-shot failures are counted in the bar');
      await act(async () => retry.click());
      check(bulkCalls.length === 2, 'each collection mode is retried with its own intent');
      const auto = bulkCalls.find(c => c.enabled === true);
      const oneShot = bulkCalls.find(c => c.enabled === false);
      check(!!auto && auto.items.length === 1 && auto.request_once === false, 'the enabled row keeps collecting automatically');
      check(!!oneShot && oneShot.items.length === 1 && oneShot.request_once === true, 'the one-shot row is retried once, not switched to auto');
      check(bulkCalls.reduce((n, c) => n + c.items.length, 0) === 2, 'the bar count matches the rows actually retried');
    } finally { act(() => mixed.unmount()); }
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
  // 「被网站挡住」只能有一份判据：状态条的 blocked/建议间隔，和「重试」按钮
  // 决定要不要清 next_due，必须同源。两处各抄一份字面量的话，一边劝「别反复点」、
  // 一边帮你立刻再打一次，正好是页面正文在劝用户别做的事。
  const status = localModule('../utils/buyboxStatus');
  for (const code of ['site_challenge', 'rate_limited', 'http_forbidden']) {
    check(status.isThrottled(code), `${code} must be treated as throttled`);
    const fleet = status.describeFleet(
      [{ ...row({ status: 'error', enabled: true, error_code: code, last_success_at: null, buybox_price: null }) }],
      { now: Date.now() });
    check(fleet.blocked === 1 && fleet.retryable === 0, `${code} must land in blocked, never retryable`);
  }
  check(!status.isThrottled('collector_unavailable'), 'collector_unavailable self-heals, so it is retryable not blocked');
  check(!status.isThrottled(undefined), 'a missing error code is never treated as blocked');
  // 失败行的集合与「重试哪些行」必须同源：describeFleet 数出来的行数，
  // 和 retryGroups 交出去的 items 总数，必须永远相等。
  {
    const failing = (m) => ({ product_id: 'p', offer_id: 'o', monitor: m });
    const rows = [
      failing({ status: 'error', error_code: 'collector_error', enabled: true }),
      failing({ status: 'error', error_code: 'collector_error', enabled: false, request_once: true }),
      failing({ status: 'error', error_code: 'site_challenge', enabled: true }),
      failing({ status: 'error', error_code: 'collector_error', enabled: false, request_once: false }),
      failing({ status: 'success', enabled: true }),
    ];
    check(status.failingRows(rows).length === 3, 'a stopped row is not a failure; a one-shot request is');
    const groups = status.retryGroups(rows);
    check(groups.length === 3, 'auto/one-shot and blocked/retryable form separate batches');
    check(groups.reduce((n, g) => n + g.items.length, 0) === 3, 'retryGroups covers exactly the failing rows');
    check(groups.filter(g => g.keep_backoff).every(g => g.items.every(r => r.monitor.error_code === 'site_challenge')),
      'only throttled rows keep their backoff');
    check(groups.filter(g => g.request_once).every(g => g.items.every(r => !r.monitor.enabled)),
      'request_once batches contain only rows the user never enabled');
    check(status.retryGroups([]).length === 0 && status.failingRows(null).length === 0, 'empty input yields no retry work');
  }
  console.log(`PASS ${checks} Buybox page assertions (jsdom, isolated API)`);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
