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

// --- 8. WCAG 对比度引擎 ------------------------------------------------------------
// 上面第 2 组是"黑名单"（已知坏色不许再出现），但黑名单守不住没见过的坏值：
// 评审实测就抓到过占位字 4.07:1、无图占位 4.18:1、白色焦点圈 1.1:1 三类漏网。
// 因此这里内置一个可复现的对比度计算器：任何人跑 `node scripts/check-visual-consistency.cjs`
// 都能得到同样的逐条判定，不依赖浏览器、不依赖本机截图。
const srgbToLin = (c) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const expandHex = (hex) => { let h = hex.replace('#', ''); if (h.length === 3) h = h.split('').map((c) => c + c).join(''); return h; };
const relLum = (hex) => {
  const h = expandHex(hex);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
};
const contrast = (a, b) => {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const fmt = (n) => n.toFixed(2);

// 令牌表：把 var(--x) 解析成十六进制，静态规则才能参与计算。
const TOKENS = {};
for (const m of tokensCss.matchAll(/(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) TOKENS[m[1]] = m[2];
const resolveColor = (value) => {
  if (!value) return null;
  let v = value.trim();
  for (let i = 0; i < 5 && /^var\(/.test(v); i += 1) {
    const name = v.match(/^var\((--[\w-]+)/)[1];
    if (!(name in TOKENS)) return null;
    v = TOKENS[name];
  }
  return /^#[0-9a-fA-F]{3,8}$/.test(v) ? v : null;
};

// 把 CSS 拆成「选择器 + 声明块」。@media 之类的外层包裹没有声明，会被自然跳过。
const cssRules = (css) => {
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) out.push({ selector: m[1].trim().replace(/\s+/g, ' '), decl: m[2] });
  return out;
};

// --- 9. 链接状态配色：逐态算对比度，并用"改回白字"的突变证明卡有效 -------------
{
  const types = read('src/types.ts');
  const map = types.match(/LINK_STATUS_MAP[^=]*=\s*\{[\s\S]*?\n\};/);
  assert.ok(map, '找不到 LINK_STATUS_MAP 定义');

  const validate = (mapSource) => {
    const entries = mapSource.match(/\{\s*label:\s*'[^']*',\s*color:\s*'#[0-9a-fA-F]{6}',\s*text:\s*'#[0-9a-fA-F]{6}'\s*\}/g) || [];
    assert.equal(entries.length, 4, `LINK_STATUS_MAP 的四个状态都必须给出 label/color/text，实际匹配到 ${entries.length} 条`);
    const bad = [];
    for (const entry of entries) {
      const bg = entry.match(/color:\s*'(#[0-9a-fA-F]{6})'/)[1];
      const fg = entry.match(/text:\s*'(#[0-9a-fA-F]{6})'/)[1];
      const r = contrast(fg, bg);
      if (r < 4.5) bad.push(`  ${entry.replace(/\s+/g, ' ')} -> ${fmt(r)}:1`);
    }
    assert.deepEqual(bad, [], `链接状态文字与底色对比度必须 >=4.5:1（白字配亮底历史上只有 1.90:1）:\n${bad.join('\n')}`);
    return entries.length;
  };

  const okCount = validate(map[0]);
  // 突变自测：把四态文字改回白色，校验必须失败。若这里不抛错，说明上面的卡是摆设。
  const whiteMutant = map[0].replace(/text:\s*'#[0-9a-fA-F]{6}'/g, "text: '#ffffff'");
  assert.throws(() => validate(whiteMutant), /对比度必须/, '突变自测失败：把文字改成白色后校验器竟然通过，等于没有设卡');
  assert.equal(okCount, 4, 'LINK_STATUS_MAP 应包含四个链接状态');
}

// --- 10. 占位提示文字：实测抓到过 4.07:1（#6b7a90 on #f5f7fa）--------------------
{
  const placeholder = resolveColor('var(--text-placeholder)');
  assert.ok(placeholder, 'ui-tokens.css 必须定义 --text-placeholder');
  for (const bg of ['#ffffff', '#f5f7fa', '#f1f3f5']) {
    const r = contrast(placeholder, bg);
    assert.ok(r >= 4.5, `占位提示色 ${placeholder} 在 ${bg} 上只有 ${fmt(r)}:1（曾实测 4.07:1），需 >=4.5:1`);
  }
}

const outlineColorOf = (ruleSource) => {
  const raw = (ruleSource.match(/outline(?:-color)?:\s*([^;}]+)/) || [])[1] || '';
  return (raw.match(/#[0-9a-fA-F]{3,8}\b/) || [])[0] || raw.trim();
};

// --- 11. 焦点圈：禁止白色；浅底上必须 >=3:1（WCAG 2.2 非文字对比）-----------------
{
  const banned = [];
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss], ['src/ui-tokens.css', tokensCss]]) {
    for (const { line, text } of styleLines(css)) {
      if (/outline(?:-color)?:\s*#(?:fff|ffffff)\b/i.test(text)) banned.push(`${file}:${line}  ${text.trim()}`);
    }
  }
  assert.deepEqual(banned, [], '焦点圈不允许用白色：浅色底上仅约 1.1:1，键盘用户根本看不见:\n' + banned.join('\n'));

  const plain = tokensCss.match(/\.cell-control:focus-visible\s*\{[^}]*\}/);
  assert.ok(plain, '找不到 .cell-control:focus-visible 规则');
  const plainColor = resolveColor(outlineColorOf(plain[0]));
  assert.ok(plainColor, '.cell-control:focus-visible 必须给出可解析的 outline 颜色');
  assert.ok(contrast(plainColor, '#ffffff') >= 3, `.cell-control 焦点圈 ${plainColor} 在白底上需 >=3:1，实测 ${fmt(contrast(plainColor, '#ffffff'))}:1`);

  const tinted = tokensCss.match(/\.cell-control--tinted:focus-visible\s*\{[^}]*\}/);
  assert.ok(tinted, '找不到 .cell-control--tinted:focus-visible 规则');
  const tintedColor = resolveColor(outlineColorOf(tinted[0]));
  assert.ok(tintedColor, '.cell-control--tinted:focus-visible 必须给出可解析的 outline-color');
  for (const bg of ['#ffffff', '#e8f1ff', '#fff4d6', '#e8f6ea']) {
    const r = contrast(tintedColor, bg);
    assert.ok(r >= 3, `着色控件焦点圈 ${tintedColor} 在 ${bg} 上只有 ${fmt(r)}:1，需 >=3:1（旧白色值仅 1.1:1）`);
  }
}

// --- 11b. 文字色令牌本身必须达标 ---------------------------------------------------
// 历史上 --color-text-secondary 在三个文件里各写一遍（#6b7280 / #64748b / #627084），
// 浏览器实测落在 4.36~4.48:1。这里把所有"名字带 text 的令牌"统一按浅底核一遍。
{
  const bad = [];
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss], ['src/ui-tokens.css', tokensCss]]) {
    for (const m of css.matchAll(/(--[\w-]*text[\w-]*)\s*:\s*([^;}]+)/g)) {
      const name = m[1];
      const raw = m[2].trim();
      if (/inverse/i.test(name)) continue;        // --text-inverse 是深底上的白字，不按浅底核
      if (/^var\(/.test(raw)) continue;           // 转寄到令牌层的，由令牌层负责
      const hex = (raw.match(/#[0-9a-fA-F]{3,8}\b/) || [])[0];
      if (!hex) continue;
      for (const bg of ['#ffffff', '#f3f5f9']) {
        const r = contrast(hex, bg);
        if (r < 4.5) bad.push(`  ${file}  ${name}: ${hex} on ${bg} -> ${fmt(r)}:1`);
      }
    }
  }
  assert.deepEqual(bad, [], `文字色令牌不得低于 4.5:1（曾有三处 --color-text-secondary 分别写成 #6b7280/#64748b/#627084，实测 4.36~4.48:1）:\n${bad.join('\n')}`);
}

// --- 12. 静态全量扫描：同一条规则里 color + background 成对出现就必须达标 ---------
{
  const hard = [];   // < 3:1：直接失败（任何字号都不可读）
  const soft = [];   // 3.0~4.5:1：记警告，由人工确认是否大字号/装饰
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss], ['src/ui-tokens.css', tokensCss]]) {
    for (const { selector, decl } of cssRules(css)) {
      if (/linear-gradient|radial-gradient|url\(/i.test(decl)) continue; // 渐变/图床底无法静态判定
      const fgRaw = (decl.match(/(?<![-\w])color:\s*([^;}]+)/) || [])[1];
      const bgRaw = (decl.match(/(?<![-\w])background(?:-color)?:\s*([^;}]+)/) || [])[1];
      const fg = resolveColor(fgRaw);
      const bg = resolveColor(bgRaw);
      if (!fg || !bg) continue;
      const r = contrast(fg, bg);
      const item = `  ${file}  ${selector}\n      ${fg} on ${bg} -> ${fmt(r)}:1`;
      if (r < 3) hard.push(item);
      else if (r < 4.5) soft.push(item);
    }
  }
  assert.deepEqual(hard, [], `以下规则文字与底色对比度不足 3:1，任何字号都不可读:\n${hard.join('\n')}`);
  if (soft.length) {
    console.log(`[提示] ${soft.length} 条规则落在 3.0~4.5:1 区间（大字号/装饰性文字可接受，正文需提升）:`);
    console.log(soft.slice(0, 12).join('\n'));
    if (soft.length > 12) console.log(`  …… 其余 ${soft.length - 12} 条从略`);
  }
}

