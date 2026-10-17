// Mount the actual page with fixture data; no backend or business writes.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const ts=require('typescript'),{JSDOM}=require('jsdom');
const watchdog=setTimeout(()=>{console.error('FAIL product interaction suite did not complete within 10s');process.exit(1);},10000);
const dom=new JSDOM('<header class="app-header"></header><div id="root"></div>',{url:'https://example.invalid/products'});
for(const key of ['window','document','navigator','HTMLElement','Node','Event','localStorage'])Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.alert=()=>{}; // Capture old-page alerts locally; never a browser/production dialog.
// jsdom 没有剪贴板；这里记下被复制的内容，用来断言「复制的是 SKU 本身」。
let clipboardText=null;
Object.defineProperty(dom.window.navigator,'clipboard',{value:{writeText:async text=>{clipboardText=text;}},configurable:true});
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
const {MemoryRouter}=require('react-router-dom');
const rows=Array.from({length:65},(_,i)=>({id:`p${i+1}`,product_no:i+1,sku:`SKU${i+1}`,product_name:`Product ${i+1}`,chinese_product_name:`商品${i+1}`,selection_status:'数据待补充',link_status:'未购买',is_archived:false}));
rows[0].takealot_url='https://example.invalid/product';
rows[1].sku='SKU2 SKU2B';rows[1].chinese_product_name='红色 蓝色';
let pendingUpdate;const writes=[];
const api={getProducts:async()=>rows,getImageUrl:x=>x,updateProduct:(...args)=>{assert.ok(pendingUpdate,'unexpected fixture mutation');writes.push(args);return pendingUpdate;}};
const remote={getStoreStorageKey:x=>x,readStoreStorage:x=>localStorage.getItem(x),getActiveStoreId:()=> 'default-store'};
// 未在 mocks 中声明的相对导入，按真实 .ts/.tsx 编译，避免新增一个模块就让整组测试失败。
function localModule(name,fromFile){if(typeof name!=='string'||!name.startsWith('.'))return null;for(const ext of ['.ts','.tsx']){const target=path.resolve(path.dirname(fromFile),name+ext);if(!fs.existsSync(target))continue;const m=new Module(target,module);m.filename=target;m.paths=Module._nodeModulePaths(path.dirname(target));m._compile(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,target);return m.exports;}return null;}
function compile(file,mocks={}){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=n=>n in mocks?mocks[n]:(localModule(n,file)||orig(n));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);return m.exports;}
const types=compile(path.resolve(__dirname,'../src/types.ts'));
const page=compile(path.resolve(__dirname,'../src/pages/ProductList.tsx'),{'../DataSourceContext':{useApi:()=>api,useDataSource:()=>({dataSource:'remote'})},'../api':{openUrl:()=>{}},'../remoteApi':remote,'../types':types,'../utils/imageFallback':{ProductImage:()=>null}}).default;
let scrolls=[];window.scrollTo=options=>scrolls.push(options);Object.defineProperty(window,'scrollY',{value:900,configurable:true});
dom.window.HTMLElement.prototype.getBoundingClientRect=function(){return {top:this.classList.contains('product-table-scroll')?240:0,height:this.classList.contains('app-header')?68:20};};
const root=createRoot(document.getElementById('root'));
const button=s=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===s);
const click=b=>b.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
const setValue=(el,value)=>{Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
(async()=>{
 await act(async()=>root.render(React.createElement(MemoryRouter,{initialEntries:['/products?search=test']},React.createElement(page))));
 assert.equal(scrolls.length,0,'initial mount must not force a scroll');
 const table=document.querySelector('.product-table-scroll');
 const checkTop=()=>{assert.equal(table.scrollTop,0);assert.equal(table.scrollLeft,120);assert.deepEqual(scrolls.at(-1),{top:1060,behavior:'auto'});};
 act(()=>click(document.querySelector('tbody input[type=checkbox]')));
 for(const [label,expected] of [['下一页','Product 21'],['下一页','Product 41'],['上一页','Product 21'],['尾页','Product 61'],['首页','Product 1']]){
   table.scrollTop=800;table.scrollLeft=120;act(()=>click(button(label)));checkTop();assert.ok(document.querySelector('tbody').textContent.includes(expected),label+' renders the expected page');
 }
 assert.deepEqual(JSON.parse(localStorage.getItem('productListSelectedSkus')),['SKU1'],'selection survives pagination');
 const jump=document.querySelector('.pagination-bar input');act(()=>setValue(jump,'3'));table.scrollTop=800;act(()=>click(button('跳转')));checkTop();assert.ok(document.querySelector('tbody').textContent.includes('Product 41'));
 act(()=>setValue(jump,'2'));table.scrollTop=800;act(()=>jump.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true})));checkTop();
 table.scrollTop=800;act(()=>setValue(document.querySelector('.pagination-bar select'),'50'));checkTop();assert.equal(document.querySelectorAll('tbody > tr').length,50);
 const before=scrolls.length;table.scrollTop=300;act(()=>setValue(jump,'1'));act(()=>click(button('跳转')));assert.equal(scrolls.length,before,'same-page jump must not disturb scrolling');assert.equal(table.scrollTop,300);
 assert.equal(document.querySelector('.product-search-input').value,'test','search filter survives pagination');
 // Same real page; all reads/writes below use in-memory fixtures, never a backend.
 act(()=>click(button('首页')));
