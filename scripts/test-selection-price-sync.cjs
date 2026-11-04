/**
 * 选品卡片：现货最低价、利润率、已建产品可回推。
 *
 * 四件事钉在这里：
 *  1. 售价要标出口径。「现货最低价」和前台显示的价不是一回事——前台显示的是
 *     配送最快那家的报价，用户看到抓到的价比前台低时会以为抓错了。
 *  2. 卡片上要同时给单件利润和利润率：能不能做看的是利润率（≥15%），
 *     只给绝对金额没法横向比。
 *  3. 已建产品的行不能锁死。改完要能回推到产品主档（按钮 + 批量按钮），
 *     而且产品列表里被人工改过的字段由后端保护，前端只管把 id 传对。
 *  4. 「重抓全部资料」要跑多轮（一次只处理 400 个），每轮都得带 refresh，
 *     并且在某一轮没修好任何价格时收手——否则 1700 多行永远只有最早那批换价。
 *  5. 每条未建档的卡片自己带「加入产品列表」，不用勾选再滚回顶部点批量；
 *     已建档的卡片不能出现这个按钮（它走的是「重新推送」）。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid/selection' });
for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event']) {
  Object.defineProperty(globalThis, k, { value: dom.window[k], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');

function compile(p, mocks = {}) {
  const f = path.resolve(__dirname, '../' + p);
  const m = new Module(f, module);
  m.filename = f;
  m.paths = Module._nodeModulePaths(path.dirname(f));
  const orig = m.require.bind(m);
  m.require = n => (n in mocks ? mocks[n] : orig(n));
  m._compile(
    ts.transpileModule(fs.readFileSync(f, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    f,
  );
  return m.exports;
}

const items = [
  {
    id: 'a', product_name: 'In Stock Cheapest', tier: 'S', selected: false,
    length_mm: 300, width_mm: 200, height_mm: 100, actual_weight_kg: 1.2,
    actual_sale_price_zar: 178, price_source: 'in_stock',
    listed_price_zar: 309, lowest_in_stock_price_zar: 178, lowest_offer_price_zar: 178,
    seller_offer_count: 3,
    purchase_cost_cny: 40, shipping_method: '海运普货', fee_category: 'Home',
    profit_margin: 0.32, profit_zar: 95.6, promoted_product_id: null,
  },
  {
    id: 'b', product_name: 'Promoted Edited', tier: 'A', selected: false,
    length_mm: 300, width_mm: 200, height_mm: 100, actual_weight_kg: 1.2,
    actual_sale_price_zar: 299, price_source: 'in_stock',
    listed_price_zar: 299, lowest_in_stock_price_zar: 299, lowest_offer_price_zar: 299,
    purchase_cost_cny: 40, shipping_method: '海运普货', fee_category: 'Home',
    profit_margin: 0.3, profit_zar: 90, promoted_product_id: 'prod-9', pool_dirty: true,
  },
];

const syncCalls = [];
const enrichCalls = [];
const promoteCalls = [];
const remote = {
  // 本机采集服务（抓 Takealot 前台）：进页面就会探活，桩必须给，否则 mount 即抛。
  getSelectionAgentBase: () => 'http://127.0.0.1:8770',
  setSelectionAgentBase: () => undefined,
  getSelectionAgentHealth: async () => ({
    ok: true, busy: false, queued: 0, backend: 'http://127.0.0.1:8123',
    sorts: ['BestSellers'], default_sort: 'BestSellers', audit_default_count: 20, requires_token: false,
  }),
  createSelectionCrawl: async () => ({ id: 'crawl-1', status: 'pending' }),
  getSelectionCrawlTask: async () => ({ id: 'crawl-1', status: 'pending' }),
  cancelSelectionCrawl: async () => ({ id: 'crawl-1', status: 'cancelled' }),
  getActiveStoreId: () => 'fixture-store',
  getSelectionBatches: async () => [],
  getSelectionItems: async () => ({ total: items.length, items }),
  getSelectionSummary: async () => ({
    total: 2, pending: 0, qualified: 2, rejected: 0, promoted: 1,
    selected: 0, duplicates: 0, with_image: 0, with_dimensions: 2, no_plid: 0,
    pending_sync: 1, price_in_stock: 2, price_manual: 0,
  }),
  getPortalTokenState: async () => ({ configured: true, expires_at: null, expired: false, days_left: null }),
  setPortalToken: async () => ({ configured: true, expires_at: null, expired: false, days_left: null }),
  getFeeCategories: async () => [],
  getImageUrl: x => x,
  // 三轮：前两轮各修好一批价格，第三轮一条都没修好 → 前端该收手，
  // 不能因为 remaining 还有就一路空转到 40 轮上限。
  // promoted_price_synced 每轮各推各的，前端要累加着报，不能只看最后一轮。
  enrichSelection: async payload => {
    enrichCalls.push(payload);
    const rounds = [
      { processed: 400, ok: 400, failed: 0, pending: 0, remaining: 1200, price_fixed: 300, promoted_price_synced: 4 },
      { processed: 400, ok: 400, failed: 0, pending: 0, remaining: 800, price_fixed: 200, promoted_price_synced: 3 },
      { processed: 400, ok: 400, failed: 0, pending: 0, remaining: 400, price_fixed: 0, promoted_price_synced: 1 },
    ];
    const base = { public_access_blocked: false, portal_limited: false, portal_retry_after: 0,
      from_product_list: 0, with_dimensions: 0, skipped_network: 0, names_filled: 0, battery_judged: 0 };
    return { ...base, ...rounds[Math.min(enrichCalls.length - 1, rounds.length - 1)] };
  },
  syncSelectionToProducts: async ids => {
    syncCalls.push(ids);
    return { updated: ids.length, skipped: 0, products: [], skip_details: [] };
  },
  promoteSelection: async ids => {
    promoteCalls.push(ids);
    return {
      created: 1,
      skipped: 0,
      products: [{ id: 'prod-new', product_id: 'prod-new', product_no: 'P000123', product_name: 'In Stock Cheapest' }],
      skip_details: [],
    };
  },
};

const page = compile('src/pages/Selection.tsx', {
  '../remoteApi': remote,
  'react-router-dom': {
    Link: ({ to, children }) => React.createElement('a', { href: to }, children),
  },
  '../types': {
    SHIPPING_METHODS: ['海运普货', '海运带电'],
    DEFAULT_SHIPPING_METHOD: '海运普货',
    BATTERY_STATUS_LABEL: {},
    formatPrice: v => (v == null ? '-' : String(v)),
    formatPercent: v => `${(v * 100).toFixed(1)}%`,
  },
  '../utils/useFeeCategories': { useFeeCategories: () => ({ categories: [] }) },
  '../utils/imageFallback': { ProductImage: () => null },
  '../utils/selectionPromotion': compile('src/utils/selectionPromotion.ts'),
  '../utils/datetime': compile('src/utils/datetime.ts'),
}).default;

const root = createRoot(document.getElementById('root'));

(async () => {
  await act(async () => root.render(React.createElement(page)));

  const card = id => {
    const node = [...document.querySelectorAll('.selection-card')].find(
      n => n.textContent.includes(items.find(i => i.id === id).product_name),
    );
    assert.ok(node, `card ${id} should render`);
    return node;
  };

  // 1. 售价口径要写在卡片上，不能只给一个数
  const a = card('a');
  const priceBlock = a.querySelector('.selection-price');
  assert.match(priceBlock.textContent, /178/, 'card shows the in-stock lowest price');
  assert.match(priceBlock.textContent, /现货最低价/, 'card must label the price basis');
  assert.match(priceBlock.getAttribute('title'), /前台展示价 R 309/,
    'hover text explains why it differs from the front page');

  // 2. 利润率要和单件利润一起给
  const result = a.querySelector('.selection-result');
  assert.match(result.textContent, /单件利润/);
  assert.match(result.textContent, /利润率/);
  assert.match(result.textContent, /32\.0%/);
  assert.ok(result.querySelector('dd.is-good'), 'qualified margin is highlighted');

  // 3. 已建产品：能编辑（不再锁死）、有「待更新」标记、有回推按钮
  const b = card('b');
  assert.ok(b.querySelector('.selection-flag.is-done'), 'promoted card is marked');
  assert.ok(b.querySelector('.selection-flag.is-sync'), 'dirty pool row is marked 待更新');
  const disabled = [...b.querySelectorAll('input, select')].filter(n => n.disabled);
  assert.equal(disabled.length, 0, 'promoted rows must stay editable');
  const syncBtn = [...b.querySelectorAll('button')].find(n => /更新到产品列表/.test(n.textContent));
  assert.ok(syncBtn, 'promoted card needs a re-push button');

  syncCalls.length = 0;
  await act(async () => syncBtn.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  assert.deepEqual(syncCalls, [['b']], 're-push sends exactly that item id');

  // 4. 单条建档：看到一条合适的直接加，不用先勾选、再滚回顶部点批量按钮
  const promoteBtn = [...a.querySelectorAll('button')].find(n => /加入产品列表/.test(n.textContent));
  assert.ok(promoteBtn, 'unpromoted card needs a one-click promote button');
  assert.equal(
    [...b.querySelectorAll('button')].some(n => /加入产品列表/.test(n.textContent)),
    false,
    'already-promoted card must not offer to promote again',
  );
  promoteCalls.length = 0;
  await act(async () => promoteBtn.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  assert.deepEqual(promoteCalls, [['a']], 'single promote sends exactly that item id');
  const promoteNotice = document.querySelector('.selection-notice').textContent;
  assert.match(promoteNotice, /已把「In Stock Cheapest」加入产品列表/, 'confirm which item was promoted');
  assert.match(promoteNotice, /P000123/, 'give the product number so it can be found in the list');

  // 5. 「重抓」要跑多轮：一次只处理 400 个，只在第一轮传 refresh 的话，
  //    1700 多行里永远只有最早那 400 行被重新取价。
  enrichCalls.length = 0;
  const refreshBtn = [...document.querySelectorAll('button')].find(n => /^重抓/.test(n.textContent));
  assert.ok(refreshBtn, 'toolbar needs the refresh button');
  // 「跳过已测算」默认勾着，按钮文案得跟着变——不然用户以为自己在重抓全部。
  assert.match(refreshBtn.textContent, /重抓未测算的资料/);
  await act(async () => refreshBtn.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  assert.ok(enrichCalls.length >= 2, 'refresh must run more than one round to cover every row');
  assert.ok(enrichCalls.every(c => c.refresh === true), 'every refresh round must ask for refresh');
  assert.ok(enrichCalls.every(c => c.skip_measured === true), 'measured rows are skipped by default');
  assert.equal(enrichCalls.length, 3, 'stops once a round fixes no price (no progress)');
  const notice = document.querySelector('.selection-notice').textContent;
  assert.match(notice, /500 个售价换成了现货最低价/, 'tells the user how many prices actually changed');
  assert.match(notice, /顺带更新了 8 个已建档产品的售价/,
    'promoted products get the new price too, summed across rounds (4+3+1)');
  assert.match(notice, /换不到现货最低价/, 'explains the leftovers instead of asking to click again');

  // 6. 取消「跳过已测算」后：按钮文案回到「重抓全部资料」，请求也不再带 skip_measured
  const skipBox = document.querySelector('.selection-skip-measured input');
  assert.ok(skipBox, 'the skip-measured switch sits next to the fetch button');
  assert.equal(skipBox.checked, true);
  await act(async () => skipBox.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  const fullBtn = [...document.querySelectorAll('button')].find(n => /^重抓/.test(n.textContent));
  assert.match(fullBtn.textContent, /重抓全部资料/);
  enrichCalls.length = 0;
  await act(async () => fullBtn.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  assert.ok(enrichCalls.every(c => c.skip_measured === false), 'unchecked switch must reach the backend');

  await act(async () => root.unmount());
  console.log('PASS 选品卡片：现货最低价口径、利润率、已建产品可编辑并回推、单条建档');
})().catch(e => {
  console.error(e);
  process.exitCode = 1;
});
