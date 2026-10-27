// Actual panel, controlled promises, no production API or purchases.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const ts=require('typescript'),{JSDOM}=require('jsdom');
const watchdog=setTimeout(()=>{console.error('FAIL sourcing save timeout');process.exit(1);},10000);
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.invalid/replenishment'});
for(const k of ['window','document','navigator','HTMLElement','Node','Event'])Object.defineProperty(globalThis,k,{value:dom.window[k],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
let finish,fail;const calls=[];
const updateProduct=(...args)=>{calls.push(args);return new Promise((resolve,reject)=>{finish=resolve;fail=reject;});};
function compile(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=n=>{if(n==='../remoteApi')return{updateProduct};if(n.startsWith('.')){for(const ext of ['.ts','.tsx']){const p=path.resolve(path.dirname(file),n+ext);if(fs.existsSync(p))return compile(p);}}return orig(n);};m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);return m.exports;}
const Panel=compile(path.resolve(__dirname,'../src/operations/SourcingPanel.tsx')).default;
const host=document.getElementById('root'),root=createRoot(host);
const input=()=>host.querySelector('#replenishment-sourcing-url');
const button=()=>host.querySelector('.sourcing-save button');
const edit=value=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input(),value);input().dispatchEvent(new Event('input',{bubbles:true}));};
(async()=>{
 await act(async()=>root.render(React.createElement(Panel,{product:{id:'fixture-product',product_name:'Fixture',purchase_url:'https://example.invalid/old'}})));
 await act(async()=>edit('https://example.invalid/new'));
 // Two clicks in the same React batch, before the busy render commits.
 await act(async()=>{button().click();button().click();});
 assert.equal(calls.length,1,'same-tick save must send one request');
 assert.equal(input().disabled,true,'pending save must lock the draft against edits that the response would overwrite');
 assert.deepEqual(calls[0],['fixture-product',{purchase_url:'https://example.invalid/new'}]);
 await act(async()=>fail(Error('fixture offline')));
 assert.equal(input().disabled,false,'failed save unlocks the field');
 assert.equal(input().value,'https://example.invalid/new','failed save preserves the draft');
 assert.match(host.querySelector('[role=alert]').textContent,/fixture offline/);
 await act(async()=>button().click());
 assert.equal(calls.length,2,'failed save can be retried');
 await act(async()=>finish({purchase_url:'https://example.invalid/new'}));
 assert.equal(input().disabled,false);
 assert.equal(button().disabled,true,'successful save has no dirty draft');
 assert.match(host.querySelector('[role=status]').textContent,/已保存/);
 await act(async()=>edit(''));
 await act(async()=>button().click());
 assert.equal(calls.length,3);
 assert.deepEqual(calls[2],['fixture-product',{purchase_url:''}]);
 await act(async()=>finish({purchase_url:''}));
 assert.match(host.querySelector('[role=status]').textContent,/已清除/);
 await act(async()=>root.unmount());
 console.log('PASS sourcing save: single request, draft lock, failure retry, save and clear (isolated API)');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(watchdog);dom.window.close();});
