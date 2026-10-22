#!/usr/bin/env node
/**
 * 一键自检：类型 + 视觉守卫 + 全部前端套件 + 后端 pytest。
 *
 * 为什么要有这个脚本：package.json 里的 check 曾经是一串手写的 test 命令，
 * 每新增一个套件就得记得回来补一行——漏了不会报错，只会「检查通过」而其实没跑。
 * 这里改成扫 scripts/test-*.cjs|mjs，新套件放进目录就自动纳入，不会再漂。
 *
 * 用法：
 *   node scripts/selfcheck.cjs                 # 全部
 *   node scripts/selfcheck.cjs --quick         # 跳过后端 pytest（前端门禁用）
 *   node scripts/selfcheck.cjs --python-only   # 只跑 Python 侧（审计 + 后端 pytest）
 *   node scripts/selfcheck.cjs --require-python # 没有可用 Python 就判失败，不许静默跳过
 *   node scripts/selfcheck.cjs --list          # 只列出会跑哪些步骤
 * 退出码 0 = 全绿；1 = 有失败（失败步骤的末尾输出会打出来）。
 *
 * 关于 Python 依赖：本机没装 fastapi/pytest 时，Python 侧默认「跳过并显式标注」，
 * 不判失败——否则只想改个前端的人会被卡住。但跳过会丢掉后端覆盖，所以 CI 里
 * 必须用 --require-python 或 --python-only，让「没装依赖」变成失败而不是悄悄变绿。
 */
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const BACKEND = path.join(ROOT, 'backend');
const args = new Set(process.argv.slice(2));
const QUICK = args.has('--quick');
const LIST = args.has('--list');
const PY_ONLY = args.has('--python-only');
// 两件事必须分开：--python-only 只决定「跑哪些步骤」，--require-python 才决定
// 「缺依赖算不算失败」。混在一起的话，只想跑后端的人会莫名其妙被前端拖慢，
// 而跳过分支也没法单独验证。CI 里一律显式写 --require-python。
const REQUIRE_PYTHON = args.has('--require-python') || process.env.SELFCHECK_REQUIRE_PYTHON === '1';

const steps = [];
const add = (name, cmd, cmdArgs, cwd, timeoutMs = 300000, skipReason = null) =>
  steps.push({ name, cmd, cmdArgs, cwd, timeoutMs, skipReason });

// 1) 类型检查：直接调本地 typescript，不经过 npx（离线也能跑，不会卡在下载上）。
let tsc = 'npx';
let tscArgs = ['tsc', '--noEmit'];
try {
  tsc = process.execPath;
  tscArgs = [require.resolve('typescript/bin/tsc'), '--noEmit'];
} catch { /* 没有本地 typescript 时退回 npx */ }
if (!PY_ONLY) add('类型检查 tsc', tsc, tscArgs, ROOT, 240000);

// 2) 视觉一致性（15 组约束：字号下限、对比度、控件尺寸、白字禁令……）
if (!PY_ONLY) add('视觉一致性', process.execPath, [path.join('scripts', 'check-visual-consistency.cjs')], ROOT, 180000);

// 3) 全部前端套件：扫目录，不写死清单。
const suites = fs.readdirSync(path.join(ROOT, 'scripts'))
  .filter(f => /^test-.*\.(cjs|mjs)$/.test(f))
  .sort();
if (!PY_ONLY) for (const f of suites) add(`前端 ${f}`, process.execPath, [path.join('scripts', f)], ROOT, 180000);

// 4) Python 侧的审计/安全测试（test-*.py 才是断言，其余是生成报告的审计脚本）。
const pySuites = fs.readdirSync(path.join(ROOT, 'scripts'))
  .filter(f => /^test-.*\.py$/.test(f))
  .sort();

// 5) 后端 pytest：需要一个装了 fastapi/pytest 的解释器。
function pythonCandidates() {
  const list = [];
  // SELFCHECK_PYTHON=none 显式表示「没有 Python」，用来验证跳过分支，也让
  // 只想跑前端的人有个不用改 PATH 的开关。
  if (process.env.SELFCHECK_PYTHON === 'none') return [];
  if (process.env.SELFCHECK_PYTHON) list.push(process.env.SELFCHECK_PYTHON);
  const home = os.homedir();
  const venv = path.join(home, '.workbuddy-ai', 'binaries', 'python', 'envs', 'default');
  list.push(process.platform === 'win32' ? path.join(venv, 'Scripts', 'python.exe') : path.join(venv, 'bin', 'python'));
  list.push(process.platform === 'win32' ? 'python' : 'python3', 'python', 'python3');
  return [...new Set(list)];
}
function usablePython() {
  for (const exe of pythonCandidates()) {
    const probe = spawnSync(exe, ['-c', 'import fastapi, pytest, sqlalchemy'], { encoding: 'utf8', timeout: 60000 });
    if (probe.status === 0) return exe;
  }
  return null;
}

