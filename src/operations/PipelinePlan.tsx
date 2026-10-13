import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getOperationsProfile, saveOperationsProfile } from '../remoteApi';
import type { Product } from '../types';

const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
export default function PipelinePlan({ product, open = false }: { product: Product; open?: boolean }) {
  const [data, setData] = useState<any>(null);
  const [form, setForm] = useState<any>(null);
  const [sales, setSales] = useState('');
  const [related, setRelated] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const load = async (init = false) => {
    const d = await getOperationsProfile(product.id); setData(d);
    if (init) {
      const p = d.profile || { stock: '', stock_date: today(), observed_from: today(), air_days: 17, sea_days: 58, air_head_days: 14, sea_head_days: 55, tail_days: 3, minimum_margin: .15, safety_days: 7, cycle_days: 30, sea_coverage_days: 60, planning_days: 120, procurement_days: 0, group: '', related_groups: [], allocations: [], sales: [] };
      setForm(p); setSales(p.sales.map((s: any) => `${s.date} | ${s.quantity}`).join('\n')); setRelated(p.related_groups.join('，'));
    }
  };
  useEffect(() => {
    let active = true;
    load(true).catch(e => active && setMessage(e.message));
    const timer = setInterval(() => load().catch(e => active && setMessage(e.message)), 60000);
    return () => { active = false; clearInterval(timer); };
  }, [product.id]);
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      const records = sales.split('\n').filter(s => s.trim()).map(s => {
        const [date, quantity] = s.split('|').map(v => v.trim());
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !quantity || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0) throw new Error('销量每行填写：YYYY-MM-DD | 正整数数量');
        return { date, quantity: Number(quantity) };
      });
      if (form.stock === '' || !Number.isInteger(Number(form.stock)) || Number(form.stock) < 0) throw new Error('请填写本店可售库存。');
      const d = await saveOperationsProfile(product.id, { ...form, stock: Number(form.stock), related_groups: related.split(/[,，;；]/).map(s => s.trim()).filter(Boolean), sales: records });
      setData(d); setForm(d.profile); setMessage('已保存。网页会自动重算；采购记录变化后提醒同步更新。');
    } catch (e) { setMessage((e as Error).message); } finally { setBusy(false); }
  };
  const field = (key: string, label: string, type = 'number') => <div className="form-group"><label>{label}</label><input type={type} value={form[key]} disabled={form.data_source === 'seller_api' && ['stock', 'stock_date', 'observed_from'].includes(key)} min={type === 'number' ? 0 : undefined} onChange={e => setForm({ ...form, [key]: type === 'number' && e.target.value !== '' ? Number(e.target.value) : e.target.value })} /></div>;
  const allocate = (id: string, key: string, value: any) => {
    const old = form.allocations.find((a: any) => a.procurement_id === id);
    const entry = { procurement_id: id, mode: 'air', eta: today(), included_in_stock: false, ...old, [key]: value };
    setForm({ ...form, allocations: [...form.allocations.filter((a: any) => a.procurement_id !== id), entry] });
  };
  const p = data?.plan;
  return <details className="card" open={open || undefined}><summary style={{ fontWeight: 600, cursor: 'pointer' }}>{product.chinese_product_name || product.product_name} · 计划参数与采购到货</summary>
    <p style={{ fontSize: 13 }}>一次录入并保存，后台固定规则重算，不调用 AI。空运14天头程＋2–3天尾程；海运55天头程＋2–3天尾程。海运为主，空运按利润只补必要缺口。</p>
    {message && <div role="status" className="alert">{message}</div>}
    {p && <div style={{ padding: 16, borderRadius: 12, background: 'var(--color-bg)', marginBottom: 16 }}>
      <strong>日均 {p.velocity} 件 · 可售 {p.cover_days ?? '—'} 天</strong>
      {p.economics?.available ? <p>空运单件利润 R {p.economics.air.profit_zar}（{(p.economics.air.profit_margin * 100).toFixed(1)}%）；海运 R {p.economics.sea.profit_zar}（{(p.economics.sea.profit_margin * 100).toFixed(1)}%）。</p> : <p>利润比较待核对：{p.economics?.reason}。以下数量仅为销量参考。</p>}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginTop: 10 }}><span>空运：{p.air_quantity > 0 ? `待补 ${p.air_quantity} 件` : p.blocked?.length ? '利润门槛限制，暂停补货' : '已覆盖，提醒隐藏'}（已购待到 {p.pending.air} 件）</span><span>海运：{p.sea_quantity > 0 ? `待补 ${p.sea_quantity} 件` : p.blocked?.length ? '利润门槛限制，暂停补货' : '已覆盖，提醒隐藏'}（已购待到 {p.pending.sea} 件）</span></div>
      {p.pre_air_shortage > 0 && <p>空运到货前仍有约 {p.pre_air_shortage} 件需求缺口，需加急或本地调货。</p>}
      {p.stale && <p>库存快照超过3天，请更新库存和实际销量；当前为参考测算。</p>}
      {!!p.overdue.length && <p>有采购超过预计到货日，请更新到货日期或确认已纳入库存。</p>}
      {!!p.unmapped.length && <p>检测到 {p.unmapped.length} 笔采购尚未设置空/海及到货日，设置后自动扣除。</p>}
      <p>{p.reason}；采购复核周期 {p.next_review_days} 天。</p>
      {!!p.timeline.length && <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>计划节点</th><th>到货数量</th><th>预计结余</th></tr></thead><tbody>{p.timeline.map((r: any) => <tr key={r.day}><td>{r.date || `第${r.day}天`}</td><td>{r.arrivals}</td><td>{r.balance}</td></tr>)}</tbody></table></div>}
    </div>}
    {form && <><div className="form-row">{field('stock', '本店可售库存（非国内库存）')}{field('stock_date', '库存快照日期（该日结束库存）', 'date')}{field('observed_from', '完整销量观测开始（新品填实际首日）', 'date')}{field('air_head_days', '空运头程（天）')}{field('sea_head_days', '海运头程（天）')}{field('tail_days', '尾程派送（2–3天）')}{field('minimum_margin', '最低补货利润率（0.15 = 15%）')}{field('safety_days', '安全覆盖天数')}{field('cycle_days', '滚动发运间隔（天）')}{field('sea_coverage_days', '海运到货后覆盖销量（天）')}{field('planning_days', '采购计划展望（天）')}{field('procurement_days', '采购至发运备货（天）')}{field('group', '细分品类（如 USB-C充电线，勿只填电子产品）', 'text')}</div>
      <div className="form-group"><label>关联品类（如 充电头，移动电源；逗号分隔）</label><input value={related} onChange={e => setRelated(e.target.value)} /></div>
      {form.data_source === 'seller_api' ? <p>库存与销量由卖家接口自动维护，无需手工录入。最近同步：{form.synced_at}</p> : <div className="form-group"><label>每日真实成交：YYYY-MM-DD | 数量；未能接入卖家接口时使用人工记录</label><textarea rows={4} style={{ width: '100%' }} value={sales} onChange={e => setSales(e.target.value)} /></div>}
      {!!data?.purchases.length && <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>采购</th><th>空/海</th><th>预计可售到货日</th><th>已计入库存快照</th></tr></thead><tbody>{data.purchases.filter((r: any) => r.status !== 'cancelled').map((r: any) => {
        const a = form.allocations.find((x: any) => x.procurement_id === r.id);
        return <tr key={r.id}><td>{r.quantity} 件 · {r.recorded_at}</td><td><select value={a?.mode || ''} onChange={e => e.target.value ? allocate(r.id, 'mode', e.target.value) : setForm({ ...form, allocations: form.allocations.filter((x: any) => x.procurement_id !== r.id) })}><option value="">待设置</option><option value="air">空运</option><option value="sea">海运</option></select></td><td><input type="date" value={a?.eta || ''} onChange={e => allocate(r.id, 'eta', e.target.value)} /></td><td><input type="checkbox" checked={a?.included_in_stock || false} onChange={e => allocate(r.id, 'included_in_stock', e.target.checked)} /></td></tr>;
      })}</tbody></table></div>}
      <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? '保存中…' : '保存并自动运行'}</button></>}
    {data?.recommendations && <div style={{ marginTop: 20 }}><strong>同品类 / 相关产品</strong><p style={{ fontSize: 13 }}>{data.recommendations.scope}</p>{[['same_category', '同品类'], ['related', '关联品类']].map(([k, label]) => <div key={k}><span>{label}：</span>{data.recommendations[k].length ? data.recommendations[k].map((r: any) => <p key={r.id}><Link to={`/products/${r.id}`}>{r.name}</Link> · R {r.price} · {r.reason}</p>) : <span>暂无符合规则的商品；请先为相关商品保存细分品类。</span>}</div>)}</div>}
  </details>;
}
