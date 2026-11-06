import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getSellerLedger, syncSellerLedger, type SellerLedgerPage } from '../remoteApi';
import { beijingDateTime } from '../utils/datetime';

export default function SellerLedger({ kind }: { kind: 'sales' | 'shipments' }) {
  const [data, setData] = useState<SellerLedgerPage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState('');
  const [draft, setDraft] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const generation = useRef(0);
  const writeLock = useRef(false);
  const activeKind = useRef(kind);
  activeKind.current = kind;
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const mine = ++generation.current;
    setLoading(true); setError('');
    getSellerLedger(kind, page, pageSize, search).then(result => {
      if (mine !== generation.current) return;
      const last = Math.max(1, Math.ceil(result.total / pageSize));
      if (page > last) { setPage(last); return; }
      setData(result);
    }).catch(e => { if (mine === generation.current) setError(e instanceof Error ? e.message : '读取失败'); })
      .finally(() => { if (mine === generation.current) setLoading(false); });
    return () => { generation.current++; };
  }, [kind, page, pageSize, search, revision]);
  const sync = async () => {
    if (writeLock.current) return;
    writeLock.current = true; setSyncing(true); setError(''); setNotice('');
    const requestedKind = kind;
    try {
      const result = await syncSellerLedger(kind, start, end);
      if (activeKind.current !== requestedKind) return;
      setNotice(`已保存 ${result.count} 条完整平台记录`);
      generation.current++; setPage(1); setRevision(x => x + 1);
    } catch (e) {
      if (activeKind.current === requestedKind) setError(e instanceof Error ? e.message : '同步失败，保留上次成功数据');
    } finally { writeLock.current = false; setSyncing(false); }
  };
  const snapshot = data?.snapshot;
  const sales = kind === 'sales';
  return <div className="page-container">
    <div className="page-header"><h2>{sales ? '订单与销量明细' : '货件看板'}</h2></div>
    <p className="muted">{sales ? '按平台订单行查看，日期范围按南非时间；金额为商品售价，未视作净收入或广告收益。' : '平台实收与破损证据独立展示。卸货不等于全部收货，本页不会更改国内库存或创建采购。'}</p>
    {!sales && <p><Link to="/inventory">查看本地发运与库存台账</Link> · <Link to="/packing">装箱工作台</Link></p>}
    <div className="card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'end' }}>
        {sales && <><label>南非起始日<input aria-label="南非起始日" type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label>南非结束日<input aria-label="南非结束日" type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></>}
        <button className="btn btn-primary" disabled={syncing} aria-busy={syncing} onClick={() => void sync()}>{syncing ? '读取平台中…' : '同步平台记录'}</button>
        <span className="muted">{sales ? '留空读取最近30天；每次最多31天。' : '只读取官方货件记录，不推测本地箱单对应关系。'}</span>
      </div>
      <p role="status">{snapshot ? `卖家 ${snapshot.seller_id} · 完整快照 v${snapshot.version} · ${beijingDateTime(snapshot.synced_at)}${snapshot.period_from ? ` · ${snapshot.period_from} 至 ${snapshot.period_to}（南非）` : ''}` : '尚无成功同步，不能视作零销量或无货件。'}</p>
      {snapshot && <details><summary>数据来源</summary><p>{snapshot.source} · 仅完整分页成功后保存；失败保留上次快照。</p></details>}
    </div>
    {error && <p role="alert" className="error-message">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <form onSubmit={e => { e.preventDefault(); setPage(1); setSearch(draft.trim()); }} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
      <input aria-label={sales ? '订单号或SKU' : '货件号或参考号'} placeholder={sales ? '订单号 / SKU' : '货件号 / 参考号'} value={draft} onChange={e => setDraft(e.target.value)} />
      <button className="btn btn-outline" type="submit">搜索</button>
      <button className="btn btn-outline" type="button" disabled={loading} onClick={() => setRevision(x => x + 1)}>刷新列表</button>
    </form>
    <div className="inventory-table-wrap" aria-busy={loading}>
      <table className="inventory-table"><thead><tr>{(sales ? ['订单 / 订单行', '南非日期', 'SKU', '下单件数', '状态', '售价（R）', '费用（R）'] : ['货件 / 参考号', '目的地区', '状态', '预计日期', '发运 / 实收 / 破损']).map(h => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{data?.items.map(row => sales ? <tr key={row.order_item_id}><td>{row.order_id}<br /><small>{row.order_item_id}</small></td><td>{row.business_date}</td><td>{row.sku}</td><td>{row.quantity}</td><td>{row.sale_status}</td><td>{row.selling_price ?? '未提供'}</td><td>{row.total_fees ?? '未提供'}</td></tr> : <tr key={row.shipment_id}><td>{row.shipment_id}<br />{row.reference}</td><td>{row.destination_region || '未提供'}</td><td>{row.cancelled ? '已取消' : row.shipped ? '已发运' : '未发运'}{row.date_unloaded ? ' · 已卸货' : ''}</td><td>{row.due_date || '未提供'}</td><td><details><summary>{row.items?.length ?? 0} 条货件行</summary>{row.items?.map(item => <p key={item.shipment_item_id}>Offer {item.offer_id}：{item.quantity_sending} / {item.quantity_received} / {item.quantity_damaged}</p>)}</details></td></tr>)}</tbody>
      </table>
      {!loading && data?.items.length === 0 && <p role="status">{snapshot ? '当前快照没有符合条件的记录' : '请先同步平台记录'}</p>}
    </div>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 12 }}>
      <span>{data?.total ?? 0} 条</span><label>每页<select aria-label="每页条数" value={pageSize} onChange={e => { setPage(1); setPageSize(Number(e.target.value)); }}><option value={20}>20</option><option value={50}>50</option><option value={100}>100</option></select></label>
      <button className="btn btn-outline" disabled={page <= 1 || loading} onClick={() => setPage(x => x - 1)}>上一页</button><span>{page} / {Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))}</span><button className="btn btn-outline" disabled={page * pageSize >= (data?.total ?? 0) || loading} onClick={() => setPage(x => x + 1)}>下一页</button>
    </div>
  </div>;
}
