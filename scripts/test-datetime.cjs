// 后端时间串的展示口径（src/utils/datetime.ts）测试：不启浏览器，直接编译 TS。
//
// 钉死两件事：
//   1) 后端返回的是**不带时区标记的 UTC**（SQLAlchemy naive DateTime + Pydantic
//      isoformat），`new Date('2026-10-21T12:00:00')` 会按本地时间解析，东八区
//      于是把 20:00 显示成 12:00 —— 早了整整 8 小时；
//   2) 带 Z / ±hh:mm 的值必须按它自己解析，不能被再补一次 Z。
//
// 另外做源码级守卫：产品详情与库存调整的时间列必须走 beijingDateTime，
// 不许再出现「toLocaleString('zh-CN') 但不给 timeZone」的写法。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

let checks = 0;
function check(condition, message) {
  checks += 1;
  assert.ok(condition, message);
}

const file = path.resolve(__dirname, '../src/utils/datetime.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const { parseServerTime, beijingDateTime, todayLocal } = mod.exports;

// ---- 1) 解析：naive 串按 UTC，带标记的按标记 ------------------------------
const naive = parseServerTime('2026-10-21T12:00:00');
check(naive instanceof Date, 'naive 时间串必须解析成 Date');
check(naive.getTime() === Date.UTC(2026, 9, 21, 12, 0, 0),
  `naive 串必须当 UTC，实际 ${naive.toISOString()}`);

const zulu = parseServerTime('2026-10-21T12:00:00Z');
check(zulu.getTime() === naive.getTime(), '带 Z 与不带 Z 必须指向同一时刻');

const offsetZero = parseServerTime('2026-10-21T12:00:00+00:00');
check(offsetZero.getTime() === naive.getTime(), '带 +00:00 与不带 Z 必须指向同一时刻');

const offsetEight = parseServerTime('2026-10-21T20:00:00+08:00');
check(offsetEight.getTime() === naive.getTime(),
  '带 +08:00 的值要按标记解析（20:00+08:00 == 12:00Z），不能被再补一次 Z');

const spaced = parseServerTime('2026-10-21 12:00:00');
check(spaced && spaced.getTime() === naive.getTime(), '空格分隔的时间串同样按 UTC');

const dateOnly = parseServerTime('2026-10-21');
check(dateOnly && dateOnly.getTime() === Date.UTC(2026, 9, 21, 0, 0, 0),
  '纯日期按 UTC 零点，不能拼成非法串');

// ---- 2) 坏输入不抛异常，交给调用方的占位符 --------------------------------
for (const bad of [null, undefined, '', '   ', 'not-a-date', 20261021, {}]) {
  check(parseServerTime(bad) === null, `坏输入必须返回 null：${String(bad)}`);
}
check(beijingDateTime(null) === '-', '空值用默认占位符');
check(beijingDateTime('not-a-date', '—') === '—', '坏值用调用方给的占位符');

// ---- 3) 展示：东八区 20:00，而不是 UTC 的 12:00 ---------------------------
const shown = beijingDateTime('2026-10-21T12:00:00');
check(/20:00:00/.test(shown), `naive UTC 12:00 在北京时间应为 20:00，实际 ${shown}`);
check(!/12:00:00/.test(shown), `不能把 UTC 时钟直接当北京时间显示，实际 ${shown}`);
check(beijingDateTime('2026-10-21T12:00:00Z') === shown, '带 Z 的值展示必须一致');

// 跨日：UTC 20:00 是北京次日 04:00，不能显示成同一天。
const crossing = beijingDateTime('2026-10-21T20:00:00');
check(/2026\/10\/22/.test(crossing), `跨日必须进位到 10-22，实际 ${crossing}`);
check(/04:00:00/.test(crossing), `跨日时间应为 04:00，实际 ${crossing}`);

// 展示口径不跟随运行机器的时区（Asia/Shanghai 固定）。
check(todayLocal(new Date('2026-10-21T20:00:00Z')) === '2026-10-22',
  'todayLocal 同样按北京时间跨日');

// ---- 4) 源码级守卫：时间列必须走 beijingDateTime ---------------------------
{
  const targets = ['../src/pages/ProductDetail.tsx', '../src/pages/Inventory.tsx'];
  for (const rel of targets) {
    const source = fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
    const lines = source.split('\n')
      .filter(line => !line.trim().startsWith('//') && !line.trim().startsWith('*'));
    const raw = lines.filter(line => line.includes("toLocaleString('zh-CN')")
      && !line.includes('timeZone'));
    check(raw.length === 0,
      `${rel} 不许出现不给 timeZone 的 toLocaleString('zh-CN')：${raw.join(' | ')}`);
    check(source.includes('beijingDateTime('), `${rel} 的时间列必须走 beijingDateTime`);
  }
}

// ---- 5) 业务日期只有一份实现：不许页面自己拼 getFullYear/getMonth/getDate ----
// Packing.tsx 原来有一份私有 todayStr()，装箱单日期与采购/库存的默认日期
// 在非东八区机器上会差一天。
{
  const rel = '../src/pages/Packing.tsx';
  const source = fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
  const lines = source.split('\n')
    .filter(line => !line.trim().startsWith('//') && !line.trim().startsWith('*'));
  const own = lines.filter(line => /getFullYear\(\)|getMonth\(\)|getDate\(\)/.test(line));
  check(own.length === 0, `${rel} 不许自己拼本地日期：${own.join(' | ')}`);
  check(source.includes("from '../utils/datetime'"), `${rel} 的业务日期必须来自 utils/datetime`);
}

console.log(`test-datetime: ${checks} checks passed`);
