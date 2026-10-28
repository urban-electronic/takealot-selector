// 详情页推荐区守卫。
//
// 这个功能有两份实现：线上走 backend/services/product_recommendation.py，
// 本地（Tauri）走 src/utils/productRecommend.ts。两份口径必须一模一样——
// 差一个权重，同一件商品在网页版和桌面版就会排出两个顺序，用户只会觉得「不准」。
//
// 所以这里守的不是「有没有这个函数」，而是：
//   1. 两份实现的常量逐项相等（权重、阈值、上限、停用词、配件词、组合表）；
//   2. 路由的默认值与上限跟引擎常量对得上（改 MAX_LIMIT 忘了改 Query 就会被抓）；
//   3. 界面上必须真的渲染推荐理由和 meta（否则「说明推荐来源/排序依据」等于没做）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (file) => fs.readFileSync(path.resolve(__dirname, '..', file), 'utf8');

const FILES = {
  py: read('backend/services/product_recommendation.py'),
  ts: read('src/utils/productRecommend.ts'),
  route: read('backend/api/product_routes.py'),
  remote: read('src/remoteApi.ts'),
  local: read('src/api.ts'),
  ui: read('src/ProductRecommendations.tsx'),
  detail: read('src/pages/ProductDetail.tsx'),
  types: read('src/types.ts'),
};

const num = (source, pattern, label) => {
  const m = source.match(pattern);
  assert.ok(m, `没解析到 ${label}——守卫的解析规则和源码写法脱节了，请同步更新本脚本`);
  return Number(m[1]);
};

const tokenSet = (text) => text.split(/\s+/).filter(Boolean).sort();

/** 解析 COMPLEMENT_RULES 的 { key: [values] }，Python 是 (..)，TS 是 [..]。 */
const parseRules = (block, open, close) => {
  const out = {};
  const re = new RegExp(`'?([a-z]+)'?\\s*:\\s*\\${open}([\\s\\S]*?)\\${close}`, 'g');
  let m = re.exec(block);
  while (m) {
    out[m[1]] = (m[2].match(/'([^']*)'/g) || []).map((s) => s.slice(1, -1));
    m = re.exec(block);
  }
  return out;
};

