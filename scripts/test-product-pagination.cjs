// Mount the actual page with fixture data; no backend or business writes.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const ts=require('typescript'),{JSDOM}=require('jsdom');
const dom=new JSDOM('<header class="app-header"></header><div id="root"></div>',{url:'https://example.invalid/products'});
for(const key of ['window','document','navigator','HTMLElement','Node','Event','localStorage'])Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
const {MemoryRouter}=require('react-router-dom');
const rows=Array.from({length:65},(_,i)=>({id:`p${i+1}`,product_no:i+1,sku:`SKU${i+1}`,product_name:`Product ${i+1}`,chinese_product_name:`商品${i+1}`,selection_status:'数据待补充',link_status:'未购买',is_archived:false}));
const api={getProducts:async()=>rows,getImageUrl:x=>x};
const remote={getStoreStorageKey:x=>x,readStoreStorage:x=>localStorage.getItem(x),getActiveStoreId:()=> 'default-store'};
function compile(file,mocks={}){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=n=>n in mocks?mocks[n]:orig(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);return m.exports;}
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
 act(()=>root.unmount());console.log('Product pagination interaction checks passed: next/previous/first/last/jump/Enter/page-size, scroll reset, horizontal and selection/filter preservation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
