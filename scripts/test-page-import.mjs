import fs from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('src/takealotPageImport.ts', 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const module = { exports: {} };
vm.runInNewContext(code, { exports: module.exports, module, URL });
const { captureTakealotPage, makeTakealotBookmarklet, parseTakealotPageImport, importSavedTakealotPage } = module.exports;
const url = 'https://www.takealot.com/test/PLID104063259';
const payload = { version: 1, url, title: '5-Pack Zenty Dust Bags', image: 'https://media.takealot.com/covers_images/real/s-pdpxl.file', price: 599, category: 'Home & Kitchen', outOfStock: true };
const imported = parseTakealotPageImport(JSON.stringify(payload));
assert.equal(imported.actual_sale_price_zar, 599);
assert.equal(imported.in_stock_price, null);
assert.equal(imported.tsin, 'PLID104063259');
for (const patch of [{ url: 'https://evil.test/PLID1' }, { url: 'https://takealot.com@evil.test/PLID1' }, { image: 'javascript:alert(1)' }, { price: -1 }, { price: '599' }, { title: '' }]) assert.throws(() => parseTakealotPageImport(JSON.stringify({ ...payload, ...patch })));
assert.throws(() => captureTakealotPage({}, 'https://example.com/PLID1'));
const attr = { alt: payload.title, src: payload.image };
const main = {
  textContent: payload.title + ' Supplier out of stock',
  querySelector(selector) { return selector === 'h1' ? { textContent: payload.title } : { textContent: 'R 1,599' }; },
  querySelectorAll(selector) { return selector === 'img' ? [{ getAttribute: key => attr[key] }] : []; },
};
const doc = { querySelector: () => main };
const captured = captureTakealotPage(doc, url);
assert.equal(captured.price, 1599);
assert.equal(captured.image, payload.image);
assert.equal(captured.outOfStock, true);
const destination = 'https://urban-electronic.github.io/takealot-selector/create';
let opened;
vm.runInNewContext(decodeURIComponent(makeTakealotBookmarklet(destination).slice('javascript:'.length)), { document: doc, location: { href: url }, URL, window: { open: value => { opened = value; } }, alert: message => { throw new Error(message); } });
assert.ok(opened.startsWith(destination + '#takealot-import='));
const result = parseTakealotPageImport(decodeURIComponent(opened.split('#takealot-import=')[1]));
assert.equal(result.product_name, payload.title);
assert.equal(result.actual_sale_price_zar, 1599);
assert.equal(result.in_stock_price, null);
const savedDoc = {
  querySelector(selector) {
    if (selector === 'link[rel="canonical"]') return { getAttribute: () => url };
    if (selector === 'main h1') return { textContent: payload.title };
    if (selector === 'meta[property="og:image"]') return { getAttribute: () => payload.image };
    if (selector === 'main') return { ...main, querySelectorAll: () => [] };
    return null;
  },
};
const saved = importSavedTakealotPage(savedDoc, url);
assert.equal(saved.product_image_url, payload.image); // Saved local image uses original metadata instead.
assert.equal(saved.actual_sale_price_zar, 1599);
assert.equal(saved.in_stock_price, null);
assert.throws(() => importSavedTakealotPage(savedDoc, 'https://www.takealot.com/wrong/PLID999'));
const metaOnly = { querySelector: selector => selector === 'meta[property="og:title"]' ? { getAttribute: () => payload.title } : selector === 'meta[property="og:image"]' ? { getAttribute: () => payload.image } : null };
const partial = importSavedTakealotPage(metaOnly, url);
assert.equal(partial.product_name, payload.title);
assert.equal(partial.actual_sale_price_zar, null);
assert.equal(partial.success, false);
assert.throws(() => importSavedTakealotPage({ querySelector: () => null }, url));
assert.throws(() => importSavedTakealotPage(metaOnly, 'https://evil.test/PLID1'));
console.log('Page capture, bookmarklet handoff, import validation and out-of-stock tests passed.');
