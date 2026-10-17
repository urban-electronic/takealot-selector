// Pure-function checks for src/utils/price.ts. No jsdom, no network, no credentials.
// 旧实现（逗号一律当千分位删除）对下列用例会放大 100 倍，因此这组断言是回归护栏。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/price.ts');
const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
compiled._compile(output, file);
const { parseZarPrice } = compiled.exports;

let checks = 0;
function equal(actual, expected, description) {
  assert.deepEqual(actual, expected, `${description}（实际 ${JSON.stringify(actual)}，期望 ${JSON.stringify(expected)}）`);
  checks++;
}

// 逗号小数的回归用例：旧实现会得到 29900 / 129900 / 249999
equal(parseZarPrice('R 299,00'), 299, '逗号小数（无千分位）');
equal(parseZarPrice('R1 299,00'), 1299, '空格千分位 + 逗号小数');
equal(parseZarPrice('R 2 499,99'), 2499.99, '空格千分位 + 两位小数');
equal(parseZarPrice('R 1 299,00 Save R 200'), 1299, '不把 Save 金额拼进价格');
equal(parseZarPrice('R 299,00 Save R 200'), 299, '不把 Save 金额拼进价格（无千分位）');

// 点小数的既有行为不能被破坏
equal(parseZarPrice('R1299.00'), 1299, '点小数（无千分位）');
equal(parseZarPrice('R 1,299.00'), 1299, '逗号千分位 + 点小数');
equal(parseZarPrice('R 1.299,00'), 1299, '点千分位 + 逗号小数（欧式）');
equal(parseZarPrice('R 1.299.999,00'), 1299999, '多位点千分位 + 逗号小数');

// 千分位
equal(parseZarPrice('R 1 299'), 1299, '空格千分位');
equal(parseZarPrice('R 1,299'), 1299, '逗号千分位');
equal(parseZarPrice('R 1,234,567'), 1234567, '多组逗号千分位');
equal(parseZarPrice('R 999,999'), 999999, '三位整数 + 三位尾数按千分位处理');

// 小数边界
equal(parseZarPrice('R 12,5'), 12.5, '一位小数');
equal(parseZarPrice('R 1,29'), 1.29, '两位小数且整数部分很短');
equal(parseZarPrice('R 0,50'), 0.5, '小于 1 的价格');
equal(parseZarPrice('R 99'), 99, '整数无分隔符');

// 不可解析：必须返回 null，不能返回 0（0 会被当成"免费"）
equal(parseZarPrice(''), null, '空串');
equal(parseZarPrice(null), null, 'null');
equal(parseZarPrice(undefined), null, 'undefined');
equal(parseZarPrice('暂无报价'), null, '无数字文案');

// 不间断空格（HTML 里常见）
equal(parseZarPrice('R\u00a01\u00a0299,00'), 1299, 'NBSP 千分位 + 逗号小数');
equal(parseZarPrice('R\u00a0299,00'), 299, 'NBSP + 逗号小数');

// 尾部孤立分隔符
equal(parseZarPrice('R 1 299,'), 1299, '尾部孤立逗号');
equal(parseZarPrice('R 1 299.'), 1299, '尾部孤立点');

console.log(`PASS ${checks} parseZarPrice assertions (pure function, zero network)`);
