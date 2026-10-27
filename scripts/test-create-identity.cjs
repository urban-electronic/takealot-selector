// Actual ProductCreate with isolated API and inert import fixtures; never a backend.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const ts=require('typescript'),{JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.invalid/create'});
for(const k of ['window','document','navigator','HTMLElement','Node','Event','localStorage'])Object.defineProperty(globalThis,k,{value:dom.window[k],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
const URL_A='https://www.takealot.com/a/PLID111',URL_B='https://www.takealot.com/b/PLID222';
const result=(url,name)=>({normalized_url:url,product_name:name,product_image_url:'',actual_sale_price_zar:null,in_stock_price:null,takealot_category_path:null,warnings:[],success:true,variants:[]});
let delayed=null;
const writes=[];
const api={getProducts:async()=>[],getFeeCategories:async()=>[],getFeeMappingRules:async()=>[],getImageUrl:x=>x,
 scrapeTakealot:async url=>result(url,url===URL_A?'Product A':'Product B'),
 translateProductName:async name=>delayed?delayed:{chinese_name:name==='Product A'?'甲商品':'乙商品'},
 createProduct:async payload=>{writes.push(payload);return{id:'fixture'};}};
function local(name,from){if(!name.startsWith('.'))return null;for(const ext of ['.ts','.tsx']){const p=path.resolve(path.dirname(from),name+ext);if(fs.existsSync(p))return compile(p);}return null;}
function compile(p,mocks={}){const m=new Module(p,module);m.filename=p;m.paths=Module._nodeModulePaths(path.dirname(p));const orig=m.require.bind(m);m.require=n=>n in mocks?mocks[n]:(local(n,p)||orig(n));m._compile(ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,p);return m.exports;}
const Page=compile(path.resolve(__dirname,'../src/pages/ProductCreate.tsx'),{'../DataSourceContext':{useApi:()=>api},'react-router-dom':{useNavigate:()=>()=>{}},'../takealotPageImport':{makeTakealotBookmarklet:()=>'',parseTakealotPageImport:()=>result(URL_A,'Product A'),importSavedTakealotPage:()=>result(URL_A,'Product A')}}).default;
const host=document.getElementById('root');let root=createRoot(host);
const set=(el,value)=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));};
const button=text=>[...host.querySelectorAll('button')].find(b=>b.textContent===text);
const chinese=()=>[...host.querySelectorAll('label')].find(l=>l.textContent==='中文品名')?.parentElement.querySelector('input');
(async()=>{
 await act(async()=>root.render(React.createElement(Page)));
 await act(async()=>set(host.querySelector('input[type=url]'),URL_A));
 await act(async()=>button('抓取产品信息').click());
 assert.equal(chinese().value,'甲商品');
 await act(async()=>set(host.querySelector('input[type=url]'),URL_B));
 await act(async()=>button('直接手动填写').click());
 assert.equal(chinese().value,'','manual product B must not inherit product A Chinese name');
 await act(async()=>button('抓取产品信息').click());
 assert.equal(chinese().value,'乙商品','product B obtains its own translated identity');
 await act(async()=>root.unmount());
 window.history.replaceState(null,'','#takealot-import=fixture');
 let finish;delayed=new Promise(resolve=>{finish=resolve;});
 root=createRoot(host);
 await act(async()=>root.render(React.createElement(Page)));
 await act(async()=>set(host.querySelector('input[type=url]'),URL_B));
 await act(async()=>button('直接手动填写').click());
 await act(async()=>finish({chinese_name:'甲商品迟到翻译'}));
 assert.equal(chinese().value,'','late imported product A translation must not fill product B');
 assert.equal(writes.length,0,'identity reads never create a business record');
 await act(async()=>root.unmount());
 console.log('PASS create identity: cross-URL Chinese reset, fresh translation, late import exclusion, no writes');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>dom.window.close());
