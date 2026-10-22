// 产品标签（EAN-13）的口径守卫：不启浏览器，直接编译 TS。
//
// 钉死的四件事：
//   1) 三份 EAN-13 实现必须同一套判据：后端 services/label_pdf.py、详情页计数用的
//      src/utils/ean.ts、真正出标签的 src/utils/labelPdf.ts。改了其中一份的权重或长度，
//      按钮上写的「N 个 SKU」和实际下载到的文件数就会对不上。
//   2) 详情页生成标签失败不能写进 `error`。那个状态渲染时整页被换成一行红字
//      （`if (error) return …`），编辑中的草稿一起丢——生成失败是可重试的小事。
//   3) 列表页「生成成功但跳过几个」不是错误，不能占用页面顶部那条红色告警。
//   4) 折行的省略号规则（只在真丢词时出现）两边一致，见 test-label-pdf.cjs / pytest。
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

console.log(`test-label-ean.cjs: ${checks} 项检查通过`);
