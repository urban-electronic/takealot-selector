// 状态翻译层的纯函数测试：不启浏览器，直接编译 src/utils/buyboxStatus.ts。
// 目的不是覆盖分支数字，而是钉死「用户看得懂」这件事本身：
//   - 三个问题（在不在采 / 数据新不新 / 我该做什么）每个状态都要有答案；
//   - 排队不得被说成成功，失败不得被说成新鲜；
//   - 旧的机器术语（推定 / 异价 / 待复核 / 失去复核1/2）不得回流。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/buyboxStatus.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const {
  describeCollection,
  describeOwnership,
  describeFleet,
  quoteFreshness,
  humanAge,
  humanUntil,
  errorCopy,
  collectorName,
  ERROR_COPY,
} = mod.exports;

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const NOW = Date.parse('2026-10-17T12:00:00Z');
const ago = ms => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const ctx = (extra = {}) => ({ now: NOW, collectorSource: 'cloud_browser', configured: true, ...extra });

// ---- 相对时间：宁可说"时间未知"，也不编一个数字 --------------------------------
check(humanAge(0) === '刚刚', 'zero age reads as 刚刚');
check(humanAge(90_000) === '1 分钟前', 'sub-2-minute age rounds down to 1 分钟前');
check(humanAge(45 * MIN) === '45 分钟前', 'minutes scale is used under an hour');
check(humanAge(5 * 60 * MIN) === '5 小时前', 'hours scale is used under a day');
check(humanAge(3 * 24 * 60 * MIN) === '3 天前', 'days scale is used beyond a day');
check(humanAge(NaN) === '时间未知', 'unparseable age is never faked');
check(humanUntil(-1) === '马上', 'past due reads as 马上');
check(humanUntil(20_000) === '不到 1 分钟内', 'sub-minute countdown is explicit');
check(humanUntil(3 * MIN) === '约 3 分钟后', 'minute countdown is rounded up');
check(humanUntil(2 * 60 * MIN) === '约 2 小时后', 'hour countdown is used beyond an hour');

// ---- 新鲜度按各自间隔判定，而不是写死 5 分钟 ----------------------------------
check(quoteFreshness({ last_success_at: ago(4 * MIN) }, NOW).fresh === 'fresh', '4 分钟 < 2×5 分钟 判为新鲜');
check(quoteFreshness({ last_success_at: ago(12 * MIN) }, NOW).fresh === 'aging', '12 分钟落在 2~6 个间隔之间判为偏旧');
check(quoteFreshness({ last_success_at: ago(40 * MIN) }, NOW).fresh === 'stale', '40 分钟超过 6 个间隔判为过期');
check(
  quoteFreshness({ last_success_at: ago(20 * MIN), interval_minutes: 60 }, NOW).fresh === 'fresh',
  '同一个 20 分钟，在 60 分钟间隔下仍然是新鲜的',
);
check(quoteFreshness({}, NOW).fresh === 'none', '从未成功过就是 none，不是 stale');
check(quoteFreshness({ last_success_at: 'not-a-date' }, NOW).fresh === 'none', '坏时间戳不得当成新鲜');
// 未来时间戳：旧实现用 Math.max(0, now-at) 把负数压成 0，于是"明天成功过"会显示成
// 「刚刚」。时钟错、时区错、脏数据都会走到这里，不能当成最新报价。
const future = quoteFreshness({ last_success_at: new Date(NOW + 24 * 3600_000).toISOString() }, NOW);
check(future.fresh === 'invalid', 'future timestamp is invalid, not fresh');
check(future.ageText !== '刚刚', 'future timestamp never renders as 刚刚');
check(future.tone === 'danger', 'future timestamp is a danger state');
check(
  quoteFreshness({ last_success_at: new Date(NOW + 30_000).toISOString() }, NOW).fresh === 'fresh',
  '几十秒的时钟偏差仍然按新鲜处理，不误报',
);

