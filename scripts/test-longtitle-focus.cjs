// Run the actual focus measurement expression with computed-style fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'ui_longtitle_focus_audit.py'), 'utf8');
const expression = source.match(/FOCUS_JS = r"""([\s\S]*?)"""/)[1];
const measure = new Function('document', 'getComputedStyle', `return (${expression})();`);
function focus(style, hostFocus = true) {
  const html = {};
  const parent = {parentElement:html};
  // `matches` 是审计用来判「焦点是否真在这个宿主元素上」的（日期框的日历按钮在
  // shadow DOM 里，Tab 停进去时 activeElement 还是 input，但 `:focus` 不再匹配它）。
  const el = {tagName:'BUTTON',className:'fixture',textContent:'保存',parentElement:parent,
              matches: sel => sel === ':focus' ? hostFocus : false};
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
// 焦点落在控件内部子部件上：宿主元素没有圈不代表没有焦点圈，必须标记出来交给
// 汇总单列（既不算通过也不算失败），不能当成「圈缺失」。
assert.equal(focus({outlineStyle:'none'}).hostFocus,true,'普通控件上焦点就在宿主元素上');
assert.equal(focus({outlineStyle:'none'},false).hostFocus,false,'子部件焦点必须被标出来');
console.log('PASS focus measurement: visible, missing, zero-width, transparent, translucent, shadow and sub-control fixtures');