// --- 12b. 渐变底上的文字：按渐变自己的色标算，不再整条跳过 -------------------------
// 上面第 12 组遇到 `linear-gradient` 直接 continue（「渐变/图床底无法静态判定」）。
// 但渐变恰恰是最容易翻车的地方：主按钮兜底色 #2059b7、渐变 0% 色标更浅，只看兜底色
// 会把白字按 5.9:1 判过，实测只有 4.11:1（docs/review/2026-10-27-button-gradient-contrast.md）。
// 这里只处理「同一条规则里既有渐变又有显式文字色」的情况——文字色靠父类继承的
// （.packing-export-btn 从 .btn 继承白字）静态看不出来，由 scripts/test-button-gradient.cjs
// 与浏览器审计 scripts/ui_contrast_audit.py 负责，两边合起来才覆盖完整。
{
  // 只取渐变函数括号内的色标：decl 里的 border-color / box-shadow 也是色值，混进来会算出 1:1。
  const gradientStops = (decl) => {
    const out = [];
    for (const fn of decl.matchAll(/(?:linear|radial)-gradient\(([^;]*?)\)(?=[,;\s]|$)/g)) {
      for (const c of fn[1].matchAll(/#[0-9a-fA-F]{3,8}\b/g)) out.push(c[0]);
    }
    return out;
  };
  const validate = (css, file) => {
    const bad = [];
    let checked = 0;
    for (const { selector, decl } of cssRules(css)) {
      if (!/gradient/i.test(decl)) continue;
      const fg = resolveColor((decl.match(/(?<![-\w])color:\s*([^;}]+)/) || [])[1]);
      const stops = gradientStops(decl);
      if (!fg || !stops.length) continue;
      const size = Number((decl.match(/font-size:\s*([\d.]+)px/) || [])[1] || 13);
      const weight = Number((decl.match(/font-weight:\s*(\d+)/) || [])[1] || 400);
      const need = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
      let worst = null;
      for (const stop of stops) {
        const r = contrast(fg, stop);
        if (!worst || r < worst.r) worst = { r, stop };
      }
      checked += 1;
      if (worst.r < need) bad.push(`  ${file}  ${selector}\n      ${fg} on ${worst.stop} -> ${fmt(worst.r)}:1（需 ${need}:1）`);
    }
    return { bad, checked };
  };

  const runCheck = (css, file) => {
    const { bad, checked } = validate(css, file);
    assert.deepEqual(bad, [], `渐变底上的文字对比度必须达标（只看 background-color 会漏掉浅色标）:\n${bad.join('\n')}`);
    return checked;
  };

  let total = 0;
  for (const [file, css] of [['src/index.css', indexCss], ['src/workspace.css', workspaceCss], ['src/ui-tokens.css', tokensCss]]) {
    total += runCheck(css, file);
  }
  // 覆盖面锁：解析器一旦退化（比如改了渐变写法就匹配不到），total 会掉到 0 而上面照样通过。
  assert.ok(total >= 5, `渐变+显式文字色的规则只解析到 ${total} 条（应 >=5），解析器可能已失效`);

  // 突变自测：把主按钮文字改成它自己渐变里的浅色标，校验必须失败。
  const mutant = workspaceCss.replace(
    /\.btn-primary\s*\{[^}]*\}/,
    (block) => block.replace(/(?<![-\w])color:\s*#fff\b/, 'color: #2864cc'),
  );
  assert.notEqual(mutant, workspaceCss, '突变自测没生效：.btn-primary 里找不到 color: #fff');
  assert.throws(() => runCheck(mutant, 'mutant'), /渐变底上的文字对比度/, '突变自测失败：文字与渐变同色时校验器竟然通过，等于没有设卡');
}

// --- 13. 渲染层：状态徽标必须取 LINK_STATUS_MAP.text，不得写死白字 ---------------

{
  for (const rel of ['src/pages/ProductList.tsx', 'src/pages/ProductDetail.tsx']) {
    const src = read(rel);
    assert.ok(!/color:\s*'#(?:fff|ffffff)'/.test(src), `${rel} 不允许写死白色文字（状态徽标请用 LINK_STATUS_MAP[...].text）`);
  }
  const productList = read('src/pages/ProductList.tsx');
  assert.match(productList, /LINK_STATUS_MAP\[[^\]]*\]\?\.text/, 'ProductList 的状态下拉必须使用 LINK_STATUS_MAP 的深色文字取值');
}

