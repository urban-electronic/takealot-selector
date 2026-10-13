import { useEffect, useState } from 'react';
import { getOperationsOverview, getProduct } from '../remoteApi';
import PipelinePlan from '../operations/PipelinePlan';
import type { Product } from '../types';

export default function Replenishment() {
  const [overview, setOverview] = useState<any>(null); const [current, setCurrent] = useState<Product | null>(null);
  const [selected, setSelected] = useState(''); const [panelError, setPanelError] = useState('');
  const [onlyAction, setOnlyAction] = useState(false); const [hideEmpty, setHideEmpty] = useState(true); const [level, setLevel] = useState('all');
  const [error, setError] = useState(''); const [search, setSearch] = useState('');
  const refresh = async () => { const d = await getOperationsOverview(); setOverview(d); setError(''); };
  useEffect(() => { refresh().catch(e => setError(e.message)); const t = setInterval(() => refresh().catch(e => setError(e.message)), 60000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!selected) return;
    let active = true; setCurrent(null); setPanelError('');
    getProduct(selected).then(p => active && setCurrent(p)).catch(e => active && setPanelError(e.message));
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelected(''); };
    document.addEventListener('keydown', escape); const old = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { active = false; document.removeEventListener('keydown', escape); document.body.style.overflow = old; };
  }, [selected]);
  const allRows: any[] = overview?.rows || [];
  const rows = allRows.filter((r: any) => (!onlyAction || r.plan?.air_quantity > 0 || r.plan?.sea_quantity > 0 || r.plan?.blocked?.length) && (!hideEmpty || r.plan?.level !== 'no_data' && !!r.plan) && (level === 'all' || r.plan?.level === level)).filter((r: any) => `${r.name} ${r.sku} ${r.product_no}`.toLowerCase().includes(search.toLowerCase()));
  const sync = overview?.sync;
  const count = (l: string) => allRows.filter(r => r.plan?.level === l).length;
  const dailyLoss = allRows.reduce((s, r) => s + (r.plan?.daily_loss_zar || 0), 0);
  const levelNames: Record<string,string> = { out_of_stock: '已断货', urgent: '紧急', watch: '关注', healthy: '健康', slow_moving: '滞销', no_data: '观察中' };
  const exportRows = () => {
    const cell = (v: unknown) => '"' + String(v ?? '').replace(/^[=+@-]/, "'").replace(/"/g, '""') + '"';
    const csv = [['商品','SKU','库存','日均销量','可售天数','空运数量','海运数量','空运单件利润','海运单件利润'], ...rows.map(r => [r.name,r.sku,r.plan?.stock,r.plan?.velocity,r.plan?.cover_days,r.plan?.air_quantity,r.plan?.sea_quantity,r.plan?.economics?.air?.profit_zar,r.plan?.economics?.sea?.profit_zar])].map(r => r.map(cell).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff'+csv], { type:'text/csv;charset=utf-8' })); const a = document.createElement('a'); a.href=url; a.download='补货计划.csv'; a.click(); URL.revokeObjectURL(url);
  };
  const margin = (r: any, mode: string) => r.plan?.economics?.available ? `R ${r.plan.economics[mode].profit_zar} / ${(r.plan.economics[mode].profit_margin * 100).toFixed(1)}%` : '待核对成本';
  const quantity = (r: any, mode: string) => {
    const p = r.plan; if (!p) return '待数据';
    if (p.economics?.available && p.economics[mode].profit_margin < p.economics.minimum_margin) return '利润不足，暂缓';
    if (p[mode + '_quantity'] > 0) return `${p.economics?.available ? '补' : '参考'} ${p[mode + '_quantity']} 件`;
    return p.velocity ? '已覆盖' : '暂无需求';
  };
  return <div><div className="dashboard-heading"><div><h1>补货计划</h1><p>海运55＋3天为主，空运14＋3天只补必要缺口；按单件经营利润筛选。</p></div><button className="btn btn-outline" onClick={() => refresh().catch(e => setError(e.message))}>刷新计划</button></div>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="replenishment-metrics"><div className="card"><small>预计断货日销售额缺口（估算）</small><strong>R {dailyLoss.toFixed(0)}</strong></div><div className="card"><small>已断货商品</small><strong>{count('out_of_stock')} / {allRows.length}</strong></div><div className="card"><small>紧急 · 可售天数不足空运时效</small><strong>{count('urgent')}</strong></div><div className="card"><small>滞销 · 30天零成交且有库存</small><strong>{count('slow_moving')}</strong></div></div>
    <div className="card"><strong>店铺订单与库存</strong><p>{sync?.status === 'success' ? `最近同步 ${sync.last_success} · ${sync.updated_products} 个商品` : sync?.status === 'running' ? '正在同步…' : sync?.error || '等待后台同步'}</p><small>每小时自动同步。默认尾程按3天留余量，可改为2天。{sync?.unmatched_products ? ` ${sync.unmatched_products} 个商品需核对SKU。` : ''}</small></div>
    <div className="card"><div className="replenishment-toolbar"><select aria-label="补货分级" value={level} onChange={e => setLevel(e.target.value)}><option value="all">全部分级</option>{Object.entries(levelNames).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select><label><input type="checkbox" checked={onlyAction} onChange={e => setOnlyAction(e.target.checked)} /> 只看需行动</label><label><input type="checkbox" checked={hideEmpty} onChange={e => setHideEmpty(e.target.checked)} /> 隐藏无需求</label><button className="btn btn-outline" onClick={exportRows}>导出CSV</button><span>显示 {rows.length} 个商品</span></div><input aria-label="搜索补货商品" placeholder="搜索名称、SKU或编号" value={search} onChange={e => setSearch(e.target.value)} style={{ marginBottom: 16 }} /><div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>商品</th><th>分级</th><th>现货 / 在途</th><th>销速 / 可售天数</th><th>空运补货</th><th>海运补货</th><th>空运单件利润</th><th>海运单件利润</th><th>操作</th></tr></thead><tbody>{rows.map((r: any) => <tr key={r.id}><td>{r.product_no} · {r.name}{!r.profile && <small style={{ display: 'block', color: 'var(--color-danger)' }}>SKU待匹配</small>}{r.plan?.stale && <small style={{ display: 'block', color: 'var(--color-danger)' }}>快照待更新</small>}{r.plan?.overdue.length > 0 && <small style={{ display: 'block', color: 'var(--color-danger)' }}>采购到货延期</small>}</td><td>{levelNames[r.plan?.level] || '待数据'}<small style={{ display: 'block' }}>{r.plan?.trend === 'rising' ? '↑ 上升' : r.plan?.trend === 'falling' ? '↓ 下降' : '→ 平稳'}</small></td><td>{r.plan?.stock ?? '—'}<small style={{ display: 'block' }}>已确认在途 {(r.plan?.pending?.air || 0) + (r.plan?.pending?.sea || 0)}</small></td><td>{r.plan?.velocity ?? '—'} 件/天<small style={{ display: 'block' }}>可售 {r.plan?.cover_days ?? '—'} 天 · 近7天 {r.plan?.units_recent ?? '—'} 件</small></td><td>{quantity(r, 'air')}</td><td>{quantity(r, 'sea')}</td><td>{margin(r, 'air')}</td><td>{margin(r, 'sea')}</td><td><button className="btn btn-outline btn-sm" onClick={() => setSelected(r.id)}>计划设置</button></td></tr>)}</tbody></table></div>{!rows.length && <p>暂无已采购或已上架商品；未买过的商品不生成补货计划。</p>}</div>
    {!!selected && <div className="replenishment-overlay" onClick={() => { setSelected(''); refresh().catch(e => setError(e.message)); }}><section className="replenishment-panel" role="dialog" aria-modal="true" aria-label="补货计划设置" onClick={e => e.stopPropagation()}><div className="replenishment-panel-heading"><div><h2>计划设置</h2><small>调整运输时效、利润门槛和采购到货；保存后影响此商品补货建议。</small></div><button className="btn btn-outline" onClick={() => { setSelected(''); refresh().catch(e => setError(e.message)); }}>关闭</button></div>{panelError ? <div className="alert alert-error">{panelError}</div> : current ? <PipelinePlan key={current.id} product={current} open /> : <p>正在加载商品计划…</p>}</section></div>}
  </div>;
}
