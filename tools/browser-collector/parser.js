// Injected only into a Takealot product tab. No cookies, credentials or page HTML leave this tab.
globalThis.collectTakealotQuote = async function(item) {
  const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
  const clean = text => String(text||'').replace(/[\s\u00a0]+/g,' ').replace(/^[✓✔•·\-—:：\s]+|[✓✔•·\-—:：\s]+$/g,'').toLowerCase();
  const result={productId:item.productId,sku:item.sku,offers:[],holdingBuybox:false,buyboxPrice:null};
  const challenged=()=> /^(just a moment\.?\.?\.?|attention required!)/i.test(document.title) || /verify you are human|checking your browser|complete the captcha/i.test(document.body?.innerText||'');
  if (challenged()) return {...result,error:'site_challenge'};
  const expected = item.url.match(/PLID\d+/i)?.[0];
  if (!expected || location.pathname.match(/PLID\d+/i)?.[0]?.toLowerCase()!==expected.toLowerCase()) return {...result,error:'product_redirect'};
  const visible = element => element.getClientRects().length > 0;
  const cards = () => [...document.querySelectorAll('[data-ref="buybox-offer"]')].filter(visible);
  for(let i=0;i<20&&!cards().length;i++){await pause(500);if(challenged())return {...result,error:'site_challenge'};}
  const variants = () => [...document.querySelectorAll('[class*="variant"],[data-ref*="variant"],[class*="swatch"]')].filter(visible);
  const label = el => el.getAttribute('title') || el.getAttribute('aria-label') || el.textContent;
  const selected = el => ['aria-selected','aria-pressed','data-selected','data-active'].some(a=>el.getAttribute(a)==='true') || /(?:^|[ _-])(selected|active|checked)(?:$|[ _-])/i.test(el.className||'') || el.querySelector('input:checked');
  const suffix = item.name?.includes(' - ') ? item.name.split(' - ').at(-1).trim() : '';
  if (variants().length) {
    if (!suffix) return {...result,error:'variant_unmatched',variantSwitchFailed:true};
    const target = clean(suffix);
    const matching = () => variants().filter(el=>clean(label(el))===target);
    if (!matching().some(selected)) {
      const match=matching().sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length)[0];
      if(!match)return {...result,error:'variant_unmatched',variantSwitchFailed:true};
      match.click();await pause(2500);
    }
    if (!matching().some(selected)) return {...result,error:'variant_unmatched',variantSwitchFailed:true};
  }
  const price = el => {
    for(const selector of ['[data-ref="price"] .currency','[class*="price"] .currency','[data-ref="price"]','.currency']) {
      for(const node of el.querySelectorAll(selector)) {
        if(!visible(node))continue;
        const text=(node.textContent||'').trim();
        if(/[€$£]/.test(text))continue;
        const match=text.match(/(?:R\s*)?(\d[\d ,]*(?:\.\d{1,2})?)/);
        const value=match?Number(match[1].replace(/[ ,]/g,'')):NaN;
        if(Number.isFinite(value)&&value>0)return value;
      }
    } return null;
  };
  const stock = text => {
    text=text.toLowerCase();
    if(/pre.?order|back.?order|ships in [3-9]|out of stock/.test(text))return 'pre_order';
    if(/get it today|get it tomorrow|delivered today|in stock|ships tomorrow|ships in [12]/.test(text))return 'in_stock';
    // Fastest is a relative label, not evidence that inventory exists.
    return 'unknown';
  };
  const read = () => cards().map(el => ({price:price(el),status:stock(el.innerText||''),offerType:/fastest delivery/i.test(el.innerText||'')?'fastest':/best price/i.test(el.innerText||'')?'best_price':'buybox',isMine:false})).filter(o=>o.price!==null);
  const pick = offers => {const present=offers.filter(o=>o.status==='in_stock');const pool=present.length?present:offers;return pool.length?Math.min(...pool.map(o=>o.price)):null;};
  const rounds=[];
  for(let i=0;i<3;i++) {rounds.push(read());if(i<2)await pause(1500);}
  const values=rounds.map(pick);const stable=values.find(v=>v!==null&&values.filter(x=>x===v).length>=2);
  if(stable===undefined)return {...result,error:values.some(v=>v!==null)?'unstable_price':'price_not_found'};
  const chosen=rounds[values.indexOf(stable)];
  result.buyboxPrice=stable;
  result.offers=chosen;
  // Only inspect offer-specific containers; never harvest recommendations/ad prices.
  const other=[...document.querySelectorAll('[data-ref="other-offer"],[data-ref="other-seller-offer"],[class*="other-offer"]')].filter(visible);
  for(const el of other){const value=price(el);if(value!==null)result.offers.push({price:value,status:stock(el.innerText||''),offerType:'other',isMine:false});}
  result.collectedAt=new Date().toISOString();
  return result;
};
