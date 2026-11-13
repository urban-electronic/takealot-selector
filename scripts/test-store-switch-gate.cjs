// Pure-function checks for src/utils/latestGate.ts. No jsdom, no network, no credentials.
//
// 锁住的是「切店铺时旧响应不能覆盖新店铺状态」这条时序。它不是性能问题：
// 界面会安静地显示一个错的结论（CK 没配却显示「密钥已配置」），而且不报错。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/latestGate.ts');
const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
compiled._compile(output, file);
const { createLatestGate } = compiled.exports;

let checks = 0;
function equal(actual, expected, description) {
  assert.deepEqual(actual, expected, `${description}（实际 ${JSON.stringify(actual)}，期望 ${JSON.stringify(expected)}）`);
  checks++;
}

// ---------------------------------------------------------------- 基本语义
{
  const gate = createLatestGate();
  const first = gate.begin();
  equal(gate.isLatest(first), true, '只发起过一次时，它是当前有效的那次');

  const second = gate.begin();
  equal(gate.isLatest(second), true, '后发起的这次是当前有效的');
  equal(gate.isLatest(first), false, '先发起的那次已经过期');
}

// ---------------------------------------------------------------- 切店铺：旧响应晚到
{
  // 生产里的真实时序：切到 CK 后发出 B 请求，结果 A 店的旧请求才返回。
  const gate = createLatestGate();
  const tokenA = gate.begin();   // 自营店铺的 status 请求
  const tokenB = gate.begin();   // 用户切到代运营CK 后重发的请求

  // B 先回来（快），写进状态
  equal(gate.isLatest(tokenB), true, 'CK 的响应先到，应当写入');
  // A 后回来（慢），必须被丢弃
  equal(gate.isLatest(tokenA), false, '自营店铺的旧响应晚到，必须丢弃，否则 CK 会显示「已配置」');
}

// ---------------------------------------------------------------- 同店铺连点「重新检查」
{
  // 不记录店铺 id 也安全：号是单调递增的，连点两次只有最后一次算数。
  const gate = createLatestGate();
  const first = gate.begin();
  const second = gate.begin();
  const third = gate.begin();
  equal([gate.isLatest(first), gate.isLatest(second), gate.isLatest(third)],
    [false, false, true], '连点三次只有最后一次有效，前两次都要丢弃');
}

// ---------------------------------------------------------------- 两次独立的 gate 互不干扰
{
  // 设置页同时有两个 gate：状态查询和密钥保存。保存结果不该让状态查询作废。
  const statusGate = createLatestGate();
  const saveGate = createLatestGate();
  const statusToken = statusGate.begin();
  saveGate.begin();
  saveGate.begin();
  equal(statusGate.isLatest(statusToken), true,
    '保存密钥不应该让正在进行的 status 查询作废（两个 gate 是独立的）');
}

// ---------------------------------------------------------------- 响应顺序完全颠倒
{
  // 三个店铺快速连切，响应倒序返回：只有最后发起的那次能写状态。
  const gate = createLatestGate();
  const tokens = [gate.begin(), gate.begin(), gate.begin()];
  const accepted = [];
  // 倒序到达
  for (const token of [...tokens].reverse()) {
    if (gate.isLatest(token)) accepted.push(token);
  }
  equal(accepted, [tokens[2]], '倒序返回时只接受最后发起的那一次');
}

console.log(`store-switch gate: ${checks} checks passed`);
