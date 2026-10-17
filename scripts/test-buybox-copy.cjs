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
check(describeOwnership({ buybox_price: 269, holding_buybox: true }).label === '已确认在持位', 'verified holding keeps its own label');
check(describeOwnership({ buybox_price: 269, price_matches_buybox: true }).label === '同价 · 卖家未核实', 'legacy equal price is not upgraded to holding');
check(describeOwnership({ buybox_price: 0 }).label === '未采集', 'a zero quote is not a real quote');
check(describeOwnership({ buybox_price: 269, price_ownership: { state: 'mismatch_pending' } }).label.includes('复核中'), 'one lost round is still pending');

// ---- 整页总览：必须说清「谁在采」 ----------------------------------------------
const rows = [
  { monitor: { status: 'success', enabled: true, last_success_at: ago(2 * MIN) } },
  { monitor: { status: 'error', error_code: 'site_challenge', last_success_at: ago(30 * MIN) } },
  { monitor: {} },
];
const fleetCloud = describeFleet(rows, ctx());
check(fleetCloud.title.includes('1 个 SKU 本轮失败'), 'fleet surfaces the failure count');
check(fleetCloud.lines[0].includes('没有任何本机采集器在线'), 'cloud path states that no local collector is online');
check(fleetCloud.lines.some(line => line.includes('报价新鲜：1/3')), 'fleet reports how many quotes are fresh');
check(fleetCloud.lines.some(line => line.includes('从未成功采集')), 'fleet reports rows that never succeeded');

const fleetLocal = describeFleet(rows, ctx({ collectorSource: 'local_collector' }));
check(fleetLocal.lines[0].includes('本机采集器在线'), 'local path states the collector owns collection');
check(fleetLocal.lines[0].includes('后台不会重复采'), 'local path explains the mutual exclusion');

check(describeFleet([], ctx()).title === '没有可采集的 SKU', 'empty fleet is explained, not blank');
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
corpus.push(fleetCorpus.title, ...fleetCorpus.lines, fleetCorpus.action);
for (const word of forbidden) {
  check(!corpus.some(text => String(text).includes(word)), `用户看不懂的旧术语不得回流：${word}`);
}

console.log(`PASS ${checks} Buybox copy assertions (pure functions, no DOM)`);
