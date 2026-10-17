// Run the actual focus measurement expression with computed-style fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'ui_longtitle_focus_audit.py'), 'utf8');
const expression = source.match(/FOCUS_JS = r"""([\s\S]*?)"""/)[1];
const measure = new Function('document', 'getComputedStyle', `return (${expression})();`);
function focus(style) {
  const html = {};
  const parent = {parentElement:html};
  const el = {tagName:'BUTTON',className:'fixture',textContent:'保存',parentElement:parent};
  const base = {backgroundColor:'rgb(255,255,255)',color:'rgb(0,0,0)',outlineStyle:'solid',outlineWidth:'2px',outlineColor:'rgb(0,0,0)',boxShadow:'none'};
  return measure({activeElement:el,body:parent,documentElement:html}, node=>node===el?{...base,...style}:base);
}
assert.equal(focus({}).ringRatio,21);
for (const style of [
  {outlineWidth:'0px'},
  {outlineStyle:'none'},
  {outlineColor:'rgba(0,0,0,0)'},
  {outlineStyle:'none',boxShadow:'rgb(0,0,0) 0px 0px 0px 0px'},
]) {
  const result=focus(style);
  assert.equal(result.ringVisible,false);
  assert.equal(result.ringRatio,null);
}
assert.ok(focus({outlineColor:'rgba(0,0,0,0.01)'}).ringRatio<3,'translucent rings use composited contrast');
assert.equal(focus({outlineStyle:'none',boxShadow:'rgb(0,0,0) 0px 0px 0px 2px'}).ringVisible,true);
console.log('PASS focus measurement: visible, missing, zero-width, transparent, translucent and shadow fixtures');
