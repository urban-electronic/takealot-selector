// 补货侧栏「采购货源」的链接规则守卫。
//
// 为什么值得单独测：这些 URL 是直接给运营点去下单的，拼错了不会报错，
// 只会打开一个搜不到东西的页面——用户会以为"1688 上没有这个货"。
// 三个具体风险：
//   1. 关键词没编码：中文/空格原样拼进 query，部分平台会截断；
//   2. 用了错的关键词：多分支商品的 chinese_product_name 是 `棕色\n卡其色\n浅蓝色`，
//      整串丢给 1688 等于搜一个不存在的商品名；
//   3. purchase_url 会被 window.open 原样打开，javascript:/data: 必须挡住。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'takealot-sourcing-'));
try {
  execFileSync(process.execPath, [
    path.join(root, 'node_modules/typescript/bin/tsc'),
    path.join(root, 'src/operations/sourcing.ts'),
    '--outDir', tmp, '--target', 'ES2020', '--module', 'commonjs', '--skipLibCheck',
  ]);
  const {
    SOURCING_PLATFORMS, sourcingKeyword, sourcingLinks, normalizePurchaseUrl, purchaseHostLabel,
  } = require(path.join(tmp, 'sourcing.js'));

  // --- 1. 平台清单是覆盖面锁：删掉一个平台必须转红，而不是悄悄少一个按钮 --------
  assert.deepEqual(
    SOURCING_PLATFORMS.map(p => p.key),
    ['1688', 'taobao', 'alibaba', 'pinduoduo'],
    '四个平台缺一不可（运营点不到的那个平台，就等于这个商品没处补货）',
  );
  for (const platform of SOURCING_PLATFORMS) {
    assert.ok(platform.label.trim(), `${platform.key} 缺少按钮文案`);
    const url = platform.search('温度控制器');
    assert.match(url, /^https:\/\//, `${platform.key} 搜索链接必须是 https`);
    assert.ok(
      url.includes(encodeURIComponent('温度控制器')),
      `${platform.key} 的关键词没有编码：${url}`,
    );
    assert.ok(!url.includes('温度控制器'), `${platform.key} 的 URL 里出现了未编码的中文：${url}`);
  }

  // --- 2. 关键词来源优先级 -----------------------------------------------------
  assert.deepEqual(sourcingKeyword({ chinese_product_name: '温度控制器', product_name: 'Thermostat' }),
    { keyword: '温度控制器', source: 'chinese', variantCount: 1, variants: [] });
  assert.equal(sourcingKeyword({ chinese_product_name: '', product_name: 'Thermostat' }).source, 'english');
  assert.equal(sourcingKeyword({ chinese_product_name: null, product_name: 'Thermostat' }).keyword, 'Thermostat');
  assert.equal(sourcingKeyword({ sku: '9901234' }).source, 'sku', '中文名和标题都空时退回 SKU');
  assert.equal(sourcingKeyword({ sku: '9901234' }).keyword, '9901234');
  assert.equal(sourcingKeyword({}).keyword, '', '什么都没有时给空串，不要编一个词出来');
  assert.equal(sourcingKeyword({}).source, 'none');
  assert.deepEqual(sourcingKeyword({}).variants, []);

  // --- 3. 多分支商品：只给变体标签时不给关键词 ---------------------------------
  // chinese_product_name 是 `棕色\n卡其色\n浅蓝色`，拿"棕色"去 1688 搜会返回一堆
  // 不相干的东西。宁可留空 + 明确提示，也不要给一个看起来能用、实际一定搜错的词。
  const multi = sourcingKeyword({ chinese_product_name: '棕色\n卡其色\n浅蓝色', product_name: 'Nursing Cover' });
  assert.equal(multi.keyword, '', '只有变体标签时不能拿第一行当关键词');
  assert.equal(multi.source, 'variant_only');
  assert.equal(multi.variantCount, 3);
  assert.deepEqual(multi.variants, ['棕色', '卡其色', '浅蓝色'], '标签要原样给界面，提示里得列出来');
  assert.equal(sourcingKeyword({ chinese_product_name: '  \n 口罩黑 \n' }).keyword, '口罩黑', '空行要跳过');
  assert.equal(sourcingKeyword({ chinese_product_name: '  \n 口罩黑 \n' }).source, 'chinese',
    '去掉空行后只剩一行，那就是正经品名，不是变体标签');

  // --- 4. 空白折叠 -------------------------------------------------------------
  assert.equal(
    sourcingKeyword({ product_name: 'Dog Hock Brace - Short (Please Select Size) - M' }).keyword,
    'Dog Hock Brace - Short (Please Select Size) - M',
  );
  assert.equal(sourcingKeyword({ product_name: 'A   B\tC' }).keyword, 'A B C', '连续空白折成一个空格');

  // --- 5. 空关键词不生成链接 ---------------------------------------------------
  assert.deepEqual(sourcingLinks(''), [], '没有关键词时宁可不出按钮，也不要给一串搜不到东西的链接');
  assert.deepEqual(sourcingLinks('   '), []);
  assert.equal(sourcingLinks('口罩黑').length, 4);
  assert.equal(sourcingLinks('口罩黑')[0].key, '1688', '1688 必须排在第一个（默认找货入口）');

  // --- 6. 采购链接只认 http/https ----------------------------------------------
  assert.equal(normalizePurchaseUrl('https://detail.1688.com/offer/123.html'), 'https://detail.1688.com/offer/123.html');
  assert.equal(normalizePurchaseUrl('  http://example.com/a  '), 'http://example.com/a');
  assert.equal(normalizePurchaseUrl(''), null);
  assert.equal(normalizePurchaseUrl(null), null);
  assert.equal(normalizePurchaseUrl('   '), null);
  for (const bad of ['javascript:alert(1)', 'data:text/html,<script>1</script>', 'detail.1688.com/offer/1.html', '//evil.example.com/x', 'file:///c:/x']) {
    assert.equal(normalizePurchaseUrl(bad), null, `必须拒绝 ${bad}`);
  }

  // --- 7. 平台名识别 ----------------------------------------------------------
  assert.equal(purchaseHostLabel('https://detail.1688.com/offer/123.html'), '1688');
  assert.equal(purchaseHostLabel('https://item.taobao.com/item.htm?id=1'), '淘宝');
  assert.equal(purchaseHostLabel('https://detail.tmall.com/item.htm?id=1'), '天猫');
  assert.equal(purchaseHostLabel('https://www.alibaba.com/product-detail/x_1.html'), '阿里巴巴国际站');
  assert.equal(purchaseHostLabel('https://mobile.yangkeduo.com/goods.html?goods_id=1'), '拼多多');
  assert.equal(purchaseHostLabel('https://item.jd.com/1.html'), '京东');
  assert.equal(purchaseHostLabel('https://www.supplier-example.cn/p/1'), 'supplier-example.cn',
    '认不出的域名退回主机名，不要写"外部链接"这种没信息量的字');
  assert.equal(purchaseHostLabel(''), '', '没有链接时给空串，界面据此不显示"打开"按钮');
  assert.equal(purchaseHostLabel('javascript:alert(1)'), '', '非法 scheme 同样不给出平台名');

  // --- 8. 四个平台的 host 不能串台（复制粘贴改 label 时最容易错）-----------------
  const expectedHosts = { '1688': 's.1688.com', taobao: 's.taobao.com', alibaba: 'www.alibaba.com', pinduoduo: 'mobile.yangkeduo.com' };
  for (const platform of SOURCING_PLATFORMS) {
    assert.equal(new URL(platform.search('x')).host, expectedHosts[platform.key], `${platform.key} 的域名不对`);
  }

  // 不报条数：其中两组断言在循环里跑（4 个平台各 4 条、4 个域名各 1 条），
  // 静态条数和实际执行次数对不上，写死一个数字迟早会漂。
  console.log('sourcing 链接规则断言全部通过');
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}
