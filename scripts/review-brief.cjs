// 审查交底 —— 按本次改动涉及的文件，列出该看哪几条清单、该跑哪几个命令。
//
// 标准正文：docs/engineering/code-review-standard.md（条目编号 A1…H4 与之对应）。
// 这个脚本只做「交底」，不做判定：永远 exit 0，不阻断任何流程。
//
// 运行：node scripts/review-brief.cjs
//       node scripts/review-brief.cjs --staged   （只看暂存区）

const { execFileSync } = require('node:child_process');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const stagedOnly = process.argv.includes('--staged');

function git(args) {
  try {
    return execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();
  } catch {
    return '';
  }
}

function changedFiles() {
  const diffArgs = stagedOnly
    ? ['diff', '--name-only', '--cached']
    : ['diff', '--name-only', 'HEAD'];
  const list = new Set();
  git(diffArgs).split('\n').filter(Boolean).forEach((f) => list.add(f.trim()));
  if (!stagedOnly) {
    git(['ls-files', '--others', '--exclude-standard'])
      .split('\n')
      .filter(Boolean)
      .forEach((f) => list.add(f.trim()));
  }
  return [...list].sort();
}

// 规则按路径匹配；一个文件可以命中多条，结论取最高风险级别。
const RULES = [
  {
    test: /^backend\/services\/(buybox_monitor|takealot_scraper|quote_browser)\.py$/,
    level: 'R1',
    label: '采集链路（卡住会冻住整条循环）',
    items: ['A3 改动后重跑真实触发路径', 'C1 每个等待都要有上限', 'C3 心跳不等于活着', 'C4 锁只在真拿到时释放', 'D1 失败必须能归因'],
    commands: ['npm run check:py', '线上回读 /api/automatic-pricing → loop.cycle_age_seconds'],
  },
  {
    test: /^backend\/(api|services)\/(pricing|profit|fee|fx)/,
    level: 'R1',
    label: '价格/成本/利润计算（算错会亏钱）',
    items: ['A1 一个 PR 一个缺陷', 'B1 0/空/null 口径全链路一致', 'E1 时间口径', 'H1 新断言必须能红'],
    commands: ['npm run check:py', 'node scripts/test-price-parse.cjs', 'node scripts/test-fx-rates.cjs', 'node scripts/test-fee-categories.cjs'],
  },
  {
    test: /^backend\//,
    level: 'R2',
    label: '后端数据与状态',
    items: ['B1 0/空/null 口径', 'B3 空列表也要走到', 'D1 失败可归因', 'D2 状态码区分暂时/永久', 'E2 禁裸 datetime.now()'],
    commands: ['npm run check:py'],
  },
  {
    test: /^src\//,
    level: 'R3',
    label: '前端界面与交互',
    items: ['B2 受控数字输入不吞输入', 'E1 展示走 beijingDateTime()', 'E3 禁不带 timeZone 的 toLocale*', 'G1 用现有 token/组件', 'G3 截断验证看 computed style'],
    commands: ['npm run check:types', 'node scripts/check-visual-consistency.cjs', 'node scripts/test-datetime.cjs', 'node scripts/test-responsive-layout.py（全尺寸，不要只挑一档）'],
  },
  {
    test: /\.css$/,
    level: 'R3',
    label: '样式（审计会逐尺寸量）',
    items: ['G2 视觉与可访问性守卫全跑', 'G3 截断/换行不要用 scrollWidth 判', 'G4 中英双行不撑破单元格'],
    commands: ['node scripts/check-visual-consistency.cjs', 'node scripts/test-responsive-layout.py', 'node scripts/test-text-overlap.py', 'node scripts/test-contrast-audit.py'],
  },
  {
    test: /^scripts\/test-.*\.(c?js|mjs|py)$/,
    level: 'R3',
    label: '测试/审计脚本本身（最容易假绿的地方）',
    items: ['H1 反向探针：撤掉修复要能红', 'H2 守卫要真的查注释里声称的规则', 'H3 钉实现，不只钉行为', 'H4 fixture 要覆盖被测路径'],
    commands: ['把该脚本改坏一次，确认它变红', '恢复后单跑该脚本'],
  },
  {
    test: /^scripts\/selfcheck\.cjs$/,
    level: 'R2',
    label: '门禁入口（加一步等于给所有人加一步）',
    items: ['新步骤要能在干净环境跑通（依赖缺失必须红灯，不许静默跳过）', '步骤清单与 test-selfcheck-modes.cjs 保持一致'],
    commands: ['node scripts/test-selfcheck-modes.cjs', 'npm run check'],
  },
  {
    test: /^scripts\/(?!test-)/,
    level: 'R3',
    label: '工具/审计脚本本身',
    items: ['H1 脚本给出结论前，先确认它能报错（改坏一次看它是否变红）', 'F4 不连线上库、不发真实写请求', '输出要能定位到文件与行，不要只打印「失败」'],
    commands: [],
    perFile: true,
  },
  {
    test: /^\.github\/workflows\//,
    level: 'R2',
    label: 'CI 门禁',
    items: ['清单只在 package.json 的 check 里维护一份，不手抄用例', 'PR Check 两个 job 都要覆盖'],
    commands: ['node scripts/test-selfcheck-modes.cjs'],
  },
  {
    test: /^package\.json$/,
    level: 'R2',
    label: '门禁清单入口（改这里等于改所有 CI）',
    items: ['新增/删除用例后同步 scripts/test-selfcheck-modes.cjs', '不要在 workflow 里再抄一份'],
    commands: ['node scripts/test-selfcheck-modes.cjs', 'npm run check'],
  },
  {
    test: /^docs\//,
    level: 'R4',
    label: '文档',
    items: ['链接可点、路径真实存在', 'engineering 只写已确认事实，review 才允许「未验证」'],
    commands: [],
  },
];

