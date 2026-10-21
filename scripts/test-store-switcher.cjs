// 页头「店铺」下拉必须显示**实际在用的那个店**（src/utils/storeSelection.ts）。
//
// 钉死的原因：/api/stores 读失败时 App.tsx 原来会 setStores([{...自营店铺}])，
// 凭空造一条店铺顶上。可请求头里的 X-Store-ID 仍然是 localStorage 里那个，
// 于是界面显示 A、实际请求发的是 B；当前店铺不在选项里时，浏览器还会把
// select 显示成空白。店铺被暂停后也一样：下拉里没有它了，请求却还带着它。
//
// 这里只测选项推导与提示文案，不启浏览器、不连后端。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/storeSelection.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const { storeOptions, storeLoadNotice } = mod.exports;
const appSource = fs.readFileSync(path.resolve(__dirname, '../src/App.tsx'), 'utf8');

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const store = (id, name, status = 'active') => ({
  id, name, status, platform: 'Takealot', owner_name: '', sync_method: 'manual',
  sync_interval_minutes: 60, external_store_ref: '', allow_negative_inventory_shipments: false, last_synced_at: null,
});

// ---- 正常：只列运营中的店铺 -------------------------------------------------
{
  const rows = [store('default-store', '自营店铺'), store('store-2', '约翰内斯堡店'), store('store-3', '已停店铺', 'paused')];
  const options = storeOptions(rows, 'default-store');
  check(options.length === 2, '只列运营中的店铺');
  check(options.every(option => option.state === 'active'), '运营中的店铺标 active');
  check(!options.some(option => option.id === 'store-3'), '暂停的店铺不进普通列表');
}

// ---- 当前店铺被暂停：它必须还在，并标注状态 ---------------------------------
{
  const rows = [store('default-store', '自营店铺'), store('store-2', '约堡店', 'paused')];
  const options = storeOptions(rows, 'store-2');
  const current = options.find(option => option.id === 'store-2');
  check(Boolean(current), '当前店铺被暂停也要留在下拉里');
  check(current.state === 'paused', '标注为已暂停');
  check(current.label === '约堡店（已暂停）', `标签要说清状态，实际 ${current.label}`);
  check(options.length === 2, '其他运营中的店铺照常可选');
  check(new Set(options.map(option => option.id)).size === options.length, '同一店铺不能出现两次');
}

// ---- 读失败：不许伪造「自营店铺」 -------------------------------------------
{
  const options = storeOptions([], 'store-2', true);
  check(options.length === 1, '读失败时只有当前店铺一项');
  check(options[0].id === 'store-2', '当前店铺 id 不能被换掉');
  check(options[0].state === 'unknown', '读不到列表就是 unknown');
  check(options[0].label.includes('未在店铺列表中'), `要说明列表没读到，实际 ${options[0].label}`);
  check(!options.some(option => option.name === '自营店铺'), '不许凭空造一条「自营店铺」');
  check(!options.some(option => option.id === 'default-store'), '不许把当前店铺改成 default-store');
}

// ---- 读失败 + 当前就是自营店：名字照旧，但带说明 ----------------------------
{
  const options = storeOptions([], 'default-store', true);
  check(options.length === 1 && options[0].label === '自营店铺（未在店铺列表中）', `实际 ${options[0].label}`);
}

// ---- 首次加载（还没返回）：不加吓人的后缀 -----------------------------------
{
  const options = storeOptions([], 'default-store', false);
  check(options[0].label === '自营店铺', `加载中只显示名字，实际 ${options[0].label}`);
  check(options[0].state === 'unknown', '加载中也算 unknown');
  const custom = storeOptions(null, 'store-9', false);
  check(custom[0].label === 'store-9', '不认识的店铺先显示 id，不冒充别的店');
}

// ---- 提示文案 ---------------------------------------------------------------
{
  const notice = storeLoadNotice('HTTP 401: Unauthorized', '自营店铺（未在店铺列表中）');
  check(notice.includes('店铺列表读取失败'), '要说清是什么失败了');
  check(notice.includes('HTTP 401'), '要带上真实原因，不能只说「暂时不可用」');
  check(notice.includes('自营店铺（未在店铺列表中）'), '要说清现在按哪个店在跑');
  check(notice.includes('切换店铺') && notice.includes('自动采集'), '要说清哪些动作没发生');
  check(storeLoadNotice('', '自营店铺').includes('原因未知'), '原因为空也要有交代');
}

// ---- 页面不能在列表返回后静默跳到第一家运营店 -------------------------------
// helper 已经保证当前店铺（暂停/未知）会留在下拉里；App 如果仍覆盖 activeStore，
// 屏幕和请求会一起悄悄切到别店，用户可能在不知情时操作另一店的装箱与库存。
check(!appSource.includes('setActiveStore(activeRows[0].id)'),
  '当前店铺暂停或缺失时不得自动切到第一家运营店，必须由用户明确选择');
check(!appSource.includes('setActiveStoreId(activeRows[0].id)'),
  '不得静默改写 localStorage 中实际生效的店铺 id');

console.log(`test-store-switcher: ${checks} 条断言通过`);
