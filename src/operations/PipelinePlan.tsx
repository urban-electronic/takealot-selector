import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getOperationsProfile, saveOperationsProfile } from '../remoteApi';
import { todayLocal } from '../utils/datetime';
import { useVisibleInterval } from '../utils/useVisibleInterval';
import type { Product } from '../types';

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
      const p = d.profile || { stock: '', stock_date: todayLocal(), observed_from: todayLocal(), air_days: 17, sea_days: 58, air_head_days: 14, sea_head_days: 55, tail_days: 3, minimum_margin: .15, safety_days: 7, cycle_days: 30, sea_coverage_days: 60, planning_days: 120, procurement_days: 0, allow_emergency_air: true, emergency_air_days: 21, adaptive_dispatch: true, dispatch_policy: 'balanced', air_share_cap: .2, first_sea_share: .75, sea_split_days: 15, batch_quantity: null, group: '', related_groups: [], allocations: [], sales: [] };
      setForm(p); setSales(p.sales.map((s: any) => `${s.date} | ${s.quantity}`).join('\n')); setRelated(p.related_groups.join('，'));
    }
  };
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    load(true).catch(e => active.current && setMessage(e.message));
    return () => { active.current = false; };
  }, [product.id]);
  // 隐藏标签页时不轮询；回到前台若已过期立即补一次
  useVisibleInterval(() => { load().catch(e => active.current && setMessage(e.message)); }, 60000);
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      const records = sales.split('\n').filter(s => s.trim()).map(s => {
        const [date, quantity] = s.split('|').map(v => v.trim());
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !quantity || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0) throw new Error('销量每行填写：YYYY-MM-DD | 正整数数量');
        return { date, quantity: Number(quantity) };
      });
      if (form.stock === '' || !Number.isInteger(Number(form.stock)) || Number(form.stock) < 0) throw new Error('请填写本店可售库存。');
      for (const purchase of data.purchases) {
        const parts=form.allocations.filter((a:any)=>a.procurement_id===purchase.id);
        if(parts.some((a:any)=>a.quantity!=null&&(!Number.isInteger(Number(a.quantity))||Number(a.quantity)<=0))) throw new Error('每批发运数量须为正整数。');
        if(parts.length>1&&parts.some((a:any)=>a.quantity==null)) throw new Error('拆批时，请为每批填写数量。');
        if(parts.reduce((n:number,a:any)=>n+(a.quantity??purchase.quantity),0)>purchase.quantity) throw new Error('分批总量超过采购数量，请减少批量。');
      }
      const d = await saveOperationsProfile(product.id, { ...form, stock: Number(form.stock), related_groups: related.split(/[,，;；]/).map(s => s.trim()).filter(Boolean), sales: records });
      setData(d); setForm(d.profile); setMessage('已保存。网页会自动重算；采购记录变化后提醒同步更新。');
    } catch (e) { setMessage((e as Error).message); } finally { setBusy(false); }
  };
  const field = (key: string, label: string, type = 'number') => <div className="form-group"><label>{label}</label><input type={type} value={form[key] ?? ''} disabled={form.data_source === 'seller_api' && ['stock', 'stock_date', 'observed_from'].includes(key)} min={type === 'number' ? 0 : undefined} onChange={e => setForm({ ...form, [key]: key==='batch_quantity' && e.target.value==='' ? null : type === 'number' && e.target.value !== '' ? Number(e.target.value) : e.target.value })} /></div>;
  const etaFor = (mode: string) => {
    const date=new Date(todayLocal()+'T00:00:00Z');
    date.setUTCDate(date.getUTCDate()+(mode==='air'?form.air_head_days:form.sea_head_days)+form.tail_days+(form.procurement_days||0));
    return date.toISOString().slice(0,10);
  };
  const allocate = (purchase: any, index: number, key: string, value: any) => {
    const entries=[...form.allocations];
    if (index < 0) entries.push({procurement_id:purchase.id,mode:key==='mode'?value:'air',quantity:purchase.quantity,eta:etaFor(key==='mode'?value:'air'),included_in_stock:false,[key]:value});
    else entries[index]={...entries[index],[key]:value};
    setForm({...form,allocations:entries});
  };
  const addBatch = (purchase:any) => {
    const old=form.allocations.filter((a:any)=>a.procurement_id===purchase.id);
    const assigned=old.reduce((n:number,a:any)=>n+(a.quantity??purchase.quantity),0);
    if(assigned>=purchase.quantity){setMessage('请先减少现有批次数量，再添加剩余发运批次。');return;}
    const latest=old.filter((a:any)=>a.mode==='sea').map((a:any)=>a.eta).sort().pop();
    const date=new Date((latest||etaFor('sea'))+'T00:00:00Z');
    if(latest) date.setUTCDate(date.getUTCDate()+(form.sea_split_days||15));
    setForm({...form,allocations:[...form.allocations,{procurement_id:purchase.id,mode:old.length?'sea':'air',quantity:purchase.quantity-assigned,eta:old.length?date.toISOString().slice(0,10):etaFor('air'),included_in_stock:false}]});
    setMessage('已添加发运批次，请核对数量和到货日期后保存。');
  };
  const p = data?.plan;
  return <details className="card" open={open || undefined}><summary style={{ fontWeight: 600, cursor: 'pointer' }}>{product.chinese_product_name || product.product_name} · 计划参数与采购到货</summary>
    <p style={{ fontSize: 13 }}>一次录入并保存，后台固定规则重算，不调用 AI。空运14天头程＋2–3天尾程；海运55天头程＋2–3天尾程。少量空运启动，海运大批先发、余量错峰。20/60/20是起点，实际按库存、销速与利润计算。</p>
    {message && <div role="status" className="alert">{message}</div>}
    {p && <div style={{ padding: 16, borderRadius: 12, background: 'var(--color-bg)', marginBottom: 16 }}>
      <strong>日均 {p.velocity} 件 · 可售 {p.cover_days ?? '—'} 天</strong>
      {p.economics?.available ? <p>空运单件利润 R {p.economics.air.profit_zar}（{(p.economics.air.profit_margin * 100).toFixed(1)}%）；海运 R {p.economics.sea.profit_zar}（{(p.economics.sea.profit_margin * 100).toFixed(1)}%）。</p> : <p>利润比较待核对：{p.economics?.reason}。以下数量仅为销量参考。</p>}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginTop: 10 }}><span>空运：{!p.allow_emergency_air ? '未开启（仅海运）' : p.air_quantity > 0 ? `限量应急 ${p.air_quantity} 件` : p.blocked?.length ? '利润门槛限制，暂停补货' : '已覆盖，提醒隐藏'}（已购待到 {p.pending.air} 件）</span><span>海运：{p.sea_quantity > 0 ? `待补 ${p.sea_quantity} 件` : p.blocked?.length ? '利润门槛限制，暂停补货' : '已覆盖，提醒隐藏'}（已购待到 {p.pending.sea} 件）</span></div>
      {p.pre_sea_shortage > 0 && <p>首批计划海运到货前仍有约 {p.pre_sea_shortage} 件需求无法覆盖。关闭或限制空运不会消除这段缺口。</p>}
      {p.shortage_windows?.map((w:any)=><p key={w.start_date}>预计缺口：{w.start_date} 至 {w.end_date} · 约 {w.quantity} 件。</p>)}
      {p.stale && <p>库存快照超过3天，请更新库存和实际销量；当前为参考测算。</p>}
      {!!p.overdue.length && <p>有采购超过预计到货日，请更新到货日期或确认已纳入库存。</p>}
      {!!p.unmapped.length && <p>检测到 {p.unmapped.length} 笔采购尚未设置空/海及到货日，设置后自动扣除。</p>}
      <p>{p.reason}；采购复核周期 {p.next_review_days} 天。</p>
      {!!p.timeline.length && <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>计划节点</th><th>到货数量</th><th>预计结余</th></tr></thead><tbody>{p.timeline.map((r: any) => <tr key={r.day}><td>{r.date || `第${r.day}天`}</td><td>{r.arrivals}</td><td>{r.balance}</td></tr>)}</tbody></table></div>}
    </div>}
    {form && <><div className="form-group"><label><input type="checkbox" checked={form.allow_emergency_air || false} onChange={e=>setForm({...form,allow_emergency_air:e.target.checked})} /> 允许此商品少量空运先行（仍受利润与数量上限限制）</label><small>只限制新建议，已采购空运仍按真实到货日计入库存。成本完整且利润达标后才建议应急量。</small></div><div className="form-group"><label><input type="checkbox" checked={form.adaptive_dispatch !== false} onChange={e=>setForm({...form,adaptive_dispatch:e.target.checked})} /> 按库存、趋势与利润自动调整分批（推荐）</label></div><div className="form-row"><div className="form-group"><label>发运方案</label><select value={form.dispatch_policy || 'balanced'} onChange={e=>setForm({...form,dispatch_policy:e.target.value})}><option value="balanced">空运先行＋海运分批</option><option value="sea_only">仅海运</option></select></div>{field('air_share_cap','空运参考比例（0.20 = 20%，自动模式会调整）')}{field('first_sea_share','海运首批参考比例（随趋势和覆盖调整）')}{field('sea_split_days','第二批海运延后发运（天）')}{field('batch_quantity','本轮备货量（规划值，留空自动计算）')}{field('emergency_air_days', '空运绝对天数上限（默认21，可改1–30）')}{field('stock', '本店可售库存（非国内库存）')}{field('stock_date', '库存快照日期（该日结束库存）', 'date')}{field('observed_from', '完整销量观测开始（新品填实际首日）', 'date')}{field('air_head_days', '空运头程（天）')}{field('sea_head_days', '海运头程（天）')}{field('tail_days', '尾程派送（2–3天）')}{field('minimum_margin', '最低补货利润率（0.15 = 15%）')}{field('safety_days', '安全覆盖天数')}{field('cycle_days', '滚动发运间隔（天）')}{field('sea_coverage_days', '海运到货后覆盖销量（天）')}{field('planning_days', '采购计划展望（天）')}{field('procurement_days', '采购至发运备货（天）')}{field('group', '细分品类（如 USB-C充电线，勿只填电子产品）', 'text')}</div>
      <div className="form-group"><label>关联品类（如 充电头，移动电源；逗号分隔）</label><input value={related} onChange={e => setRelated(e.target.value)} /></div>
      {form.data_source === 'seller_api' ? <p>库存与销量由卖家接口自动维护，无需手工录入。最近同步：{form.synced_at}</p> : <div className="form-group"><label>每日真实成交：YYYY-MM-DD | 数量；未能接入卖家接口时使用人工记录</label><textarea rows={4} style={{ width: '100%' }} value={sales} onChange={e => setSales(e.target.value)} /></div>}
      {!!data?.purchases.length && <div style={{overflowX:'auto'}}><p>同一笔采购可以分空运、海运和不同到货日；未分配余量视为国内备货，不会提前计入店铺库存。</p><table className="data-table"><thead><tr><th>采购 / 剩余备货</th><th>批量</th><th>空 / 海</th><th>预计到货</th><th>已在库存</th><th>批次</th></tr></thead><tbody>{data.purchases.filter((r:any)=>r.status!=='cancelled').flatMap((r:any)=>{
        const batches=form.allocations.map((a:any,index:number)=>({...a,index})).filter((a:any)=>a.procurement_id===r.id);
        const remaining=r.quantity-batches.reduce((n:number,a:any)=>n+(a.quantity??r.quantity),0);
        return (batches.length?batches:[{index:-1}]).map((a:any,n:number)=><tr key={r.id+'-'+a.index}><td>{r.quantity} 件<small>{r.recorded_at?.slice(0,10)}</small><small className={remaining<0?'text-danger':''}>{remaining<0?`超分配 ${-remaining} 件`:`待分配 ${remaining} 件`}</small></td><td><input type="number" min="1" step="1" aria-label={`采购${r.id}批次${n+1}数量`} style={{width:70}} value={a.quantity??r.quantity} onChange={e=>allocate(r,a.index,'quantity',Number(e.target.value))}/></td><td><select value={a.mode||''} onChange={e=>e.target.value&&allocate(r,a.index,'mode',e.target.value)}><option value="">待设置</option><option value="air">空运</option><option value="sea">海运</option></select></td><td><input type="date" value={a.eta||''} onChange={e=>allocate(r,a.index,'eta',e.target.value)}/></td><td><input type="checkbox" checked={a.included_in_stock||false} onChange={e=>allocate(r,a.index,'included_in_stock',e.target.checked)}/></td><td><div className="row-buttons">{n===0&&<button className="btn btn-outline btn-sm" disabled={remaining<=0} title={remaining<=0?'先减少当前批量，再拆分剩余数量':'添加剩余货的运输批次'} onClick={()=>addBatch(r)}>＋拆批</button>}{a.index>=0&&<button className="btn btn-outline btn-sm" onClick={()=>setForm({...form,allocations:form.allocations.filter((_:any,index:number)=>index!==a.index)})}>移除</button>}</div></td></tr>);
      })}</tbody></table></div>}
      <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? '保存中…' : '保存并自动运行'}</button></>}
    {data?.recommendations && <div style={{ marginTop: 20 }}><strong>同品类 / 相关产品</strong><p style={{ fontSize: 13 }}>{data.recommendations.scope}</p>{[['same_category', '同品类'], ['related', '关联品类']].map(([k, label]) => <div key={k}><span>{label}：</span>{data.recommendations[k].length ? data.recommendations[k].map((r: any) => <p key={r.id}><Link to={`/products/${r.id}`}>{r.name}</Link> · R {r.price} · {r.reason}</p>) : <span>暂无符合规则的商品；请先为相关商品保存细分品类。</span>}</div>)}</div>}
  </details>;
}
