// 产品标签（EAN-13）的口径守卫：不启浏览器，直接编译 TS。
//
// 钉死的七件事：
//   1) 三份 EAN-13 实现必须同一套判据：后端 services/label_pdf.py、详情页计数用的
//      src/utils/ean.ts、真正出标签的 src/utils/labelPdf.ts。改了其中一份的权重或长度，
//      按钮上写的「N 个 SKU」和实际下载到的文件数就会对不上。
//   2) 详情页生成标签失败不能写进 `error`。那个状态渲染时整页被换成一行红字
//      （`if (error) return …`），编辑中的草稿一起丢——生成失败是可重试的小事。
//   3) 列表页「生成成功但跳过几个」不是错误，不能占用页面顶部那条红色告警。
//   4) 折行的省略号规则（只在真丢词时出现）两边一致，见 test-label-pdf.cjs / pytest。
//   5) 列表页按钮上的数量 = 真正能出标签的数量（合法 EAN-13），不是勾选总数。
//   6) 两份生成器的版式常量逐字对齐（网页版与接口版印出来的必须是同一张标签）。
//   7) 列表页下载标签时，产品名不能只取「当前筛选结果」——勾选是持久化的。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const load = (relative) => {
  const file = path.resolve(__dirname, '..', relative);
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(path.dirname(file));
  mod._compile(
    ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    file,
  );
  return mod.exports;
};

const read = relative => fs.readFileSync(path.resolve(__dirname, '..', relative), 'utf8');

/** 截出某个函数体，用于「实现细节」守卫（源码守卫只认这一处，别处怎么写不管）。 */
const bodyOf = (source, marker) => {
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `源码里找不到 ${marker}`);
  const end = source.indexOf('\n  };', start);
  assert.ok(end > start, `找不到 ${marker} 的结尾`);
  return source.slice(start, end);
};

const { isEan13: isEan13Detail } = load('src/utils/ean.ts');
const { isEan13: isEan13Label } = load('src/utils/labelPdf.ts');

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

// ---- 1. EAN-13 判据（两份前端实现必须一致）--------------------------------
// 合法样本取自后端用例（test_label_pdf.py / test_label_routes.py 用的同一批），
// 校验位错的那个是 SKU_BAD，后端也判非法。
const VALID = ['9902348621873', '9902348623136', '4006381333931'];
const INVALID = ['9902348621874', '4006381333932', '990234862187', '99023486218733', '', 'abcdefghijklm', '990234862187 '];
for (const impl of [{ name: 'utils/ean.ts', fn: isEan13Detail }, { name: 'utils/labelPdf.ts', fn: isEan13Label }]) {
  for (const code of VALID) check(impl.fn(code), `${impl.name}: ${code} 是合法 EAN-13`);
  for (const code of INVALID) check(!impl.fn(code), `${impl.name}: ${JSON.stringify(code)} 必须判非法`);
  check(!impl.fn(null), `${impl.name}: null 判非法`);
  check(!impl.fn(undefined), `${impl.name}: undefined 判非法`);
}

// ---- 2. 三份实现的权重规则同源 -------------------------------------------
const py = read('backend/services/label_pdf.py');
check(/3 if i % 2 else 1/.test(py), '后端 is_ean13 仍按 3/1 交替加权');
check(/len\(code\) != 13/.test(py), '后端仍要求 13 位');
check(/i % 2 \? 3 : 1/.test(read('src/utils/ean.ts')), 'utils/ean.ts 仍按 3/1 交替加权');
check(/i % 2 \? 3 : 1/.test(read('src/utils/labelPdf.ts')), 'utils/labelPdf.ts 仍按 3/1 交替加权');
check(/code\.length !== 13/.test(read('src/utils/ean.ts')), 'utils/ean.ts 仍要求 13 位');
check(/code\.length !== 13/.test(read('src/utils/labelPdf.ts')), 'utils/labelPdf.ts 仍要求 13 位');

