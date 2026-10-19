// 调价列表的版式约束：商品格要有图，价格和状态胶囊不能挤成一坨。
// jsdom 没有排版引擎，量不出「重叠」；所以这里分两层：
//   1) 结构层：缩略图、英文名、价格与胶囊必须是各自独立的节点；
//   2) 样式层：直接读 index.css，钉住「胶囊可换行」「下拉有上限」这两条真正的修复。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const watchdog = setTimeout(() => { console.error('FAIL pricing suite did not complete within 10s'); process.exit(1); }, 10000);
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid/auto-pricing' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'localStorage'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { MemoryRouter } = require('react-router-dom');

let writes = 0;
const rows = [];
const remote = {
  getAutomaticPricing: async () => ({ rows: rows.map(r => ({ ...r })), loop: null, configured: true, collector_source: 'cloud_browser' }),
  configureAutomaticPricing: async () => { writes += 1; return { ok: true }; },
  pauseAutomaticPricing: async () => { writes += 1; return { ok: true }; },
  reconcileAutomaticPricing: async () => { writes += 1; return { ok: true }; },
  bulkAutomaticPricing: async () => { writes += 1; return { ok: true }; },
  pauseAllAutomaticPricing: async () => { writes += 1; return { ok: true }; },
  configureBuyboxMonitor: async () => { writes += 1; return { ok: true }; },
  collectBuyboxNow: async () => { writes += 1; return { ok: true }; },
  bulkBuyboxMonitor: async () => { writes += 1; return { ok: true }; },
  getImageUrl: (u) => `/api/image-proxy?url=${encodeURIComponent(u)}`,
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
  m.require = n => (n in mocks ? mocks[n] : (localModule(n, target) || orig(n)));
  m._compile(ts.transpileModule(fs.readFileSync(target, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, target);
  return m.exports;
}

const Page = compile(path.resolve(__dirname, '../src/pages/AutoPricing.tsx'), {
  '../remoteApi': remote,
  '../utils/useVisibleInterval': { useVisibleInterval: () => {}, isPageHidden: () => false },
}).default;

const row = (over) => ({
  product_id: 'p1', offer_id: 'o1', sku: '9900000000001', name: 'Wireless Adapter',
  chinese_name: '无线转接器', name_status: 'ok', url: 'https://example.invalid/p1',
  price: 269, stock: 11, suggested_min: 170, image: '', group: '未分类',
  monitor: { status: 'success', enabled: true, last_success_at: '2026-10-17T10:00:00Z', buybox_price: 269, price_ownership: { state: 'matched' } },
  variant: { base: '无线转接器', count: 1, siblings: [] },
  rule: { strategy: 'takealot_buybox', enabled: false, use_system_min: true, min_price: null, max_price: null, last_evaluated_at: null },
  ...over,
});

rows.push(
  // 有图、中文名与英文名不同 → 两行都显示
  row({ image: 'https://takealot.example/cover/s.file' }),
  // 无图 → 占位方块，不是裂图
  row({ product_id: 'p2', offer_id: 'o2', sku: '9900000000002', name: 'Camping Hat', chinese_name: '露营帽', image: '', price: 1999 }),
  // 没有中文名 → 标题回退到「中文名待核对」，英文名必须还在
  row({ product_id: 'p3', offer_id: 'o3', sku: '9900000000003', name: 'Dog Ball', chinese_name: '', name_status: 'missing', image: '' }),
  // 配额用满 → 详情里要说清「今天不会再动」，否则看到的是「运行中但价格不变」
  row({
    product_id: 'p4', offer_id: 'o4', sku: '9900000000004', name: 'Quota Hat', chinese_name: '配额帽', image: '',
    rule: {
      strategy: 'takealot_buybox', enabled: true, use_system_min: true, min_price: null, max_price: null,
      last_evaluated_at: null, max_daily_changes: 10, today_changes: 10,
      change_day: new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Johannesburg' }).format(new Date()),
      plan: { regime: 'hold', reason: '今日改价次数已用满', confidence: 'low', risk: 'low', floor: 100, ceiling: null,
              cheapest: null, live_count: 0, dark_count: 0, sole_seller: false },
    },
  }),
);

const host = document.getElementById('root');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const root = createRoot(host);
(async () => {
  await act(async () => root.render(React.createElement(MemoryRouter, { initialEntries: ['/auto-pricing'] }, React.createElement(Page))));
  await act(async () => { await Promise.resolve(); });

  const rowOf = (sku) => [...host.querySelectorAll('.pricing-table tbody tr')].find(tr => tr.textContent.includes(sku));
  const first = rowOf('9900000000001');

  // 1) 缩略图：有图就出 <img>，没图给占位方块，且不把「图暂不可用」五个字塞进 44px。
  const thumb = first.querySelector('.product-thumb');
  check(!!thumb, 'every product cell has a thumb slot');
  check(!!thumb.querySelector('img'), 'a product with an image renders a thumbnail');
  check(thumb.querySelector('img').getAttribute('src').includes('image-proxy'), 'the thumbnail goes through the image proxy');
  check(thumb.querySelector('img').getAttribute('alt') === '', 'the thumbnail is decorative: the row already names the product');
  const noImg = rowOf('9900000000002').querySelector('.product-thumb');
  check(noImg.classList.contains('is-empty'), 'a product without an image gets the placeholder, not a broken img');
  check(!noImg.querySelector('img'), 'the placeholder is not an img element');
  check(noImg.textContent.trim() === '无图', 'the placeholder says 无图, short enough for 44px');

  // 2) 中文名 / 英文名 / SKU 三行分开，英文名不挤在名字后面
  check(first.querySelector('.product-link').textContent === '无线转接器', 'the link shows the Chinese name');
  const en = first.querySelector('.product-en');
  check(!!en, 'the English title gets its own line when it differs from the Chinese name');
  check(en.textContent === 'Wireless Adapter', 'the English line holds the platform title');
  check(en.getAttribute('title') === 'Wireless Adapter', 'the English line keeps the full title on hover');
  check(rowOf('9900000000003').querySelector('.product-en').textContent === 'Dog Ball', 'a row without a Chinese name still shows its English title');

  // 3) 价格与归属胶囊是两个独立节点：胶囊放不下时能整块换行，而不是压在价格上。
  const quote = first.querySelector('.quote-line');
  check(!!quote, 'the price and the ownership pill live in one line box');
  check(quote.children.length === 2, 'the line box holds exactly the price and the pill');
  check(quote.children[0].classList.contains('own-price'), 'the price is its own element');
  check(quote.children[1].classList.contains('status-pill'), 'the ownership label is its own pill');
  check(first.querySelector('.quote-sub').textContent.includes('Buybox'), 'the Buybox detail stays on a separate line');

  // 4) 样式层：真正的修复写在 CSS 里，这里直接读源码钉住。
  const css = fs.readFileSync(path.resolve(__dirname, '../src/index.css'), 'utf8');
  const rule = (selector) => {
    const i = css.indexOf(selector);
    // 源码里冒号后的空格不统一（flex-wrap:wrap / flex: 0 0 auto），比对前先抹平。
    return i < 0 ? '' : css.slice(i, css.indexOf('}', i)).replace(/:\s+/g, ':');
  };
  const quoteLine = rule('.pricing-table .quote-cell .quote-line');
  check(quoteLine.includes('flex-wrap:wrap'), 'the quote line may wrap, so a long ownership label drops to its own line');
  check(quoteLine.includes('row-gap:4px'), 'wrapped lines keep a vertical gap');
  check(rule('.pricing-table .quote-line .status-pill').includes('flex:0 0 auto'), 'the pill never shrinks into the price');
  const select = rule('.pricing-table .price-range select');
  check(select.includes('max-width:200px'), 'the strategy select is capped: it must not stretch across the row');
  check(rule('.pricing-table thead th:nth-child(5)').includes('430px'), 'the pricing column has a fixed width instead of eating all the slack');
  check(!/pricing-table thead th:nth-child\(2\)/.test(css), 'the product column is the only elastic one');

  // 5) 配额：用满时必须自己说出来。后端 max_daily_changes 是硬闸，
  //    闸落下来的时候界面还在说「运行中」，用户只会判定成故障。
  const capped = rowOf('9900000000004');
  const quotaLine = [...capped.querySelectorAll('.decision-card span')].find(s => s.textContent.includes('今日改价'));
  check(!!quotaLine, 'the decision card shows how many changes were used today');
  check(quotaLine.textContent.includes('10/10'), 'the counter shows used against the daily cap');
  check(quotaLine.textContent.includes('已用满'), 'a used-up quota says so instead of leaving the reason blank');
  check(!!quotaLine.querySelector('b.text-warning'), 'a used-up quota is highlighted, not printed in the same tone as normal');

  check(writes === 0, 'rendering the table never writes to the backend');
  await act(async () => root.unmount());
  console.log(`PASS ${checks} pricing layout assertions (jsdom + css, isolated API)`);
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { clearTimeout(watchdog); dom.window.close(); });
