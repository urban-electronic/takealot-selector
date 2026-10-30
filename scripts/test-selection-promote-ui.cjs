const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),{JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.invalid/selection'});
for(const k of ['window','document','navigator','HTMLElement','Node','Event'])Object.defineProperty(globalThis,k,{value:dom.window[k],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
function compile(p,mocks={}){const f=path.resolve(__dirname,'../'+p),m=new Module(f,module);m.filename=f;m.paths=Module._nodeModulePaths(path.dirname(f));const orig=m.require.bind(m);m.require=n=>n in mocks?mocks[n]:orig(n);m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,f);return m.exports;}
const rows=Array.from({length:625},(_,i)=>({id:`s${i}`,selected:true,product_name:`Fixture ${i}`,fee_category:'',tier:'',promoted_product_id:null}));
let reads=[],writes=[],confirmations=[],pending;
const remote={getSelectionItems:async p=>{reads.push(p);return p.only_selected?{total:625,items:rows.slice(p.offset,p.offset+p.limit)}:{total:625,items:[rows[0]]};},getSelectionSummary:async()=>({total:625,selected:625}),getSelectionBatches:async()=>[],getImageUrl:x=>x,promoteSelection:ids=>{writes.push(ids);if(writes.length===1)return new Promise(r=>pending=r);return Promise.resolve({created:ids.length,skipped:0});}};
const page=compile('src/pages/Selection.tsx',{'../remoteApi':remote,'../types':{SHIPPING_METHODS:[],formatPrice:String,formatPercent:String},'../utils/useFeeCategories':{useFeeCategories:()=>({categories:[]})},'../utils/imageFallback':{ProductImage:()=>null},'../utils/selectionPromotion':compile('src/utils/selectionPromotion.ts')}).default;
window.confirm=s=>{confirmations.push(s);return true;};
const root=createRoot(document.getElementById('root'));
(async()=>{
 await act(async()=>root.render(React.createElement(page)));
 const button=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('批量加入产品列表'));
 assert.ok(button);assert.equal(button.disabled,false);
 const click=()=>button.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
 await act(async()=>{click();click();});
 assert.equal(confirmations.length,1);assert.match(confirmations[0],/625/);assert.match(confirmations[0],/其他页/);
 assert.equal(writes.length,1);assert.equal(writes[0].length,500);assert.equal(button.disabled,true);
 await act(async()=>pending({created:500,skipped:0}));
 assert.equal(writes.length,2);assert.equal(writes[1].length,125);assert.equal(new Set(writes.flat()).size,625);
 assert.match(document.body.textContent,/已新建 625 个产品/);
 assert.deepEqual(reads.filter(p=>p.only_selected).map(p=>p.offset),[0,300,600]);
 await act(async()=>root.unmount());console.log('PASS real Selection: cross-page 625 confirmations, 500/125 chunks, double-click lock, final readback');
})().catch(e=>{console.error(e);process.exitCode=1;});
