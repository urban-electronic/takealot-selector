import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getOperationsAlerts } from '../remoteApi';
export default function ReplenishmentAlerts() {
  const [rows, setRows] = useState<any[]>([]); const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const refresh = () => getOperationsAlerts().then(r => { if (active) { setRows(r); setError(''); } }).catch(e => active && setError(e.message));
    refresh(); const t = setInterval(refresh, 60000);
    return () => { active = false; clearInterval(t); };
  }, []);
  if (!rows.length && !error) return null;
  const products = new Set(rows.map(r => r.product_id)).size;
  const air = rows.filter(r => r.mode === 'air').reduce((n,r) => n + r.quantity, 0);
  const sea = rows.filter(r => r.mode === 'sea').reduce((n,r) => n + r.quantity, 0);
  return <section className="dashboard-panel replenishment-summary" aria-label="补货提醒">
    <div className="dashboard-panel-title"><div><h2>补货提醒</h2><p>{error ? '补货计划暂未更新' : `${products} 个产品待补货 · 空运缺口 ${air} 件 · 海运缺口 ${sea} 件`}</p></div><Link className="btn btn-outline btn-sm" to="/replenishment">查看补货计划 →</Link></div>
    {error && <small className="text-danger">{error}</small>}
  </section>;
}
