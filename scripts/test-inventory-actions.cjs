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
const shipment=(id,status)=>({id,shipment_no:id,shipment_date:'2026-10-20',total_skus:1,total_quantity:1,shipping:'海运',status,void_reason:'',lines:[{sku:'TEST',name_zh:'Test',cartons:1,count_per_carton:1,total_quantity:1,domestic_quantity:1,legacy_quantity:0}]});
const api={getInventory:async()=>[{product_id:'p',sku:'TEST',name:'Test',available:5}],getShipments:async()=>[shipment('S-TRANSIT','transit'),shipment('S-EXPORTED','exported')],getInventoryAdjustments:async()=>[],getOperationLogs:async()=>[],createInventoryAdjustment:body=>{calls.push(body);return new Promise((resolve,reject)=>{finish={resolve,reject};});}};
const file=path.resolve(__dirname,'../src/pages/Inventory.tsx');
const compiled=new Module(file,module);compiled.filename=file;compiled.paths=Module._nodeModulePaths(path.dirname(file));
// 未显式 mock 的相对导入按真实 .ts/.tsx 编译，避免新增一个模块就让整组测试失败。
const localModule=name=>{if(typeof name!=='string'||!name.startsWith('.'))return null;for(const ext of ['.ts','.tsx']){const target=path.resolve(path.dirname(file),name+ext);if(!fs.existsSync(target))continue;const m=new Module(target,module);m.filename=target;m.paths=Module._nodeModulePaths(path.dirname(target));m._compile(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,target);return m.exports;}return null;};
const original=compiled.require.bind(compiled);
compiled.require=name=>name==='../operations/ShipmentReconciliation'?{default:()=>null}:name==='../remoteApi'?api:name==='react-router-dom'?{useNavigate:()=>()=>{},useSearchParams:()=>[new URLSearchParams()]}:name==='../utils/imageFallback'?{ProductImage:()=>null}:(localModule(name)||original(name));
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
 // 撤回按钮的文案必须与它实际做的事一致：confirmed 和 transit 都扣过库存，
 // 撤回会返还库存并跳去装箱页恢复草稿；原来只认 confirmed，transit 那行写着
 // 「撤回记录」，用户以为只是删条记录，点下去库存却变了。
 act(()=>click(button('装箱 / 发货记录')));
 const rowButton=no=>document.querySelector(`tr:nth-child(${no}) .btn.btn-sm`).textContent;
 assert.equal(rowButton(1),'撤回并修改','a transit shipment deducts stock, so withdrawing restores it');
 assert.equal(rowButton(2),'撤回记录','an exported-only shipment never deducted stock');
 // 源码守卫：撤回 / 入仓导出 / 库存调整都必须走 notice()/fail()（BANNER-EXCLUSIVE-02）。
 // 「装箱单记录已撤回」不清上一条「导出失败」，「导出失败」也不清上一条
 // 「库存调整已撤销」——两条横幅并排挂着，用户看不出这次到底成没成。
 // 唯一允许直写的是 load() 的分区降级：那是「保存成功、但列表没读回来」，真的两件事。
 const inventorySource=fs.readFileSync(file,'utf8');
 assert.ok(inventorySource.includes('const notice = ')&&inventorySource.includes('const fail = '),
   '库存页必须提供 notice()/fail()：一个动作只留一条横幅');
 const bannerWrites=inventorySource.match(/(?:setMessage|setError)\((?!'')/g)||[];
 assert.equal(bannerWrites.length,3,
   `只允许 notice()/fail() 与 load() 的分区降级写横幅，实际 ${bannerWrites.length} 处`);
 act(()=>root.unmount());
 console.log('12 inventory interaction assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
