/**
 * 选品卡片：未测算 / 已测算 的区分。
 *
 * 背景：1700 条候选里，已经填完重尺和成本、算出利润率的只有一部分。原来这两种
 * 卡片长得几乎一样——差别只在卡片最底部一个「待补数据 / 利润率 32%」的徽章，
 * 一屏 12 张时基本看不到。现在靠四样东西区分：
 *   1. 卡片类名 is-unmeasured / is-measured / is-promoted（左侧状态轨靠它上色）
 *   2. 未测算的卡片顶部有「未测算」标签
 *   3. 未测算时结果区列出「还缺 长宽高、采购价」，而不是一句「待补数据」
 *   4. 列表按这两类分成两个可折叠分区，已测算的默认折叠（折叠时卡片不进 DOM）
 * 外加汇总条上的进度块，点一下切到未测算。
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

// 四种典型状态。字段故意只填一部分，好验证「还缺什么」是按真实缺项算的。
const items = [
  {
    id: 'a', product_name: 'Unmeasured Yoga Mat', tier: 'S', selected: false,
    length_mm: null, width_mm: null, height_mm: null, actual_weight_kg: 1.2,
    actual_sale_price_zar: 299, purchase_cost_cny: null, shipping_method: '', fee_category: '',
    profit_margin: null, profit_zar: null, promoted_product_id: null,
  },
  {
    id: 'b', product_name: 'Measured Good Item', tier: 'A', selected: false,
    length_mm: 300, width_mm: 200, height_mm: 100, actual_weight_kg: 1.2,
    actual_sale_price_zar: 299, purchase_cost_cny: 40, shipping_method: '海运普货', fee_category: 'Home',
    profit_margin: 0.32, profit_zar: 95.6, promoted_product_id: null,
  },
  {
    id: 'c', product_name: 'Measured Thin Margin', tier: 'B', selected: false,
    length_mm: 300, width_mm: 200, height_mm: 100, actual_weight_kg: 1.2,
    actual_sale_price_zar: 199, purchase_cost_cny: 60, shipping_method: '海运普货', fee_category: 'Home',
    profit_margin: 0.04, profit_zar: 8, promoted_product_id: null,
  },
  {
    id: 'd', product_name: 'Promoted Item', tier: 'C', selected: false,
    length_mm: 300, width_mm: 200, height_mm: 100, actual_weight_kg: 1.2,
    actual_sale_price_zar: 299, purchase_cost_cny: 40, shipping_method: '海运普货', fee_category: 'Home',
    profit_margin: 0.3, profit_zar: 90, promoted_product_id: 'prod-1',
  },
];

const reads = [];
const remote = {
  getActiveStoreId: () => 'fixture-store',
  getSelectionBatches: async () => [],
  getSelectionItems: async params => {
    reads.push(params);
    return { total: items.length, items };
  },
  getSelectionSummary: async () => ({
    total: 4, pending: 1, qualified: 1, rejected: 1, promoted: 1,
    selected: 0, duplicates: 0, with_image: 0, with_dimensions: 3, no_plid: 0,
  }),
  getPortalTokenState: async () => ({ configured: true, expires_at: null, expired: false, days_left: null }),
  setPortalToken: async () => ({ configured: true, expires_at: null, expired: false, days_left: null }),
  getFeeCategories: async () => [],
  getImageUrl: x => x,
};

const page = compile('src/pages/Selection.tsx', {
  '../remoteApi': remote,
  // 卡片里的「查看产品」是 <Link>，它要 Router 上下文；这里只需要一个能渲染的 a。
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

  // 未测算：类名 + 顶部标签 + 缺项清单
  const a = card('a');
  assert.match(a.className, /\bis-unmeasured\b/);
  assert.doesNotMatch(a.className, /\bis-measured\b/);
  assert.ok(a.querySelector('.selection-flag.is-unmeasured'), 'unmeasured card needs a header pill');
  assert.match(a.querySelector('.selection-flag.is-unmeasured').textContent, /未测算/);
  const missing = a.querySelector('.selection-missing').textContent;
  // 长宽高缺、重量有、售价有、采购价缺、物流方式空、类目空
  assert.match(missing, /长宽高/);
  assert.match(missing, /采购价/);
  assert.match(missing, /物流方式/);
  assert.match(missing, /等 4 项/);
  assert.doesNotMatch(missing, /重量|售价/);
  assert.ok(a.querySelector('.selection-margin.is-unmeasured'), 'bottom badge should say 未测算');

  // 两个分区：未测算展开、已测算折叠。折叠时那些卡片根本不该进 DOM——不是藏起来，
  // 是没渲染，否则「一页 60 张算好的卡片」照样要排版、照样拖慢滚动。
  const sections = [...document.querySelectorAll('.selection-section')];
  assert.equal(sections.length, 2, 'two sections: 未测算 / 已测算');
  const [unmeasuredSection, measuredSection] = sections;
  assert.match(unmeasuredSection.textContent, /未测算/);
  assert.match(unmeasuredSection.textContent, /1/, '未测算 count');
  assert.equal(unmeasuredSection.querySelectorAll('.selection-card').length, 1);
  assert.match(measuredSection.textContent, /已测算/);
  assert.match(measuredSection.textContent, /3/, '已测算 count');
  assert.equal(measuredSection.querySelector('.selection-section-head').getAttribute('aria-expanded'), 'false');
  assert.equal(measuredSection.querySelectorAll('.selection-card').length, 0,
    'collapsed section must not render cards');

  // 点标题展开已测算（整条标题都是按钮，不用瞄准三角）
  await act(async () => measuredSection.querySelector('.selection-section-head')
    .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  const expanded = document.querySelectorAll('.selection-section')[1];
  assert.equal(expanded.querySelector('.selection-section-head').getAttribute('aria-expanded'), 'true');
  assert.equal(expanded.querySelectorAll('.selection-card').length, 3);

  // 已测算（达标）：不挂未测算标签，徽章给结论「合格」
  const b = card('b');
  assert.match(b.className, /\bis-measured\b/);
  assert.doesNotMatch(b.className, /\bis-below\b/);
  assert.equal(b.querySelector('.selection-flag.is-unmeasured'), null);
  assert.equal(b.querySelector('.selection-missing'), null);
  // 徽章只说结论，数字在结果区网格的「利润率」一行——两边都写一遍是重复。
  assert.match(b.querySelector('.selection-margin').textContent, /合格/);
  assert.doesNotMatch(b.querySelector('.selection-margin').textContent, /利润率/);
  assert.match(b.querySelector('.selection-result dl').textContent, /利润率/);

  // 已测算但不够 15%：左轨要标红（is-below），徽章给「不合格」
  const c = card('c');
  assert.match(c.className, /\bis-measured\b/);
  assert.match(c.className, /\bis-below\b/);
  assert.match(c.querySelector('.selection-margin').textContent, /不合格/);

  // 已建产品：既不算未测算，也不该被当成可继续测算的卡片
  const d = card('d');
  assert.match(d.className, /\bis-promoted\b/);
  assert.doesNotMatch(d.className, /\bis-unmeasured\b/);

  // 汇总条：3/4 已测算 = 75%，点一下切到未测算
  const progress = document.querySelector('.selection-progress');
  assert.ok(progress, 'summary needs a progress block');
  assert.match(progress.textContent, /3/);
  assert.match(progress.textContent, /\/ 4 已测算/);
  assert.match(progress.textContent, /75%/);
  assert.match(progress.textContent, /未测算 1 个/);
  assert.equal(progress.tagName, 'BUTTON', 'progress must be clickable');

  reads.length = 0;
  await act(async () => progress.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
  assert.ok(
    reads.some(r => r.status === '待核算'),
    'clicking progress must request only unmeasured items',
  );
  assert.match(document.querySelector('.selection-progress').className, /is-active/);

  // 再点一次回到全部
  reads.length = 0;
  await act(async () =>
    document.querySelector('.selection-progress').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })),
  );
  assert.ok(
    reads.some(r => !r.status),
    'second click must clear the status filter',
  );
  assert.doesNotMatch(document.querySelector('.selection-progress').className, /is-active/);

  await act(async () => root.unmount());
  console.log('PASS 选品卡片区分未测算/已测算：类名、标签、缺项清单、进度块筛选');
})().catch(e => {
  console.error(e);
  process.exitCode = 1;
});
