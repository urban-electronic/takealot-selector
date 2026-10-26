const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
// Browser contrast audit reads backgroundColor only. Verify the actual opaque
// gradient paint, including hover, on the shared small white-text buttons.
const css = fs.readFileSync(process.argv[2] || path.join(__dirname, '../src/workspace.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const lum = rgb => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  .reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
const rgb = hex => hex.match(/\w\w/g).map(v => parseInt(v, 16));
let checked = 0;
for (const selector of ['.btn-primary', '.btn-primary:hover', '.btn-success', '.btn-success:hover', '.packing-export-btn', '.packing-export-btn:hover']) {
  const blocks = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m => m[1].trim().split(',').map(v => v.trim()).includes(selector));
  const body = blocks.map(m => m[2]).join('\n');
  const gradient = [...body.matchAll(/background-image:\s*linear-gradient\(180deg,\s*#([0-9a-f]{6})\s+0%,\s*#([0-9a-f]{6})\s+100%\)/gi)].at(-1);
  assert.ok(gradient, selector + ': expected opaque two-stop gradient; update the audit for a new paint shape');
  const a = rgb(gradient[1]), b = rgb(gradient[2]);
  for (let n = 0; n <= 100; n++) {
    const bg = a.map((v, i) => v + (b[i] - v) * n / 100);
    const ratio = 1.05 / (lum(bg) + .05);
    assert.ok(ratio >= 4.5, selector + ': white text on painted gradient at ' + n + '% = ' + ratio.toFixed(2) + ':1; need 4.5:1');
  }
  checked++;
}
assert.equal(checked, 6);
console.log('PASS 6 shared button gradients, 606 paint samples >= 4.5:1');
