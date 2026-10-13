import { useEffect, useState } from 'react';
import { getProducts } from '../remoteApi';
import OperationsAssist from '../operations/OperationsAssist';
import type { Product } from '../types';
export default function AutoPricing() {
  const [products, setProducts] = useState<Product[]>([]); const [id, setId] = useState(''); const [error, setError] = useState('');
  useEffect(() => { getProducts().then(setProducts).catch(e => setError(e.message)); }, []);
  const p = products.find(x => x.id === id);
  return <div><h1>自动调价</h1><div className="card"><strong>自动执行尚未启用</strong><p>当前支持价格建议测算；竞品自动采集、持续调价执行和平台写价链路仍未接通。此页不会自动修改本店售价。</p></div>{error && <div className="alert alert-error">{error}</div>}<div className="card"><label>选择商品</label><select value={id} onChange={e => setId(e.target.value)} style={{ width: '100%' }}><option value="">请选择需要查看定价的商品</option>{products.map(p => <option key={p.id} value={p.id}>{p.product_no} · {p.chinese_product_name || p.product_name}</option>)}</select></div>{p && <OperationsAssist key={p.id} product={p} />}</div>;
}
