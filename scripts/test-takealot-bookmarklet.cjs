// 书签脚本与页面导入的价格解析回归（jsdom，零网络）。
// 重点验证：parseZarPrice 被 toString() 注入书签后仍然可用，且逗号小数不再放大 100 倍。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');

const srcDir = path.resolve(__dirname, '../src');
const cache = new Map();

function compileModule(file) {
  if (cache.has(file)) return cache.get(file);
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const loaded = new Module(file, module);
  loaded.filename = file;
  loaded.paths = Module._nodeModulePaths(path.dirname(file));
  cache.set(file, loaded.exports);
  loaded._compile(output, file);
  cache.set(file, loaded.exports);
  return loaded.exports;
}

// 让 require('./utils/price') 能解析到 .ts（Node 默认不认这个扩展名）
const originalRequire = Module.prototype.require;
Module.prototype.require = function patched(request) {
  try {
    return originalRequire.apply(this, arguments);
  } catch (error) {
    if (!error || error.code !== 'MODULE_NOT_FOUND' || !request.startsWith('.')) throw error;
    const base = path.resolve(path.dirname(this.filename), request);
    for (const extension of ['.ts', '.tsx']) {
      if (fs.existsSync(base + extension)) return compileModule(base + extension);
    }
    throw error;
  }
};

const { makeTakealotBookmarklet, parseTakealotPageImport, captureTakealotPage } = compileModule(
  path.join(srcDir, 'takealotPageImport.ts'),
);
const { parseZarPrice } = compileModule(path.join(srcDir, 'utils/price.ts'));

let checks = 0;
function check(value, description) {
  assert.ok(value, description);
  checks++;
}

const PAGE = (priceHtml, stockText = '') => `<!doctype html><html><body><main>
  <h1>金色项链</h1>
  <img alt="金色项链" src="https://media.takealot.com/covers_images/abc-s-pdpxl.jpg">
  <div data-ref="price">${priceHtml}</div>
  <table><tr><td>Categories</td><td><a>Jewellery</a><a>Necklaces</a></td></tr></table>
  <div class="stock">${stockText}</div>
</main></body></html>`;

function runBookmarklet(html, pageUrl = 'https://www.takealot.com/necklace/PLID99024164') {
  const dom = new JSDOM(html, { url: pageUrl, runScripts: 'dangerously' });
  const opened = [];
  dom.window.open = (url) => {
    opened.push(url);
    return null;
  };
  const script = decodeURIComponent(makeTakealotBookmarklet('https://example.invalid/create').slice('javascript:'.length));
  dom.window.eval(script);
  assert.equal(opened.length, 1, '书签脚本应打开一个导入页');
  const payload = decodeURIComponent(opened[0].split('#takealot-import=')[1]);
  return JSON.parse(payload);
}

// 1. 书签脚本在打包后仍能拿到价格函数（序列化假设成立）
const captured = runBookmarklet(PAGE('<span class="currency">R 1 299,00</span>'));
check(captured.price === 1299, `书签解析逗号小数应为 1299，实际 ${captured.price}`);
check(captured.title === '金色项链', '书签读到商品标题');
check(captured.category === 'Jewellery > Necklaces', '书签读到分类路径');
check(captured.outOfStock === false, '在售商品不应标记缺货');

// 2. 旧实现会在这里得到 129900
const spaced = runBookmarklet(PAGE('<span class="currency">R1 299,00</span>'));
check(spaced.price === 1299, `空格千分位 + 逗号小数应为 1299，实际 ${spaced.price}`);

// 3. 点小数不能被改坏
const dotted = runBookmarklet(PAGE('<span class="currency">R 2,499.99</span>'));
check(dotted.price === 2499.99, `逗号千分位 + 点小数应为 2499.99，实际 ${dotted.price}`);

// 4. 缺货识别
const soldOut = runBookmarklet(PAGE('<span class="currency">R 899,00</span>', 'Supplier out of stock'));
check(soldOut.outOfStock === true, '缺货文案应被识别');
check(soldOut.price === 899, `缺货商品仍应读到展示价，实际 ${soldOut.price}`);

// 5. 导入后的字段语义：缺货时 in_stock_price 必须为 null（与实际售价分离）
const importPayload = (payload) =>
  parseTakealotPageImport(JSON.stringify({ ...payload, image: payload.image || 'https://media.takealot.com/covers_images/abc-s-pdpxl.jpg' }));

const inStock = importPayload(captured);
check(inStock.actual_sale_price_zar === 1299, '在售：实际售价 1299');
check(inStock.in_stock_price === 1299, '在售：在货价等于售价');

const outOfStock = importPayload(soldOut);
check(outOfStock.actual_sale_price_zar === 899, '缺货：仍保留展示价 899');
check(outOfStock.in_stock_price === null, '缺货：在货价必须为 null，不能照抄展示价');
check(
  outOfStock.warnings.some((warning) => warning.includes('缺货')),
  '缺货：必须给出提示',
);

// 6. captureTakealotPage 作为参数注入的解析函数仍被调用（防止签名改动后悄悄退化）
let called = 0;
const direct = captureTakealotPage(
  new JSDOM(PAGE('<span class="currency">R 299,00</span>')).window.document,
  'https://www.takealot.com/necklace/PLID99024164',
  (text) => {
    called++;
    return parseZarPrice(text);
  },
);
check(called === 1, 'captureTakealotPage 应调用注入的解析函数一次');
check(direct.price === 299, `注入解析器后价格应为 299，实际 ${direct.price}`);

// 7. 非 Takealot 域名必须拒绝
let rejected = false;
try {
  captureTakealotPage(
    new JSDOM(PAGE('<span class="currency">R 299,00</span>')).window.document,
    'https://example.invalid/PLID123',
    parseZarPrice,
  );
} catch {
  rejected = true;
}
check(rejected, '非 Takealot 域名应拒绝');

console.log(`PASS ${checks} bookmarklet/page-import assertions (jsdom, zero network)`);