const py = usablePython();
// 没装依赖时：默认「跳过但标注」，CI 里用 --require-python 让它变成失败。
const pySkip = py ? null
  : (REQUIRE_PYTHON ? null
    : '本机没有装了 fastapi/pytest 的 Python：设 SELFCHECK_PYTHON 指向它；要强制要求请加 --require-python');
for (const f of pySuites) {
  add(`审计 ${f}`, py || '', py ? [path.join('scripts', f)] : [], ROOT, 180000, pySkip);
}
if (!QUICK) {
  // --basetemp 指到一个每次唯一的目录：pytest 默认把上一轮的临时目录挪进
  // `garbage-*` 再整目录删掉，本机沙箱的批量删除闸（>50 个文件）会拦下这一步，
  // 于是测试全过、退出码却是 1。每次给个新目录，就没有可删的旧目录了。
  // 父目录必须先建好 —— pytest 建 basetemp 时不用 parents=True。
  const tmpRoot = path.join(BACKEND, '.pytest-tmp');
  fs.mkdirSync(tmpRoot, { recursive: true });
  const basetemp = path.join(tmpRoot, String(Date.now()));
  add('后端 pytest', py || '',
      py ? ['-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider', '--basetemp', basetemp] : [],
      BACKEND, 420000, pySkip);
}

if (LIST) {
  for (const s of steps) console.log(`${s.name}\t${s.cmd || '(缺少可用 Python)'} ${s.cmdArgs.join(' ')}`);
  process.exit(0);
}

const RED = '\u001b[31m', GREEN = '\u001b[32m', DIM = '\u001b[2m', YELLOW = '\u001b[33m', RESET = '\u001b[0m';
const results = [];
const startedAll = Date.now();

for (const step of steps) {
  const started = Date.now();
  if (step.skipReason) {
    results.push({ ...step, ok: true, skipped: true, ms: 0, tail: step.skipReason });
    process.stdout.write(`${YELLOW}−${RESET} ${step.name} ${DIM}已跳过${RESET}\n`);
    continue;
  }
  if (!step.cmd) {
    results.push({ ...step, ok: false, ms: 0, tail: '找不到装了 fastapi/pytest 的 Python：设 SELFCHECK_PYTHON 指向它，或跳过（--quick）。' });
    process.stdout.write(`${RED}✗${RESET} ${step.name}\n`);
    continue;
  }
  const run = spawnSync(step.cmd, step.cmdArgs, { cwd: step.cwd, encoding: 'utf8', timeout: step.timeoutMs, shell: false });
  const ms = Date.now() - started;
  const ok = run.status === 0;
  const out = `${run.stdout || ''}${run.stderr || ''}`;
  results.push({ ...step, ok, ms, tail: out.trim().split('\n').slice(-12).join('\n') });
  process.stdout.write(`${ok ? `${GREEN}✓${RESET}` : `${RED}✗${RESET}`} ${step.name} ${DIM}${(ms / 1000).toFixed(1)}s${RESET}\n`);
}

const failed = results.filter(r => !r.ok);
const skipped = results.filter(r => r.skipped);
console.log('');
for (const r of failed) {
  console.log(`${RED}── ${r.name} 失败（${(r.ms / 1000).toFixed(1)}s）${RESET}`);
  console.log(r.tail || '(无输出)');
  console.log('');
}
const total = ((Date.now() - startedAll) / 1000).toFixed(1);
const passedCount = results.length - failed.length - skipped.length;
if (failed.length) {
  console.log(`${RED}${failed.length}/${results.length} 步失败${RESET}，用时 ${total}s：${failed.map(f => f.name).join('、')}`);
} else if (skipped.length) {
  // 跳过不等于通过：必须在结论里点名，否则「绿」会骗人。
  console.log(`${GREEN}${passedCount} 步通过${RESET}，${YELLOW}${skipped.length} 步跳过${RESET}（${skipped.map(s => s.name).join('、')}），用时 ${total}s`);
  console.log(`${DIM}跳过的原因是本机缺少 Python 依赖；CI 会用 --require-python 强制要求。${RESET}`);
} else {
  console.log(`${GREEN}全部 ${results.length} 步通过${RESET}，用时 ${total}s`);
}
process.exit(failed.length ? 1 : 0);
