import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { createProcurementRecord, getOperationsOverview, getProduct, listProcurementRecords } from '../remoteApi';
import PipelinePlan from '../operations/PipelinePlan';
import SourcingPanel from '../operations/SourcingPanel';
import type { Product, ProcurementRecord } from '../types';
import { formatPercent, formatPrice } from '../types';
import { todayLocal } from '../utils/datetime';
import { useVisibleInterval } from '../utils/useVisibleInterval';

const levels: Record<string,string> = { out_of_stock: '断货', urgent: '紧急', watch: '关注', healthy: '健康', slow_moving: '滞销', no_data: '观察中' };
export default function Replenishment() {
  const [overview, setOverview] = useState<any>(null);
  const [current, setCurrent] = useState<Product | null>(null);
  const [selected, setSelected] = useState(''); const [panelError, setPanelError] = useState('');
  const [onlyAction, setOnlyAction] = useState(false); const [hideEmpty, setHideEmpty] = useState(true);
  const [level, setLevel] = useState('all'); const [search, setSearch] = useState('');
  const [view, setView] = useState('products'); const [mode, setMode] = useState('all');
  const [page, setPage] = useState(1); const [error, setError] = useState('');
  // 采购登记：在补货计划里直接把「这次买了多少、花了多少钱」写进采购记录，
  // 不用再切到采购记录页重填一遍商品和序号。写入后刷新 overview，
  // 在途/可售天数会按新采购重算，形成闭环。
  const [buy, setBuy] = useState<{ row: any; qty: string; amount: string; date: string; status: 'in_transit' | 'received'; notes: string } | null>(null);
  const [buyHistory, setBuyHistory] = useState<ProcurementRecord[]>([]);
  const [buySaving, setBuySaving] = useState(false);
  const [buyError, setBuyError] = useState('');
  // 成功提示要能一键跳去采购记录核对，所以除了文案还要带上这条采购的序号。
  const [notice, setNotice] = useState<{ text: string; no: number | null } | null>(null);
  const refresh = async () => { setOverview(await getOperationsOverview()); setError(''); };
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  // 隐藏标签页时不轮询；回到前台若已过期立即补一次
  useVisibleInterval(() => { refresh().catch(e => setError(e.message)); }, 60000);
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
  const rows = all.filter(r => (!onlyAction || r.plan?.air_quantity > 0 || r.plan?.sea_quantity > 0 || r.plan?.blocked?.length) && (!hideEmpty || r.plan?.velocity > 0) && (level === 'all' || r.plan?.level === level) && `${r.name} ${r.english_name || ''} ${r.sku} ${r.product_no}`.toLowerCase().includes(search.toLowerCase()));
  const schedule = rows.flatMap(r => (r.plan?.schedule || []).filter((s: any) => mode === 'all' || s.mode === mode).map((s: any) => ({ ...s, product: r }))).sort((a,b) => a.purchase_date.localeCompare(b.purchase_date) || a.mode.localeCompare(b.mode) || a.product.name.localeCompare(b.product.name));
  const dataset = view === 'schedule' ? schedule : rows;
  const pages = Math.max(1, Math.ceil(dataset.length / 25)); const activePage = Math.min(page, pages);
  const shown = dataset.slice((activePage-1)*25, activePage*25);
  const totals = all.reduce((a,r) => { a.air += r.plan?.air_quantity || 0; a.sea += r.plan?.sea_quantity || 0; a.future += r.plan?.total_sea_quantity || 0; return a; }, { air: 0, sea: 0, future: 0 });
  const share = totals.future + totals.air ? Math.round(totals.future / (totals.future+totals.air) * 100) : 0;
  // 金额与百分比统一走 formatPrice / formatPercent：原来直接拼 profit_zar，
  // 字段是字符串时显示原始值，且百分比用 toFixed(0) 与其余页面两位小数不一致
  const margin = (r: any, m: string) => r.plan?.economics?.available
    ? `${formatPrice(r.plan.economics[m].profit_zar, 'ZAR')} · ${formatPercent(r.plan.economics[m].profit_margin)}`
    : '待核对成本';
  // 后端算的是浮点数（销量 ÷ 天数），直接渲染会出现 1.7999999999999998 这种尾巴。
  // 日均/可售天数/缺口都按「一位小数、整数不带 .0」显示。
  const num = (v: any, digits = 1) => (typeof v === 'number' && Number.isFinite(v))
    ? String(Number(v.toFixed(digits)))
    : (v ?? '—');
  const qty = (r: any, m: string) => {
    const p = r.plan;
    if (!p) return '待匹配';
    if (m === 'air' && !p.allow_emergency_air) return '未开启';
    if (m === 'air' && !p.economics?.available) return '待核对成本';
    if (p.economics?.available && p.economics[m].profit_margin < p.economics.minimum_margin) return '利润不足';
    return p[m+'_quantity'] ? num(p[m+'_quantity'], 0) : (m === 'air' ? '无需再补' : '已覆盖');
  };
  // 主名用中文（运营看的是中文），英文标题单独一行：核对规格、跟供应商对账时
  // 要靠平台原文，只有中文名时经常分不清是哪个变体。
  const name = (r: any) => <><button className="product-link" title={r.name} onClick={() => setSelected(r.id)}>{r.name}</button>{r.english_name && r.english_name !== r.name && <small className="row-subtitle product-en" title={r.english_name}>{r.english_name}</small>}<small className="row-subtitle">{r.sku || r.product_no}{r.plan?.stale ? ' · 库存待更新' : ''}{r.plan?.overdue?.length ? ' · 到货延期' : ''}</small></>;
  const exportRows = () => {
    const cell = (v: unknown) => '"'+String(v ?? '').replace(/^[=+@-]/,"'").replace(/"/g,'""')+'"';
    const values = view === 'schedule' ? [['商品','英文标题','SKU','方式','采购日期','发运日期','预计到货','覆盖至','数量','性质','估算总经营成本ZAR'],...schedule.map(s => [s.product.name,s.product.english_name || '',s.product.sku,s.mode==='sea'?'海运':'空运',s.purchase_date,s.departure_date,s.arrival_date,s.coverage_until,s.quantity,s.conditional?'后续待复核':'当前建议',s.estimated_cost_zar])] : [['商品','英文标题','SKU','库存','日均销量','可售天数','当前海运','限量应急空运','首批海运前缺口'],...rows.map(r=>[r.name,r.english_name || '',r.sku,r.plan?.stock,num(r.plan?.velocity),num(r.plan?.cover_days,0),r.plan?.sea_quantity,r.plan?.air_quantity,r.plan?.pre_sea_shortage])];
    const url=URL.createObjectURL(new Blob(['\ufeff'+values.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='补货发运计划.csv';a.click();URL.revokeObjectURL(url);
  };
  // 打开登记面板：建议数量直接带出来（日历视图是这一批，清单视图是本次海运/空运建议），
  // 用户只需要填实际花的钱。同时把该商品已有采购记录列出来，避免重复登记。
  const openBuy = (row: any, suggested: any) => {
    const qty = Number(suggested);
    setBuy({ row, qty: Number.isFinite(qty) && qty > 0 ? String(qty) : '', amount: '', date: todayLocal(), status: 'in_transit', notes: '' });
    setBuyError(''); setBuyHistory([]);
    listProcurementRecords()
      .then(all => setBuyHistory(all.filter(r => r.product_id === row.id).slice(0, 5)))
      .catch(() => { /* 历史读不到不影响登记，写成功后会重新拉 */ });
  };
  const submitBuy = async () => {
    if (!buy || buySaving) return;
    const qty = parseInt(buy.qty, 10);
    const amount = parseFloat(buy.amount);
    if (!Number.isFinite(qty) || qty <= 0) { setBuyError('采购数量必须是大于 0 的整数'); return; }
    if (!Number.isFinite(amount) || amount < 0) { setBuyError('采购总价请填写有效金额（可填 0）'); return; }
    setBuySaving(true); setBuyError('');
    try {
      const saved = await createProcurementRecord({
        product_id: buy.row.id,
        product_no: buy.row.product_no ?? 0,
        product_name: buy.row.english_name || buy.row.name || '',
        quantity: qty,
        total_amount: amount,
        notes: buy.notes.trim(),
        status: buy.status,
        recorded_at: buy.date,
      });
      const no = buy.row.product_no ?? null;
      setNotice({ no, text: `已登记采购：#${no ?? '—'} ${buy.row.name} · ${saved.quantity} 件 · ¥${Number(saved.total_amount || 0).toFixed(2)}，已同步到采购记录` });
      setBuy(null);
      await refresh();
    } catch (e: any) {
      setBuyError(e?.message || String(e) || '登记失败');
    } finally {
      setBuySaving(false);
    }
  };
  const close = () => { setSelected(''); refresh().catch(e=>setError(e.message)); };
  return <div className="operations-workspace">
    <div className="dashboard-heading"><div><h1>补货计划</h1><p>少量空运先行 · 两批海运错峰 · 计划随真实销量重算</p></div><button className="btn btn-outline" onClick={()=>refresh().catch(e=>setError(e.message))}>刷新</button></div>
    {error && <div className="alert alert-error">{error}</div>}
    {notice && <div className="alert alert-success" role="status">{notice.text}{notice.no != null && <Link className="btn btn-sm btn-outline" style={{marginLeft:12}} to={`/procurement?product_no=${notice.no}`}>查看采购记录</Link>}<button className="btn btn-sm" style={{marginLeft:12}} onClick={()=>setNotice(null)}>知道了</button></div>}
    <div className="replenishment-metrics"><div className="card"><small>本次海运待补</small><strong>{overview ? totals.sea : '—'}<span> 件</span></strong></div><div className="card"><small>本轮限量空运</small><strong>{overview ? totals.air : '—'}<span> 件</span></strong></div><div className="card"><small>展望计划海运占比 · 数量</small><strong>{overview ? share : '—'}<span> %</span></strong></div><div className="card"><small>断货 / 紧急商品</small><strong>{overview ? all.filter(r=>['urgent','out_of_stock'].includes(r.plan?.level)).length : '—'}<span> / {all.length}</span></strong></div></div>
    <div className="operations-status"><span className={`status-dot ${overview?.sync?.status==='success'?'active':''}`} />{overview?.sync?.status==='success'?`库存与销量已同步 · ${overview.sync.last_success}`:overview?.sync?.status==='running'?'订单与库存正在同步':overview?.sync?.error || '等待同步'}<span>每小时更新</span></div>
    <div className="card operations-table-card">
      <div className="operations-tabs"><button className={view==='products'?'selected':''} onClick={()=>setView('products')}>商品补货清单</button><button className={view==='schedule'?'selected':''} onClick={()=>setView('schedule')}>采购与发运日历</button><span>空运20%为参考 · 数量与批次按库存、销速及利润调整</span></div>
      <div className="replenishment-toolbar"><input aria-label="搜索补货商品" placeholder="搜索商品 / SKU" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="补货分级" value={level} onChange={e=>setLevel(e.target.value)}><option value="all">全部分级</option>{Object.entries(levels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>{view==='schedule'&&<select aria-label="运输方式" value={mode} onChange={e=>setMode(e.target.value)}><option value="all">空运＋海运</option><option value="sea">海运</option><option value="air">空运</option></select>}<label><input type="checkbox" checked={onlyAction} onChange={e=>setOnlyAction(e.target.checked)} /> 当前需补</label><label><input type="checkbox" checked={hideEmpty} onChange={e=>setHideEmpty(e.target.checked)} /> 隐藏零销</label><button className="btn btn-outline btn-sm" disabled={!overview} onClick={exportRows}>导出计划</button></div>
      <div className="operations-note">空运约17天，海运约58天。空运按早期缺口和利润调整，不固定为20%；海运先发大批，余量以15天间隔为参考。若第二批延迟会缺货，必要数量提前到第一批。首批前的缺口仍单独提示，未来采购与发运须复核。已登记海外发货纳入在途；到货日期按运输天数估算，不代表已到仓。</div>
      <div className="operations-table-scroll"><table className="data-table"><thead>{view==='schedule'?<tr><th className="operations-no">#</th><th>商品 / SKU</th><th>运输</th><th>采购 / 发运</th><th>预计可售到货</th><th>目标覆盖至</th><th>数量</th><th>单件利润</th><th>计划状态</th><th>操作</th></tr>:<tr><th className="operations-no">#</th><th>商品 / SKU</th><th>状态</th><th>现货 / 在途</th><th>日均 / 可售天数</th><th>本次海运 / 下一批</th><th>海运利润</th><th>首批前缺口</th><th>限量应急空运</th><th>操作</th></tr>}</thead><tbody>{shown.map((item:any)=>{const r=view==='schedule'?item.product:item;return <tr key={view==='schedule'?`${r.id}-${item.mode}-${item.purchase_date}`:r.id}><td className="operations-no">{r.product_no ?? '—'}</td><td className="operations-product">{name(r)}</td>{view==='schedule'?<><td><span className={`mode-pill ${item.mode}`}>{item.mode==='sea'?'海运':'空运'}</span></td><td>{item.purchase_date}<small className="row-subtitle">发运 {item.departure_date}</small></td><td>{item.arrival_date}</td><td>{item.coverage_until}</td><td><strong>{item.quantity}</strong> 件</td><td>{margin(r,item.mode)}</td><td><span className={`status-pill ${item.conditional?'':'active'}`}>{item.deferred_dispatch?'同轮备货 · 延后发运':item.conditional?'后续采购复核':'当前建议'}</span>{r.plan?.provisional&&<small className="row-subtitle">短观测 / 旧快照参考</small>}{!r.plan?.economics?.available&&<small className="row-subtitle">利润待核对</small>}</td></>:<><td><span className={`status-pill tone-${r.plan?.level || 'no_data'}`}>{levels[r.plan?.level]||'待匹配'}</span></td><td>{r.plan?.stock??'—'}<small className="row-subtitle">在途 {(r.plan?.pending?.air||0)+(r.plan?.pending?.sea||0)}</small></td><td>{num(r.plan?.velocity)} 件<small className="row-subtitle">可售 {num(r.plan?.cover_days, 0)} 天</small></td><td>{qty(r,'sea')}{r.plan?.schedule?.filter((b:any)=>b.mode==='sea'&&b.conditional).slice(0,1).map((b:any)=><small key={b.purchase_date} className="row-subtitle">{b.purchase_date} 未来补货建议 {b.quantity} 件 · 未下单</small>)}</td><td>{margin(r,'sea')}</td><td>{!r.plan?'待数据':r.plan.pre_sea_shortage>0?`约 ${num(r.plan.pre_sea_shortage, 0)} 件`:'已覆盖'}{r.plan?.shortage_windows?.filter((w:any)=>w.start_date < r.plan.sea_start_date).slice(0,1).map((w:any)=><small key={w.start_date} className="row-subtitle">{w.start_date} 至 {w.end_date}</small>)}</td><td>{qty(r,'air')}</td></>}<td className="operations-actions"><button className="btn btn-outline btn-sm" onClick={()=>openBuy(r, view==='schedule'?item.quantity:(r.plan?.sea_quantity||r.plan?.air_quantity||''))}>登记采购</button><button className="btn btn-outline btn-sm" onClick={()=>setSelected(r.id)}>设置</button></td></tr>})}</tbody></table></div>
      {!!overview&&!dataset.length&&<p className="operations-empty">当前筛选下没有计划。未买过的产品不生成补货，零成交商品暂停自动备货。</p>}
      <div className="operations-pagination"><span>{dataset.length} {view==='schedule'?'批次':'商品'} · 每页25条</span><button className="btn btn-outline btn-sm" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>上一页</button><span>{activePage} / {pages}</span><button className="btn btn-outline btn-sm" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>下一页</button></div>
    </div>
    <details className="operations-explanation"><summary>利润与资金安排说明</summary><p>海运成本较低且销量稳定时，滚动海运更有利于利润。计划比较现有成本表中的空海单件利润，低于设置门槛则暂缓；没有完整成本时只提供数量参考。60天覆盖不是一次买完全部展望需求，未来批次必须复核销量、价格和可用资金；采购成本、仓储费、资金占用及滞销风险仍需核对，不能把运输差价当作净利润保证。新品不足14天的数据标为参考，建议先小批验证，再拉长覆盖。</p></details>
    {!!selected&&<div className="replenishment-overlay" onClick={close}><section className="replenishment-panel" role="dialog" aria-modal="true" aria-label="补货计划设置" onClick={e=>e.stopPropagation()}><div className="replenishment-panel-heading"><h2>商品计划设置</h2><button className="btn btn-outline" onClick={close}>关闭</button></div>{panelError?<div className="alert alert-error">{panelError}</div>:current?<><SourcingPanel key={`sourcing-${current.id}`} product={current} /><PipelinePlan key={current.id} product={current} open /></>:<p>正在加载…</p>}</section></div>}
    {!!buy&&<div className="app-modal-backdrop" onClick={()=>{ if(!buySaving) setBuy(null); }}><div className="app-modal procurement-quick-modal" role="dialog" aria-modal="true" aria-label="登记采购" onClick={e=>e.stopPropagation()}>
      <h3 className="dialog-title">登记采购 · #{buy.row.product_no ?? '—'} {buy.row.name}</h3>
      <p className="procurement-quick-meta">{buy.row.english_name && buy.row.english_name !== buy.row.name ? `${buy.row.english_name} · ` : ''}{buy.row.sku || buy.row.product_no}{buy.row.plan ? ` · 现货 ${buy.row.plan.stock ?? '—'} / 在途 ${(buy.row.plan.pending?.air||0)+(buy.row.plan.pending?.sea||0)}` : ''}</p>
      <div className="procurement-quick-grid">
        <label>采购数量（件）<input type="number" min="1" step="1" inputMode="numeric" value={buy.qty} autoFocus onChange={e=>setBuy(prev=>prev?{...prev,qty:e.target.value}:prev)} placeholder="实际采购件数" /></label>
        <label>采购总价（¥）<input type="number" min="0" step="0.01" inputMode="decimal" value={buy.amount} onChange={e=>setBuy(prev=>prev?{...prev,amount:e.target.value}:prev)} placeholder="这一批实际总花费" /></label>
        <label>采购日期<input type="date" value={buy.date} onChange={e=>setBuy(prev=>prev?{...prev,date:e.target.value}:prev)} /></label>
        <label>到货状态<select value={buy.status} onChange={e=>setBuy(prev=>prev?{...prev,status:e.target.value as 'in_transit'|'received'}:prev)}><option value="in_transit">在途（待分配发运批次）</option><option value="received">已到仓（需在商品设置里更新现货）</option></select></label>
      </div>
      <label className="procurement-quick-note">备注（选填）<input type="text" value={buy.notes} onChange={e=>setBuy(prev=>prev?{...prev,notes:e.target.value}:prev)} placeholder="供应商 / 批次 / 到货说明…" /></label>
      <p className="procurement-quick-unit">单价自动核算：{(() => { const q=parseFloat(buy.qty), a=parseFloat(buy.amount); return q>0&&Number.isFinite(a)?`¥${(a/q).toFixed(2)} / 件`:'—'; })()}</p>
      {buyHistory.length>0&&<div className="procurement-quick-history"><strong>该商品已有采购</strong>{buyHistory.map(h=><div key={h.id}><span>{(h.recorded_at||'').slice(0,10)}</span><span>{h.quantity} 件</span><span>¥{Number(h.total_amount||0).toFixed(2)}</span><span className={`status-pill ${h.status==='received'?'active':''}`}>{h.status==='received'?'已入库':h.status==='cancelled'?'已取消':'在途'}</span></div>)}</div>}
      {buyError&&<div className="alert alert-error">{buyError}</div>}
      <div className="dialog-actions"><button className="btn" onClick={()=>setBuy(null)} disabled={buySaving}>取消</button><button className="btn btn-primary" onClick={submitBuy} disabled={buySaving}>{buySaving?'保存中…':'保存并同步采购记录'}</button></div>
    </div></div>}
  </div>;
}
