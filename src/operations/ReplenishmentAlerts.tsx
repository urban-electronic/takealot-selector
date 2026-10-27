import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getOperationsAlerts } from '../remoteApi';
import { useVisibleInterval } from '../utils/useVisibleInterval';
import { describeCaveats, summarizeAlerts } from './replenishmentSummary';
import type { ReplenishmentAlert } from './replenishmentSummary';
export default function ReplenishmentAlerts() {
  const [rows, setRows] = useState<ReplenishmentAlert[]>([]); const [error, setError] = useState('');
  const active = useRef(true);
  const refresh = useCallback(() => {
    getOperationsAlerts()
      .then(r => { if (active.current) { setRows(r); setError(''); } })
      .catch(e => { if (active.current) setError(e.message); });
  }, []);
  useEffect(() => { active.current = true; refresh(); return () => { active.current = false; }; }, [refresh]);
  useVisibleInterval(refresh, 60000);
  if (!rows.length && !error) return null;
  const summary = summarizeAlerts(rows);
  // 库存/采购数据过期时，这几个缺口数字是用旧数据算的。补货计划页每行都标了
  // 「库存待更新」「到货延期」，工作台这句汇总原来一个字都不提，读起来像刚盘过。
  const caveats = error ? '' : describeCaveats(summary);
  return <section className="dashboard-panel replenishment-summary" aria-label="补货提醒">
    <div className="dashboard-panel-title"><div><h2>补货提醒</h2><p>{error ? '补货计划暂未更新' : `${summary.products} 个产品待补货 · 空运缺口 ${summary.air} 件 · 海运缺口 ${summary.sea} 件`}{caveats && <small className="replenishment-caveat"> {caveats}</small>}</p></div><Link className="btn btn-outline btn-sm" to="/replenishment">查看补货计划 →</Link></div>
    {error && <small className="text-danger">{error}</small>}
  </section>;
}
