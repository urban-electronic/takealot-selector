import { useEffect, useState } from 'react';
import { getOperationsOverview, getProduct } from '../remoteApi';
import PipelinePlan from '../operations/PipelinePlan';
import type { Product } from '../types';

const levels: Record<string,string> = { out_of_stock: '断货', urgent: '紧急', watch: '关注', healthy: '健康', slow_moving: '滞销', no_data: '观察中' };
export default function Replenishment() {
  const [overview, setOverview] = useState<any>(null);
  const [current, setCurrent] = useState<Product | null>(null);
  const [selected, setSelected] = useState(''); const [panelError, setPanelError] = useState('');
  const [onlyAction, setOnlyAction] = useState(false); const [hideEmpty, setHideEmpty] = useState(true);
  const [level, setLevel] = useState('all'); const [search, setSearch] = useState('');
  const [view, setView] = useState('products'); const [mode, setMode] = useState('all');
  const [page, setPage] = useState(1); const [error, setError] = useState('');
  const refresh = async () => { setOverview(await getOperationsOverview()); setError(''); };
  useEffect(() => { refresh().catch(e => setError(e.message)); const t = setInterval(() => refresh().catch(e => setError(e.message)), 60000); return () => clearInterval(t); }, []);
  useEffect(() => { setPage(1); }, [search, level, mode, view, onlyAction, hideEmpty]);
  useEffect(() => {
    if (!selected) return;
    let active = true; setCurrent(null); setPanelError('');
    getProduct(selected).then(p => active && setCurrent(p)).catch(e => active && setPanelError(e.message));
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelected(''); };
    document.addEventListener('keydown', escape); const old = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { active = false; document.removeEventListener('keydown', escape); document.body.style.overflow = old; };
  }, [selected]);
  const all: any[] = overview?.rows || [];
  const rows = all.filter(r => (!onlyAction || r.plan?.air_quantity > 0 || r.plan?.sea_quantity > 0 || r.plan?.blocked?.length) && (!hideEmpty || r.plan?.velocity > 0) && (level === 'all' || r.plan?.level === level) && `${r.name} ${r.sku} ${r.product_no}`.toLowerCase().includes(search.toLowerCase()));
  const schedule = rows.flatMap(r => (r.plan?.schedule || []).filter((s: any) => mode === 'all' || s.mode === mode).map((s: any) => ({ ...s, product: r }))).sort((a,b) => a.purchase_date.localeCompare(b.purchase_date) || a.mode.localeCompare(b.mode) || a.product.name.localeCompare(b.product.name));
  const dataset = view === 'schedule' ? schedule : rows;
  const pages = Math.max(1, Math.ceil(dataset.length / 25)); const activePage = Math.min(page, pages);
  const shown = dataset.slice((activePage-1)*25, activePage*25);
  const totals = all.reduce((a,r) => { a.air += r.plan?.air_quantity || 0; a.sea += r.plan?.sea_quantity || 0; a.future += r.plan?.total_sea_quantity || 0; return a; }, { air: 0, sea: 0, future: 0 });
  const share = totals.future + totals.air ? Math.round(totals.future / (totals.future+totals.air) * 100) : 0;
  const margin = (r: any, m: string) => r.plan?.economics?.available ? `R ${r.plan.economics[m].profit_zar} · ${(r.plan.economics[m].profit_margin*100).toFixed(0)}%` : '待核对成本';
  const qty = (r: any, m: string) => {
    const p = r.plan;
    if (!p) return '待匹配';
    if (m === 'air' && !p.allow_emergency_air) return '未开启';
    if (m === 'air' && !p.economics?.available) return '待核对成本';
    if (p.economics?.available && p.economics[m].profit_margin < p.economics.minimum_margin) return '利润不足';
    return p[m+'_quantity'] || (m === 'air' ? '无需再补' : '已覆盖');
  };
  const name = (r: any) => <><button className="product-link" title={r.name} onClick={() => setSelected(r.id)}>{r.name}</button><small className="row-subtitle">{r.sku || r.product_no}{r.plan?.stale ? ' · 库存待更新' : ''}{r.plan?.overdue?.length ? ' · 到货延期' : ''}</small></>;
  const exportRows = () => {
    const cell = (v: unknown) => '"'+String(v ?? '').replace(/^[=+@-]/,"'").replace(/"/g,'""')+'"';
    const values = view === 'schedule' ? [['商品','SKU','方式','采购日期','发运日期','预计到货','覆盖至','数量','性质','估算总经营成本ZAR'],...schedule.map(s => [s.product.name,s.product.sku,s.mode==='sea'?'海运':'空运',s.purchase_date,s.departure_date,s.arrival_date,s.coverage_until,s.quantity,s.conditional?'后续待复核':'当前建议',s.estimated_cost_zar])] : [['商品','SKU','库存','日均销量','可售天数','当前海运','限量应急空运','首批海运前缺口'],...rows.map(r=>[r.name,r.sku,r.plan?.stock,r.plan?.velocity,r.plan?.cover_days,r.plan?.sea_quantity,r.plan?.air_quantity,r.plan?.pre_sea_shortage])];
    const url=URL.createObjectURL(new Blob(['\ufeff'+values.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='补货发运计划.csv';a.click();URL.revokeObjectURL(url);
  };
  const close = () => { setSelected(''); refresh().catch(e=>setError(e.message)); };
  return <div className="operations-workspace">
    <div className="dashboard-heading"><div><h1>补货计划</h1><p>多批海运持续衔接 · 默认不开空运 · 计划随真实销量重算</p></div><button className="btn btn-outline" onClick={()=>refresh().catch(e=>setError(e.message))}>刷新</button></div>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="replenishment-metrics"><div className="card"><small>本次海运待补</small><strong>{overview ? totals.sea : '—'}<span> 件</span></strong></div><div className="card"><small>已开启的限量应急空运</small><strong>{overview ? totals.air : '—'}<span> 件</span></strong></div><div className="card"><small>展望计划海运占比 · 数量</small><strong>{overview ? share : '—'}<span> %</span></strong></div><div className="card"><small>断货 / 紧急商品</small><strong>{overview ? all.filter(r=>['urgent','out_of_stock'].includes(r.plan?.level)).length : '—'}<span> / {all.length}</span></strong></div></div>
    <div className="operations-status"><span className={`status-dot ${overview?.sync?.status==='success'?'active':''}`} />{overview?.sync?.status==='success'?`库存与销量已同步 · ${overview.sync.last_success}`:overview?.sync?.status==='running'?'订单与库存正在同步':overview?.sync?.error || '等待同步'}<span>每小时更新</span></div>
    <div className="card operations-table-card">
      <div className="operations-tabs"><button className={view==='products'?'selected':''} onClick={()=>setView('products')}>商品补货清单</button><button className={view==='schedule'?'selected':''} onClick={()=>setView('schedule')}>采购与发运日历</button><span>默认海运覆盖60天＋7天安全量，每30天复核</span></div>
      <div className="replenishment-toolbar"><input aria-label="搜索补货商品" placeholder="搜索商品 / SKU" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="补货分级" value={level} onChange={e=>setLevel(e.target.value)}><option value="all">全部分级</option>{Object.entries(levels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>{view==='schedule'&&<select aria-label="运输方式" value={mode} onChange={e=>setMode(e.target.value)}><option value="all">空运＋海运</option><option value="sea">海运</option><option value="air">空运</option></select>}<label><input type="checkbox" checked={onlyAction} onChange={e=>setOnlyAction(e.target.checked)} /> 当前需补</label><label><input type="checkbox" checked={hideEmpty} onChange={e=>setHideEmpty(e.target.checked)} /> 隐藏零销</label><button className="btn btn-outline btn-sm" disabled={!overview} onClick={exportRows}>导出计划</button></div>
      <div className="operations-note">海运约58天，按30天间隔滚动安排多批，提前到货持续衔接。默认不开空运；如按商品开启应急空运，默认最多补7天销量且须利润达标。首批海运前的真实缺口单独提示。后续批次须复核，尚未下单。</div>
      <div className="operations-table-scroll"><table className="data-table"><thead>{view==='schedule'?<tr><th>商品 / SKU</th><th>运输</th><th>采购 / 发运</th><th>预计可售到货</th><th>计划覆盖至</th><th>数量</th><th>单件利润</th><th>计划状态</th><th>操作</th></tr>:<tr><th>商品 / SKU</th><th>状态</th><th>现货 / 在途</th><th>日均 / 可售天数</th><th>本次海运 / 下一批</th><th>海运利润</th><th>首批前缺口</th><th>限量应急空运</th><th>操作</th></tr>}</thead><tbody>{shown.map((item:any)=>{const r=view==='schedule'?item.product:item;return <tr key={view==='schedule'?`${r.id}-${item.mode}-${item.purchase_date}`:r.id}><td className="operations-product">{name(r)}</td>{view==='schedule'?<><td><span className={`mode-pill ${item.mode}`}>{item.mode==='sea'?'海运':'空运'}</span></td><td>{item.purchase_date}<small className="row-subtitle">发运 {item.departure_date}</small></td><td>{item.arrival_date}</td><td>{item.coverage_until}</td><td><strong>{item.quantity}</strong> 件</td><td>{margin(r,item.mode)}</td><td><span className={`status-pill ${item.conditional?'':'active'}`}>{item.conditional?'后续复核':'当前建议'}</span>{r.plan?.provisional&&<small className="row-subtitle">短观测 / 旧快照参考</small>}{!r.plan?.economics?.available&&<small className="row-subtitle">利润待核对</small>}</td></>:<><td><span className={`status-pill ${r.plan?.level==='healthy'?'active':''}`}>{levels[r.plan?.level]||'待匹配'}</span></td><td>{r.plan?.stock??'—'}<small className="row-subtitle">在途 {(r.plan?.pending?.air||0)+(r.plan?.pending?.sea||0)}</small></td><td>{r.plan?.velocity??'—'} 件<small className="row-subtitle">可售 {r.plan?.cover_days??'—'} 天</small></td><td>{qty(r,'sea')}{r.plan?.schedule?.filter((b:any)=>b.mode==='sea'&&b.conditional).slice(0,1).map((b:any)=><small key={b.purchase_date} className="row-subtitle">{b.purchase_date} 复核 {b.quantity} 件</small>)}</td><td>{margin(r,'sea')}</td><td>{!r.plan?'待数据':r.plan.pre_sea_shortage>0?`约 ${r.plan.pre_sea_shortage} 件`:'已覆盖'}{r.plan?.shortage_windows?.filter((w:any)=>w.start_date < r.plan.sea_start_date).slice(0,1).map((w:any)=><small key={w.start_date} className="row-subtitle">{w.start_date} 至 {w.end_date}</small>)}</td><td>{qty(r,'air')}</td></>}<td><button className="btn btn-outline btn-sm" onClick={()=>setSelected(r.id)}>设置</button></td></tr>})}</tbody></table></div>
      {!!overview&&!dataset.length&&<p className="operations-empty">当前筛选下没有计划。未买过的产品不生成补货，零成交商品暂停自动备货。</p>}
      <div className="operations-pagination"><span>{dataset.length} {view==='schedule'?'批次':'商品'} · 每页25条</span><button className="btn btn-outline btn-sm" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>上一页</button><span>{activePage} / {pages}</span><button className="btn btn-outline btn-sm" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>下一页</button></div>
    </div>
    <details className="operations-explanation"><summary>利润与资金安排说明</summary><p>海运成本较低且销量稳定时，滚动海运更有利于利润。计划比较现有成本表中的空海单件利润，低于设置门槛则暂缓；没有完整成本时只提供数量参考。60天覆盖不是一次买完全部展望需求，未来批次必须复核销量、价格和可用资金；采购成本、仓储费、资金占用及滞销风险仍需核对，不能把运输差价当作净利润保证。新品不足14天的数据标为参考，建议先小批验证，再拉长覆盖。</p></details>
    {!!selected&&<div className="replenishment-overlay" onClick={close}><section className="replenishment-panel" role="dialog" aria-modal="true" aria-label="补货计划设置" onClick={e=>e.stopPropagation()}><div className="replenishment-panel-heading"><h2>商品计划设置</h2><button className="btn btn-outline" onClick={close}>关闭</button></div>{panelError?<div className="alert alert-error">{panelError}</div>:current?<PipelinePlan key={current.id} product={current} open />:<p>正在加载…</p>}</section></div>}
  </div>;
}
