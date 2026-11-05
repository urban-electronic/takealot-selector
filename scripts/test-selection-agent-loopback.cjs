const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const src = fs.readFileSync(path.join(__dirname, '../src/remoteApi.ts'), 'utf8')
  .replaceAll('import.meta.env', '({})');
const js = ts.transpileModule(src, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;

async function main() {
  const calls = [];
  const exports = {};
  const fetchFixture = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, text: async () => '{"ok":true}' };
  };
  new Function('exports', 'fetch', js)(exports, fetchFixture);
  const save = base => exports.setSelectionAgentConfig(base, { api_key: 'isolated-marker' });
  for (const base of ['https://example.invalid', 'http://127.0.0.1@example.invalid',
    'http://127.0.0.1:8770/path', 'http://127.0.0.1:8770?target=external',
    'ftp://127.0.0.1:8770', 'http://localhost.example.invalid']) {
    await assert.rejects(() => save(base), /回环/);
  }
  await assert.rejects(() => exports.getSelectionAgentHealth('https://example.invalid'), /回环/);
  assert.equal(calls.length, 0);

  for (const base of ['', 'http://127.0.0.1:8770/', 'http://[::1]:8770']) {
    await save(base);
    const { url, options } = calls.at(-1);
    assert.match(url, /^http:\/\/(127\.0\.0\.1|\[::1\]):8770\/api\/agent-config$/);
    assert.equal(options.redirect, 'error');
    assert.equal(JSON.parse(options.body).api_key, 'isolated-marker');
  }
  console.log('PASS selection agent loopback and redirect guard');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
