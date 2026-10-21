const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

let stored = null;
global.localStorage = { getItem: () => stored };
const file = path.resolve(__dirname, '../src/utils/useFxRates.ts');
const fallback = { base: 'ZAR', rates: { SGD: 12, CNY: 2.4 }, fallback: true };
const m = new Module(file, module);
m.filename = file;
m.paths = Module._nodeModulePaths(path.dirname(file));
const original = m.require.bind(m);
m.require = name => name === './marketPrice' ? { FALLBACK_FX: fallback }
  : name === '../remoteApi' ? { getFxRates: () => { throw Error('unexpected network'); } }
  : name === './storage' ? { safeSetItem: () => true } : original(name);
m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, file);
const current = m.exports.currentFxRates;

assert.equal(current(), fallback);
stored = JSON.stringify(fallback);
assert.equal(current().fallback, true, 'persisted fallback must retain its label');
const base = { base: 'ZAR', rates: { SGD: 12, CNY: 2.4 }, stale: false, fallback: false };
stored = JSON.stringify({ ...base, fetched_at: new Date(Date.now() - 48 * 3600000).toISOString() });
assert.equal(current().stale, true, 'expired local cache must say it is stale before the request returns');
stored = JSON.stringify({ ...base, fetched_at: new Date().toISOString() });
assert.equal(current().stale, false, 'fresh cache retains its status');
stored = JSON.stringify({ ...base, fetched_at: 'invalid' });
assert.equal(current().stale, true, 'unknown age is not fresh');
console.log('FX cache: 5 checks passed (no network)');
