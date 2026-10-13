(() => {
  if (location.origin !== 'https://urban-electronic.github.io' || !location.pathname.startsWith('/takealot-selector/')) return;
  let last = '';
  const attach = async () => {
    const enabled = localStorage.getItem('browser_auto_collect') !== 'false';
    const apiKey = localStorage.getItem('api_key') || '';
    const store = localStorage.getItem('active_store_id') || 'default-store';
    try {
      const response = await chrome.runtime.sendMessage({type:'ATTACH',enabled,apiKey,store});
      const status = response?.status || 'unknown';
      if (status !== last) last = status;
      window.postMessage({source:'takealot-browser-collector',...response},location.origin);
    } catch {
      window.postMessage({source:'takealot-browser-collector',status:'reload_extension',message:'扩展已更新，请刷新系统网页'},location.origin);
    }
  };
  window.addEventListener('message',event => {
    if (event.source === window && event.origin === location.origin && event.data?.source === 'takealot-browser-ui' && event.data.type === 'PING') attach();
  });
  attach(); setInterval(attach,15000);
})();
