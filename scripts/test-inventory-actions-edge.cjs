// Windows 本机补充回归（PR111 接续）：库存调整交互的边界与慢网络行为。
// 与 test-inventory-actions.cjs 一样使用隔离测试接口（deferred promise），
// 不触达生产 API、不做真实库存调整写入。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
let calls = [], finish, reloads = 0;
const api = {
  getInventory: async () => { reloads++; return [{ product_id: 'p', sku: 'TEST', name: 'Test', available: 5 }]; },
  getShipments: async () => [],
  getInventoryAdjustments: async () => [],
  getOperationLogs: async () => [],
  createInventoryAdjustment: body => { calls.push(body); return new Promise((resolve, reject) => { finish = { resolve, reject }; }); }
};
const file = path.resolve(__dirname, '../src/pages/Inventory.tsx');
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
const original = compiled.require.bind(compiled);
compiled.require = name => name === '../remoteApi' ? api
  : name === 'react-router-dom' ? { useNavigate: () => () => {}, useSearchParams: () => [new URLSearchParams()] }
  : name === '../utils/imageFallback' ? { ProductImage: () => null }
  : original(name);
compiled._compile(ts.transpileModule(fs.readFileSync(process.env.INVENTORY_SOURCE || file, 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, file);
const root = createRoot(document.getElementById('root'));
const button = text => [...document.querySelectorAll('button')].find(b => b.textContent === text);
const click = b => b.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
const quantity = value => {
  const el = document.querySelector('input[placeholder="数量"]');
  Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
};
const errorText = () => (document.body.textContent.match(/请选择 SKU[^"]*/) || [''])[0];

(async () => {
  await act(async () => root.render(React.createElement(compiled.exports.default)));
  const reloadsAtMount = reloads;
  assert.ok(reloadsAtMount >= 1, 'mount 时已加载一次库存');

  act(() => click(button('调整库存')));

  // ① 非法/小数数量全部拒绝，且不静默取整、不发请求
  for (const bad of ['1.5', '2.5', '0.5', '0', '-1', 'abc', '']) {
    act(() => quantity(bad));
    act(() => click(button('确认记录')));
    assert.equal(calls.length, 0, `数量 "${bad}" 必须被拒绝（不得静默取整或发请求）`);
    assert.ok(errorText().length > 0, `数量 "${bad}" 应给出错误提示`);
  }

  // ② 慢网络：连续三次点击只发一个请求（接口 pending 中）
  act(() => quantity('3'));
  const submit = button('确认记录');
  act(() => { click(submit); click(submit); click(submit); });
  assert.equal(calls.length, 1, '慢网络下三连击仍只能发送一个库存调整请求');
  assert.equal(calls[0].quantity_delta, 3);

  // ③ pending 期间输入被锁定，已发出的请求体不受后续输入影响
  const qtyInput = document.querySelector('input[placeholder="数量"]');
  assert.ok(qtyInput.disabled, '提交中数量输入应锁定');
  assert.equal(calls[0].quantity_delta, 3, '已发请求的数量不得被后续输入改写');

  // ④ 失败保留草稿并可重试
  await act(async () => finish.reject(new Error('network unavailable')));
  assert.equal(document.querySelector('input[placeholder="数量"]').value, '3', '失败应保留草稿数量');
  assert.ok(button('确认记录') && !button('确认记录').disabled, '失败后应解锁重试');

  // ⑤ 成功：编辑器清空 + 触发一次库存刷新
  const reloadsBeforeSuccess = reloads;
  act(() => click(button('确认记录')));
  assert.equal(calls.length, 2, '显式重试应被允许');
  await act(async () => finish.resolve({}));
  assert.equal(document.querySelector('input[placeholder="数量"]'), null, '成功后应清空编辑器');
  assert.ok(reloads > reloadsBeforeSuccess, '成功后应触发库存刷新');

  act(() => root.unmount());
  console.log('14 inventory edge assertions passed (Windows native)');
})().catch(e => { console.error(e); process.exitCode = 1; });
