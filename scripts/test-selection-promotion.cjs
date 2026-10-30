const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript'),Module=require('node:module'),path=require('node:path');
const file=path.resolve(__dirname,'../src/utils/selectionPromotion.ts'),m=new Module(file,module);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file);
const read=m.exports.selectedPromotionIds;
const items=Array.from({length:625},(_,i)=>({id:`s${i}`,selected:true,promoted_product_id:i===624?'p1':null}));
(async()=>{
 const calls=[];const ids=await read(async p=>{calls.push(p);return {total:625,items:items.slice(p.offset,p.offset+p.limit)};},'batch-fixture');
 assert.equal(ids.length,624);assert.equal(ids.at(-1),'s623');assert.deepEqual(calls.map(p=>p.offset),[0,300,600]);assert.ok(calls.every(p=>p.batch_id==='batch-fixture'&&p.only_selected));
 await assert.rejects(read(async p=>({total:p.offset?624:625,items:items.slice(p.offset,p.offset+300)})),/已变化/);
 await assert.rejects(read(async p=>({total:625,items:p.offset?[]:items.slice(0,300)})),/不完整/);
 await assert.rejects(read(async p=>({total:625,items:items.slice(0,300)})),/已变化/);
 await assert.rejects(read(async()=>{throw Error('fixture offline');}),/offline/);
 assert.deepEqual(await read(async()=>({total:0,items:[]})),[]);
 console.log('PASS cross-page selection snapshot: 625 rows, batch scope, promoted skip, drift/duplicate/partial/offline stops');
})().catch(e=>{console.error(e);process.exitCode=1;});
