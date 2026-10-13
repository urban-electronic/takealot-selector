const SITE='https://urban-electronic.github.io/takealot-selector/';
const BASE='https://takealot-selector-production.up.railway.app';
let working=false;
let state={status:'waiting',message:'等待系统网页'};
const trusted = sender => sender.tab?.url?.startsWith(SITE);
const api = async (config,path,method='GET',body) => {
  const response=await fetch(BASE+path,{method,headers:{'X-API-Key':config.apiKey,'X-Store-ID':'default-store','Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error(`服务连接失败 HTTP ${response.status}`);
  return response.json();
};
const attached = async () => {
  const {client}=await chrome.storage.session.get('client');
  if(!client||Date.now()-client.seen>60000)return null;
  const tab=await chrome.tabs.get(client.tabId).catch(()=>null);
  return tab?.url?.startsWith(SITE)?client:null;
};
async function loaded(tabId) {
  const until=Date.now()+45000;
  while(Date.now()<until) {const tab=await chrome.tabs.get(tabId);if(tab.status==='complete')return;await new Promise(r=>setTimeout(r,500));}
  throw new Error('商品页加载超时');
}
async function run() {
  if(working)return;working=true;
  let config;let leased=[];
  try {
    config=await attached();if(!config)return;
    await api(config,'/api/collector/web-open','POST',{initial:!!config.initial});
    if(config.initial){config.initial=false;await chrome.storage.session.set({client:config});}
    const batch=await api(config,'/api/collector/products');
    leased=batch.items.map(item=>item.productId);
    const report=async quote=>{await api(config,'/api/collector/offers','POST',{items:[quote]});leased=leased.filter(id=>id!==quote.productId);};
    state={status:'running',message:batch.items.length?`本轮 ${batch.items.length} 个商品，正在读取`:'等待下一个5分钟采集周期'};
    for(const item of batch.items) {
      if(!(await attached()))break;
      let url;
      try{url=new URL(item.url);}catch{await report({productId:item.productId,sku:item.sku,error:'invalid_url'});continue;}
      if(!['www.takealot.com','takealot.com'].includes(url.hostname)||url.protocol!=='https:'||!/PLID\d+/i.test(url.pathname)){await report({productId:item.productId,sku:item.sku,error:'invalid_url'});continue;}
      let tab;let keep=false;
      try {
        tab=await chrome.tabs.create({url:item.url,active:false});
        await loaded(tab.id);
        await chrome.scripting.executeScript({target:{tabId:tab.id},files:['parser.js']});
        const response=await chrome.scripting.executeScript({target:{tabId:tab.id},func:async item=>globalThis.collectTakealotQuote(item),args:[item]});
        const quote=response[0]?.result || {...item,error:'collector_error'};
        if(quote.error==='site_challenge') {
          keep=true;state={status:'verification',message:'请在保留的Takealot商品页完成验证或登录、设置配送地址',tabId:tab.id};
          await chrome.tabs.update(tab.id,{active:true});
          // Give the person time to interact. No automated challenge solver or cookie export.
          for(let i=0;i<60;i++) {
            if(!(await attached()))break;
            await new Promise(r=>setTimeout(r,2000));
            const retry=await chrome.scripting.executeScript({target:{tabId:tab.id},func:async item=>globalThis.collectTakealotQuote(item),args:[item]}).catch(()=>[]);
            const result=retry[0]?.result;
            if(result&&!result.error){await report(result);keep=false;state={status:'running',message:'验证后已读到报价，继续采集'};break;}
          }
          if(keep){await report(quote);break;}
        } else {
          await report(quote);
          state={status:quote.error?'quote_error':'running',message:quote.error?`此商品未获取有效行情：${quote.error}`:'报价已回传，继续采集'};
        }
      } catch(error) {state={status:'error',message:error.message};await report({productId:item.productId,sku:item.sku,error:'navigation_timeout'}).catch(()=>{});}
      finally {if(tab&&!keep)await chrome.tabs.remove(tab.id).catch(()=>{});}
      await new Promise(r=>setTimeout(r,1500));
    }
  } catch(error){state={status:'error',message:error.message};}
  finally{if(config&&leased.length)await api(config,'/api/collector/release','POST',{product_ids:leased}).catch(()=>{});working=false;}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(!trusted(sender)||message.type!=='ATTACH')return;
  (async()=>{
    if(!message.enabled){const {client}=await chrome.storage.session.get('client');if(client?.tabId===sender.tab.id)await chrome.storage.session.remove('client');state={status:'paused',message:'网页自动采集已暂停'};return { ...state };}
    if(message.store!=='default-store')return {status:'other_store',message:'当前仅采集自营店铺'};
    if(!message.apiKey)return {status:'missing_key',message:'请先在网站设置中填写服务访问密钥'};
    const {client}=await chrome.storage.session.get('client');
    // Keep one active controller when several system tabs are open.
    const owner=client?await chrome.tabs.get(client.tabId).catch(()=>null):null;
    if(!client||client.tabId===sender.tab.id||Date.now()-client.seen>60000||!owner?.url?.startsWith(SITE))await chrome.storage.session.set({client:{tabId:sender.tab.id,apiKey:message.apiKey,seen:Date.now(),initial:!client||!owner||Date.now()-client.seen>60000||!!client.initial}});
    run();return {...state};
  })().then(reply).catch(error=>reply({status:'error',message:error.message}));return true;
});
chrome.alarms.create('collect',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='collect')run();});
chrome.action.onClicked.addListener(()=>{if(state.tabId)chrome.tabs.update(state.tabId,{active:true}).catch(()=>{});else chrome.tabs.create({url:SITE+'auto-pricing'});});
