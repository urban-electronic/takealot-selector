// 站外货源（1688 / Takealot）守卫。
//
// 这块数据和库内推荐的风险不一样：推荐推错了顶多「不准」，站外货源推错了，
// 用户会照着链接去进货或比价。所以这里守的是几件「错了要花钱」的事：
//   1. 平台和域名必须对得上（Takealot 链接填成 1688，点开就是错的平台）；
//   2. 链接 scheme 只放 http/https——这个字段最终渲染成可点链接；
//   3. 价格 0 不能当价格（1688 上「价格待议」常填 0，界面上会显示 ¥0）；
//   4. 关键词命中规则：英文整词、中文子串（`cap` 不能命中 `capacity`）；
//   5. 界面必须回答「为什么挂在这里」和「匹配规则/排序/上限」；
//   6. 桌面端（本地数据源）没有这张表，必须转调服务端，不能各存一份。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (file) => fs.readFileSync(path.resolve(__dirname, '..', file), 'utf8');

const FILES = {
  service: read('backend/services/external_offers.py'),
  route: read('backend/api/external_offer_routes.py'),
  main: read('backend/main.py'),
  model: read('backend/models.py'),
  remote: read('src/remoteApi.ts'),
  local: read('src/api.ts'),
  ui: read('src/ExternalOffers.tsx'),
  detail: read('src/pages/ProductDetail.tsx'),
  types: read('src/types.ts'),
};

const num = (source, pattern, label) => {
  const m = source.match(pattern);
  assert.ok(m, `没解析到 ${label}——守卫的解析规则和源码写法脱节了，请同步更新本脚本`);
  return Number(m[1]);
};

