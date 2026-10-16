// Executes the real inventory page with isolated, deferred API responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', {url:'https://example.invalid'});
for (const key of ['window','document','navigator','HTMLElement','Node','Event'])
  Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react');
const {act}=React;
const {createRoot}=require('react-dom/client');
let calls=[],finish;
const api={getInventory:async()=>[{product_id:'p',sku:'TEST',name:'Test',available:5}],getShipments:async()=>[],getInventoryAdjustments:async()=>[],getOperationLogs:async()=>[],createInventoryAdjustment:body=>{calls.push(body);return new Promise((resolve,reject)=>{finish={resolve,reject};});}};
const file=path.resolve(__dirname,'../src/pages/Inventory.tsx');
const compiled=new Module(file,module);compiled.filename=file;compiled.paths=Module._nodeModulePaths(path.dirname(file));
const original=compiled.require.bind(compiled);
compiled.require=name=>name==='../operations/ShipmentReconciliation'?{default:()=>null}:name==='../remoteApi'?api:name==='react-router-dom'?{useNavigate:()=>()=>{},useSearchParams:()=>[new URLSearchParams()]}:name==='../utils/imageFallback'?{ProductImage:()=>null}:original(name);
compiled._compile(ts.transpileModule(fs.readFileSync(process.env.INVENTORY_SOURCE || file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);
const root=createRoot(document.getElementById('root'));
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text);
const click=b=>b.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
const quantity=value=>{const el=document.querySelector('input[placeholder="数量"]');Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));};
(async()=>{
 await act(async()=>root.render(React.createElement(compiled.exports.default)));
 act(()=>click(button('调整库存')));
 if (!process.env.DOUBLE_ONLY) {
 act(()=>quantity('1.5'));
 act(()=>click(button('确认记录')));
 assert.equal(calls.length,0,'fractional quantity must not silently become one unit');
 }
 act(()=>quantity('2'));
 const submit=button('确认记录');
 act(()=>{click(submit);click(submit);});
 assert.equal(calls.length,1,'rapid double click must send exactly one inventory adjustment');
 assert.equal(calls[0].quantity_delta,2);
 assert.ok(button('记录中...').disabled,'pending request shows disabled progress');
 assert.ok(button('取消').disabled,'pending edit cannot be discarded');
 assert.ok(button('调整库存').disabled,'cannot switch draft SKU during submission');
 await act(async()=>finish.reject(new Error('network unavailable')));
 assert.ok(button('确认记录')&&!button('确认记录').disabled,'failure unlocks retry');
 assert.equal(document.querySelector('input[placeholder="数量"]').value,'2','failure preserves draft');
 act(()=>click(button('确认记录')));
 await act(async()=>finish.resolve({}));
 assert.equal(calls.length,2,'explicit retry is allowed');
 assert.equal(document.querySelector('input[placeholder="数量"]'),null,'success clears editor');
 act(()=>root.unmount());
 console.log('10 inventory interaction assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