const LEVEL_ORDER = { R1: 1, R2: 2, R3: 3, R4: 4 };

function main() {
  const files = changedFiles();
  if (!files.length) {
    console.log('没有未提交的改动（或全部已提交）。');
    console.log('要看上一笔提交的改动范围：node scripts/review-brief.cjs --staged 之前先 git diff HEAD^ --name-only');
    return;
  }

  const hits = [];
  for (const file of files) {
    for (const rule of RULES) {
      if (rule.test.test(file)) hits.push({ file, rule });
    }
  }

  const level = hits.length
    ? hits.map((h) => h.rule.level).sort((a, b) => LEVEL_ORDER[a] - LEVEL_ORDER[b])[0]
    : 'R4';

  console.log(`改动文件 ${files.length} 个，风险级别 ${level}\n`);

  if (!hits.length) {
    console.log('没有命中规则，按 R4 处理：自审 + 链接可点即可。');
    console.log('但若改的是 AGENTS.md 声称的红线相关内容，仍按标准 §F 自查。');
  }

  const seen = new Set();
  for (const { rule } of hits) {
    const key = rule.label;
    if (seen.has(key)) continue;
    seen.add(key);
    console.log(`[${rule.level}] ${rule.label}`);
    for (const item of rule.items) console.log(`  - ${item}`);
    console.log('');
  }

  const commands = [];
  for (const { file, rule } of hits) {
    if (rule.perFile) {
      const cmd = file.endsWith('.py') ? `python ${file}` : `node ${file}`;
      if (!commands.includes(cmd)) commands.push(cmd);
      continue;
    }
    for (const cmd of rule.commands) if (!commands.includes(cmd)) commands.push(cmd);
  }
  if (commands.length) {
    console.log('本次建议执行：');
    for (const cmd of commands) console.log(`  ${cmd}`);
    console.log('');
  }

  // 验收口径固定提醒：本地验收不许用 --quick 顶替全量。
  if (level === 'R1' || level === 'R2') {
    console.log('验收口径：本地跑全量 npm run check（37 步，含后端 pytest）；');
    console.log('          --quick 只用于中间迭代，不能当验收结果。');
    console.log('合并前：PR Check 两个 job 的 head SHA 必须等于当前 PR head。');
  } else {
    console.log('合并前：确认 PR Check 的 run 属于当前 head（红/缺失/过期都算未通过）。');
  }
}

main();
