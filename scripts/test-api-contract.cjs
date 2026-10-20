// 前后端接口契约守卫：src/remoteApi.ts 里每一条 request() 都必须对应后端真实注册的路由。
//
// 为什么要专门守这一件事：同一个错误已经出现过三次，而且每次都只在网页版复现
// （Tauri 版走 api.ts 的 invoke，完全不经过 HTTP，本地测不出来）：
//   - updateFeeMappingRule 用 PUT 打只注册了 PATCH 的路由（已删）
//   - updateFeeCategory 用 PUT 打只注册了 PATCH 的 /fee-categories/{id}（405）
//   - translateProductName 少写了 /products 段，打 /api/translate（404）
// 405/404 都不会被类型检查发现：路径是字符串，动词是字符串，tsc 一个字都看不见。
// 而调用方往往把异常吞掉（翻译那两个调用点就是 .catch(() => {})），于是表现成
// 「某个功能在网页版从来不生效」，既不报错也没有日志。
//
// 这里做的是静态比对：解析 backend/api/*.py 与 backend/main.py 的路由表，
// 解析 src/remoteApi.ts 的调用清单，逐条核对「动词 + 路径」。
// 不做网络请求，不连生产，不读任何密钥。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

const HTTP_VERBS = ['get', 'post', 'put', 'patch', 'delete'];

/** 后端路径归一：`{product_id}` → `:p`，去掉查询串与尾部斜杠。 */
const normalizeBackendPath = (value) =>
  value.replace(/\{[^}]+\}/g, ':p').split('?')[0].replace(/\/+$/, '') || '/';

/**
 * 前端模板字符串归一：
 *   `${encodeURIComponent(id)}`（路径参数）→ `:p`
 *   `${qs ? `?${qs}` : ''}`（查询串拼装）→ 丢掉整段
 * 必须按大括号配对扫描，不能用正则：查询串那段里嵌了反引号和第二层 ${}。
 */
