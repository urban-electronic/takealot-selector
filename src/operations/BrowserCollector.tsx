import { useEffect, useState } from 'react';
export default function BrowserCollector() {
  const [enabled,setEnabled]=useState(()=>localStorage.getItem('browser_auto_collect')!=='false');
  const [state,setState]=useState({status:'not_installed',message:'安装采集助手后，打开系统网页即可自动采集本店有库存商品。'});
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{
      if(event.source!==window||event.origin!==location.origin||event.data?.source!=='takealot-browser-collector')return;
      setState({status:String(event.data.status||'unknown'),message:String(event.data.message||'采集助手已连接')});
    };
    window.addEventListener('message',receive);
    window.postMessage({source:'takealot-browser-ui',type:'PING'},location.origin);
    return ()=>window.removeEventListener('message',receive);
  },[]);
  const toggle=()=>{
    localStorage.setItem('browser_auto_collect',String(!enabled));setEnabled(!enabled);
    window.postMessage({source:'takealot-browser-ui',type:'PING'},location.origin);
  };
  return <section className="card browser-collector-panel">
    <div className="dashboard-panel-title"><div><h2>打开网页自动采集</h2><p>{state.message}</p></div><button className="btn btn-outline btn-sm" onClick={toggle}>{enabled?'暂停网页采集':'开启网页采集'}</button></div>
    <small>只读取行情，不会开启调价。每5分钟为目标周期，排队和页面验证会延后；关闭系统网页后，本机采集停止。</small>
    {state.status==='not_installed'||state.status==='reload_extension'?<details><summary>首次使用：安装一次 Chrome / Edge 采集助手</summary><ol><li><a href={`${import.meta.env.BASE_URL}downloads/takealot-browser-collector.zip?v=1.0.2`} download>下载采集助手 v1.0.2</a>并解压。</li><li>打开浏览器“扩展管理”，开启开发者模式，选择“加载已解压的扩展”，选中解压后的文件夹。</li><li>刷新本页。若 Takealot 页面要求验证，按页面提示完成，并设置南非配送地址；之后复用当前浏览器会话。</li></ol></details>:null}
  </section>;
}
