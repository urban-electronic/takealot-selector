// 数值收口与分区降级的纯函数回归（零网络、零 jsdom）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

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

const srcDir = path.resolve(__dirname, '../src');
const { toNumber, toAmount } = compileModule(path.join(srcDir, 'utils/number.ts'));
const { applySettled, failureSummary, reasonText } = compileModule(path.join(srcDir, 'utils/settle.ts'));
const { formatPrice, formatPercent } = compileModule(path.join(srcDir, 'types.ts'));

let checks = 0;
function equal(actual, expected, description) {
  assert.deepEqual(actual, expected, `${description}（实际 ${JSON.stringify(actual)}，期望 ${JSON.stringify(expected)}）`);
  checks++;
}
function ok(value, description) {
  assert.ok(value, description);
  checks++;
}

// --- toNumber：缺失/非法必须是 null，不能是 0 ---
equal(toNumber(null), null, 'null');
equal(toNumber(undefined), null, 'undefined');
equal(toNumber(''), null, '空串');
equal(toNumber('   '), null, '纯空格');
equal(toNumber('abc'), null, '非数字字符串');
equal(toNumber(NaN), null, 'NaN');
equal(toNumber(Infinity), null, 'Infinity');
equal(toNumber(0), 0, '数字 0 必须保留为 0');
equal(toNumber(120.5), 120.5, '正常数字');
equal(toNumber('120.00'), 120, '字符串金额');
equal(toNumber(' 120.00 '), 120, '带空格字符串');
equal(toNumber('1,299.00'), 1299, '带千分位字符串');
equal(toNumber('-5'), -5, '负数');

// --- toAmount：求和场景按 0 兜底，且必须是 number ---
equal(toAmount(null), 0, '求和 null -> 0');
equal(toAmount('120.00'), 120, '求和字符串');
equal(typeof toAmount('abc'), 'number', '求和结果类型必须是 number');
equal([120, '50.00', null, undefined, '30'].reduce((sum, value) => sum + toAmount(value), 0), 200, '混合类型求和');
// 旧写法会得到字符串 "12050.0030"
ok(String([120, '50.00', null, '30'].reduce((sum, value) => sum + (value ?? 0), 0)) !== '200', '旧写法确实会退化成字符串拼接');

// --- formatPrice / formatPercent：字符串入参不能再抛 TypeError ---
equal(formatPrice('120.00'), 'R 120.00', 'formatPrice 接受字符串');
equal(formatPrice(null), '-', 'formatPrice 缺失显示 -');
equal(formatPrice(undefined), '-', 'formatPrice undefined 显示 -');
equal(formatPrice('abc'), '-', 'formatPrice 非法值显示 -');
equal(formatPrice(0), 'R 0.00', 'formatPrice 0 必须显示 0.00');
equal(formatPercent('0.25'), '25.00%', 'formatPercent 接受字符串');
equal(formatPercent(null), '-', 'formatPercent 缺失显示 -');
equal(formatPrice(138.18), 'R 138.18', '最低价保留两位小数（不再 toFixed(0) 抹成 138）');

// --- settle：分区降级 ---
const failures = [];
const applied = [];
const fulfilled = { status: 'fulfilled', value: [1, 2] };
const rejected = { status: 'rejected', reason: new Error('库存接口 500') };
ok(applySettled(fulfilled, '库存', (value) => applied.push(value), failures) === true, '成功返回 true');
equal(applied, [[1, 2]], '成功时写入状态');
ok(applySettled(rejected, '发货单', () => applied.push('不该执行'), failures) === false, '失败返回 false');
equal(applied, [[1, 2]], '失败时不写入状态');
equal(failures, ['发货单：库存接口 500'], '失败清单带区块名与原因');
equal(failureSummary([]), '', '无失败时不提示');
equal(failureSummary(['发货单：x']), '部分数据加载失败：发货单：x', '失败提示拼接');
equal(reasonText('纯字符串'), '纯字符串', '字符串原因');
equal(reasonText(new Error('e')), 'e', 'Error 原因');
equal(reasonText({ code: 1 }), '{"code":1}', '对象原因');

console.log(`PASS ${checks} value-coercion/settle assertions (pure functions, zero network)`);