// --- 14. 组件内联 style 不得直接写裸色值 -----------------------------------------
// 审计时界面里有 332 个硬编码色值，令牌层收敛了样式表，但 .tsx 的 style={{...}}
// 里还留着 52 个（#fff / #ddd / #eee / #f0f0f0 / #555 …）。它们绕过令牌，
// 换主题时要翻 6 个页面逐个改，而且同一个语义在不同页面深浅不一。
// 允许的例外只有两类：
//   1. var(--token, #hex) 的兜底形式（有令牌就生效，裸色值只是保险）；
//   2. src/types.ts 里的状态配色表（那是数据，不是主题；本身已带对比度注释）。
{
  const offenders = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.tsx')) {
        const rel = path.relative(root, full).split(path.sep).join('/');
        fs.readFileSync(full, 'utf8').split('\n').forEach((text, i) => {
          if (/^\s*(\/\/|\*|\/\*)/.test(text)) return;              // 注释
          const stripped = text.replace(/var\([^)]*\)/g, '');        // 令牌兜底
          if (/'#[0-9a-fA-F]{3,8}'/.test(stripped)) offenders.push(`${rel}:${i + 1}  ${text.trim()}`);
        });
      }
    }
  };
  walk(path.join(root, 'src'));
  assert.deepEqual(offenders, [], `组件内联 style 不得写裸色值，请改用 ui-tokens.css 里的令牌（如 var(--color-border) / var(--surface-muted) / var(--text-muted)）:\n${offenders.join('\n')}`);
}

