import { useState } from 'react';
import type { Product } from '../types';
import { evaluatePricing, type CompetitorOffer } from './pricing';
import { computeReplenish } from './replenish';
import { buildSuggestions } from './recommend';

export default function OperationsAssist({ product }: { product: Product }) {
  const [tab, setTab] = useState('pricing');
  const [values, setValues] = useState<Record<string, string>>({ floor: String(product.minimum_price_at_20_margin ?? ''), ceiling: '', current: '', stock: '', onWay: '0', lead: '1', holding: 'no', strategy: 'takealot_buybox', sea: '45', air: '15', safety: '14', ownSeller: '', zeroSales: 'no', offers: '', sales: '', candidates: '' });
  const [result, setResult] = useState('');
  const field = (key: string, label: string) => <div className="form-group"><label>{label}</label><input type="number" min="0" step="any" value={values[key]} onChange={e => { setValues({ ...values, [key]: e.target.value }); setResult(''); }} /></div>;
  const number = (key: string, optional = false) => {
    if (!values[key].trim()) { if (optional) return null; throw new Error('请填写全部必填数字。'); }
    const n = Number(values[key]);
    if (!Number.isFinite(n) || n < 0) throw new Error('数字必须有效且不小于零。');
    return n;
  };
  const calculate = () => {
    try {
      const now = new Date();
      const lines = (key: string) => values[key].split('\n').filter(s => s.trim()).map(s => s.split('|').map(x => x.trim()));
      if (tab === 'pricing') {
        if (!values.ownSeller.trim()) throw new Error('请填写本店卖家名称，以排除自己的报价。');
        const current = number('current')!; const floor = number('floor')!; const ceiling = number('ceiling', true);
        if (current <= 0 || floor <= 0 || (ceiling !== null && ceiling < floor)) throw new Error('请填写有效的本店现价和最低价，最高价不得低于最低价。');
        const offers: CompetitorOffer[] = lines('offers').map(([seller, price, status, card, time]) => {
          const fetchedAt = new Date(time); const p = Number(price);
          if (!/(Z|[+-]\d{2}:\d{2})$/i.test(time || '') || !seller || !price || !Number.isFinite(p) || p <= 0 || !['in_stock', 'pre_order', 'backorder', 'out_of_stock'].includes(status) || !['fastest', 'best_price', 'buybox', 'other'].includes(card) || !time || !Number.isFinite(fetchedAt.getTime()) || fetchedAt > now) throw new Error('竞品每行须填写：卖家 | 正数价格 | 库存状态 | 卡位 | 带时区的采集时间。');
          return { seller, isMine: seller.toLowerCase() === values.ownSeller.trim().toLowerCase(), price: p, status: status as CompetitorOffer['status'], offerType: card as CompetitorOffer['offerType'], fetchedAt };
        });
        if (!offers.length) throw new Error('请录入竞品快照；不要把本店报价填为竞品。');
        if (offers.some(o => now.getTime() - o.fetchedAt.getTime() > 30 * 60000)) throw new Error('竞品快照超过30分钟，请更新后重算。');
        const stock = number('stock')!;
        if (!Number.isInteger(stock)) throw new Error('本店现货库存须为整数。');
        const r = evaluatePricing({ currentPrice: current, minPrice: floor, maxPrice: ceiling, competitorOffers: offers.filter(o => o.status === 'in_stock'), rule: { strategy: values.strategy as 'takealot_buybox' | 'follow_lowest', leadAmount: number('lead')!, excludedSellers: [], minPriceChange: 1, cooldownMinutes: 10 }, state: { stock, lastPricedAt: null, todayChanges: 0, maxDailyChanges: 0, offersMaxAgeMs: 30 * 60000, now, holdingBuybox: values.holding === 'yes', buyboxPrice: null } });
        setResult(r.decision === 'execute' ? `建议售价 R ${r.newPrice}。仅为测算，未改动商品或店铺报价。` : (r.reason || '维持现价'));
      } else if (tab === 'replenish') {
        const sales = lines('sales').map(([date, quantity, price]) => {
          const orderDate = new Date(date); const q = Number(quantity); const p = Number(price);
          if (!date || !Number.isFinite(orderDate.getTime()) || orderDate > now || !quantity || !Number.isInteger(q) || q <= 0 || !price || !Number.isFinite(p) || p <= 0) throw new Error('订单每行须填写：成交日期 | 正整数数量 | 正数成交单价。');
          return { orderDate, quantity: q, sellingPrice: p };
        });
        if (!sales.length && values.zeroSales !== 'yes') throw new Error('请提供真实成交记录；采购和发货记录不能替代销量。');
        const stock = number('stock')!; const onWay = number('onWay')!;
        if (!Number.isInteger(stock) || !Number.isInteger(onWay)) throw new Error('库存和在途数量须为整数。');
        const r = computeReplenish({ sku: product.sku || product.id, name: product.product_name || "", price: 0, stockTotal: stock, onWay, sales, now, config: { seaLeadTimeDays: number('sea')!, airLeadTimeDays: number('air')!, safetyStockDays: number('safety')! } });
        setResult(`近30天销量 ${r.unitsWindow} 件，近7天 ${r.unitsRecent} 件；采用日均 ${r.vUsed} 件。\n${r.reasons.join('；')}\n海运建议 ${r.suggestedSea} 件 / 空运建议 ${r.suggestedAir} 件（两种运输方案，请择一；均已扣除在途）。`);
      } else {
        const floor = number('floor')!;
        if (floor <= 0) throw new Error('选品价格门槛须大于零。');
        const candidates = lines('candidates').map(([title, price, url]) => {
          const p = Number(price);
          if (!title || !price || !Number.isFinite(p) || p <= 0 || !url || !/^https:\/\/(www\.)?takealot\.com\/[^\s]*PLID\d+/i.test(url)) throw new Error('候选每行须填写：英文标题 | 正数市场价 | Takealot商品链接。');
          return { title, price: p, url };
        });
        if (!candidates.length) throw new Error('请录入候选商品。');
        const r = buildSuggestions({ sku: product.sku || product.id, title: product.product_name || "", minPrice: floor }, '', candidates);
        setResult(r.accepted.length ? r.accepted.map(c => `${c.title} · R ${c.price} · 相似度 ${Math.round(c.similarity * 100)}%`).join('\n') + `\n已过滤 ${r.rejected} 个不匹配或低于价格门槛的候选。` : '暂无符合相似度和价格门槛的候选。');
      }
    } catch (e) { setResult((e as Error).message); }
  };
  return <details className="card" key={product.id}>
    <summary style={{ cursor: 'pointer', fontWeight: 600 }}>经营助手 · 定价、补货与相似选品</summary>
    <p style={{ color: 'var(--color-text-secondary)', fontSize: 13 }}>沿用当前成本与利润数据。竞品和订单需提供真实记录；计算建议不会自动改价或下单。</p>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>{[['pricing', '定价建议'], ['replenish', '补货测算'], ['recommend', '相似选品']].map(([key, label]) => <button key={key} className={`btn ${tab === key ? 'btn-primary' : 'btn-outline'}`} onClick={() => { setTab(key); setResult(''); }}>{label}</button>)}</div>
    <div className="form-row">
      {tab !== 'replenish' && field('floor', '最低价 / 选品价格门槛 (ZAR，必填)')}
      {tab === 'pricing' && <>{field('current', '本店实时售价 (ZAR，必填)')}{field('ceiling', '最高价 (ZAR，可选)')}{field('lead', '领先金额 (ZAR)')}</>}
      {tab !== 'recommend' && field('stock', '本店可售现货库存（必填，非国内库存）')}
      {tab === 'replenish' && <>{field('onWay', '已确认在途数量')}{field('sea', '海运提前期（天）')}{field('air', '空运提前期（天）')}{field('safety', '安全库存（天）')}</>}
    </div>
    {tab === 'pricing' && <div className="form-row"><div className="form-group"><label>本店卖家名称（用于排除自身报价）</label><input value={values.ownSeller} onChange={e => { setValues({ ...values, ownSeller: e.target.value }); setResult(''); }} /></div><div className="form-group"><label>策略</label><select value={values.strategy} onChange={e => { setValues({ ...values, strategy: e.target.value }); setResult(''); }}><option value="takealot_buybox">抢 Buybox</option><option value="follow_lowest">跟随现货最低价</option></select></div><div className="form-group"><label>是否已确认持有主 Buybox</label><select value={values.holding} onChange={e => { setValues({ ...values, holding: e.target.value }); setResult(''); }}><option value="no">未确认 / 未持有（只降不升）</option><option value="yes">已确认持有（允许回弹）</option></select></div></div>}
    <div className="form-group"><label>{tab === 'pricing' ? '竞品快照：卖家 | 售价 | in_stock / pre_order / backorder / out_of_stock | fastest / best_price / buybox / other | ISO采集时间（如 2026-10-13T20:00:00+08:00）' : tab === 'replenish' ? '真实成交记录：日期（建议带时区） | 数量 | 成交单价 ZAR；一行一笔，提供完整近30天记录' : '候选商品：英文标题 | 市场价 ZAR | Takealot商品链接；一行一个'}</label><textarea rows={4} style={{ width: '100%' }} value={values[tab === 'pricing' ? 'offers' : tab === 'replenish' ? 'sales' : 'candidates']} onChange={e => { setValues({ ...values, [tab === 'pricing' ? 'offers' : tab === 'replenish' ? 'sales' : 'candidates']: e.target.value }); setResult(''); }} /></div>
    {tab === 'replenish' && <label style={{ display: 'block', marginBottom: 12 }}><input type="checkbox" checked={values.zeroSales === 'yes'} onChange={e => { setValues({ ...values, zeroSales: e.target.checked ? 'yes' : 'no' }); setResult(''); }} /> 已核对完整近30天订单；空白记录表示零成交</label>}
    <button className="btn btn-primary" onClick={calculate}>计算建议</button>
    {result && <div role="status" style={{ marginTop: 14, padding: 16, borderRadius: 12, background: 'var(--color-bg)', whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>{result}</div>}
  </details>;
}
