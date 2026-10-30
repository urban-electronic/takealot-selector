// Real SelectionCard events; fixture only, no API/network/business writes.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const ts = require('typescript'), { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', {url:'https://example.invalid/selection'});
for (const key of ['window','document','navigator','HTMLElement','Node','Event']) Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react'), {act} = React, {createRoot} = require('react-dom/client');
const file = path.resolve(__dirname,'../src/pages/Selection.tsx');
const m = new Module(file,module); m.filename=file; m.paths=Module._nodeModulePaths(path.dirname(file));
const orig=m.require.bind(m);
m.require=n=> n==='../remoteApi'?{getImageUrl:x=>x}:n==='../types'?{SHIPPING_METHODS:[],formatPercent:String,formatPrice:String}:n==='../utils/useFeeCategories'?{}:n==='../utils/imageFallback'?{ProductImage:()=>null}:orig(n);
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8')+'\nexport { SelectionCard };',{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);
const root=createRoot(document.getElementById('root'));
const fields={'长':'length_mm','宽':'width_mm','高':'height_mm','重量':'actual_weight_kg','供货单价':'unit_price_cny','采购数量':'purchase_quantity','采购价':'purchase_cost_cny','国内运费':'purchase_shipping_cny'};
(async()=>{
 for(const [label,field] of Object.entries(fields)){
  const patches=[];
  const item={id:'fixture',product_name:'Fixture',selected:false,fee_category:'',tier:'',length_mm:10,width_mm:20,height_mm:30,actual_weight_kg:1,purchase_quantity:4,purchase_cost_cny:40,purchase_shipping_cny:5,unit_price_cny:field==='unit_price_cny'?10:null};
  await act(async()=>root.render(React.createElement(m.exports.SelectionCard,{item,busy:false,onPatch:p=>patches.push(JSON.parse(JSON.stringify(p))),onToggle:()=>{},feeCategories:[]})));
  const input=[...document.querySelectorAll('label.selection-field')].find(el=>el.querySelector('span')?.firstChild?.textContent===label)?.querySelector('input');
  assert.ok(input,label); assert.equal(input.disabled,false,label);
  await act(async()=>input.focus());
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await act(async()=>input.blur());
  assert.deepEqual(patches,[{[field]:null}],`${label}: clearing must survive JSON serialization`);
 }
 for (const [label,field,text] of [
  ['中文品名','chinese_product_name','蓝色便携搅拌杯'],
  ['1688 / 货源链接','purchase_url','https://detail.1688.com/offer/123.html'],
  ['选品备注','note','2件装；供应商已核对'],
 ]) {
  const patches=[];
  const item={id:'text-fixture',product_name:'Fixture',selected:false,fee_category:'',tier:'',[field]:''};
  const render=()=>root.render(React.createElement(m.exports.SelectionCard,{item,busy:false,onPatch:p=>patches.push(JSON.parse(JSON.stringify(p))),onToggle:()=>{},feeCategories:[]}));
  await act(async()=>render());
  const input=[...document.querySelectorAll('label.selection-field')].find(el=>el.querySelector('span')?.textContent===label).querySelector('input');
  await act(async()=>input.focus());
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,text);input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  assert.deepEqual(patches,[],'typing must not submit per character');
  await act(async()=>input.blur());
  assert.deepEqual(patches,[{[field]:text}],label);
  item[field]=text;
  await act(async()=>render());
  assert.equal(input.value,text,'saved readback remains visible');
 }
 await act(async()=>root.unmount()); console.log('PASS selection card: 8 clear-field payloads preserve explicit null');
})().catch(e=>{console.error(e);process.exitCode=1;});
