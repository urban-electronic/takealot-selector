import { useEffect, useState } from 'react';
import { getOperationsOverview, getProducts } from '../remoteApi';
import PipelinePlan from '../operations/PipelinePlan';
import type { Product } from '../types';

export default function Replenishment() {
  const [overview, setOverview] = useState<any>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const refresh = async () => {
    const d = await getOperationsOverview(); setOverview(d); setError('');
  };
  useEffect(() => {
    refresh().catch(e => setError(e.message)); getProducts().then(setProducts).catch(e => setError(e.message));
    const timer = setInterval(() => refresh().catch(e => setError(e.message)), 60000);
    return () => clearInterval(timer);
  }, []);
  const current = products.find(p => p.id === selected);
  const rows = (overview?.rows || []).filter((r: any) => `${r.name} ${r.sku} ${r.product_no}`.toLowerCase().includes(search.toLowerCase()));
  const sync = overview?.sync;
  return <div><div className="dashboard-heading"><div><h1>补货计划</h1><p>只评估已采购或已上架商品，空运补缺口、海运接力。</p></div><button className="btn btn-outline" onClick={() => refresh().catch(e => setError(e.message))}>刷新计划</button></div>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="card"><strong>销售与库存同步</strong><p>{sync?.status === 'success' ? `已从卖家接口同步 · ${sync.last_success} · 已匹配 ${sync.updated_products} 个商品` : sync?.status === 'running' ? '后台正在读取实际订单和库存…' : sync?.error || '等待后台同步实际订单和库存'}</p><small>服务器每小时同步，关闭网页仍会运行；网页每分钟刷新评估。{sync?.unmatched_products ? ` ${sync.unmatched_products} 个商品SKU未能唯一匹配，未覆盖数据。` : ''}</small></div>
    <div className="card"><input aria-label="搜索补货商品" placeholder="搜索名称、SKU或编号" value={search} onChange={e => setSearch(e.target.value)} style={{ marginBottom: 16 }} />
      <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>商品</th><th>本店可售库存</th><th>日均销量</th><th>空运</th><th>海运</th><th>数据状态</th><th>操作</th></tr></thead><tbody>{rows.map((r: any) => <tr key={r.id}><td>{r.product_no} · {r.name}</td><td>{r.plan?.stock ?? '—'}</td><td>{r.plan?.velocity ?? '—'}</td><td>{r.plan ? r.plan.air_quantity > 0 ? `补 ${r.plan.air_quantity} 件` : '已覆盖' : '待数据'}</td><td>{r.plan ? r.plan.sea_quantity > 0 ? `补 ${r.plan.sea_quantity} 件` : '已覆盖' : '待数据'}</td><td>{!r.profile ? '等待店铺匹配' : r.profile.data_source === 'seller_api' ? r.plan?.stale ? '接口快照待更新' : '卖家实际销量' : '手动数据'}{r.plan?.overdue.length ? ' · 到货延期' : ''}</td><td><button className="btn btn-outline btn-sm" onClick={() => setSelected(selected === r.id ? '' : r.id)}>{selected === r.id ? '收起' : '调整时效 / 采购'}</button></td></tr>)}</tbody></table></div>
      {!rows.length && <p>没有符合条件的商品。未采购商品不会生成补货计划。</p>}
    </div>
    {current && <PipelinePlan key={current.id} product={current} open />}
  </div>;
}
