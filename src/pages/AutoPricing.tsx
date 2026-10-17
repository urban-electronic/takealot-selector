import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
const CloudBrowser = lazy(() => import('../operations/CloudBrowser'));
import { getAutomaticPricing, configureAutomaticPricing, pauseAutomaticPricing, reconcileAutomaticPricing, bulkAutomaticPricing, pauseAllAutomaticPricing, configureBuyboxMonitor, collectBuyboxNow, bulkBuyboxMonitor } from '../remoteApi';
export default function AutoPricing() {
  const buyboxOnly = false;
  const [rows, setRows] = useState<any[]>([]); const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [maintenance, setMaintenance] = useState(false);
  const [interval, setIntervalMinutes] = useState(5);
  const [loaded, setLoaded] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [collectorSource, setCollectorSource] = useState('cloud_browser');
  const [refreshing, setRefreshing] = useState(false);
  const refreshState = useRef<{ active: boolean; sequence: number; pending: Promise<void> | null }>({ active: false, sequence: 0, pending: null });
  const [segment, setSegment] = useState('all'); const [segmentName, setSegmentName] = useState('');
  const [selected, setSelected] = useState<string[]>([]); const [group, setGroup] = useState('all'); const [groupName, setGroupName] = useState('');
  useEffect(() => { setPage(1); }, [search, group, segment]);
  const key = (r: any) => `${r.product_id}:${r.offer_id}`;
  const refresh = useCallback((afterMutation = false): Promise<void> => {
    const state = refreshState.current;
    if (!state.active) return Promise.resolve();
    // Ordinary reads share one request; a completed write needs a newer snapshot.
    if (state.pending && !afterMutation) return state.pending;
    const sequence = ++state.sequence;
    const isCurrent = () => state.active && sequence === state.sequence;
    setRefreshing(true);
    const pending = getAutomaticPricing().then(d => {
      if (!isCurrent()) return;
      setRows(d.rows.filter((r: any) => typeof r.stock === 'number' && r.stock > 0));
      setConfigured(d.configured); setCollectorSource(d.collector_source || 'cloud_browser'); setLoaded(true); setError('');
    }).catch(e => {
      if (isCurrent()) setError(e.message);
    }).finally(() => {
      if (isCurrent()) { state.pending = null; setRefreshing(false); }
    });
    state.pending = pending;
    return pending;
  }, []);
  useEffect(() => {
    const state = refreshState.current;
    state.active = true;
    void refresh();
    const t = setInterval(() => { void refresh(); }, 30000);
    return () => { clearInterval(t); state.active = false; state.sequence++; state.pending = null; };
  }, [refresh]);
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
      await refresh(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const emergencyStop = async () => { setBusy('all'); try { await pauseAllAutomaticPricing(); await refresh(true); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } };
  const bulk = async (action: string) => {
    if (!selected.length) { setError('请先勾选需要操作的SKU。'); return; }
    setBusy('bulk'); setError('');
    try {
      const chosen = rows.filter(r => selected.includes(key(r)));
      await bulkAutomaticPricing({ action, group: groupName, items: chosen.map(r => ({ product_id: r.product_id, offer_id: r.offer_id })) });
      await refresh(true); setSelected([]);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitor = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'once') await collectBuyboxNow(r.product_id,r.offer_id);
      else await configureBuyboxMonitor(r.product_id,r.offer_id,{enabled:action==='start',interval_minutes:interval});
      await refresh(true);
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitorBulk = async (enabled: boolean, currentPage = false) => {
    setBusy('monitor'); setError('');
    try {
      await bulkBuyboxMonitor({enabled,interval_minutes:interval,segment:enabled?(segmentName.trim()||`第${activePage}段`):'',items:(currentPage?pageRows:rows.filter(r=>selected.includes(key(r)))).map(r=>({product_id:r.product_id,offer_id:r.offer_id}))});
      await refresh(true);
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const visible = rows.filter(r => typeof r.stock === 'number' && r.stock > 0 && (group === 'all' || r.group === group) && (segment==='all' || (r.monitor?.segment||'未分段')===segment) && `${r.chinese_name || ''} ${r.name} ${r.sku}`.toLowerCase().includes(search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(visible.length / 10)); const activePage = Math.min(page, pages);
  const pageRows = visible.slice((activePage-1)*10, activePage*10);
  const input = (r: any, field: string, label: string) => <input type="number" min="1" step="1" aria-label={`${r.sku}${label}`} placeholder={field === 'min_price' ? `自动 ${r.suggested_min ?? '待补'}` : '智能上限'} style={{ width: 86 }} value={drafts[key(r)]?.[field] ?? (field === 'min_price' && r.rule.use_system_min !== false ? r.suggested_min ?? '' : r.rule[field] ?? '')} onChange={e => setDrafts({ ...drafts, [key(r)]: { ...drafts[key(r)], [field]: e.target.value, ...(field === 'min_price' ? { use_system_min: e.target.value === '' } : {}) } })} />;
  const relative = (v: string) => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
  const priceEstimate = (m:any) => ({matched:'同价 · 推定持有',mismatch_pending:'推定持有 · 失去复核1/2',mismatch:'异价 · 推定未持有',checking:'本店售价待复核',verification_failed:'本店售价复核未完成'} as Record<string,string>)[m.price_ownership?.state] || (m.price_matches_buybox ? '历史同价 · 推定持有' : m.holding_buybox===false ? '历史异价 · 推定未持有' : '');
  const market = (r: any) => buyboxOnly ? (r.monitor||{}) : r.monitor?.last_success_at && (!r.rule.last_evaluated_at || r.monitor.last_success_at > r.rule.last_evaluated_at) ? r.monitor : r.rule;
  const collectionStatus = (r:any) => {
    const m=r.monitor || {};
    if (m.status==='error') return ['danger','采集失败'];
    if (m.status==='paused') return ['neutral','已停采'];
    if (m.status==='running') return ['info','采集中'];
    if (m.status==='queued') return m.next_due>Date.now()/1000 ? ['neutral','等待下轮'] : ['info','排队中'];
    return m.enabled ? ['success','自动采集'] : m.last_success_at ? ['neutral','手动采集'] : ['neutral','未开启'];
  };
  const waitingDetail = (r:any) => {
    const m=r.monitor || {};
    if (m.status!=='queued') return '';
    if (m.next_due>Date.now()/1000) return '尚未到计划时间，无需重复采集。';
    return '任务已提交，等待采集器领取；尚未收到本轮完成结果。持续等待时，请检查采集器运行日志或联系维护人员，勿反复提交。';
  };
  return <div className="operations-workspace pricing-workspace">
    <div className="dashboard-heading"><div><h1>Buybox与自动调价</h1><p>共享商品与行情 · 采集和调价分别调度、分别开关</p></div><button className="btn btn-outline" disabled={refreshing} aria-busy={refreshing} onClick={()=>{void refresh();}}>刷新列表</button></div>
    <div className="operations-note">本店售价/库存：卖家API · Buybox/竞品：{collectorSource==='browser_extension'?'浏览器采集器心跳有效':collectorSource==='local_collector'?'本机采集器心跳有效':'后台采集'}</div>
    <div className="pricing-summary">{true&&<div className="card"><small>自动采集</small><strong className="text-info">{loaded ? rows.filter(r=>r.monitor?.enabled).length : '—'}<span> / {rows.length} SKU</span></strong></div>}{!buyboxOnly&&<div className="card"><small>实际调价运行中</small><strong className="text-success">{loaded ? rows.filter(r=>r.rule.enabled).length : '—'}<span> SKU</span></strong></div>}{true&&<div className="card"><small>采集失败</small><strong className="text-danger">{loaded ? rows.filter(r=>r.monitor?.status==='error').length : '—'}<span> SKU</span></strong></div>}{!buyboxOnly&&<div className="pricing-stop"><button className="btn btn-danger" disabled={!!busy||!rows.some(r=>r.rule.enabled)} onClick={emergencyStop}>停止全部调价</button></div>}</div>
    {error&&<div role="alert" className="alert alert-error">{error}</div>}
    {loaded&&!configured&&<div className="alert">卖家接口尚未配置，采集及调价暂不可开启。</div>}
    <div className="card operations-table-card">
      <div className="pricing-toolbar"><input aria-label="搜索调价商品" placeholder="商品名称 / SKU" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="SKU分类筛选" value={group} onChange={e=>{setGroup(e.target.value);setSelected([]);}}><option value="all">全部分类</option>{[...new Set(rows.map(r=>r.group))].map(g=><option key={g} value={g}>{g}</option>)}</select>{true&&<><select aria-label="采集分段筛选" value={segment} onChange={e=>{setSegment(e.target.value);setSelected([]);}}><option value="all">全部分段</option>{[...new Set(rows.map(r=>r.monitor?.segment||'未分段'))].map(s=><option key={s} value={s}>{s}</option>)}</select><input placeholder="本段名称（可选）" value={segmentName} onChange={e=>setSegmentName(e.target.value)}/></>}<span className="selection-count">已选 {selected.length} SKU</span></div>
      <div className="pricing-actions">{true&&<div><span>Buybox采集</span><select aria-label="自动采集间隔" value={interval} onChange={e=>setIntervalMinutes(Number(e.target.value))}>{[5,10,15,30,60,120].map(n=><option key={n} value={n}>{n}分钟</option>)}</select><button className="btn btn-info" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(true)}>开启选中采集</button><button className="btn btn-info" disabled={!!busy||!pageRows.length||!configured} onClick={()=>monitorBulk(true,true)}>开启本段 {pageRows.length} 个</button><button className="btn btn-outline" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(false)}>停采</button></div>}{!buyboxOnly&&<div><span>实际调价</span><button className="btn btn-success" disabled={!!busy||!selected.length||!configured} onClick={()=>bulk('start')}>启动选中调价</button><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('pause')}>暂停</button><input placeholder="分类名称" value={groupName} onChange={e=>setGroupName(e.target.value)} /><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('group')}>归类</button></div>}</div>
      <div className="operations-note">{buyboxOnly?'默认每段10个SKU，可分别开启、暂停。5分钟为正常采集的目标间隔，队列或网站限制会延后；失败时显示具体原因与下次尝试时间，旧报价带时间保留。采集开关不会开启实际调价。':'采集和调价独立启停。'}</div>
      <div className="operations-table-scroll"><table className="data-table pricing-table"><thead><tr><th><input type="checkbox" aria-label="勾选当前筛选的全部SKU" checked={visible.length>0&&visible.every(r=>selected.includes(key(r)))} onChange={e=>setSelected(e.target.checked?visible.map(key):[])} /></th><th>中文名 / 英文名 / 本店售价</th><th>Buybox / 现货竞品</th><th>采集状态 / 频率</th><th>最低 / 最高价 R</th><th>调价状态 / 策略</th><th>库存</th><th>调价结果</th>{!buyboxOnly&&<th>操作</th>}</tr></thead><tbody>{pageRows.map(r=>{
        const m=market(r); const [tone,label]=collectionStatus(r); const hasQuote=m.buybox_price!=null;
        return <tr key={key(r)}><td><input type="checkbox" aria-label={`选择SKU ${r.sku}`} checked={selected.includes(key(r))} onChange={e=>setSelected(e.target.checked?[...selected,key(r)]:selected.filter(k=>k!==key(r)))} /></td>
          <td className="operations-product"><a className="product-link" title={r.name} href={r.url} target="_blank" rel="noreferrer">{r.chinese_name || '中文名待核对'}</a><small className="product-english-name" title={r.name}>英文名：{r.name || '未记录'}</small>{!r.chinese_name&&<small className="text-warning">{r.name_status==='invalid'?'中文字段为非中文，请修正':r.name_status==='ambiguous'?'SKU与中文名对应不明确':'中文名待补充'}</small>}<small>{r.sku} · {r.group}</small><b className="own-price">R {r.price??'—'}</b></td>
          <td><strong className="quote-price">R {m.buybox_price??'—'}</strong><span title="按原程序的价格比较与连续两轮失去确认推定；同价不证明卖家身份，不作为已验证持有触发调价。" className={`status-pill tone-${!hasQuote?'neutral':m.holding_buybox===true?'success':m.holding_buybox===false||m.price_matches_buybox||priceEstimate(m)?'warning':'neutral'}`}>{!hasQuote?'未采集':m.holding_buybox===true?'持有':priceEstimate(m)||(m.holding_buybox===false?'未持有':m.price_matches_buybox?'同价 · 卖家未核实':'待核对卖家')}</span><details className="row-details"><summary>报价详情</summary>{m.price_ownership?.own_price!=null&&<small>对照本店价：R {m.price_ownership.own_price} · {m.price_ownership.own_price_source==='official_live'?'实时API':m.price_ownership.own_price_source==='official_snapshot'?'官方同步快照':'任务售价快照'}<br/>对照时点：{relative(m.price_ownership.own_price_asof)}</small>}<small>卖家：{m.snapshot?.buyboxSeller||'未读取'}</small>{hasQuote&&<small>报价来源：{m.source==='cloud_browser'?'后台采集':m.source==='local_collector'?'本机采集':m.source==='browser_extension'?'浏览器扩展':m.source==='pricepilot_relay'?'原程序转发':'未记录'}</small>}{hasQuote&&['queued','running','error','paused'].includes(r.monitor?.status)&&<small>上次有效报价 · 非本轮结果</small>}</details>{m.competitor_price!=null&&<small>竞品 R {m.competitor_price}</small>}</td>
          {true&&<td className="collection-status-cell"><span className={`status-pill tone-${tone}`}>{label}</span><small>最近成功 {relative(r.monitor?.last_success_at)}</small>{waitingDetail(r)&&<details className="row-details"><summary>等待详情</summary><small role="status">{waitingDetail(r)}</small></details>}{r.monitor?.status==='error'&&<small className="text-danger" title={r.monitor.error}>{r.monitor.last_success_at?'旧报价 · 待重试':'本轮未获取报价'}</small>}<div className="row-buttons"><button className="btn btn-outline btn-sm" disabled={!!busy||!configured||['queued','running'].includes(r.monitor?.status)} onClick={()=>monitor(r,'once')}>立即采集</button><button className="btn btn-outline btn-sm" disabled={!!busy||!configured} onClick={()=>monitor(r,r.monitor?.enabled?'stop':'start')}>{r.monitor?.enabled?'停采':'自动'}</button></div>{r.monitor?.error&&<details><summary className="text-danger">{r.monitor.error_code==='site_challenge'?'网站验证阻挡':'失败详情'}</summary><small>{r.monitor.error}</small><small>最近尝试 {relative(r.monitor.last_attempt_at)}</small></details>}<small>下次采集 {(r.monitor?.enabled||r.monitor?.request_once)&&r.monitor?.next_due?relative(new Date(r.monitor.next_due*1000).toISOString()):'—'}</small><small>{r.monitor?.interval_minutes||5}分钟 · {r.monitor?.segment||'未分段'}</small></td>}
          {!buyboxOnly&&<><td><div className="price-range">{input(r,'min_price','最低')}{input(r,'max_price','最高')}</div><small>{(drafts[key(r)]?.use_system_min??r.rule.use_system_min??true)?'系统利润底价':'手动底价'} · 最高可留空</small><button className="btn btn-outline btn-sm" disabled={!!busy} onClick={()=>act(r,'save')}>保存价格区间</button></td>
          <td><span className={`status-pill tone-${r.rule.write_pending?'warning':r.rule.enabled?'success':'neutral'}`}>{r.rule.write_pending?'待核验':r.rule.enabled?'调价运行中':'调价关闭'}</span><select aria-label={`${r.sku}调价策略`} value={drafts[key(r)]?.strategy??r.rule.strategy??'takealot_buybox'} onChange={e=>setDrafts({...drafts,[key(r)]:{...drafts[key(r)],strategy:e.target.value}})}><option value="takealot_buybox">Buybox / 智能试涨</option><option value="follow_lowest">跟随现货最低价</option></select></td></>}
          <td><span className={`stock-count ${r.stock===0?'text-danger':''}`}>{r.stock??'—'}</span>{r.stock===0&&<small>缺货</small>}</td>
          <td className="pricing-result" title={r.monitor?.error||r.rule.last_result}><details className="row-details"><summary>采集记录</summary><span className={r.monitor?.status==='error'?'text-danger':''}>采集：{r.monitor?.status==='error'?'本轮失败':r.monitor?.status==='queued'?label:r.monitor?.status==='running'?'进行中':r.monitor?.last_success_at?'已有成功记录':'未开始'}</span></details>{!buyboxOnly&&<><small>调价：{r.rule.last_result||'未启动'}</small>{r.rule.last_changed_at&&<small>改价 {relative(r.rule.last_changed_at)}</small>}</>}</td>
          {!buyboxOnly&&<td><button className={`btn ${r.rule.enabled?'btn-outline':'btn-success'}`} disabled={!!busy||!configured} onClick={()=>act(r,r.rule.write_pending?'reconcile':r.rule.enabled?'pause':'start')}>{busy===key(r)?'处理中…':r.rule.write_pending?'核验现价':r.rule.enabled?'暂停调价':'启动调价'}</button></td>}</tr>;
      })}</tbody></table></div>
      <div className="operations-pagination"><span>{visible.length} 个SKU · 每段10条</span><button className="btn btn-outline btn-sm" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>上一页</button><span>{activePage} / {pages}</span><button className="btn btn-outline btn-sm" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>下一页</button></div>
      {loaded&&!rows.length&&<p className="operations-empty">暂无本店有库存的SKU，请先同步库存。</p>}
    </div>
    <details className="operations-explanation" onToggle={e=>setMaintenance(e.currentTarget.open)}><summary>采集维护（高级）</summary><p>日常采集由服务器后台执行。仅排查网站验证或配送设置时打开；关闭此区域会释放操作会话。</p>{maintenance&&<Suspense fallback={<p>正在加载维护工具…</p>}><CloudBrowser rows={rows} onCollected={()=>{void refresh(true);}} /></Suspense>}</details>
    {!buyboxOnly&&<details className="operations-explanation"><summary>查看 PricePilot 调价与试涨规则</summary><p>最低价默认使用系统20%利润底价。与Buybox同价但卖家身份未核实则保持现价；明确核实持有后才评估试涨。未持有时只降不升。确认竞品非现货时约3%试涨，12小时观察一次，累计不超过启用参考价20%；48小时无新成交时回退。采集失败保持现价，改价结果未确认时自动暂停。</p></details>}
  </div>;
}
