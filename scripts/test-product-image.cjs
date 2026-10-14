// Component execution only (jsdom); this does not prove browser/CDN/collector E2E.
// Install jsdom@26.1.0 in a separate test prefix and expose it with NODE_PATH.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { JSDOM } = require('jsdom');
const ts = require('typescript');
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://example.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const file = path.resolve(__dirname, '../src/utils/imageFallback.tsx');
const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
compiled._compile(output, file);
const { ProductImage } = compiled.exports;
const host = document.getElementById('root');
const root = createRoot(host);
const messages = [];
const previousError = console.error;
console.error = (...args) => messages.push(args.map(String).join(' '));
let checks = 0;
function check(value, description) { assert.ok(value, description); checks++; }
function render(src, extra = {}) {
  act(() => root.render(React.createElement(ProductImage, { src, style: { width: 42, height: 42 }, ...extra })));
}
function fail(img, size = 42) {
  img.getBoundingClientRect = () => ({ width: size, height: size });
  act(() => img.dispatchEvent(new Event('error')));
}
try {
  render('https://example.invalid/first.png', { className: 'test-image', referrerPolicy: 'no-referrer' });
  check(host.querySelector('img')?.getAttribute('loading') === 'lazy', 'lazy loading default');
  check(host.querySelector('img')?.className === 'test-image', 'class retained on image');
  check(host.querySelector('img')?.getAttribute('referrerpolicy') === 'no-referrer', 'referrer policy retained');
  fail(host.querySelector('img'));
  check(host.querySelectorAll('[data-image-fallback]').length === 1, 'one React fallback');
  check(!host.querySelector('img'), 'failed image removed by React');
  check(host.querySelector('[role="img"]')?.textContent === '图暂不可用', 'honest fallback text');
  check(host.firstChild.style.width === '42px' && host.firstChild.style.height === '42px', 'measured size retained');
  render('https://example.invalid/first.png');
  check(!host.querySelector('img'), 'same source does not retry repeatedly');
  render('https://example.invalid/second.png');
  check(host.querySelector('img')?.src.endsWith('/second.png'), 'new source retries');
  check(!host.querySelector('[data-image-fallback]'), 'old failure removed after source change');
  fail(host.querySelector('img'), 120);
  check(host.firstChild.style.width === '120px', 'second failure records new layout');
  act(() => root.render(null));
  check(host.childNodes.length === 0, 'unmount after fallback leaves no orphan DOM');
  render('https://example.invalid/second.png');
  check(host.querySelectorAll('img').length === 1, 'remount starts with one fresh image');
  for (let i = 0; i < 5; i++) {
    render(`https://example.invalid/change-${i}.png`);
    check(host.querySelectorAll('img').length === 1, 'one image after repeated URL change');
    fail(host.querySelector('img'));
    check(host.querySelectorAll('[data-image-fallback]').length === 1, 'one fallback after repeated failure');
  }
  act(() => root.render(React.createElement(React.StrictMode, null,
    React.createElement(ProductImage, { src: 'https://example.invalid/strict.png' }))));
  check(host.querySelectorAll('img').length === 1, 'StrictMode remount renders one image');
  fail(host.querySelector('img'));
  act(() => root.unmount());
  check(host.childNodes.length === 0, 'root unmount is clean');
  check(messages.length === 0, 'no React ownership/state warnings: ' + messages.join('\n'));
  console.log(`PASS ${checks} ProductImage component assertions (jsdom, zero network)`);
} finally {
  console.error = previousError;
  dom.window.close();
}
