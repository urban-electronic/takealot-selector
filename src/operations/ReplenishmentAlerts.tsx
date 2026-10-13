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
  return <div className="card"><div className="card-title">空运 / 海运补货</div>{error ? <p>补货计划读取失败：{error}</p> : rows.map(r => <p key={`${r.product_id}:${r.mode}`}><Link to={`/products/${r.product_id}`}>{r.name}</Link> · {r.mode === 'air' ? '空运' : '海运'}缺口 {r.quantity} 件 {r.stale ? '· 待更新库存快照' : ''} {r.overdue ? '· 采购到货延期' : ''}</p>)}</div>;
}
