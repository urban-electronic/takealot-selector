// 视觉一致性护栏 —— 把界面上曾经退化的三类问题钉死，避免再次出现。
//
// 背景（2026-10-17 全路由实测审计，1366/768/390 三档 × 10 个页面）：
//   1. 最小字号掉到 9px：表格里的"主/分支"徽标白字只有 2.7:1，几乎看不清；
//   2. 次要文字大面积不达标：装箱/采购/库存三处小字实测 2.9~3.9:1（AA 要求 4.5:1）；
//   3. 行内控件只有 21~23px 高：产品表里 82 个可点目标低于 24px；
//   4. 同一张表头写成四种度量（13px/11px、padding 10/11/8/6）；
//   5. 链接状态用饱和底色配白字，"未购买/已上架/已发货"实测 1.90/2.27/2.16:1。
//
// 这些退化用肉眼很难守住，但用静态检查可以：本脚本不依赖浏览器，可随 CI 一起跑。
// 运行：node scripts/check-visual-consistency.cjs

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

const indexCss = stripComments(read('src/index.css'));
const workspaceCss = stripComments(read('src/workspace.css'));
const tokensCss = stripComments(read('src/ui-tokens.css'));
const mainTsx = read('src/main.tsx');

const styleLines = (css) => css.split('\n').map((text, i) => ({ line: i + 1, text }));

// --- 1. 最小字号：样式层不允许出现 11px 以下的字号 ----------------------------------
{
  const offenders = [];
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss], ['src/ui-tokens.css', tokensCss]]) {
    for (const { line, text } of styleLines(css)) {
      const m = text.match(/font-size:\s*(\d+(?:\.\d+)?)px/);
      if (m && Number(m[1]) < 11) offenders.push(`${file}:${line}  ${text.trim()}`);
    }
  }
  assert.deepEqual(offenders, [], `字号不得小于 11px（曾出现 9px 徽标导致白字仅 2.7:1）:\n${offenders.join('\n')}`);
}

// --- 2. 文字对比度：禁止再次引入实测不达标的浅色文字 --------------------------------
{
  // 全部为实测白底/浅底 < 4.5:1 的取值，替换目标见 src/ui-tokens.css 的文字色令牌。
  const FAILING_TEXT_COLORS = [
    '#8792a5', '#71829a', '#94a3b8', '#8b99ab', '#718096', '#8490a1', '#8a96a6',
    '#758386', '#718083', '#8a919d', '#9aa2af', '#98a1af', '#7b8493', '#8c929d',
    '#7d8591', '#87909e', '#707887', '#b4bac4', '#9aa6b5', '#74849b', '#71839b',
    '#6b7d95', '#d48806', '#059669', '#d46b08', '#b67822', '#768192', '#77808d',
    '#6b7f73', '#a3adba', '#8a94a5', '#bbb', '#aaa',
  ];
  const offenders = [];
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss]]) {
    for (const { line, text } of styleLines(css)) {
      const m = text.match(/(?<![-\w])color:\s*(#[0-9a-fA-F]{3,8})\b/);
      if (m && FAILING_TEXT_COLORS.includes(m[1].toLowerCase())) offenders.push(`${file}:${line}  ${text.trim()}`);
    }
  }
  assert.deepEqual(offenders, [], `不得使用对比度不达标的文字色（改用 --text-muted / --text-subtle / --warning-strong 等令牌）:\n${offenders.join('\n')}`);
}

// --- 3. 控件目标尺寸：兜底规则与行内控件令牌必须存在 --------------------------------
{
  assert.match(tokensCss, /--control-h-min:\s*24px/, 'ui-tokens.css 必须定义 --control-h-min（WCAG 2.5.8 目标尺寸下限）');
  assert.match(tokensCss, /--control-h-compact:\s*28px/, 'ui-tokens.css 必须定义 --control-h-compact（表格行内控件高度）');
  assert.match(tokensCss, /:where\(button,\s*select\)\s*\{[^}]*min-height:\s*var\(--control-h-min\)/, '缺少按钮/下拉的最小高度兜底规则');
  assert.match(tokensCss, /\.cell-control\s*\{[^}]*height:\s*var\(--control-h-compact\)/, '.cell-control 必须使用行内控件高度令牌');

  // 两个图标按钮是历史上最小的可点目标（23×23 与 18×18），必须走令牌。
  const priceRefresh = indexCss.match(/\.price-refresh-button\s*\{[^}]*\}/);
  assert.ok(priceRefresh, '找不到 .price-refresh-button 规则');
  assert.match(priceRefresh[0], /width:\s*var\(--control-h-compact\)/, '.price-refresh-button 宽度需引用令牌');
  const priceStatus = indexCss.match(/\.price-refresh-status\s*\{[^}]*\}/);
  assert.ok(priceStatus, '找不到 .price-refresh-status 规则');
  assert.match(priceStatus[0], /height:\s*var\(--control-h-min\)/, '.price-refresh-status 高度需引用令牌');
}