const normalizeFrontendPath = (raw) => {
  let out = '';
  let i = 0;
  while (i < raw.length) {
    if (raw[i] === '$' && raw[i + 1] === '{') {
      const end = skipTemplateExpr(raw, i);
      const body = raw.slice(i + 2, end - 1);
      // 查询串拼装（`${qs ? `?${qs}` : ''}`）不属于路径，整段丢掉；
      // 路径段的字面量三元（`${apply ? 'apply' : 'preview'}`）必须保留成占位符。
      // 判据是「? 紧跟在引号/反引号后面」，也就是拼接的是一个 ? 开头的东西。
      out += /[`'"]\?/.test(body) ? '' : ':p';
      i = end;
      continue;
    }
    out += raw[i];
    i += 1;
  }
  return out.split('?')[0].replace(/\/+$/, '') || '/';
};

/**
 * 从 `index`（指向 `${` 的 `$`）跳到配对的大括号之后。
 * 模板表达式里可以再嵌模板字符串和 `${}`（`${qs ? `?${qs}` : ''}`），必须按层数走。
 */
function skipTemplateExpr(text, index) {
  let depth = 0;
  let i = index;
  while (i < text.length) {
    if (text[i] === '$' && text[i + 1] === '{') { depth += 1; i += 2; continue; }
    if (text[i] === '}') { depth -= 1; i += 1; if (depth === 0) return i; continue; }
    i += 1;
  }
  return text.length;
}

// ---- 后端路由表 ------------------------------------------------------------
const backendFiles = [
  ...fs.readdirSync(path.join(ROOT, 'backend/api')).filter(f => f.endsWith('.py')).map(f => path.join(ROOT, 'backend/api', f)),
  path.join(ROOT, 'backend/main.py'),
];

const routes = new Map(); // 'METHOD /path' -> 定义所在文件
for (const file of backendFiles) {
  const text = fs.readFileSync(file, 'utf8');
  const isMain = path.basename(file) === 'main.py';
  const prefixMatch = text.match(/APIRouter\(\s*prefix\s*=\s*['"]([^'"]+)['"]/);
  const prefix = isMain ? '' : (prefixMatch ? prefixMatch[1] : '');
  const pattern = isMain
    ? /@app\.(get|post|put|patch|delete)\(\s*['"]([^'"]*)['"]/g
    : /@router\.(get|post|put|patch|delete)\(\s*['"]([^'"]*)['"]/g;
  for (const match of text.matchAll(pattern)) {
    const method = match[1].toUpperCase();
    const full = normalizeBackendPath(prefix + match[2]);
    routes.set(`${method} ${full}`, path.basename(file));
  }
}

// 解析器自身失效（比如后端换了写法）时必须红，否则这份守卫会静默变成 0 条比对。
check(routes.size >= 70, `后端路由解析结果只有 ${routes.size} 条，解析器可能失效（预期 ≥70）`);

// ---- 前端调用清单 ----------------------------------------------------------
const remoteSource = fs.readFileSync(path.join(ROOT, 'src/remoteApi.ts'), 'utf8');

/**
 * 从 `request<...>(` 里取出第一个字符串字面量。
 * 泛型参数里可能有嵌套尖括号（`Record<string, unknown>`），不能用正则 [^>]* 截断。
 */
const findRequestLiteral = (chunk) => {
  const start = chunk.indexOf('request');
  if (start < 0) return null;
  let i = start + 'request'.length;
  if (chunk[i] === '<') {
    let depth = 0;
    while (i < chunk.length) {
      if (chunk[i] === '<') depth += 1;
      else if (chunk[i] === '>') { depth -= 1; if (depth === 0) { i += 1; break; } }
      i += 1;
    }
  }
  while (i < chunk.length && /\s/.test(chunk[i])) i += 1;
  if (chunk[i] !== '(') return null;
  i += 1;
  while (i < chunk.length && /\s/.test(chunk[i])) i += 1;
  const quote = chunk[i];
  if (quote !== '`' && quote !== "'" && quote !== '"') return null;
  const bodyStart = i + 1;
  let j = bodyStart;
  while (j < chunk.length) {
    if (chunk[j] === '\\') { j += 2; continue; }
    // 反引号字面量里可以有嵌套的 ${...}，里面还可能再出现反引号
    // （`/api/products${qs ? `?${qs}` : ''}`）——不跳过就会被内层反引号截断。
    if (quote === '`' && chunk[j] === '$' && chunk[j + 1] === '{') {
      j = skipTemplateExpr(chunk, j);
      continue;
    }
    if (chunk[j] === quote) break;
    j += 1;
  }
  if (j >= chunk.length) return null;
  return { raw: chunk.slice(bodyStart, j), end: j + 1 };
};

const calls = [];
for (const chunk of remoteSource.split(/\nexport const /).slice(1)) {
  const name = chunk.split('=')[0].trim();
  const literal = findRequestLiteral(chunk);
  if (!literal) continue;
  const raw = literal.raw.replace(/^\$\{getBaseUrl\(\)\}/, '');
  // 动词只在这个函数的正文里找，不能跨函数（否则会把下一个函数的 method 算进来）
  const verbMatch = chunk.slice(literal.end).match(/method:\s*['"](\w+)['"]/);
  calls.push({
    name,
    method: verbMatch ? verbMatch[1].toUpperCase() : 'GET',
    path: normalizeFrontendPath(raw),
    raw,
  });
}

check(calls.length >= 60, `前端调用解析结果只有 ${calls.length} 条，解析器可能失效（预期 ≥60）`);
check(calls.every(c => HTTP_VERBS.includes(c.method.toLowerCase())), '解析出的动词必须在 get/post/put/patch/delete 之内');

// ---- 逐条核对 --------------------------------------------------------------
// 先按字面量精确比对；带路径参数的调用（`:p`）再按「同一段位置上的参数」比对。
// 参数位只允许吃掉一段（`[^/]+`），所以少写一段、多写一段、动词写错都仍然会红。
const routeKeys = [...routes.keys()];
const resolve = (method, target) => {
  if (routes.has(`${method} ${target}`)) return `${method} ${target}`;
  if (!target.includes(':p')) return null;
  const pattern = new RegExp(`^${method} ${target.split(':p').map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+')}$`);
  return routeKeys.find(key => pattern.test(key)) || null;
};

const missing = calls
  .filter(c => !resolve(c.method, c.path))
  .map(c => `${c.name}: ${c.method} ${c.path}`);
check(missing.length === 0, `以下调用在后端没有对应路由（网页版会 404/405）：\n  ${missing.join('\n  ')}`);

// ---- 钉住已经修过的两处：改回旧写法必须红，且报错说清后果 ------------------
const patchRoute = 'PATCH /api/fee-categories/:p';
const translateRoute = 'POST /api/products/translate';
check(routes.has(patchRoute), `后端必须注册 ${patchRoute}（费率分类的启用/停用与成功率都走它）`);
check(routes.has(translateRoute), `后端必须注册 ${translateRoute}`);
check(!routes.has('PUT /api/fee-categories/:p'), '费率分类没有 PUT 路由，前端不得用 PUT');
check(!routes.has('POST /api/translate'), '后端没有 /api/translate，翻译接口挂在 /api/products 下');

const byName = new Map(calls.map(c => [c.name, c]));
check(byName.get('updateFeeCategory')?.method === 'PATCH',
  'updateFeeCategory 必须用 PATCH：用 PUT 打 PATCH 路由是 405，设置页改费率会直接报错');
check(byName.get('translateProductName')?.path === '/api/products/translate',
  'translateProductName 必须打 /api/products/translate：少 /products 段是 404，而调用方把异常吞了，网页版自动翻译永远不生效');

console.log(`test-api-contract: ${checks} checks passed (${routes.size} routes / ${calls.length} calls)`);
