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
const api={getProducts:async()=>[],getImageUrl:x=>x,updateProduct:(...args)=>{assert.ok(pendingUpdate,'unexpected fixture mutation');writes.push(args);return pendingUpdate;}};
// getStores 给空数组：拿不到店铺时页面按「代理」处理（保留参考价列），
// 这样这条用例只量分页，不被代理/自营的显示差异干扰。
let source='remote',storeId='default-store',coverageCalls=[],pendingCoverage=[],previewIds;
const remote={getSellerCoverage:q=>{coverageCalls.push(q);return new Promise((resolve,reject)=>pendingCoverage.push({resolve,reject}));},previewSellerAssist:async ids=>{previewIds=ids;return {items:[],fillable:0};},getStoreStorageKey:x=>x,readStoreStorage:x=>localStorage.getItem(x),getActiveStoreId:()=>storeId,getStores:()=>Promise.resolve([])};
// 未在 mocks 中声明的相对导入，按真实 .ts/.tsx 编译，避免新增一个模块就让整组测试失败。
// 递归里也要接管 m.require：只编译第一层的话，被编译模块再 import 一个相对 .ts
// （例如 marketPrice → utils/datetime）会掉回 Node 的默认解析，报 MODULE_NOT_FOUND。
function localModule(name,fromFile){if(typeof name!=='string'||!name.startsWith('.'))return null;for(const ext of ['.ts','.tsx']){const target=path.resolve(path.dirname(fromFile),name+ext);if(!fs.existsSync(target))continue;const m=new Module(target,module);m.filename=target;m.paths=Module._nodeModulePaths(path.dirname(target));const orig=m.require.bind(m);m.require=n=>localModule(n,target)||orig(n);m._compile(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,target);return m.exports;}return null;}
function compile(file,mocks={}){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=n=>n in mocks?mocks[n]:(localModule(n,file)||orig(n));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,file);return m.exports;}
const types=compile(path.resolve(__dirname,'../src/types.ts'));
const page=compile(path.resolve(__dirname,'../src/pages/ProductList.tsx'),{'../DataSourceContext':{useApi:()=>api,useDataSource:()=>({dataSource:source})},'../api':{openUrl:()=>{}},'../remoteApi':remote,'../types':types,'../utils/imageFallback':{ProductImage:()=>null}}).default;
let scrolls=[];window.scrollTo=options=>scrolls.push(options);Object.defineProperty(window,'scrollY',{value:900,configurable:true});
dom.window.HTMLElement.prototype.getBoundingClientRect=function(){return {top:this.classList.contains('product-table-scroll')?240:0,height:this.classList.contains('app-header')?68:20};};
const root=createRoot(document.getElementById('root'));
const button=s=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===s);
const click=b=>b.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
const setValue=(el,value)=>{Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};

const result={seller_offer_count:1,seller_plid_count:1,local_product_count:1,local_plid_count:1,matched_count:0,sku_mismatch_count:1,sku_mismatch:[{product_id:'old-sku',product_no:3,name:'Fixture',local_sku:['123'],seller_sku:['W-NEW']}],seller_only_count:0,seller_only:[],local_only_count:0,truncated:false};
(async()=>{
 const render=()=>root.render(React.createElement(MemoryRouter,{initialEntries:['/products?custom_sku=1']},React.createElement(page)));
 await act(async()=>render());
 assert.equal(coverageCalls.length,1,'filtered empty list starts one coverage read');
 await act(async()=>render());assert.equal(coverageCalls.length,1,'rerender never duplicates a pending read');
 await act(async()=>pendingCoverage.shift().resolve(result));
 assert.match(document.body.textContent,/SKU 和卖家后台不一致：1/);
 const preview=button('核对本店资料');assert.equal(preview.disabled,false,'empty filter still permits mismatch preview');
 await act(async()=>click(preview));assert.deepEqual(previewIds,['old-sku'],'preview targets mismatches, not empty filtered IDs');
 // Switching data source must invalidate old server evidence.
 source='local';await act(async()=>render());
 assert.match(document.body.textContent,/本地数据源或当前店铺尚不支持/);
 assert.equal(document.body.textContent.includes('SKU 和卖家后台不一致：1'),false);
 source='remote';storeId='other-store';await act(async()=>render());
 assert.equal(coverageCalls.length,1,'other stores do not query default-store seller data');
 storeId='default-store';await act(async()=>render());
 const q=document.querySelector('#coverage-tsin');await act(async()=>setValue(q,'104410843,96097290'));
 await act(async()=>click(button('核对')));assert.equal(coverageCalls.at(-1),'104410843,96097290');
 storeId='other-store';await act(async()=>render());
 await act(async()=>pendingCoverage.shift().resolve(result));
 assert.equal(document.body.textContent.includes('SKU 和卖家后台不一致：1'),false,'late response cannot leak across stores');
 await act(async()=>root.unmount());
 console.log('PASS seller coverage page: empty preview, one request, source/store isolation, exact TSIN query, late-response rejection; zero business writes');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(watchdog);dom.window.close();});