// ---- 每个状态都要同时给出：现状、依据、动作 ------------------------------------
const required = view => {
  check(view.label.length > 0, 'state has a short label');
  check(view.headline.length > 0, 'state has a one-line headline');
  check(view.detail.length > 0, 'state explains itself');
  check(typeof view.action === 'string', 'state declares whether the user must act');
  check(['success', 'info', 'warning', 'danger', 'neutral'].includes(view.tone), 'state has a known tone');
};

const running = describeCollection({ status: 'running', last_success_at: ago(3 * MIN) }, ctx());
required(running);
check(running.label === '采集中' && running.action === '', 'running needs no action');

const queuedFuture = describeCollection({ status: 'queued', next_due: NOW / 1000 + 300 }, ctx());
required(queuedFuture);
check(queuedFuture.label === '等待下轮', 'future schedule is 等待下轮, not 排队中');
check(queuedFuture.headline.includes('约 5 分钟后'), 'future schedule states when');
check(queuedFuture.action === '', 'waiting for its own schedule needs no action');

const queuedCloud = describeCollection({ status: 'queued', next_due: 0 }, ctx());
required(queuedCloud);
check(queuedCloud.headline.includes('后台马上执行'), 'due queue on the cloud path says who executes');
check(queuedCloud.action !== '', 'stuck queue tells the user what to check');
check(!/成功|已更新/.test(queuedCloud.headline), 'queue never claims success');

const queuedLocal = describeCollection({ status: 'queued', next_due: 0 }, ctx({ collectorSource: 'local_collector' }));
required(queuedLocal);
check(queuedLocal.headline.includes('本机采集器'), 'queue on the local path names the local collector');
check(queuedLocal.action.includes('本机采集器'), 'queue on the local path points at the collector, not the server');
check(queuedLocal.tone === 'warning', 'a queue nobody is draining is a warning, not a neutral state');

const failed = describeCollection(
  { status: 'error', error_code: 'site_challenge', error: 'blocked', last_success_at: ago(50 * MIN) },
  ctx(),
);
required(failed);
check(failed.label === '采集失败', 'error keeps a stable label');
check(failed.headline === '网站要求人工验证', 'error headline is the plain-language cause');
check(failed.action.includes('采集维护'), 'site challenge points at the manual verification tool');
check(failed.detail.includes('旧报价'), 'error states what the number on screen still is');
check(!failed.headline.includes('site_challenge'), 'raw error codes never reach the headline');

const failedUnknown = describeCollection({ status: 'error', error_code: 'TimeoutError', error: 'boom' }, ctx());
required(failedUnknown);
check(failedUnknown.headline === '本轮没拿到报价', 'unknown error codes fall back instead of leaking');

const paused = describeCollection({ status: 'paused', last_success_at: ago(30 * MIN) }, ctx());
required(paused);
check(paused.label === '已停采' && paused.action !== '', 'paused explains how to resume');

const freshAuto = describeCollection(
  { status: 'success', enabled: true, interval_minutes: 5, last_success_at: ago(2 * MIN), next_due: NOW / 1000 + 180 },
  ctx(),
);
required(freshAuto);
check(freshAuto.tone === 'success', 'fresh auto collection is a success tone');
check(freshAuto.label === '自动采集', 'auto collection says so');
check(freshAuto.headline.includes('2 分钟前'), 'headline carries the data age');
check(freshAuto.action === '', 'a healthy row asks nothing of the user');

const agingManual = describeCollection({ status: 'success', last_success_at: ago(15 * MIN) }, ctx());
required(agingManual);
check(agingManual.tone === 'warning', 'aging manual data is a warning');
check(agingManual.action.includes('立即采集'), 'manual aging data tells the user to refresh');

const never = describeCollection({}, ctx());
required(never);
check(never.label === '未开始', 'a row that never ran is 未开始');
check(never.action.includes('立即采集'), 'never-run row offers a first action');

const unconfigured = describeCollection({ status: 'success', last_success_at: ago(MIN) }, ctx({ configured: false }));
required(unconfigured);
check(unconfigured.tone === 'danger' && unconfigured.action.includes('设置'), 'missing seller API outranks every other state');

