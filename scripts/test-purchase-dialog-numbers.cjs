// Execute the actual submitBuy validation before its write lock, no API or inventory writes.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
function validation(code, qty, amount) {
  const start = code.indexOf('    if (!buy || buyWriting.current) return;', code.indexOf('const submitBuy ='));
  const end = code.indexOf('    buyWriting.current = true;', start);
  assert.ok(start >= 0 && end > start, 'actual submit validation must be found');
  let error = '';
  new Function('buy', 'buyWriting', 'setBuyError', code.slice(start, end))(
    {product: {}, qty, amount}, {current: false}, value => { error = value; });
  return error;
}
for (const qty of ['1.5', '0.5', '0', '-1', 'abc', '', '9007199254740992'])
  assert.ok(validation(source, qty, '10'), 'reject invalid quantity ' + qty);
for (const amount of ['', ' ', '-1', 'abc', 'Infinity'])
  assert.ok(validation(source, '2', amount), 'reject invalid amount');
assert.equal(validation(source, '2', '0'), '', 'explicit zero money is valid');
assert.equal(validation(source, '2', '12.50'), '', 'normal purchase is valid');
const old = source.replace('!Number.isSafeInteger(qty)', '!Number.isFinite(qty)')
  .replace('!buy.amount.trim() || ', '');
assert.equal(validation(old, '1.5', '10'), '', 'old implementation accepts a fraction');
assert.equal(validation(old, '2', ''), '', 'old implementation accepts blank money');
assert.ok(!source.includes('quantity: Math.round(qty)'), 'submission must preserve validated integer');
console.log('PASS purchase dialog numeric validation and old-code negative probes');
