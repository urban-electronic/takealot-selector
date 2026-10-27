// 「只看自建 SKU」这条链路的守卫：搜索框 → URL 参数 → 两套 API → 后端判据。
// 链路长且跨语言（tsx / ts / rs / py），任何一环漏了都只是「按钮点了没反应」，
// 不会报错，所以逐环钉住；末尾对每条规则做突变自测，确认删掉那行真的会红。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// 工作区被 core.autocrlf 检出成 CRLF，统一归一化后再匹配，否则正则和突变规则会错配。
const read = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8').replace(/\r\n/g, '\n');
const page = read('src/pages/ProductList.tsx');
const localApi = read('src/api.ts');
const remoteApi = read('src/remoteApi.ts');
const rust = read('src-tauri/src/commands/products.rs');
const backend = read('backend/api/product_routes.py');

const checks = [
  // --- 界面 ---
  [page, /placeholder="[^"]*SKU[^"]*"/, '搜索框提示要写清能搜 SKU'],
  [page, /aria-label="[^"]*SKU[^"]*"/, '搜索框的无障碍名称也要提到 SKU'],
  [page, /searchParams\.get\('custom_sku'\) === '1'/, '要从 URL 读 custom_sku（刷新/分享后仍生效）'],
  [page, /if \(customSkuOnly\) params\.custom_sku = '1'/, '要把开关转成请求参数'],
  [page, /multiSkuOnly, duplicateSkuOnly, customSkuOnly, archivedMode\]/, '依赖数组要带上开关，否则切换不重新请求'],
  [page, /只看自建 SKU/, '要有可点的开关'],
  [page, /aria-pressed=\{customSkuOnly\}/, '开关要暴露按下状态'],

  // --- 两套 API 都要透传 ---
  [remoteApi, /Object\.entries\(params\)\.forEach/, '远程接口按 params 通用透传，新参数不用改它'],
  [localApi, /customSku: params\?\.custom_sku === '1' \? true : null/, '桌面版要把 custom_sku 转成 customSku 传给 Rust'],

  // --- 桌面版 Rust ---
  [rust, /custom_sku: Option<bool>/, 'Rust 命令要接 custom_sku'],
  [rust, /fn is_custom_sku\(value: &str\) -> bool/, 'Rust 侧要有同一口径的判据'],
  [rust, /products\.retain\(\|product\| is_custom_sku\(product\.sku\.as_deref\(\)\.unwrap_or\(""\)\)\)/, 'Rust 要真的按判据过滤'],
  [rust, /OR sku LIKE \?/, 'Rust 的搜索 SQL 也要查 sku'],

  // --- 后端 ---
  [backend, /custom_sku: bool = Query\(False\)/, '后端要接 custom_sku'],
  [backend, /if custom_sku:\s*\n\s*products = \[product for product in products if is_custom_sku\(product\.sku\)\]/, '后端要真的按判据过滤'],
  [backend, /def is_custom_sku\(value: Optional\[str\]\) -> bool/, '后端要有判据函数'],
  [backend, /\| \(Product\.sku\.ilike\(like\)\)/, '后端搜索要包含 sku'],
];

for (const [source, pattern, why] of checks) {
  assert.match(source, pattern, why);
}

// 突变自测：把关键行删掉，确认上面的正则真的会失败。
// 只测「删掉后会静默失效」的那几条；不测纯提示文案。
const mutants = [
  ['搜索框提示', page, /placeholder="[^"]*SKU[^"]*"/, (s) => s.replace(/placeholder="搜索[^"]*"/, 'placeholder="搜索"')],
  ['请求参数', page, /if \(customSkuOnly\) params\.custom_sku = '1'/, (s) => s.replace("    if (customSkuOnly) params.custom_sku = '1';\n", '')],
  ['依赖数组', page, /multiSkuOnly, duplicateSkuOnly, customSkuOnly, archivedMode\]/, (s) => s.replace('duplicateSkuOnly, customSkuOnly, archivedMode]', 'duplicateSkuOnly, archivedMode]')],
  ['桌面版透传', localApi, /customSku: params\?\.custom_sku === '1' \? true : null/, (s) => s.replace(/^\s*customSku: params\?\.custom_sku === '1' \? true : null,\n/m, '')],
  ['Rust 过滤', rust, /products\.retain\(\|product\| is_custom_sku/, (s) => s.replace(/products\.retain\(\|product\| is_custom_sku[^\n]*\n/, '')],
  ['Rust 搜索', rust, /OR sku LIKE \?/, (s) => s.replace(' OR sku LIKE ?', '')],
  ['后端过滤', backend, /if custom_sku:\r?\n\s*products = \[product for product in products if is_custom_sku\(product\.sku\)\]/, (s) => s.replace(/if custom_sku:\r?\n\s*products = \[product for product in products if is_custom_sku\(product\.sku\)\]\r?\n/, '')],
  ['后端搜索', backend, /\| \(Product\.sku\.ilike\(like\)\)/, (s) => s.replace('            | (Product.sku.ilike(like))\n', '')],
];

for (const [name, source, pattern, mutate] of mutants) {
  const mutated = mutate(source);
  assert.notEqual(mutated, source, `突变自测没生效：${name} 的删除规则没匹配到内容`);
  assert.doesNotMatch(mutated, pattern, `突变自测失败：${name} 删掉后守卫仍然通过，说明它没在守这一行`);
}

console.log('PASS 自建 SKU 筛选：界面开关、URL 参数、远程/桌面两套 API、Rust 与后端判据、搜索含 SKU（含 8 条突变自测）');
