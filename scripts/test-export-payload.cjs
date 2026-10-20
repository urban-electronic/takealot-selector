// 「一键导出到云端」的 payload 口径测试：不启浏览器，直接编译 src/utils/exportPayload.ts。
//
// 钉死的是数据安全，不是格式：
//   后端 /api/migrate 会先 DELETE 本次 payload 涉及的每一张表，再 INSERT。
//   所以「读不到」必须整表省略；发一张空表过去 = 把云端那张表清空，
//   而界面还写「导出成功」。这条一旦回退，用户丢的是云端的费率映射规则。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/exportPayload.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const { buildExportPayload } = mod.exports;

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

const settings = { exchange_rate: '2.55', default_margin: '0.3' };
const base = {
  products: [{ product_no: 1 }, { product_no: 2 }],
  feeCategories: [{ name: 'Home' }],
  settings,
};

// ---- 读不到 ≠ 0 条：必须整表省略，否则后端会清空云端规则 --------------------
{
  const { payload, skipped } = buildExportPayload({ ...base, feeRules: null });
  const tables = payload.map(t => t.table);
  check(!tables.includes('fee_mapping_rules'), '读不到规则时不得把 fee_mapping_rules 放进 payload（否则云端被清空）');
  check(skipped.includes('fee_mapping_rules'), '读不到规则时必须在 skipped 里点名，界面才能如实说明');
  check(tables.includes('products') && tables.includes('fee_categories') && tables.includes('system_settings'),
    '其余读成功的表照常同步，不能因为一张表失败就整批不导');
}

// ---- 本地确实是 0 条：照常发空数组（那是用户的真实状态） --------------------
{
  const { payload, skipped } = buildExportPayload({ ...base, feeRules: [] });
  const table = payload.find(t => t.table === 'fee_mapping_rules');
  check(!!table, '0 条规则仍然要同步（空数组不是读取失败）');
  check(Array.isArray(table.rows) && table.rows.length === 0, '0 条规则发的是空数组');
  check(skipped.length === 0, '0 条规则不算跳过');
}

// ---- 读到了就原样带过去 -----------------------------------------------------
{
  const rules = [{ id: 'a', category: 'Home', rate: 0.15 }, { id: 'b', category: 'Toys', rate: 0.12 }];
  const { payload } = buildExportPayload({ ...base, feeRules: rules });
  const table = payload.find(t => t.table === 'fee_mapping_rules');
  check(table && table.rows.length === 2, '读到的规则必须全部带过去');
  check(table.rows[1].id === 'b', '规则内容不得被改写');
}

// ---- settings 转成 key/value 行，且一条不丢 --------------------------------
{
  const { payload } = buildExportPayload({ ...base, feeRules: [] });
  const table = payload.find(t => t.table === 'system_settings');
  check(table.rows.length === 2, 'settings 每条都要变成一行');
  check(table.rows.every(r => typeof r.key === 'string' && typeof r.value === 'string'), 'settings 行必须是 {key,value}');
  check(table.rows.some(r => r.key === 'exchange_rate' && r.value === '2.55'), 'settings 值必须原样保留');
}

// ---- 表名不重复：后端按 payload 里的表名决定清哪几张 ------------------------
{
  const { payload } = buildExportPayload({ ...base, feeRules: [] });
  const tables = payload.map(t => t.table);
  check(new Set(tables).size === tables.length, 'payload 里同一张表不得出现两次（后端会按表名重复清空）');
  check(payload[0].table === 'products' && payload[1].table === 'fee_categories',
    'products 与 fee_categories 顺序保持原样');
}

console.log(`test-export-payload: ${checks} checks passed`);
