import { useEffect, useRef, useState } from 'react';
import { openCloudBrowser, cloudBrowserAction, cloudBrowserFrame, collectBuyboxNow } from '../remoteApi';
import { isPageHidden } from '../utils/useVisibleInterval';

type Row = {product_id:string;offer_id:string;sku:string;name:string};
export default function CloudBrowser({rows,onCollected}:{rows:Row[];onCollected:()=>void}) {
  const [choice,setChoice]=useState('');
  const [session,setSession]=useState('');
  const [image,setImage]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [text,setText]=useState('');
  const mounted=useRef(true);
  const token=useRef(''); const objectUrl=useRef(''); const selected=useRef<Row>();
  const clearSession=()=>{token.current='';setSession('');setImage('');setText('');if(objectUrl.current){URL.revokeObjectURL(objectUrl.current);objectUrl.current='';}};
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;if(token.current)void cloudBrowserAction(token.current,{action:'close'}).catch(()=>{});token.current='';if(objectUrl.current)URL.revokeObjectURL(objectUrl.current);};},[]);
  const frame=async(s:string)=>{
    const blob=await cloudBrowserFrame(s);
    if(token.current!==s)return;
    const next=URL.createObjectURL(blob);if(objectUrl.current)URL.revokeObjectURL(objectUrl.current);
    objectUrl.current=next;setImage(next);
  };
  useEffect(()=>{
    if(!session)return;
    let active=true;
    let pending=false;
    const tick=async()=>{if(pending)return;pending=true;try{await frame(session);}catch(e){if(active){setError((e as Error).message);if((e as Error & {status?:number}).status===410)clearSession();}}finally{pending=false;}};
    void tick();
    // 画面轮询最贵（每 2.5 秒一张截图）：标签页隐藏时停掉，回到前台立刻补一帧，
    // 避免用户看着旧画面点击导致坐标落在错误位置。
    const timer=window.setInterval(()=>{if(isPageHidden())return;void tick();},2500);
    const onVisible=()=>{if(!isPageHidden())void tick();};
    document.addEventListener('visibilitychange',onVisible);
    return()=>{active=false;clearInterval(timer);document.removeEventListener('visibilitychange',onVisible);};
  },[session]);
  const open=async()=>{
    const row=rows.find(r=>`${r.product_id}:${r.offer_id}`===(choice||`${rows[0]?.product_id}:${rows[0]?.offer_id}`));
    if(!row)return;setBusy(true);setError('');
    try{const result=await openCloudBrowser(row.product_id);if(!mounted.current){await cloudBrowserAction(result.session,{action:'close'});return;}token.current=result.session;selected.current=row;setSession(result.session);}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const action=async(data:Record<string,unknown>)=>{if(!token.current||busy)return;setBusy(true);setError('');try{await cloudBrowserAction(token.current,data);await frame(token.current);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  const close=async(collect=false)=>{
    setBusy(true);setError('');
    try{
      await cloudBrowserAction(token.current,{action:'close'});clearSession();
      if(collect&&selected.current){await collectBuyboxNow(selected.current.product_id,selected.current.offer_id);onCollected();}
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  return <section className="card browser-collector-panel">
    <div className="dashboard-panel-title"><div><h2>云端 Buybox 采集</h2><p>采集在网站服务器运行，无需安装程序或扩展。自动采集与实际调价分别启停。</p></div></div>
    <div className="row-buttons"><select aria-label="云端浏览器商品" value={choice} onChange={e=>setChoice(e.target.value)} disabled={!!session||busy}>{rows.map(r=><option key={`${r.product_id}:${r.offer_id}`} value={`${r.product_id}:${r.offer_id}`}>{r.sku} · {r.name.slice(0,40)}</option>)}</select><button className="btn btn-info" disabled={busy||!rows.length||!!session} onClick={open}>{busy&&!session?'正在打开…':'打开云端浏览器'}</button></div>
    <small>首次遇到网站验证或需要设置南非配送位置时，可在这里操作同一个采集浏览器。网页画面在会话期间传输，5分钟闲置或15分钟后自动关闭；会话期间暂停后台使用该浏览器。验证仍可能被网站拒绝。</small>
    {error&&<p role="alert" className="text-danger">{error}</p>}
    {session&&<div className="cloud-browser-session">
      <div className="row-buttons"><strong>{selected.current?.sku} · 云端页面</strong><button className="btn btn-outline" disabled={busy} onClick={()=>close()}>关闭并保存会话</button><button className="btn btn-info" disabled={busy} onClick={()=>close(true)}>保存并采集此SKU</button></div>
      <p>点击画面操作；输入配送位置可使用下方输入框。此窗口仅供商品浏览、配送和网站验证，不支持购买或账号操作。</p>
      {image?<img src={image} alt="云端Takealot商品页面，可点击操作" style={{width:'100%',maxWidth:1366,borderRadius:12,cursor:busy?'wait':'pointer'}} onClick={e=>{
        const rect=e.currentTarget.getBoundingClientRect();void action({action:'click',x:Math.min(1365,Math.max(0,Math.floor((e.clientX-rect.left)*1366/rect.width))),y:Math.min(767,Math.max(0,Math.floor((e.clientY-rect.top)*768/rect.height)))});
      }}/>:<p>正在读取云端页面…</p>}
      <div className="row-buttons"><input aria-label="配送位置输入文字" placeholder="先点画面中的输入框，再在这里输入" maxLength={160} value={text} onChange={e=>setText(e.target.value)}/><button className="btn btn-outline" disabled={busy||!text} onClick={()=>{void action({action:'text',text});setText('');}}>输入文字</button>{['Tab','Enter','Backspace','Escape'].map(key=><button key={key} className="btn btn-outline" disabled={busy} onClick={()=>action({action:'key',key})}>{key}</button>)}<button className="btn btn-outline" disabled={busy} onClick={()=>action({action:'scroll',delta:-500})}>向上</button><button className="btn btn-outline" disabled={busy} onClick={()=>action({action:'scroll',delta:500})}>向下</button></div>
    </div>}
  </section>;
}