function collect(files) {
  const errors = [];
  const eq = (left, right, label) => {
    if (JSON.stringify(left) !== JSON.stringify(right)) {
      errors.push(`${label} 两份实现不一致：Python=${JSON.stringify(left)} / TS=${JSON.stringify(right)}`);
    }
  };

  // --- 1. 权重 ---
  const pyWeights = {
    category: num(files.py, /WEIGHT_CATEGORY = ([\d.]+)/, 'Python WEIGHT_CATEGORY'),
    attribute: num(files.py, /WEIGHT_ATTRIBUTE = ([\d.]+)/, 'Python WEIGHT_ATTRIBUTE'),
    complement: num(files.py, /WEIGHT_COMPLEMENT = ([\d.]+)/, 'Python WEIGHT_COMPLEMENT'),
    behavior: num(files.py, /WEIGHT_BEHAVIOR = ([\d.]+)/, 'Python WEIGHT_BEHAVIOR'),
  };
  const tsWeightsBlock = (files.ts.match(/WEIGHTS = \{([^}]*)\}/) || [])[1] || '';
  const tsWeights = {
    category: num(tsWeightsBlock, /category:\s*([\d.]+)/, 'TS weights.category'),
    attribute: num(tsWeightsBlock, /attribute:\s*([\d.]+)/, 'TS weights.attribute'),
    complement: num(tsWeightsBlock, /complement:\s*([\d.]+)/, 'TS weights.complement'),
    behavior: num(tsWeightsBlock, /behavior:\s*([\d.]+)/, 'TS weights.behavior'),
  };
  eq(pyWeights, tsWeights, '打分权重');

  const weightSum = Object.values(pyWeights).reduce((sum, n) => sum + n, 0);
  if (Math.abs(weightSum - 1) > 1e-9) errors.push(`四个维度权重之和必须为 1，现在是 ${weightSum}`);

  // --- 2. 阈值与上限 ---
  eq(
    num(files.py, /MIN_SCORE = ([\d.]+)/, 'Python MIN_SCORE'),
    num(files.ts, /MIN_SCORE = ([\d.]+)/, 'TS MIN_SCORE'),
    '入榜阈值 MIN_SCORE',
  );
  eq(
    num(files.py, /MAX_LIMIT = (\d+)/, 'Python MAX_LIMIT'),
    num(files.ts, /MAX_LIMIT = (\d+)/, 'TS MAX_LIMIT'),
    '单栏上限 MAX_LIMIT',
  );
  const tsLimitsBlock = (files.ts.match(/DEFAULT_LIMITS = \{([^}]*)\}/) || [])[1] || '';
  eq(
    {
      similar: num(files.py, /DEFAULT_SIMILAR_LIMIT = (\d+)/, 'Python DEFAULT_SIMILAR_LIMIT'),
      complement: num(files.py, /DEFAULT_COMPLEMENT_LIMIT = (\d+)/, 'Python DEFAULT_COMPLEMENT_LIMIT'),
    },
    {
      similar: num(tsLimitsBlock, /similar:\s*(\d+)/, 'TS limits.similar'),
      complement: num(tsLimitsBlock, /complement:\s*(\d+)/, 'TS limits.complement'),
    },
    '默认数量上限',
  );

  // --- 3. 词表 ---
  const pyStop = (files.py.match(/STOPWORDS = frozenset\("""([\s\S]*?)"""\.split\(\)\)/) || [])[1];
  const tsStop = (files.ts.match(/STOPWORDS = new Set\(\s*`([\s\S]*?)`\s*\.split/) || [])[1];
  if (pyStop == null || tsStop == null) {
    errors.push('没解析到 STOPWORDS（Python 用 frozenset("""...""")，TS 用 new Set(`...`)）');
  } else {
    eq(tokenSet(pyStop), tokenSet(tsStop), '停用词表');
  }

  const pyAcc = (files.py.match(/ACCESSORY_TERMS = frozenset\("""([\s\S]*?)"""\.split\(\)\)/) || [])[1];
  const tsAcc = (files.ts.match(/ACCESSORY_TERMS = new Set\(\s*`([\s\S]*?)`\s*\.split/) || [])[1];
  if (pyAcc == null || tsAcc == null) {
    errors.push('没解析到 ACCESSORY_TERMS');
  } else {
    eq(tokenSet(pyAcc), tokenSet(tsAcc), '配件词表');
  }

  // --- 4. 静态组合表 ---
  const pyRulesBlock = (files.py.match(/COMPLEMENT_RULES[^=]*=\s*\{([\s\S]*?)\n\}/) || [])[1];
  const tsRulesBlock = (files.ts.match(/COMPLEMENT_RULES[^=]*=\s*\{([\s\S]*?)\n\};/) || [])[1];
  if (pyRulesBlock == null || tsRulesBlock == null) {
    errors.push('没解析到 COMPLEMENT_RULES 组合表');
  } else {
    const pyRules = parseRules(pyRulesBlock, '(', ')');
    const tsRules = parseRules(tsRulesBlock, '[', ']');
    eq(Object.keys(pyRules).sort(), Object.keys(tsRules).sort(), '组合表的品类键');
    for (const key of Object.keys(pyRules)) {
      if (key in tsRules) eq(pyRules[key], tsRules[key], `组合表 ${key} 的配件`);
    }
  }

  // --- 5. 引擎关键行为：阈值过滤 + 去重 + 两栏互斥 ---
  if (!/if score < MIN_SCORE:/.test(files.py)) errors.push('Python 引擎必须按 MIN_SCORE 过滤，不能全量返回');
  if (!/score < MIN_SCORE\) continue/.test(files.ts)) errors.push('TS 引擎必须按 MIN_SCORE 过滤，不能全量返回');
  for (const [name, source] of [['Python', files.py], ['TS', files.ts]]) {
    if (!/is_archived/.test(source)) errors.push(`${name} 引擎必须跳过归档商品`);
    if (!/identity_keys|identityKeys/.test(source)) errors.push(`${name} 引擎必须按身份键去重`);
  }
  if (!/if item\['accessory'\]:\s*\n\s*continue/.test(files.py)) {
    errors.push('Python：同类栏必须跳过配件（两栏不重复出现同一件商品）');
  }
  if (!/if \(item\.accessory\) continue;/.test(files.ts)) {
    errors.push('TS：同类栏必须跳过配件（两栏不重复出现同一件商品）');
  }

  // --- 6. 路由：路径、参数名、默认值与上限必须跟引擎常量对得上 ---
  const routeSignature = files.route.match(
    /@router\.get\("\/\{product_id\}\/recommendations"\)\s*\ndef product_recommendations\(([\s\S]*?)\):/,
  );
  if (!routeSignature) {
    errors.push('缺少 GET /api/products/{product_id}/recommendations 路由');
  } else {
    const body = routeSignature[1];
    const similarDefault = num(body, /similar_limit: int = Query\((\d+)/, '路由 similar_limit 默认值');
    const complementDefault = num(body, /complement_limit: int = Query\((\d+)/, '路由 complement_limit 默认值');
    const similarMax = num(body, /similar_limit[\s\S]*?le=(\d+)/, '路由 similar_limit 上限');
    const complementMax = num(body, /complement_limit[\s\S]*?le=(\d+)/, '路由 complement_limit 上限');
    eq(
      { similar: similarDefault, complement: complementDefault },
      {
        similar: num(files.py, /DEFAULT_SIMILAR_LIMIT = (\d+)/, 'Python DEFAULT_SIMILAR_LIMIT'),
        complement: num(files.py, /DEFAULT_COMPLEMENT_LIMIT = (\d+)/, 'Python DEFAULT_COMPLEMENT_LIMIT'),
      },
      '路由默认值与引擎默认值',
    );
    const maxLimit = num(files.py, /MAX_LIMIT = (\d+)/, 'Python MAX_LIMIT');
    if (similarMax !== maxLimit || complementMax !== maxLimit) {
      errors.push(`路由上限（${similarMax}/${complementMax}）必须等于引擎 MAX_LIMIT（${maxLimit}）`);
    }
    if (!/product_recommendations[\s\S]*?raise HTTPException\(status_code=404/.test(files.route)) {
      errors.push('路由要先确认商品存在再算，不存在应回 404 而不是 500');
    }
  }

  // --- 7. 前端接线：两套数据源都要有，少一边桌面端就是 undefined is not a function ---
  if (!/export const getRecommendations = \(/.test(files.remote)) {
    errors.push('remoteApi 缺少 getRecommendations');
  }
  if (!/`\/api\/products\/\$\{encodeURIComponent\(id\)\}\/recommendations\$\{qs \? `\?\$\{qs\}` : ''\}`/.test(files.remote)) {
    errors.push('remoteApi 的 getRecommendations 必须打到 /api/products/{id}/recommendations，并带上 limit 参数');
  }
  if (!/export const getRecommendations = async/.test(files.local)) {
    errors.push('本地 api.ts 也要实现 getRecommendations，否则桌面端详情页会报错');
  }
  if (!/recommendProducts\(anchor, pool, records/.test(files.local)) {
    errors.push('本地实现必须复用 utils/productRecommend 的口径，不能另写一套');
  }
  if (!/<ProductRecommendations productId=\{product\.id\} \/>/.test(files.detail)) {
    errors.push('详情页必须挂上推荐区');
  }
  if (!/import ProductRecommendations from '\.\.\/ProductRecommendations'/.test(files.detail)) {
    errors.push('详情页要导入推荐组件');
  }
  if (!/api\.getRecommendations\(productId\)/.test(files.ui)) {
    errors.push('推荐组件必须经 useApi 取数，不能直连某一种数据源');
  }

  // --- 8. 界面必须把「为什么推」和「推荐口径」真的渲染出来 ---
  if (!/item\.reasons\.map/.test(files.ui)) errors.push('每张推荐卡片必须渲染 reasons（推荐依据）');
  for (const field of ['strategy', 'weights', 'min_score', 'limits', 'candidate_count', 'scored_count', 'dedup']) {
    if (!new RegExp(`meta\\.${field}`).test(files.ui)) {
      errors.push(`推荐依据面板必须展示 meta.${field}（否则「说明推荐来源/排序依据/数量上限」没落地）`);
    }
  }
  if (!/没有达到阈值的同类商品/.test(files.ui)) errors.push('同类栏空态要写清是阈值没到，不能留白让人以为坏了');
  if (!/没有识别到配套商品/.test(files.ui)) errors.push('配套栏空态要写清判据，不能留白');
  if (!/推荐只影响这一块/.test(files.ui)) errors.push('推荐失败要降级成这一块的提示，不能把整页弄红');
  if (!/setShowMeta\(\(v\) => !v\)/.test(files.ui)) errors.push('推荐依据要能展开/收起，默认不占版面');

  for (const name of ['RecommendationItem', 'RecommendationMeta', 'RecommendationResult']) {
    if (!new RegExp(`export interface ${name}`).test(files.types)) errors.push(`types.ts 缺少 ${name}`);
  }

  return errors;
}

const errors = collect(FILES);
assert.deepEqual(errors, [], `详情页推荐守卫未通过：\n${errors.join('\n')}`);

// 突变自测：故意让两边不一致 / 删掉界面渲染，守卫必须变红。
const mutants = [
  ['权重漂移', 'py', (s) => s.replace('WEIGHT_CATEGORY = 0.35', 'WEIGHT_CATEGORY = 0.40')],
  ['配件词漂移', 'ts', (s) => s.replace('cable cord adapter charger', 'cable cord adapter')],
  ['组合表漂移', 'ts', (s) => s.replace("'wig cap', 'wig net'", "'wig cap'")],
  ['默认上限漂移', 'ts', (s) => s.replace('DEFAULT_LIMITS = { similar: 6, complement: 4 }', 'DEFAULT_LIMITS = { similar: 8, complement: 4 }')],
  ['阈值过滤被删', 'ts', (s) => s.replace('if (scoredOne.score < MIN_SCORE) continue;', '')],
  ['同类栏不再排除配件', 'ts', (s) => s.replace('if (item.accessory) continue;', '')],
  ['路由上限与引擎脱节', 'route', (s) => s.replace('le=12', 'le=20')],
  ['本地实现丢失', 'local', (s) => s.replace('export const getRecommendations = async', 'export const getRecommendationsDisabled = async')],
  ['详情页没挂推荐区', 'detail', (s) => s.replace('<ProductRecommendations productId={product.id} />', '')],
  ['理由不再渲染', 'ui', (s) => s.replace('item.reasons.map', 'item.reasons.slice(0, 0).map')],
  ['meta 面板缺项', 'ui', (s) => s.replace(/meta\.dedup/g, 'meta.unknown')],
];

for (const [name, key, mutate] of mutants) {
  const mutated = mutate(FILES[key]);
  assert.notEqual(mutated, FILES[key], `突变自测没生效：${name} 的替换规则没匹配到内容`);
  const found = collect({ ...FILES, [key]: mutated });
  assert.ok(found.length > 0, `突变自测失败：${name} 之后守卫仍然通过，说明它没在守这一项`);
}

console.log(
  `PASS 详情页推荐：两份实现常量一致（4 权重 / 阈值 / 上限 / 停用词 / 配件词 / ${Object.keys(parseRules((FILES.py.match(/COMPLEMENT_RULES[^=]*=\s*\{([\s\S]*?)\n\}/) || [])[1] || '', '(', ')')).length} 组组合表），`
  + `路由默认值与上限对齐，界面渲染理由与 meta（含 ${mutants.length} 条突变自测）`,
);
