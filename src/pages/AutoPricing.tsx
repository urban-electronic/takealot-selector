import { useEffect, useState } from 'react';
import { getAutomaticPricing, configureAutomaticPricing, pauseAutomaticPricing, reconcileAutomaticPricing, bulkAutomaticPricing, pauseAllAutomaticPricing, configureBuyboxMonitor, collectBuyboxNow, bulkBuyboxMonitor } from '../remoteApi';
export default function AutoPricing() {
  const buyboxOnly = false;
  const [rows, setRows] = useState<any[]>([]); const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [interval, setIntervalMinutes] = useState(5);
  const [loaded, setLoaded] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [collectorSource, setCollectorSource] = useState('cloud_browser');
  const [segment, setSegment] = useState('all'); const [segmentName, setSegmentName] = useState('');
  const [selected, setSelected] = useState<string[]>([]); const [group, setGroup] = useState('all'); const [groupName, setGroupName] = useState('');
  useEffect(() => { setPage(1); }, [search, group, segment]);
  const key = (r: any) => `${r.product_id}:${r.offer_id}`;
  const refresh = () => getAutomaticPricing().then(d => { setRows(d.rows.filter((r: any) => typeof r.stock === 'number' && r.stock > 0)); setConfigured(d.configured); setCollectorSource(d.collector_source || 'cloud_browser'); setLoaded(true); setError(''); });
  useEffect(() => { refresh().catch(e => setError(e.message)); const t = setInterval(() => refresh().catch(e => setError(e.message)), 30000); return () => clearInterval(t); }, []);
  const act = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'reconcile') await reconcileAutomaticPricing(r.product_id, r.offer_id);
      else if (action === 'pause') await pauseAutomaticPricing(r.product_id, r.offer_id);
      else {
        const draft = { ...r.rule, ...drafts[key(r)] };
        const useSystem = drafts[key(r)]?.use_system_min ?? r.rule.use_system_min ?? true;
        const min = useSystem ? r.suggested_min : Number(draft.min_price);
        if (!min || !Number.isInteger(min)) throw new Error('系统底价缺失，请补全成本或手工输入该SKU最低价。');
        if (draft.max_price && !Number.isInteger(Number(draft.max_price))) throw new Error('最高价必须为整数，也可留空使用智能上限。');
        await configureAutomaticPricing(r.product_id, r.offer_id, { min_price: useSystem ? null : min, use_system_min: useSystem, smart_raise: (draft.strategy || 'takealot_buybox') === 'takealot_buybox', max_price: draft.max_price ? Number(draft.max_price) : null, strategy: draft.strategy || 'takealot_buybox', lead_amount: 1, cooldown_minutes: 10, max_daily_changes: 10, enabled: action === 'start' ? true : !!r.rule.enabled });
        setDrafts(prev => { const next = { ...prev }; delete next[key(r)]; return next; });
      }
      await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const emergencyStop = async () => { setBusy('all'); try { await pauseAllAutomaticPricing(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } };
  const bulk = async (action: string) => {
    if (!selected.length) { setError('请先勾选需要操作的SKU。'); return; }
    setBusy('bulk'); setError('');
    try {
      const chosen = rows.filter(r => selected.includes(key(r)));
      await bulkAutomaticPricing({ action, group: groupName, items: chosen.map(r => ({ product_id: r.product_id, offer_id: r.offer_id })) });
      await refresh(); setSelected([]);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitor = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'once') await collectBuyboxNow(r.product_id,r.offer_id);
      else await configureBuyboxMonitor(r.product_id,r.offer_id,{enabled:action==='start',interval_minutes:interval});
      await refresh();
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitorBulk = async (enabled: boolean, currentPage = false) => {
    setBusy('monitor'); setError('');
    try {
      await bulkBuyboxMonitor({enabled,interval_minutes:interval,segment:enabled?(segmentName.trim()||`第${activePage}段`):'',items:(currentPage?pageRows:rows.filter(r=>selected.includes(key(r)))).map(r=>({product_id:r.product_id,offer_id:r.offer_id}))});
      await refresh();
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const visible = rows.filter(r => typeof r.stock === 'number' && r.stock > 0 && (group === 'all' || r.group === group) && (segment==='all' || (r.monitor?.segment||'未分段')===segment) && `${r.name} ${r.sku}`.toLowerCase().includes(search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(visible.length / 10)); const activePage = Math.min(page, pages);
  const pageRows = visible.slice((activePage-1)*10, activePage*10);
  const input = (r: any, field: string, label: string) => <input type="number" min="1" step="1" aria-label={`${r.sku}${label}`} placeholder={field === 'min_price' ? `自动 ${r.suggested_min ?? '待补'}` : '智能上限'} style={{ width: 86 }} value={drafts[key(r)]?.[field] ?? (field === 'min_price' && r.rule.use_system_min !== false ? r.suggested_min ?? '' : r.rule[field] ?? '')} onChange={e => setDrafts({ ...drafts, [key(r)]: { ...drafts[key(r)], [field]: e.target.value, ...(field === 'min_price' ? { use_system_min: e.target.value === '' } : {}) } })} />;
  const relative = (v: string) => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
  const market = (r: any) => buyboxOnly ? (r.monitor||{}) : r.monitor?.last_success_at && (!r.rule.last_evaluated_at || r.monitor.last_success_at > r.rule.last_evaluated_at) ? r.monitor : r.rule;
  const collectionStatus = (r:any) => {
    const m=r.monitor || {};
    return m.status==='error' ? ['danger','采集失败'] : m.status==='running' ? ['info','采集中'] : m.status==='queued' ? ['info','排队中'] : m.enabled ? ['success','自动采集'] : m.last_success_at ? ['neutral','手动采集'] : ['neutral','未开启'];
  };
  return <div className="operations-workspace pricing-workspace">
    <div className="dashboard-heading"><div><h1>Buybox与自动调价</h1><p>共享商品与行情 · 采集和调价分别调度、分别开关</p></div><button className="btn btn-outline" onClick={()=>refresh().catch(e=>setError(e.message))}>刷新列表</button></div>
    <div className="operations-note">本店售价/库存：卖家API · Buybox/竞品：{collectorSource==='local_collector'?'原本机采集器已连接':'云端浏览器（原本机采集器未连接）'}</div>
    <div className="pricing-summary">{true&&<div className="card"><small>自动采集</small><strong className="text-info">{loaded ? rows.filter(r=>r.monitor?.enabled).length : '—'}<span> / {rows.length} SKU</span></strong></div>}{!buyboxOnly&&<div className="card"><small>实际调价运行中</small><strong className="text-success">{loaded ? rows.filter(r=>r.rule.enabled).length : '—'}<span> SKU</span></strong></div>}{true&&<div className="card"><small>采集失败</small><strong className="text-danger">{loaded ? rows.filter(r=>r.monitor?.status==='error').length : '—'}<span> SKU</span></strong></div>}{!buyboxOnly&&<div className="pricing-stop"><button className="btn btn-danger" disabled={!!busy||!rows.some(r=>r.rule.enabled)} onClick={emergencyStop}>停止全部调价</button></div>}</div>
    {error&&<div role="alert" className="alert alert-error">{error}</div>}
    {loaded&&!configured&&<div className="alert">卖家接口尚未配置，采集及调价暂不可开启。</div>}
    <div className="card operations-table-card">
      <div className="pricing-toolbar"><input aria-label="搜索调价商品" placeholder="商品名称 / SKU" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="SKU分类筛选" value={group} onChange={e=>{setGroup(e.target.value);setSelected([]);}}><option value="all">全部分类</option>{[...new Set(rows.map(r=>r.group))].map(g=><option key={g} value={g}>{g}</option>)}</select>{true&&<><select aria-label="采集分段筛选" value={segment} onChange={e=>{setSegment(e.target.value);setSelected([]);}}><option value="all">全部分段</option>{[...new Set(rows.map(r=>r.monitor?.segment||'未分段'))].map(s=><option key={s} value={s}>{s}</option>)}</select><input placeholder="本段名称（可选）" value={segmentName} onChange={e=>setSegmentName(e.target.value)}/></>}<span className="selection-count">已选 {selected.length} SKU</span></div>
      <div className="pricing-actions">{true&&<div><span>Buybox采集</span><select aria-label="自动采集间隔" value={interval} onChange={e=>setIntervalMinutes(Number(e.target.value))}>{[5,10,15,30,60,120].map(n=><option key={n} value={n}>{n}分钟</option>)}</select><button className="btn btn-info" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(true)}>开启选中采集</button><button className="btn btn-info" disabled={!!busy||!pageRows.length||!configured} onClick={()=>monitorBulk(true,true)}>开启本段 {pageRows.length} 个</button><button className="btn btn-outline" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(false)}>停采</button></div>}{!buyboxOnly&&<div><span>实际调价</span><button className="btn btn-success" disabled={!!busy||!selected.length||!configured} onClick={()=>bulk('start')}>启动选中调价</button><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('pause')}>暂停</button><input placeholder="分类名称" value={groupName} onChange={e=>setGroupName(e.target.value)} /><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('group')}>归类</button></div>}</div>
      <div className="operations-note">{buyboxOnly?'默认每段10个SKU，可分别开启、暂停。5分钟为正常采集的目标间隔，队列或网站限制会延后；失败时显示具体原因与下次尝试时间，旧报价带时间保留。采集开关不会开启实际调价。':'采集默认5分钟，可分段开启；调价按自身周期读取最新行情。两个开关互不启停，失败或行情过期时保持现价。'}</div>
      <div className="operations-table-scroll"><table className="data-table pricing-table"><thead><tr><th><input type="checkbox" aria-label="勾选当前筛选的全部SKU" checked={visible.length>0&&visible.every(r=>selected.includes(key(r)))} onChange={e=>setSelected(e.target.checked?visible.map(key):[])} /></th><th>商品 / 本店售价</th><th>Buybox / 现货竞品</th><th>采集状态 / 频率</th><th>最低 / 最高价 R</th><th>调价状态 / 策略</th><th>库存</th><th>最近结果</th>{!buyboxOnly&&<th>操作</th>}</tr></thead><tbody>{pageRows.map(r=>{
        const m=market(r); const [tone,label]=collectionStatus(r); const hasQuote=m.buybox_price!=null;
        return <tr key={key(r)}><td><input type="checkbox" aria-label={`选择SKU ${r.sku}`} checked={selected.includes(key(r))} onChange={e=>setSelected(e.target.checked?[...selected,key(r)]:selected.filter(k=>k!==key(r)))} /></td>
          <td className="operations-product"><a className="product-link" title={r.name} href={r.url} target="_blank" rel="noreferrer">{r.name}</a><small>{r.sku} · {r.group}</small><b className="own-price">R {r.price??'—'}</b></td>
          <td><strong className="quote-price">R {m.buybox_price??'—'}</strong><span className={`status-pill tone-${!hasQuote?'neutral':m.holding_buybox===true?'success':m.holding_buybox===false||m.price_matches_buybox?'warning':'neutral'}`}>{!hasQuote?'未采集':m.holding_buybox===true?'持有':m.holding_buybox===false?'未持有':m.price_matches_buybox?'同价 · 待确认':'待核对卖家'}</span><small>竞品 R {m.competitor_price??'—'}</small></td>
          {true&&<td className="collection-status-cell"><span className={`status-pill tone-${tone}`}>{label}</span><small>{relative(r.monitor?.last_success_at)}</small>{r.monitor?.status==='error'&&<small className="text-danger" title={r.monitor.error}>{r.monitor.last_success_at?'旧报价 · 待重试':'本轮未获取报价'}</small>}<div className="row-buttons"><button className="btn btn-outline btn-sm" disabled={!!busy||!configured} onClick={()=>monitor(r,'once')}>立即采集</button><button className="btn btn-outline btn-sm" disabled={!!busy||!configured} onClick={()=>monitor(r,r.monitor?.enabled?'stop':'start')}>{r.monitor?.enabled?'停采':'自动'}</button></div>{r.monitor?.error&&<small className="text-danger">{r.monitor.error}</small>}<small>下次 {r.monitor?.enabled&&r.monitor?.next_due?relative(new Date(r.monitor.next_due*1000).toISOString()):'—'}</small><small>{r.monitor?.interval_minutes||5}分钟 · {r.monitor?.segment||'未分段'}</small></td>}
          {!buyboxOnly&&<><td><div className="price-range">{input(r,'min_price','最低')}{input(r,'max_price','最高')}</div><small>{(drafts[key(r)]?.use_system_min??r.rule.use_system_min??true)?'系统利润底价':'手动底价'} · 最高可留空</small><button className="btn btn-outline btn-sm" disabled={!!busy} onClick={()=>act(r,'save')}>保存价格区间</button></td>
          <td><span className={`status-pill tone-${r.rule.write_pending?'warning':r.rule.enabled?'success':'neutral'}`}>{r.rule.write_pending?'待核验':r.rule.enabled?'调价运行中':'调价关闭'}</span><select aria-label={`${r.sku}调价策略`} value={drafts[key(r)]?.strategy??r.rule.strategy??'takealot_buybox'} onChange={e=>setDrafts({...drafts,[key(r)]:{...drafts[key(r)],strategy:e.target.value}})}><option value="takealot_buybox">Buybox / 智能试涨</option><option value="follow_lowest">跟随现货最低价</option></select></td></>}
          <td><span className={`stock-count ${r.stock===0?'text-danger':''}`}>{r.stock??'—'}</span>{r.stock===0&&<small>缺货</small>}</td>
          <td className="pricing-result" title={r.monitor?.error||r.rule.last_result}>{buyboxOnly?<><span className={r.monitor?.status==='error'?'text-danger':''}>{r.monitor?.error|| (r.monitor?.last_success_at?'采集成功':'未开始采集')}</span><small>尝试 {relative(r.monitor?.last_attempt_at)}</small><small>下次 {r.monitor?.enabled&&r.monitor?.next_due?relative(new Date(r.monitor.next_due*1000).toISOString()):'—'}</small>{r.monitor?.diagnostics&&<small title={JSON.stringify(r.monitor.diagnostics)}>HTTP {r.monitor.diagnostics.http_status??'—'} · {r.monitor.error_code||'正常'}</small>}</>:<><span>{r.rule.last_result||'未启动实际调价'}</span><small>改价 {relative(r.rule.last_changed_at)}</small></>}</td>
          {!buyboxOnly&&<td><button className={`btn ${r.rule.enabled?'btn-outline':'btn-success'}`} disabled={!!busy||!configured} onClick={()=>act(r,r.rule.write_pending?'reconcile':r.rule.enabled?'pause':'start')}>{busy===key(r)?'处理中…':r.rule.write_pending?'核验现价':r.rule.enabled?'暂停调价':'启动调价'}</button></td>}</tr>;
      })}</tbody></table></div>
      <div className="operations-pagination"><span>{visible.length} 个SKU · 每段10条</span><button className="btn btn-outline btn-sm" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>上一页</button><span>{activePage} / {pages}</span><button className="btn btn-outline btn-sm" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>下一页</button></div>
      {loaded&&!rows.length&&<p className="operations-empty">暂无本店有库存的SKU，请先同步库存。</p>}
    </div>
    {!buyboxOnly&&<details className="operations-explanation"><summary>查看 PricePilot 调价与试涨规则</summary><p>最低价默认使用系统20%利润底价。未持有时只降不升；持有时可小步回弹。确认竞品非现货时约3%试涨，12小时观察一次，累计不超过启用参考价20%；48小时无新成交时回退。采集失败保持现价，改价结果未确认时自动暂停。</p></details>}
  </div>;
}