/** 去掉注释再查负面断言，否则注释里提到旧写法会把守卫自己弄红。 */
const stripComments = (text, lang) => {
  if (lang === 'ts' || lang === 'tsx') {
    return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  }
  return text.replace(/"""[\s\S]*?"""/g, '').replace(/^\s*#.*$/gm, '');
};

function collect(files) {
  const errors = [];

  // --- 1. 平台与域名白名单 ---
  const hosts1688 = /PLATFORM_1688: \(([^)]*)\)/.exec(files.route);
  const hostsTa = /PLATFORM_TAKEALOT: \(([^)]*)\)/.exec(files.route);
  if (!hosts1688 || !/['"]1688\.com['"]/.test(hosts1688[1])) {
    errors.push('1688 的域名白名单必须包含 1688.com');
  }
  if (!hostsTa || !/['"]takealot\.com['"]/.test(hostsTa[1])) {
    errors.push('Takealot 的域名白名单必须包含 takealot.com');
  }
  if (!/host\.endswith\('\.' \+ suffix\)/.test(files.route)) {
    errors.push('域名比对必须认子域（m.takealot.com 这类），否则移动端链接会被误挡');
  }

  // --- 2. scheme 校验 ---
  if (!/parsed\.scheme not in \('http', 'https'\)/.test(files.route)) {
    errors.push('链接 scheme 必须只放 http/https——这个字段会渲染成可点链接');
  }

  // --- 3. 价格 0 不算价格 ---
  const zeroChecks = (files.route.match(/value <= 0/g) || []).length;
  if (zeroChecks < 2) {
    errors.push('价格/起订量校验要覆盖「新增」和「修改」两处，否则改的时候能把 0 写回去');
  }
  if (/value >= 0/.test(stripComments(files.route, 'py'))) {
    errors.push('价格校验不能写成 >= 0：0 会被当成有效价格，界面上显示 ¥0');
  }
  if (!/if 'price' in data\.model_fields_set:\s*\n\s*offer\.price = data\.price if data\.price is not None and data\.price > 0 else None/.test(files.route)) {
    errors.push('修改接口要把 <=0 的价格落成空，不能原样存');
  }

  // --- 4. 默认上限与路由对齐 ---
  const defaultLimit = num(files.service, /DEFAULT_LIMIT = (\d+)/, 'DEFAULT_LIMIT');
  const maxLimit = num(files.service, /MAX_LIMIT = (\d+)/, 'MAX_LIMIT');
  if (defaultLimit > maxLimit) errors.push('默认上限不能大于硬上限');
  if (!new RegExp(`limit: int = Query\\(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT\\)`).test(files.route)) {
    errors.push('路由的 limit 必须引用引擎常量，不能写死数字（改上限时容易漏改）');
  }
  if (!/limit = max\(0, min\(int\(limit\), MAX_LIMIT\)\)/.test(files.service)) {
    errors.push('服务层要把 limit 夹在 0..MAX_LIMIT，不能只靠路由校验');
  }

  // --- 5. 命中规则：英文整词、中文子串 ---
  if (!/keyword\.isascii\(\)/.test(files.service)) {
    errors.push('英文与中文要走不同的命中规则（英文整词、中文子串）');
  }
  if (!/contains_phrase\(text, keyword\)/.test(files.service)) {
    errors.push('英文短语关键词必须走 contains_phrase 整词匹配，不能用 in / includes');
  }
  // 注意 `(?<!el)`：中文分支是 `elif keyword in text:`，它是**正确**写法，
  // 不带这个断言会被当成英文退回了子串匹配（`if keyword in text:` 是它的子串）。
  if (/(?<!el)if keyword in text:/.test(stripComments(files.service, 'py'))) {
    errors.push('英文关键词退回了子串匹配：cap 会命中 capacity、clip 会命中 clipper');
  }
  if (!/from services\.product_recommendation import contains_phrase, tokens/.test(files.service)) {
    errors.push('分词与整词匹配要复用 product_recommendation，不要另起一套口径');
  }

  // --- 6. 不凑数：没有命中就不展示 ---
  if (!/if not hits:\s*\n\s*unscored \+= 1\s*\n\s*continue/.test(files.service)) {
    errors.push('没命中关键词的货源不能展示——宁可版面空着，也不要塞不相干的东西');
  }
  if (!/result\['meta'\]\['total'\] = 1/.test(files.service) && !/len\(rows\)/.test(files.service)) {
    errors.push('meta 要如实回报登记总数，关掉展示不该让统计变成 0');
  }

  // --- 7. 路由注册 ---
  if (!/from api import external_offer_routes/.test(files.main)) {
    errors.push('main.py 没有导入 external_offer_routes');
  }
  if (!/app\.include_router\(external_offer_routes\.router\)/.test(files.main)) {
    errors.push('main.py 没有注册 external_offer_routes.router');
  }
  if (!/prefix="\/api\/external-offers"/.test(files.route)) {
    errors.push('路由前缀必须是 /api/external-offers');
  }
  if (!/@router\.get\('\/for-product\/\{product_id\}'\)/.test(files.route)) {
    errors.push('缺少 for-product 路由：详情页按商品取货源走这个');
  }

  // --- 8. 模型 ---
  if (!/class ExternalOffer\(Base\)/.test(files.model)) errors.push('models.py 缺少 ExternalOffer');
  if (!/__tablename__ = "external_offers"/.test(files.model)) errors.push('表名应为 external_offers');
  for (const column of ['platform', 'url', 'price', 'currency', 'keywords', 'usage', 'scenario', 'reason', 'is_active']) {
    if (!new RegExp(`^\\s{4}${column} = Column`, 'm').test(files.model)) {
      errors.push(`ExternalOffer 缺少 ${column} 字段`);
    }
  }

  // --- 9. 两套数据源：桌面端转调服务端 ---
  if (!/export const getExternalOffers = \([\s\S]{0,600}?\/api\/external-offers\/for-product\//.test(files.remote)) {
    errors.push('remoteApi 的 getExternalOffers 必须打到 /api/external-offers/for-product/{id}');
  }
  if (!/remoteApi\.getExternalOffers\(productId, options\)/.test(files.local)) {
    errors.push('本地数据源要转调 remoteApi：桌面端没有这张表，各存一份就会对不上');
  }

  // --- 10. 界面 ---
  if (!/offer\.matched\.map/.test(files.ui)) {
    errors.push('界面要显示「为什么挂在这里」（命中的关键词），否则挂错了看不出来');
  }
  if (!/result\.meta\.match_rule/.test(files.ui) || !/result\.meta\.sort/.test(files.ui)) {
    errors.push('界面要展示匹配规则与排序依据，不能只给一堆卡片');
  }
  if (!/limit_per_platform/.test(files.ui)) {
    errors.push('界面要展示每栏数量上限');
  }
  if (!/关键词必填/.test(files.ui)) {
    errors.push('前端要把关键词当必填——没有关键词就挂不到任何商品下面');
  }
  if (!/不在你自己的仓库里/.test(files.ui)) {
    errors.push('界面要说清这些不是自家商品，否则会被当成已上架的商品');
  }
  if (!/不做实时抓取/.test(files.ui)) {
    errors.push('界面要说明数据靠登记、不做实时抓取，别让人以为价格是实时同步的');
  }
  if (!/站外货源暂时读不到/.test(files.ui)) {
    errors.push('读失败要降级成这一块的提示，且与「没有货源」区分开');
  }
  if (!/<ExternalOffers[\s\S]{0,200}?productId=\{product\.id\}/.test(files.detail)) {
    errors.push('详情页没有挂 ExternalOffers');
  }
  if (!/s\.1688\.com\/selloffer\/offer_search\.htm\?keywords=/.test(files.ui)) {
    errors.push('空态要给 1688 搜索入口');
  }
  if (!/takealot\.com\/all\?q=/.test(files.ui)) {
    errors.push('空态要给 Takealot 搜索入口');
  }

  // --- 11. 类型 ---
  for (const name of ['ExternalOffer', 'ExternalOfferResult', 'ExternalOfferInput', 'ExternalPlatform']) {
    if (!new RegExp(`export (interface|type) ${name}\\b`).test(files.types)) {
      errors.push(`types.ts 缺少 ${name}`);
    }
  }

  return errors;
}

const errors = collect(FILES);
assert.deepEqual(errors, [], `站外货源守卫未通过：\n${errors.join('\n')}`);

// 突变自测：把关键判定改坏，守卫必须变红。
const mutants = [
  ['域名白名单被放宽', 'route', (s) => s.replace("PLATFORM_1688: (\"1688.com\",)", "PLATFORM_1688: ('',)")],
  ['scheme 校验被删', 'route', (s) => s.replace("parsed.scheme not in ('http', 'https')", "parsed.scheme not in ()")],
  ['价格 0 又变成有效', 'route', (s) => s.replace('if value is not None and value <= 0:\n            return None', 'if value is not None and value >= 0:\n            return None')],
  ['修改接口不再过滤 0 价', 'route', (s) => s.replace('offer.price = data.price if data.price is not None and data.price > 0 else None', 'offer.price = data.price')],
  ['路由上限写死数字', 'route', (s) => s.replace('Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT)', 'Query(8, ge=0, le=20)')],
  ['英文退回子串匹配', 'service', (s) => s.replace('if contains_phrase(text, keyword):', 'if keyword in text:')],
  ['中英文不再分流', 'service', (s) => s.replace('if keyword.isascii():', 'if True:')],
  ['没命中也开始展示', 'service', (s) => s.replace('if not hits:\n            unscored += 1\n            continue', 'if not hits:\n            hits = []')],
  ['上限不再夹紧', 'service', (s) => s.replace('limit = max(0, min(int(limit), MAX_LIMIT))', 'limit = int(limit)')],
  ['路由没注册', 'main', (s) => s.replace('app.include_router(external_offer_routes.router)', '')],
  ['桌面端另存一份', 'local', (s) => s.replace('remoteApi.getExternalOffers(productId, options)', 'Promise.resolve({ sourcing: [], market: [], meta: {} as never })')],
  ['详情页没挂', 'detail', (s) => s.replace(/<ExternalOffers[\s\S]{0,200}?\/>/, '')],
  ['不再显示命中关键词', 'ui', (s) => s.replace('offer.matched.map', 'offer.matched.slice(0, 0).map')],
  ['关键词不再必填', 'ui', (s) => s.replace('关键词必填：用它决定这条货源出现在哪些商品下面', '可填可不填')],
  ['没说明不是自家商品', 'ui', (s) => s.replace('不在你自己的仓库里', '商品列表')],
  ['空态不再给搜索入口', 'ui', (s) => s.replace(/s\.1688\.com\/selloffer\/offer_search\.htm\?keywords=/g, 's.1688.com/')],
];

for (const [name, key, mutate] of mutants) {
  const mutated = mutate(FILES[key]);
  assert.notEqual(mutated, FILES[key], `突变自测没生效：${name} 的替换规则没匹配到内容`);
  const found = collect({ ...FILES, [key]: mutated });
  assert.ok(found.length > 0, `突变自测失败：${name} 之后守卫仍然通过，说明它没在守这一项`);
}

console.log(
  `PASS 站外货源：域名白名单与 scheme 校验、0 价不算价（新增+修改两处）、`
  + `每栏上限 ${num(FILES.service, /DEFAULT_LIMIT = (\d+)/, 'DEFAULT_LIMIT')}/${num(FILES.service, /MAX_LIMIT = (\d+)/, 'MAX_LIMIT')} 与路由对齐、`
  + `中英文命中分流、未命中不展示、桌面端转调服务端、界面渲染命中词与 meta（含 ${mutants.length} 条突变自测）`,
);