// ---- 错误码表：每个都要有原因和动作，且不能把机器码当文案 ----------------------
for (const [code, copy] of Object.entries(ERROR_COPY)) {
  check(copy.title.length > 0 && copy.title !== code, `${code} has a human title`);
  check(copy.why.length > 10, `${code} explains the cause`);
  check(copy.action.length > 5, `${code} tells the user what to do`);
  check(!/[a-z_]{6,}/.test(copy.title), `${code} title contains no raw identifier`);
}
check(errorCopy('').title === '本轮没拿到报价', 'empty code falls back');
check(errorCopy('rate_limited').title === '被网站限流', 'rate limit is translated');

// ---- 持有推定：是价格推断，不是身份核实 ----------------------------------------
check(describeOwnership({}).label === '未采集', 'no quote means 未采集, never a holding claim');
const matched = describeOwnership({ buybox_price: 269, price_ownership: { state: 'matched' } });
check(matched.label === '疑似在持位', 'equal price is only 疑似在持位');
check(matched.explain.includes('不能证明是本店在占位'), 'equal price is explicitly not proof of identity');
// Codex 复核：holding_buybox 单独一个 true 只说明"当时同价"，不构成卖家身份证据。
// 真正算证据的是 `price_ownership.identity_verified`——采集器按卖家名比对过
// （buybox_monitor.py 会把 True/False/None 三态落库），这里必须没有它就不给成功态。
const legacyHolding = describeOwnership({ buybox_price: 269, holding_buybox: true });
check(legacyHolding.label === '同价 · 卖家未核实', 'legacy holding flag is not presented as verified identity');
check(legacyHolding.tone === 'warning', 'legacy holding flag never gets a success tone');
check(
  describeOwnership({ buybox_price: 269, holding_buybox: true }).label !== '已确认在持位',
  'no field in the current backend constitutes identity evidence',
);
// 反过来：卖家名核实过就必须说出来，不能继续降级成"疑似"。
const verifiedHolding = describeOwnership({ buybox_price: 269, price_ownership: { state: 'matched', identity_verified: true } });
check(verifiedHolding.label === '已核实持位', 'seller-name verified holding is stated as verified');
check(verifiedHolding.tone === 'success', 'verified holding is the only path allowed to claim success');
check(verifiedHolding.explain.includes('Sold by'), 'verified holding names the seller-name evidence');
const verifiedMismatch = describeOwnership({ buybox_price: 269, price_ownership: { state: 'mismatch', identity_verified: true } });
check(verifiedMismatch.label.includes('已核实'), 'verified mismatch says the seller name decided it');
check(describeOwnership({ buybox_price: 269, price_matches_buybox: true }).label === '同价 · 卖家未核实', 'legacy equal price is not upgraded to holding');
check(describeOwnership({ buybox_price: 0 }).label === '未采集', 'a zero quote is not a real quote');
check(describeOwnership({ buybox_price: 269, price_ownership: { state: 'mismatch_pending' } }).label.includes('复核中'), 'one lost round is still pending');

// ---- 页头一句话：说清什么坏了、为什么、该怎么办 --------------------------------
const rows = [
  { monitor: { status: 'success', enabled: true, last_success_at: ago(2 * MIN) } },
  { monitor: { status: 'error', error_code: 'site_challenge', enabled: true, last_success_at: ago(30 * MIN) } },
  { monitor: { enabled: true } },
];
const fleetBad = describeFleet(rows, ctx());
check(fleetBad.summary.includes('1 个 SKU 采集失败'), 'fleet states how many failed');
check(fleetBad.summary.includes('网站要求人工验证'), 'fleet states the dominant plain-language reason');
check(fleetBad.action.length > 0, 'fleet always offers an action when something is broken');
check(fleetBad.retryable === 0 && fleetBad.blocked === 1, '被网站挡住的行只算 blocked，不算可重试');
check(fleetBad.suggestedInterval === 120, 'throttling suggests a longer interval');
check(fleetBad.tone === 'warning', 'partial failure is a warning, not a neutral state');

