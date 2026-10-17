// Renders the real Procurement page against isolated API fixtures.
// 目的：序列号被改掉、但产品关联还指着原产品时，绝不能提交。
// 这种记录写进去之后，补货、成本、毛利都会按错的产品算，是数据完整性问题，不是提示文案问题。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');

const watchdog = setTimeout(() => {
  console.error('FAIL Procurement identity suite did not complete within 10s');
  process.exit(1);
}, 10000);

const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event'])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');

const file = path.resolve(__dirname, '../src/pages/Procurement.tsx');
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));

const created = [];
const updated = [];
const products = [{ id: 'p-a', product_no: 1001, product_name: 'Product A', chinese_product_name: '产品A' }];

const api = {
  listProcurementRecords: async () => [],
  getProducts: async () => products,
  searchProductsByNo: async () => products,
  createProcurementRecord: async payload => { created.push(payload); return { id: 'new', ...payload }; },
  updateProcurementRecord: async (id, payload) => { updated.push({ id, payload }); return { id, ...payload }; },
  deleteProcurementRecord: async () => ({}),
};

// 未显式 mock 的相对导入按真实 .ts/.tsx 编译，避免新增一个模块就让整组测试失败。
const localModule = name => {
  if (typeof name !== 'string' || !name.startsWith('.')) return null;
  for (const ext of ['.ts', '.tsx']) {
    const target = path.resolve(path.dirname(file), name + ext);
    if (!fs.existsSync(target)) continue;
    const m = new Module(target, module);
    m.filename = target;
    m.paths = Module._nodeModulePaths(path.dirname(target));
    m._compile(
      ts.transpileModule(fs.readFileSync(target, 'utf8'), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
      }).outputText,
      target,
    );
    return m.exports;
  }
  return null;
};

const overrides = {
  'react-router-dom': { useNavigate: () => () => {} },
  '../DataSourceContext': { useApi: () => api, useDataSource: () => ({ dataSource: 'local' }) },
  '../remoteApi': { migrateToRemote: async () => ({ imported: {} }) },
};

const originalRequire = compiled.require.bind(compiled);
compiled.require = name => overrides[name] || localModule(name) || originalRequire(name);
compiled._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);

const Page = compiled.exports.default;
const host = document.getElementById('root');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

const type = (input, value) => {
  const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
};
const submit = () => host.querySelector('form').querySelector('button[type="submit"]');
// 直接派发 submit：这样测的是 handleSubmit 里的守卫，而不是只测按钮的 disabled 属性。
const fireSubmit = async () => {
  await act(async () => {
    host.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  });
};

(async () => {
  const root = createRoot(host);
  await act(async () => root.render(React.createElement(Page)));
  await act(async () => { host.querySelector('button.btn-primary').click(); });
  check(!!host.querySelector('form'), 'the create form is open');

  const serial = host.querySelector('form input[type="text"]');
  // 搜索并选中产品 A：product_id / product_name 都被填上，linkedProductNo=1001。
  await act(async () => { type(serial, '1001'); });
  await act(async () => { await new Promise(r => setTimeout(r, 350)); });
  // 只点真正的搜索结果行（容器 div 的 textContent 也会包含 #1001，点到容器不会触发 onClick）。
  const option = [...host.querySelectorAll('form div')]
    .find(d => (d.getAttribute('style') || '').includes('cursor: pointer') && d.textContent.includes('#1001'));
  check(!!option, 'the product search returns the matching serial');
  await act(async () => { option.click(); });
  check(host.querySelector('form input[type="text"]').value === '1001', 'selecting a product fills the serial');

  // 关键场景：把序列号改掉，但关联仍指向产品 A。
  await act(async () => { type(serial, '2002'); });
  const alert = host.querySelector('form [role="alert"]');
  check(!!alert && alert.textContent.includes('先解决关联才能保存'), 'mismatch is reported as blocking, not advisory');
  check(submit().disabled === true, 'submit is disabled while the identity is inconsistent');

  const qty = host.querySelectorAll('form input[type="number"]')[0];
  await act(async () => { type(qty, '3'); });
  await fireSubmit();
  check(created.length === 0 && updated.length === 0, 'no write happens while the identity is inconsistent');

  // 解除关联后才允许保存。
  await act(async () => { alert.querySelector('button').click(); });
  check(!host.querySelector('form [role="alert"]'), 'unlinking clears the warning');
  check(submit().disabled === false, 'submit is enabled after the link is resolved');
  await fireSubmit();
  check(created.length === 1, 'the record is written once the identity is consistent');
  check(created[0].product_id === '' && created[0].product_no === 2002, 'the saved record carries the new serial with no stale product link');

  await act(async () => root.unmount());
  clearTimeout(watchdog);
  console.log(`PASS ${checks} Procurement identity assertions (jsdom, isolated API)`);
})().catch(error => {
  console.error('FAIL', error.message);
  process.exit(1);
});
