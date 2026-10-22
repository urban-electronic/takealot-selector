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

  // 护线：官方版式里起/中/止六根护线比数据条长 9.6pt（31.181 vs 21.581），
  // 一直伸到 13 位数字那一行。少了它整张标签会比官方矮一截。
  check((text.match(/31\.181 re f/g) || []).length === 6,
    `护线必须正好 6 根，实际 ${(text.match(/31\.181 re f/g) || []).length}`);
  check((text.match(/21\.581 re f/g) || []).length > 0, '数据条高必须是 21.581');
  check(text.includes('0.459 0.935 31.181 re f'), '首根护线要在 y=0.459、宽 0.935');
  // 文字基线（官方实测）：产品名 53.3 / 45.3 / 37.3，M 23.13，P 14.63，数字 2.37
  check(text.includes('0 53.30 Tm') && text.includes('0 45.30 Tm') && text.includes('0 37.30 Tm'),
    '产品名三行基线必须是 53.3 / 45.3 / 37.3');
  check(text.includes('0.00 23.13 Tm') && text.includes('0.00 14.63 Tm'), 'M / P 基线必须是 23.13 / 14.63');
  check(text.includes('2.37 Tm'), '13 位数字基线必须是 2.37');
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
  check(one.filename === `${SKU}.pdf`, `没给中文名时文件名就是 SKU，实际 ${one.filename}`);

  // 文件名 = SKU + 中文简写，打印时一眼能认出是哪个品
  const named = buildLabelBundle([{ sku: SKU, name: NAME, cn: '皮革黑色包' }]);
  check(named.filename === `${SKU}_皮革黑色包.pdf`, `文件名要带中文简写，实际 ${named.filename}`);
  // 占位值不能当文件名（多规格没对上颜色/尺码时会出现）
  const placeholder = buildLabelBundle([{ sku: SKU, name: NAME, cn: '规格待识别' }]);
  check(placeholder.filename === `${SKU}.pdf`, `占位值不进文件名，实际 ${placeholder.filename}`);

  const many = buildLabelBundle([
    { sku: SKU, name: NAME, cn: '皮革黑色包' },
    { sku: '9902348621873', name: 'SHORT' },
    { sku: '123', name: 'BAD' },
  ]);
  check(many.kind === 'zip', '多个 SKU 应该打包成 zip');
  check(many.count === 2, `非法条码不占张数，实际 ${many.count}`);
  check(many.skipped.length === 1 && many.skipped[0].sku === '123', '非法条码要单独记下来');
  const bytes = new Uint8Array(await many.blob.arrayBuffer());
  check(bytes[0] === 0x50 && bytes[1] === 0x4b, 'zip 必须以 PK 开头');
  // 文件名是 UTF-8 的（通用标志位 bit 11 已置位），要按 utf8 解码才能比对中文
  const zipUtf8 = Buffer.from(bytes).toString('utf8');
  check(zipUtf8.includes(`${SKU}_皮革黑色包.pdf`), 'zip 里文件名要带中文简写');
  check(zipUtf8.includes('9902348621873.pdf'), '没有中文名的回退成纯 SKU');
  const zipLatin = Buffer.from(bytes).toString('latin1');
  check((zipLatin.match(/\x00\x08\x00\x00/g) || []).length >= 2, 'zip 条目要置 UTF-8 标志位');

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

  // ---- 9) 折行：名字放得下就不许丢词，真丢词时才必须标省略号 ----
  // 产品名行是 PDF 里最前面的几个 Tj（之后才是 1-6-6 的数字分组和 M/P）。
  const nameLines = (name) => {
    const text = Buffer.from(buildLabelPdf(name, SKU)).toString('latin1');
    const out = [];
    for (const m of text.matchAll(/\(([^()]*)\) Tj/g)) {
      if (/^\d+$/.test(m[1])) break;
      out.push(m[1]);
    }
    return out;
  };
  const rep = (n) => Array.from({ length: n }, () => 'MMMM').join(' ');
  // 先量出「一行能放几个词」，再由它推出 3 行能放几个——不写死字宽表
  let perLine = 0;
  for (let n = 1; n <= 40; n += 1) {
    const lines = nameLines(rep(n));
    if (lines.length === 1) perLine = n;
    else break;
  }
  check(perLine >= 2, `单行至少要放得下 2 个词，实得 ${perLine}`);
  const capacity = perLine * 3;
  for (let n = 1; n <= 40; n += 1) {
    const name = rep(n);
    const lines = nameLines(name);
    check(lines.length > 0 && lines.length <= 3, `${n} 个词最多折 3 行`);
    const last = lines[lines.length - 1];
    if (n <= capacity) {
      // 全部放得下：一个词都不许丢，末行也不许带省略号。
      // 旧实现「凑满 3 行就截末行」正是在这里出错（n 正好等于 3 行容量时）。
      check(lines.join(' ') === name, `${n} 个词放得下，不许丢词（实得 ${JSON.stringify(lines)}）`);
      check(!last.endsWith('...'), `${n} 个词放得下，末行不许加省略号（实得 ${last}）`);
    } else {
      check(last.endsWith('...'), `${n} 个词放不下，末行必须标省略号（实得 ${last}）`);
    }
  }

  console.log(`test-label-pdf: ${checks} checks passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