// 自身故障（解析器/网络）才是可以一键重试的；这两类必须分开计数，
// 否则界面一边劝"别反复点采集"，按钮自己就在点。
const fleetFault = describeFleet([
  { monitor: { status: 'error', error_code: 'collector_error', enabled: true } },
], ctx());
check(fleetFault.retryable === 1 && fleetFault.blocked === 0, '自身故障行算可重试');
check(fleetFault.suggestedInterval === null, '自身故障不建议改间隔');

const fleetAllBad = describeFleet([
  { monitor: { status: 'error', error_code: 'site_challenge', enabled: true } },
  { monitor: { status: 'error', error_code: 'site_challenge', enabled: true } },
], ctx());
check(fleetAllBad.summary.startsWith('全部 2 个 SKU 采集失败'), 'total failure says so explicitly');
check(fleetAllBad.tone === 'danger', 'total failure is a danger');

const fleetLocal = describeFleet(rows, ctx({ collectorSource: 'local_collector' }));
check(fleetBad.action.includes('启动本机采集器'), 'cloud path points at the local collector as the workaround');
check(!fleetLocal.action.includes('启动本机采集器'), 'an already-online collector is not told to start itself');
check(fleetLocal.action.includes('120 分钟'), 'local path still suggests a longer interval');

const fleetHealthy = describeFleet([
  { monitor: { status: 'success', enabled: true, last_success_at: ago(2 * MIN) } },
  { monitor: { status: 'success', enabled: true, last_success_at: ago(4 * MIN) } },
], ctx());
check(fleetHealthy.summary.includes('2 个 SKU 自动采集中'), 'healthy fleet states the running count');
check(fleetHealthy.summary.includes('2 分钟前'), 'healthy fleet states data age');
check(fleetHealthy.action === '', 'a healthy fleet asks nothing of the user');
check(fleetHealthy.retryable === 0, 'a healthy fleet offers no retry');
check(fleetHealthy.suggestedInterval === null, 'a healthy fleet does not suggest interval changes');

// 一句话就必须是一句话：超过两行说明又在堆信息了。
for (const view of [fleetBad, fleetAllBad, fleetHealthy, describeFleet([], ctx()), describeFleet(rows, ctx({ configured: false }))]) {
  check(view.summary.length <= 40, `fleet summary stays one short line: ${view.summary}`);
  check(!view.summary.includes('\n'), 'fleet summary is a single line');
}

check(describeFleet([], ctx()).summary === '没有可采集的 SKU', 'empty fleet is explained, not blank');
check(describeFleet(rows, ctx({ configured: false })).tone === 'danger', 'unconfigured fleet is a danger');
check(describeFleet([{ monitor: { enabled: true, last_success_at: ago(MIN) } }], ctx()).tone === 'success', 'healthy auto fleet is a success');
check(collectorName('browser_extension') === '浏览器扩展', 'collector names are translated');
check(collectorName(undefined) === '采集器', 'unknown collector has a neutral name');

// ---- 术语回归：这几个词正是用户说"完全看不懂"的地方 ----------------------------
const forbidden = ['推定', '异价', '待复核', '失去复核1/2', '验证可用'];
const corpus = [];
for (const m of [{}, { status: 'running' }, { status: 'queued' }, { status: 'queued', next_due: 0 },
  { status: 'error', error_code: 'site_challenge' }, { status: 'paused' },
  { status: 'success', enabled: true, last_success_at: ago(MIN) },
  { status: 'success', last_success_at: ago(90 * MIN) }]) {
  const view = describeCollection(m, ctx());
  corpus.push(view.label, view.headline, view.detail, view.action);
}
for (const copy of Object.values(ERROR_COPY)) corpus.push(copy.title, copy.why, copy.action);
for (const state of ['matched', 'mismatch_pending', 'mismatch', 'checking', 'verification_failed']) {
  const view = describeOwnership({ buybox_price: 269, price_ownership: { state } });
  corpus.push(view.label, view.explain);
}
const fleetCorpus = describeFleet(rows, ctx());
corpus.push(fleetCorpus.summary, fleetCorpus.action);
for (const word of forbidden) {
  check(!corpus.some(text => String(text).includes(word)), `用户看不懂的旧术语不得回流：${word}`);
}

console.log(`PASS ${checks} Buybox copy assertions (pure functions, no DOM)`);