// ---- 3. 详情页：失败走 labelError，不整页替换 ------------------------------
const detail = read('src/pages/ProductDetail.tsx');
const detailBody = bodyOf(detail, 'const downloadLabels = () => {');
check(detailBody.includes('setLabelError('), '详情页生成失败要写 labelError');
check(!detailBody.includes('setError('), '详情页生成失败不许写 error（会整页替换、草稿丢失）');
check(/\{labelError && /.test(detail), 'labelError 必须渲染出来，否则失败静默');

// ---- 4. 列表页：成功但跳过几个不许报成红色错误 ------------------------------
const list = read('src/pages/ProductList.tsx');
const listBody = bodyOf(list, 'const downloadLabels = () => {');
const skipIdx = listBody.indexOf('if (bundle.skipped.length) {');
check(skipIdx >= 0, '列表页必须处理「生成成功但跳过了几个」');
const skipBlock = skipIdx >= 0 ? listBody.slice(skipIdx, listBody.indexOf('\n      }', skipIdx)) : '';
check(skipBlock.includes('setLabelNote('), '被跳过的 SKU 必须提示（否则用户以为标签丢了）');
check(!skipBlock.includes('setError('), '「已生成 N 张、跳过 M 个」不许走 setError（那是一条加载失败的红色告警）');
check(/\{labelNote && /.test(list), 'labelNote 必须渲染出来');

// ---- 5. 列表页按钮上的数量 = 真正能出标签的数量 -----------------------------
// `utils/ean.ts` 的头注释写着「前端先筛一遍是为了按钮上能显示真实数量——否则后端
// 跳过几个，用户以为出错了」，但列表页按钮原来写的是 `selectedSkus.size`（含不合法的），
// 勾 3 个只下到 1 张，看着就像文件丢了。详情页的 `labelCount` 一直是合法数，两页必须一致。
const btnStart = list.indexOf('disabled={labelCount === 0');
check(btnStart >= 0, '列表页标签按钮必须按合法数量决定禁用（labelCount === 0）');
const labelBtn = btnStart >= 0 ? list.slice(btnStart, list.indexOf('</button>', btnStart)) : '';
check(labelBtn.includes('下载标签${labelCount > 0'), '按钮上的数字必须是合法 EAN-13 的数量');
check(labelBtn.includes('都不是合法 EAN-13 条码'), '一个都不合法时要说明原因，不能让按钮静默失效');
check(/const labelCount = Array\.from\(selectedSkus\)\.filter\(isEan13\)\.length/.test(list),
  '列表页的 labelCount 必须复用 utils/ean 的 isEan13（别自己再写一份判据）');

// ---- 6. 两份生成器的版式常量必须逐字对齐 -----------------------------------
// 同一套版式有两份实现：`src/utils/labelPdf.ts`（网页实际用的）与
// `backend/services/label_pdf.py`（接口 / A4 拼版用的）。文档写得很清楚——参数
// 「必须逐字对齐，改一边不改另一边，同一个 SKU 在网页和接口里会印出两张不一样的标签」，
// 但此前没有任何东西真的比过这两份常量：改错一位只会表现为「打印出来对不齐」，
// 谁都不会在自检里看到。这里逐项比数值，改名或漏改都会点名。
const tsLabel = read('src/utils/labelPdf.ts');
const pyLabel = py; // 上面已读过 backend/services/label_pdf.py

/** TS 侧 `const X = <值>;`（允许 `: Type` 注解）；返回原文，找不到返回 null。 */
const tsValue = (src, name) => {
  const m = src.match(new RegExp(`\\b${name}\\s*(?::[^=\\n]+)?=\\s*([^;\\n]+)`));
  return m ? m[1].trim() : null;
};
/** PY 侧 `X = <值>`，去掉行尾注释。 */
const pyValue = (src, name) => {
  const m = src.match(new RegExp(`^${name}\\s*=\\s*(.+)$`, 'm'));
  return m ? m[1].split('#')[0].trim() : null;
};
const numbers = (text) => (String(text).match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
const sameNumbers = (a, b) => a.length === b.length && a.every((n, i) => Math.abs(n - b[i]) < 1e-9);

// 版式常量：两份必须同值（单位都是 pt，原点在内容左下角）
const LAYOUT = [
  'CONTENT_W', 'CONTENT_H', 'NAME_SIZE', 'NAME_BASELINES', 'DIGITS_BASELINE',
  'BAR_X', 'BAR_TOP', 'BAR_WIDTH', 'BAR_HEIGHT', 'GUARD_HEIGHT',
  'MARK_X', 'MARK_M_BASELINE', 'MARK_P_BASELINE', 'DIGIT_X', 'PAD',
];
for (const name of LAYOUT) {
  const tsRaw = tsValue(tsLabel, name);
  const pyRaw = pyValue(pyLabel, name);
  check(tsRaw !== null, `labelPdf.ts 里找不到常量 ${name}（改名了就要同步这里）`);
  check(pyRaw !== null, `label_pdf.py 里找不到常量 ${name}（改名了就要同步这里）`);
  if (tsRaw === null || pyRaw === null) continue;
  check(sameNumbers(numbers(tsRaw), numbers(pyRaw)),
    `${name} 两份生成器不一致：labelPdf.ts=${tsRaw} / label_pdf.py=${pyRaw}`);
}
// PAD 必须是「毫米换算出来的」而不是随手一个 pt 数字：1.6mm 才是官方量出来的边距
check(/1\.6\s*\*\s*MM/.test(tsValue(tsLabel, 'PAD') || ''), 'labelPdf.ts 的 PAD 必须是 1.6 * MM');
check(/1\.6\s*\*\s*mm/.test(pyValue(pyLabel, 'PAD') || ''), 'label_pdf.py 的 PAD 必须是 1.6 * mm');

// 页面尺寸表与默认尺寸
const tsSizes = (tsLabel.slice(tsLabel.indexOf('LABEL_SIZES'), tsLabel.indexOf('LABEL_SIZES') + 240)
  .match(/\[(\d+),\s*(\d+)\]/g) || []).map((s) => s.replace(/[[\]]/g, '').replace(/\s/g, ''));
const pySizes = (pyValue(pyLabel, 'LABEL_SIZES') || '').match(/\((\d+),\s*(\d+)\)/g) || [];
const pySizeList = pySizes.map((s) => s.replace(/[()]/g, '').replace(/\s/g, ''));
check(tsSizes.length === 2 && tsSizes.join('|') === pySizeList.join('|'),
  `LABEL_SIZES 两份不一致：labelPdf.ts=${tsSizes.join('|')} / label_pdf.py=${pySizeList.join('|')}`);
const tsDefault = (tsLabel.match(/DEFAULT_LABEL_SIZE[^=]*=\s*'([^']+)'/) || [])[1];
const pyDefault = (pyLabel.match(/^DEFAULT_SIZE\s*=\s*'([^']+)'/m) || [])[1];
check(tsDefault && tsDefault === pyDefault,
  `默认尺寸两份不一致：labelPdf.ts=${tsDefault} / label_pdf.py=${pyDefault}`);
check(tsDefault === '40x30', '默认必须是 40x30：30x40 竖版会把条宽压到 0.258mm，低于 EAN-13 下限');

// 折行宽度与行数同源：常量对齐了、调用处各自换个数，还是印出两张不一样的标签
check(/wrapName\(name, NAME_SIZE, NAME_MAX_WIDTH, NAME_BASELINES\.length\)/.test(tsLabel),
  'labelPdf.ts 的折行必须用 (NAME_SIZE, NAME_MAX_WIDTH, 3 行)');
check(/_wrap\(name, NAME_SIZE, NAME_MAX_WIDTH, len\(NAME_BASELINES\)\)/.test(pyLabel),
  'label_pdf.py 的折行必须用 (NAME_SIZE, NAME_MAX_WIDTH, 3 行)');
// 折行宽度是 CONTENT_W + 0.5 的容差，不是 CONTENT_W - BAR_X（88.82）：
// 官方第 2 行实测 97.35pt，用内容宽折行会把 DIN 挤到下一行，三行断词位置就跟官方不一样。
for (const [label, src, expr] of [
  ['labelPdf.ts', tsLabel, 'CONTENT_W + 0.5'],
  ['label_pdf.py', pyLabel, 'CONTENT_W + 0.5'],
]) {
  const raw = (label === 'labelPdf.ts' ? tsValue(src, 'NAME_MAX_WIDTH') : pyValue(src, 'NAME_MAX_WIDTH')) || '';
  check(raw.replace(/\s/g, '') === expr.replace(/\s/g, ''), `${label} 的 NAME_MAX_WIDTH 必须是 ${expr}，实得 ${raw}`);
}
check(tsLabel.includes('2 * PAD) / CONTENT_W') && pyLabel.includes('2 * PAD) / CONTENT_W'),
  '两边缩放公式都必须是 (尺寸 - 2*PAD) / CONTENT_W（少一个 PAD 会整体偏移）');

// ---- 7. 列表页下载标签时的产品名不能只取当前筛选结果 -----------------------
// 「勾选」存在 localStorage 里，不随筛选变化；`products` 只是当前筛选结果。
// 按 SKU 反查产品名如果只查当前这一屏，被筛掉的那些 SKU 会退回用 SKU 当产品名
// 印出来——标签上的名字本身是错的，而且看不出哪里错。名字要累积在 ref 里。
const labelNamesRef = list.indexOf('labelNames');
check(labelNamesRef >= 0, '列表页必须用 labelNames 缓存 SKU→产品名');
check(/labelNames\.current\.set\(/.test(list), 'SKU→产品名要累积写进 labelNames.current');
check(/labelNames\.current\.get\(/.test(listBody), '下载标签时产品名要从 labelNames.current 取');
check(!listBody.includes('products.forEach'),
  '下载标签时不许临时扫 products：那是筛选后的结果，被筛掉的 SKU 会拿 SKU 当产品名');
check(!/\{labelNote && <div className="alert alert-info">/.test(list),
  'labelNote 要有 role="status"，否则读屏用户听不到「跳过几个 SKU」（同页其它信息条都有）');

console.log(`test-label-ean.cjs: ${checks} 项检查通过`);