const cellEdit=(field)=>document.querySelector(`[aria-label="编辑${field}：SKU1"]`);
for(const [field,label] of [['中文品名','中文品名：SKU1'],['售价','售价：SKU1']]){
  const edit=cellEdit(field);assert.ok(edit,field+' has a named edit control');
  assert.equal(edit.tagName,'BUTTON',field+' supports native keyboard activation');
  edit.focus();act(()=>click(edit));
  const input=document.querySelector(`input[aria-label="${label}"]`);assert.ok(input,field+' editor has a label');
  assert.equal(document.activeElement,input,field+' editor receives focus');
  act(()=>input.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
}
// SKU 只读：它是平台侧唯一标识，改了采集/调价/装箱单就对不上号。
// 这里守两件事——没有编辑入口，且「复制」复制的是 SKU 原文。
assert.equal(document.querySelector('[aria-label="编辑SKU：SKU1"]'),null,'SKU has no inline editor');
assert.ok(document.querySelector('.sku-value'),'SKU is plain text, not a button that opens an editor');
const copySkuBtn=document.querySelector('[aria-label="复制SKU：SKU1"]');
assert.ok(copySkuBtn,'SKU offers a copy control');
assert.equal(copySkuBtn.tagName,'BUTTON','copy is a native button (keyboard activatable)');
await act(async()=>click(copySkuBtn));
assert.equal(clipboardText,'SKU1','copy writes the SKU itself to the clipboard');
assert.ok(copySkuBtn.textContent.includes('已复制'),'copy confirms success on the control');
assert.equal(writes.length,0,'copying a SKU never writes to the backend');
 const external=document.querySelector('a[href="https://example.invalid/product"]');
 assert.ok(external,'product navigation uses a real link for keyboard and new-tab navigation');
 assert.equal(external.target,'_blank');
 act(()=>click(button('展开 2 个规格')));
 const opener=document.querySelector('.branch-edit-btn');opener.focus();act(()=>click(opener));
 const dialog=document.querySelector('[role="dialog"][aria-labelledby="variant-editor-title"]');
 assert.ok(dialog,'variant editor has a named modal dialog');
 const sku=document.getElementById('variant-sku');
 assert.equal(document.activeElement,sku,'opening the dialog focuses the SKU field');
 assert.equal(document.querySelector('label[for="variant-sku"]').control,sku,'SKU label activates its field');
 const save=[...dialog.querySelectorAll('button')].find(b=>b.textContent==='保存分支');
 save.focus();const tab=new dom.window.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true});
 act(()=>save.dispatchEvent(tab));assert.ok(tab.defaultPrevented);assert.equal(document.activeElement,sku,'Tab wraps inside the dialog');
 act(()=>sku.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true})));
 assert.equal(document.activeElement,save,'Shift+Tab wraps to the final control');
 act(()=>save.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
 assert.equal(document.querySelector('.variant-editor-modal'),null,'Escape closes without saving');
 assert.equal(document.activeElement,opener,'closing restores focus to the opener');
 assert.equal(writes.length,0,'opening/cancelling editors must not write');
 let resolveUpdate;pendingUpdate=new Promise(resolve=>{resolveUpdate=resolve;});
 act(()=>click(opener));
 const savingDialog=document.querySelector('.variant-editor-modal');
 const saveAgain=[...savingDialog.querySelectorAll('button')].find(b=>b.textContent==='保存分支');
 act(()=>{click(saveAgain);click(saveAgain);});
 assert.equal(writes.length,1,'two same-tick save clicks share one mutation');
 assert.deepEqual(writes[0],['p2',{sku:'SKU2 SKU2B',chinese_product_name:'红色 蓝色'}],'fixture save preserves SKU/spec correspondence');
 assert.equal(savingDialog.getAttribute('aria-busy'),'true');
 assert.ok([...savingDialog.querySelectorAll('input,button')].every(el=>el.disabled),'pending save locks all dialog fields/actions');
 act(()=>savingDialog.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
 assert.ok(document.querySelector('.variant-editor-modal'),'Escape cannot dismiss an in-flight save');
 await act(async()=>resolveUpdate(rows[1]));
 assert.equal(document.querySelector('.variant-editor-modal'),null,'successful fixture save closes dialog');
 for(const [label,field,value] of [['中文品名','chinese_product_name','新商品名'],['售价','actual_sale_price_zar','123.45']]){
   writes.length=0;
   act(()=>click(document.querySelector(`[aria-label="编辑${label}：SKU1"]`)));
   let input=document.querySelector(`input[aria-label="${label}：SKU1"]`);
   act(()=>setValue(input,value));
   let fail;pendingUpdate=new Promise((resolve,reject)=>{fail=reject;});
   act(()=>{
     input.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
     input.dispatchEvent(new dom.window.FocusEvent('focusout',{bubbles:true}));
   });
   assert.equal(writes.length,1,label+': Enter then blur must send one update');
   await act(async()=>fail(Error('fixture offline')));
   input=document.querySelector(`input[aria-label="${label}：SKU1"]`);
   assert.ok(input,label+': failure keeps editor open');assert.equal(input.value,value,label+': failure preserves draft');
   assert.equal(document.activeElement,input,label+': failed field regains focus for retry');
   assert.ok(document.querySelector('[role="alert"]').textContent.includes('fixture offline'),label+': actionable inline error');
   let retry;pendingUpdate=new Promise(resolve=>{retry=resolve;});
   act(()=>input.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true})));
   assert.equal(writes.length,2,label+': failed save can be retried');
   const saved=field==='actual_sale_price_zar'?Number(value):value;
   await act(async()=>retry({...rows[0],[field]:saved}));
   assert.equal(document.querySelector(`input[aria-label="${label}：SKU1"]`),null,label+': successful save closes editor');
 }
 // 取消后迟到的 blur 不能把草稿写回去（用中文品名验证，SKU 已不可编辑）。
 act(()=>click(document.querySelector('[aria-label="编辑中文品名：SKU1"]')));
 const cancelled=document.querySelector('input[aria-label="中文品名：SKU1"]');act(()=>setValue(cancelled,'DO-NOT-SAVE'));
 const count=writes.length;act(()=>{cancelled.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));cancelled.dispatchEvent(new dom.window.FocusEvent('focusout',{bubbles:true}));});
 assert.equal(writes.length,count,'Escape followed by late blur cannot save a cancelled draft');
 // 多规格行的 SKU 也只读：展开/收起是看，不是改；复制拿得到整串。
 assert.equal(document.querySelector('[aria-label^="编辑SKU："]'),null,'no SKU in the list is editable');
 assert.ok(document.querySelector('[aria-label="复制SKU：SKU2 SKU2B"]'),'multi-variant row copies the whole SKU string');
 act(()=>root.unmount());console.log('Product interaction checks passed: pagination/scroll/selection, labelled native edit controls, read-only SKU with copy, real product links, modal focus/Tab/Escape/restore and duplicate-save protection (isolated fixtures).');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(watchdog);dom.window.close();});
