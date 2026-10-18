#!/usr/bin/env node
/**
 * selfcheck.cjs 自身的模式测试。
 *
 * 为什么要测这个：它是所有门禁的唯一入口，脚本自己错了会让「全绿」变成假象。
 * 这里只验证「选哪些步骤 / 怎么判定」的逻辑，不去真的跑一遍全部套件（太慢），
 * 所以统一用 --list 看步骤清单、用真实子进程看退出码和结论行。
 *
 * 覆盖的坑：
 *  - CI 里没有 Python 依赖时，曾经把「后端 pytest」判成硬失败，前端部署直接挂。
 *  - 反过来，静默跳过更糟：三个 .py 审计测试找不到 Python 就悄悄不跑，
 *    结论照样写「全部通过」。跳过必须在结论里点名。
 */
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const SCRIPT = path.join(ROOT, 'scripts', 'selfcheck.cjs');

let failures = 0;
function check(ok, label, detail) {
  if (ok) {
    console.log(`  ✓ ${label}`);
  } else {
    failures += 1;
    console.log(`  ✗ ${label}${detail ? `\n      ${detail}` : ''}`);
  }
}

function run(args, env = {}) {
  const res = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 240000,
    env: { ...process.env, ...env },
  });
  return { code: res.status, out: `${res.stdout || ''}${res.stderr || ''}` };
}

function listOf(args, env) {
  const res = run([...args, '--list'], env);
  return { code: res.code, names: res.out.trim().split('\n').filter(Boolean).map(l => l.split('\t')[0]) };
}

console.log('selfcheck 模式');

// 1) 默认清单：必须覆盖类型检查、视觉守卫、前端套件、后端 pytest
{
  const { names } = listOf([]);
  check(names.includes('类型检查 tsc'), '默认清单包含类型检查');
  check(names.includes('视觉一致性'), '默认清单包含视觉一致性守卫');
  check(names.filter(n => n.startsWith('前端 ')).length >= 15, '默认清单包含全部前端套件');
  check(names.includes('后端 pytest'), '默认清单包含后端 pytest');
}

// 2) --quick 只该去掉后端 pytest，前端一个都不能少
{
  const full = listOf([]).names;
  const quick = listOf(['--quick']).names;
  check(!quick.includes('后端 pytest'), '--quick 去掉后端 pytest');
  check(quick.filter(n => n.startsWith('前端 ')).length ===
        full.filter(n => n.startsWith('前端 ')).length, '--quick 不减少任何前端套件');
  check(quick.filter(n => n.startsWith('审计 ')).length ===
        full.filter(n => n.startsWith('审计 ')).length, '--quick 不减少审计套件');
}

// 3) --python-only：只剩 Python 侧，不该再跑前端
{
  const names = listOf(['--python-only']).names;
  check(!names.some(n => n.startsWith('前端 ')), '--python-only 不跑前端套件');
  check(!names.includes('类型检查 tsc'), '--python-only 不跑 tsc');
  check(names.includes('后端 pytest'), '--python-only 保留后端 pytest');
}

// 4) 没有 Python 时：默认跳过、退出码 0，且结论行必须点名「跳过」
{
  const res = run(['--python-only'], { SELFCHECK_PYTHON: 'none' });
  check(res.code === 0, '缺 Python 时默认不判失败', res.out.slice(-300));
  check(/跳过/.test(res.out), '缺 Python 时结论里写明跳过', res.out.slice(-300));
  check(!/全部 \d+ 步通过/.test(res.out), '跳过时不能说「全部通过」', res.out.slice(-300));
}

// 5) 没有 Python 但要求必须有（CI 用法）：必须失败，不能悄悄变绿
{
  const res = run(['--python-only', '--require-python'], { SELFCHECK_PYTHON: 'none' });
  check(res.code !== 0, '--require-python 在缺 Python 时判失败', res.out.slice(-300));
  check(/失败/.test(res.out), '--require-python 失败时结论里写明失败', res.out.slice(-300));
}

// 6) 环境变量形态的强制要求（CI 里不想改命令行时用）
{
  const res = run(['--python-only'], { SELFCHECK_PYTHON: 'none', SELFCHECK_REQUIRE_PYTHON: '1' });
  check(res.code !== 0, 'SELFCHECK_REQUIRE_PYTHON=1 等价于 --require-python', res.out.slice(-300));
}

if (failures) {
  console.log(`\n${failures} 项未通过`);
  process.exit(1);
}
console.log('\n全部通过');
