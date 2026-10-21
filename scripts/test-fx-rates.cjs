// 兰特汇率缓存（src/utils/useFxRates.ts）的取值口径测试：不启浏览器，直接编译 TS。
//
// 钉死的三件事：
//   1) 拿不到汇率时退回调用方给的兜底值，界面永远算得出兰特；
//   2) 缓存里的坏数据（坏 JSON、没有一种可用货币）当没缓存，不能拿它去乘价格；
//   3) **缓存里存的是「内置兜底常量」时，`fallback` 标记必须原样带出来**。
//      这一条是重点：标记被洗掉之后，刷新页面看到的是一组看着很新的数字，
//      汇率说明里也不会再写「兜底汇率」——用户没法分辨它是今天拉到的还是写死的。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// useFxRates 在渲染期读 localStorage；Node 里没有这个全局，先装一个可控的。
const store = new Map();
let throwOnRead = false;
globalThis.localStorage = {
  getItem(key) {
    if (throwOnRead) throw new Error('SecurityError: localStorage 不可用');
    return store.has(key) ? store.get(key) : null;
  },
  setItem(key, value) { store.set(key, String(value)); },
  removeItem(key) { store.delete(key); },
};

const file = path.resolve(__dirname, '../src/utils/useFxRates.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const { currentFxRates } = mod.exports;

const CACHE_KEY = 'ck:fx:zar';
const FALLBACK = { base: 'ZAR', rates: { SGD: 12.8345, CNY: 2.4453 }, src: '内置兜底', fallback: true };

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

// ---- 没有任何缓存：退回调用方给的兜底值 -------------------------------------
{
  check(currentFxRates(FALLBACK) === FALLBACK, '没有缓存时直接用兜底值（同一个对象，不复制）');
}

// ---- 缓存里是正常汇率：按缓存走，并保留来源信息 -----------------------------
{
  const real = {
    base: 'ZAR', rates: { SGD: 12.8345, CNY: 2.4453 }, at: '2026-10-21',
    src: 'open.er-api.com', fetched_at: '2026-10-21T00:02:32+00:00', stale: false, fallback: false,
  };
  store.set(CACHE_KEY, JSON.stringify(real));
  const got = currentFxRates(FALLBACK);
  check(got.rates.SGD === 12.8345, '缓存命中时用缓存的汇率');
  check(got.fallback === false, '正常汇率不能标成兜底');
  check(got.at === '2026-10-21', '汇率生效日期要带出来（界面要显示）');
  check(got !== FALLBACK, '缓存命中时不要退回兜底对象');
}

// ---- 缓存里是内置兜底常量：fallback 标记必须原样保留 -----------------------
{
  // 后端从没成功拉过上游时会返回这组写死的常量（并标 fallback），前端会把它缓存下来。
  // 刷新页面时如果把这个标记洗掉，界面就会把硬编码数字当成今天拉到的汇率。
  store.set(CACHE_KEY, JSON.stringify({
    base: 'ZAR', rates: { SGD: 12.8345, CNY: 2.4453 }, at: null,
    src: 'open.er-api.com（内置兜底）', fetched_at: '2026-10-21T00:02:32+00:00',
    stale: true, fallback: true,
  }));
  const got = currentFxRates(FALLBACK);
  check(got.fallback === true, '兜底汇率刷新后仍要标着兜底（否则会被当成真汇率）');
  check(got.stale === true, '过期标记也要保留，说明里要写「汇率未更新」');
  check(got.src.includes('内置兜底'), '来源文案原样带出，便于核对');
}

// ---- 缓存只有一种货币可用：照用，缺失的那种由调用方退回原币 ----------------
{
  store.set(CACHE_KEY, JSON.stringify({ base: 'ZAR', rates: { SGD: 12.8 }, fallback: false }));
  const got = currentFxRates(FALLBACK);
  check(got.rates.SGD === 12.8 && got.rates.CNY === undefined, '缺一种货币时保留另一种');
}

// ---- 坏缓存一律当没缓存 -----------------------------------------------------
for (const bad of ['not json', '[]', 'null', '{"rates":null}', '{"rates":{}}',
  '{"rates":{"SGD":"12.8","CNY":0}}', '{"rates":{"SGD":-1}}', '{"rates":{"SGD":null}}']) {
  store.set(CACHE_KEY, bad);
  check(currentFxRates(FALLBACK) === FALLBACK, `缓存是 ${bad} 时要退回兜底值`);
}

// ---- localStorage 本身不可用（隐私模式 / 配额）：不许抛，退回兜底 ----------
{
  throwOnRead = true;
  check(currentFxRates(FALLBACK) === FALLBACK, 'localStorage 读不了时退回兜底，不能打断渲染');
  throwOnRead = false;
}

console.log(`test-fx-rates: ${checks} checks passed`);