// --- 4. 表头度量：只允许来自令牌 ---------------------------------------------------
{
  const banned = [
    { pattern: /background:\s*#f1f4f0/i, why: '旧表头底色 #f1f4f0 已废弃，改用 var(--table-head-bg)' },
  ];
  for (const { pattern, why } of banned) {
    for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss]]) {
      for (const { line, text } of styleLines(css)) {
        if (pattern.test(text)) assert.fail(`${file}:${line} ${why}\n  ${text.trim()}`);
      }
    }
  }
  const dataTableHead = indexCss.match(/\.operations-workspace \.data-table th\s*\{[^}]*\}/);
  assert.ok(dataTableHead, '找不到 .operations-workspace .data-table th 规则');
  assert.match(dataTableHead[0], /font-size:\s*var\(--table-head-fs\)/, '表格表头字号必须来自令牌，不得硬编码 11px/13px');
  assert.match(indexCss, /\.inventory-table th\s*\{[^}]*padding:\s*var\(--table-head-pad-y\) var\(--table-head-pad-x\)/, '.inventory-table 表头内边距必须来自令牌');
  assert.match(indexCss, /\.inventory-table th\s*\{[^}]*font-size:\s*var\(--table-head-fs\)/, '.inventory-table 表头字号必须来自令牌');
}

// --- 5. 令牌层必须晚于页面样式引入（否则兜底约束会被压掉） --------------------------
{
  const idx = mainTsx.indexOf("import './index.css'");
  const ws = mainTsx.indexOf("import './workspace.css'");
  const tk = mainTsx.indexOf("import './ui-tokens.css'");
  assert.ok(idx >= 0 && ws >= 0 && tk >= 0, 'main.tsx 必须引入 index.css / workspace.css / ui-tokens.css');
  assert.ok(tk > idx && tk > ws, 'ui-tokens.css 必须排在 index.css 与 workspace.css 之后引入');
}

// --- 6. 行内控件不再用内联样式写死尺寸 ----------------------------------------------
{
  const productList = read('src/pages/ProductList.tsx');
  assert.ok(!/padding:\s*'2px 4px'/.test(productList), 'ProductList 的行内控件应使用 .cell-control，而不是内联 padding');
}

// --- 7. 链接状态配色必须提供深色文字，避免"饱和底 + 白字" --------------------------
{
  const types = read('src/types.ts');
  const map = types.match(/LINK_STATUS_MAP[^=]*=\s*\{[\s\S]*?\n\};/);
  assert.ok(map, '找不到 LINK_STATUS_MAP 定义');
  const entries = map[0].match(/\{\s*label:\s*'[^']*',\s*color:\s*'#[0-9a-fA-F]{6}',\s*text:\s*'#[0-9a-fA-F]{6}'\s*\}/g) || [];
  assert.equal(entries.length, 4, `LINK_STATUS_MAP 的四个状态都必须给出 label/color/text 三个取值，实际匹配到 ${entries.length} 条`);
}

console.log('视觉一致性检查通过：字号下限、文字对比度、控件尺寸、表头度量、令牌引入顺序、状态配色 7 组约束均满足。');
