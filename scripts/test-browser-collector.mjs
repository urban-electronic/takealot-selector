import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
const source=await readFile('tools/browser-collector/parser.js','utf8');
async function run({title='Test Product | takealot.com',cards=[],variants=[]}={}) {
  const nodes=cards.map(card=>({getClientRects:()=>[{}],innerText:card.text,querySelectorAll:()=>[{getClientRects:()=>[{}],textContent:`R ${card.price}`}]}));
  const context=vm.createContext({setTimeout:fn=>queueMicrotask(fn),document:{title,body:{innerText:title},querySelectorAll:selector=>selector==='[data-ref="buybox-offer"]'?nodes:selector.startsWith('[class*="variant"]')?variants:[]},location:{pathname:'/item/PLID123'},URL,console});
  vm.runInContext(source,context);
  return context.collectTakealotQuote({productId:'job-token',sku:'SKU1',name:'Test Product',url:'https://www.takealot.com/item/PLID123'});
}
assert.equal((await run({title:'Just a moment...'})).error,'site_challenge');
assert.equal((await run()).error,'price_not_found');
const quote=await run({cards:[{price:120,text:'Fastest Delivery Get it tomorrow'},{price:90,text:'Best Price Pre-order'}]});
assert.equal(quote.buyboxPrice,120);
assert.equal(quote.offers[1].status,'pre_order');
assert.equal(quote.offers[0].offerType,'fastest');
assert.equal(quote.productId,'job-token');
assert.equal((await run({cards:[{price:120,text:'in stock'}],variants:[{getClientRects:()=>[{}]}]})).variantSwitchFailed,true);
console.log('Browser collector parser checks passed: challenge, missing quotes, delivery stock, job identity and ambiguous variants.');
