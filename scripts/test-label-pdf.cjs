// 前端标签生成器（src/utils/labelPdf.ts）测试：不启浏览器，直接编译 TS 跑。
//
// 钉死这几件事：
//   1) EAN-13 编码是 95 模块、首尾守卫 101、中间分隔 01010 —— 少一位都扫不出来；
//   2) 页面就是标签本身 40x30mm（113.386 x 85.039pt），不是 A4 拼版；
//   3) 一个 SKU 一个文件，copies 是同一份 PDF 里的多页（打印机连打就是多张）；
//   4) 条码不合法的 SKU 只跳过，不拖累其它 SKU；
//   5) 同样输入两次生成字节完全一致（zip 时间戳固定成 1980-01-01）。
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

const file = path.resolve(__dirname, '../src/utils/labelPdf.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const {
  isEan13, ean13Modules, buildLabelPdf, buildLabelBundle,
  LABEL_SIZES, DEFAULT_LABEL_SIZE,
} = mod.exports;

const SKU = '9902348623136';
const NAME = 'CHARLES & KEITH WOMENS TWO-TONE METALLIC ACCENT SQUARE TOE MARY JANE FLATS';

// ---- 1) 校验位 ----
check(isEan13(SKU), '合法 EAN-13 必须通过');
check(!isEan13('9902348623137'), '校验位错的必须拒绝');
check(!isEan13('123'), '位数不够必须拒绝');
check(!isEan13('990234862313X'), '带非数字必须拒绝');
check(!isEan13(''), '空串必须拒绝');
check(!isEan13(null), 'null 必须拒绝');

// ---- 2) 编码结构 ----
{
  const bits = ean13Modules(SKU);
  check(bits.length === 95, `EAN-13 必须是 95 个模块，实际 ${bits.length}`);
  check(bits.slice(0, 3).join('') === '101', '起始守卫必须是 101');
  check(bits.slice(-3).join('') === '101', '结束守卫必须是 101');
  check(bits.slice(45, 50).join('') === '01010', '中间分隔必须是 01010');
  // 首位决定左侧 6 位的奇偶排列：9902348… 首位 9 → LGGLGL
  check(bits.every(b => b === 0 || b === 1), '模块只能是 0/1');
  check(bits.some(b => b === 1), '不能全是白模块');
}

// ---- 3) 单页尺寸与页数 ----
{
  const pdf = buildLabelPdf(NAME, SKU);
  const text = Buffer.from(pdf).toString('latin1');
  check(text.startsWith('%PDF-'), '必须是合法 PDF 头');
  check(text.includes('/MediaBox [0 0 113.386 85.039]'),
    '页面必须正好 40x30mm（113.386 x 85.039pt）');
  check((text.match(/\/Type \/Page[^s]/g) || []).length === 1, '一个 SKU 默认只出一页');
  check(text.includes('(M) Tj') && text.includes('(P) Tj'), '条码左侧的 M / P 标记不能丢');
  check(text.includes('(9) Tj'), '13 位数字要按 1-6-6 拆开画');
  check(text.includes('(902348) Tj') && text.includes('(623136) Tj'), '数字分组必须是 1-6-6');
  check(!/[^\x00-\x7f]/.test(text), 'PDF 里不能出现非 ASCII（会算错 xref 偏移）');
}

// ---- 4) copies 是同一份多页 ----
{
  const pdf = buildLabelPdf(NAME, SKU, { copies: 3 });
  const text = Buffer.from(pdf).toString('latin1');
  check((text.match(/\/Type \/Page[^s]/g) || []).length === 3, 'copies=3 应该是 3 页');
}

// ---- 5) 尺寸参数 ----
check(LABEL_SIZES['40x30'][0] === 40 && LABEL_SIZES['40x30'][1] === 30, '40x30 是 40 宽 30 高');
check(DEFAULT_LABEL_SIZE === '40x30', '默认必须是 40x30（30 宽会把条宽压到 0.258mm）');
{
  const pdf = buildLabelPdf(NAME, SKU, { size: '30x40' });
  const text = Buffer.from(pdf).toString('latin1');
  check(text.includes('/MediaBox [0 0 85.039 113.386]'), '30x40 应该是 30 宽 40 高');
}

// ---- 6) 单 SKU 给 pdf、多 SKU 给 zip ----
(async () => {
  const one = buildLabelBundle([{ sku: SKU, name: NAME }]);
  check(one.kind === 'pdf', '只有一个 SKU 时直接给 pdf');
  check(one.count === 1, '只有一个 SKU 时张数是 1');
  check(one.filename === `${SKU}.pdf`, `文件名应该是 SKU，实际 ${one.filename}`);

  const many = buildLabelBundle([
    { sku: SKU, name: NAME },
    { sku: '9902348621873', name: 'SHORT' },
    { sku: '123', name: 'BAD' },
  ]);
  check(many.kind === 'zip', '多个 SKU 应该打包成 zip');
  check(many.count === 2, `非法条码不占张数，实际 ${many.count}`);
  check(many.skipped.length === 1 && many.skipped[0].sku === '123', '非法条码要单独记下来');
  const bytes = new Uint8Array(await many.blob.arrayBuffer());
  check(bytes[0] === 0x50 && bytes[1] === 0x4b, 'zip 必须以 PK 开头');
  const zipText = Buffer.from(bytes).toString('latin1');
  check(zipText.includes(`${SKU}.pdf`), 'zip 里必须有一个 SKU 一个 pdf');
  check(zipText.includes('9902348621873.pdf'), '第二个 SKU 也要在');

  // 全是非法条码 → 0 张，不抛异常
  const none = buildLabelBundle([{ sku: '123', name: 'X' }]);
  check(none.count === 0, '全是非法条码时张数是 0');

  // ---- 7) 确定性 ----
  const a = Buffer.from(buildLabelPdf(NAME, SKU));
  const b = Buffer.from(buildLabelPdf(NAME, SKU));
  check(a.equals(b), '同样输入两次生成的字节必须完全一致');

  // ---- 8) 中文名不炸，替换成 ? ----
  const cn = buildLabelPdf('查尔斯 凯斯 女包', SKU);
  const cnText = Buffer.from(cn).toString('latin1');
  check(cnText.startsWith('%PDF-'), '中文产品名也要能生成');
  check(!/[^\x00-\x7f]/.test(cnText), '中文要被替换掉，不能写进 PDF 文本流');

  console.log(`test-label-pdf: ${checks} checks passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