// --- 15. 动效必须尊重 reduced-motion ---------------------------------------------
// 页面里有 transition / animation，但只有 .btn 和头部导航做了降级。
// 统一在令牌层兜底：任何过渡与动画在 reduced-motion 下都退化为无。
{
  assert.match(tokensCss, /@media\s*\(prefers-reduced-motion:\s*reduce\)/, 'ui-tokens.css 必须提供 prefers-reduced-motion 兜底');
  const block = tokensCss.slice(tokensCss.search(/@media\s*\(prefers-reduced-motion:\s*reduce\)/));
  assert.match(block, /animation-duration:\s*0?\.01ms\s*!important/, 'reduced-motion 下必须把 animation-duration 压到近零');
  assert.match(block, /transition-duration:\s*0?\.01ms\s*!important/, 'reduced-motion 下必须把 transition-duration 压到近零');
}

// --- 16. SKU 单元格：一列只允许一套排版 -------------------------------------------
// 2026-10-31 现场：同一列里单规格走 .sku-value（12px 等宽），多规格走
// .sku-branch-summary <b>（继承表格 13px 无衬线粗体），于是同一列出现两种字；
// 「复制」按钮又是 inline 跟在文本后面，x 位置随 SKU 文字宽度漂移，整列对不齐。
// 这条护栏钉三件事：主标识只有一套字、复制按钮在独立的 grid 列、旧类不许回潮。
{
  const cssRule = (css, selector) => {
    const marker = `\n${selector} {`;
    const at = css.indexOf(marker);
    if (at < 0) return '';
    const start = at + marker.length;
    return css.slice(start, css.indexOf('}', start));
  };

  assert.ok(!indexCss.includes('.sku-branch-summary'), '旧的 .sku-branch-summary 已删除（它让多规格 SKU 用表格默认字体），不要加回来');

  const cellRule = cssRule(indexCss, '.sku-cell');
  assert.match(cellRule, /display:\s*grid/, '.sku-cell 必须是 grid：复制按钮要落在独立列里，才可能整列对齐');
  assert.match(cellRule, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/, '.sku-cell 必须是「标识 1fr + 操作 auto」两列');
  assert.match(cellRule, /align-items:\s*center/, '.sku-cell 必须垂直居中，单行/两行单元格重心才会在同一条线上');

  const valueRule = cssRule(indexCss, '.sku-value');
  assert.match(valueRule, /font-family:\s*var\(--font-mono\)/, '.sku-value 必须用等宽令牌，逐位核对才看得清');
  assert.match(valueRule, /font-size:\s*var\(--fs-label\)/, '.sku-value 字号必须走令牌（12px）');
  assert.match(valueRule, /font-weight:\s*600/, '.sku-value 字重固定 600，不要靠 <b> 继承表格字重');

  assert.match(cssRule(indexCss, '.sku-copy'), /justify-self:\s*end/, '.sku-copy 必须靠右钉在操作列');

  const listTsx = read('src/pages/ProductList.tsx');
  assert.ok(!/<b translate="no">/.test(listTsx), 'SKU 主标识不得再用 <b>（会继承表格字体，与单规格行不一致）');
  assert.match(listTsx, /className="sku-line"/, 'SKU 主标识必须走 .sku-line 结构');

  // 突变自测：把 grid 改回 flex，上面的断言必须变红，否则这条护栏是摆设。
  const mutated = indexCss.replace(/(\.sku-cell \{[\s\S]*?)display: grid;/, '$1display: flex;');
  assert.notEqual(mutated, indexCss, '突变自测未能改写 .sku-cell，说明规则形态变了，请同步更新本组断言');
  assert.ok(!/display:\s*grid/.test(cssRule(mutated, '.sku-cell')), '突变自测：.sku-cell 改回 flex 时必须被拦住');
}

console.log('视觉一致性检查通过：字号下限、文字对比度黑名单、控件尺寸、表头度量、令牌引入顺序、状态配色（含对比度计算与白字突变自测）、占位提示、焦点圈、静态全量扫描、渐变底文字（含突变自测）、渲染层白字禁令、组件裸色值禁令、reduced-motion 兜底、SKU 单元格单一排版 共 17 组约束均满足。');
