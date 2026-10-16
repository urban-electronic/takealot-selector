import { useRef, useState } from 'react';
import { reconcileShipmentManifest } from '../remoteApi';

export default function ShipmentReconciliation({ replaceIds, onSaved }: { replaceIds: string[]; onSaved: () => Promise<void> }) {
  const [text, setText] = useState(''); const [plan, setPlan] = useState<any>(null);
  const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false); const pending = useRef(false);
  const input = () => ({ replace_ids: replaceIds, lines: text.trim().split(/\n/).filter(Boolean).map(row => {
    const parts = row.trim().split(/\s+/);
    if (parts.length !== 5) throw new Error('每行填写：发货日期 运输方式 箱号 SKU 数量');
    const [date, shipping, box, sku, quantity] = parts;
    if (!Number.isSafeInteger(Number(quantity)) || Number(quantity) <= 0) throw new Error('数量必须为正整数');
    return { date, shipping, box, sku, quantity: Number(quantity) };
  }) });
  const run = async (apply: boolean) => {
    if (pending.current) return; pending.current = true; setBusy(true); setError(''); setMessage('');
    try {
      const payload = input();
      if (apply && (!plan || plan.input !== JSON.stringify(payload))) throw new Error('内容或选单已变化，请重新预览');
      const result = await reconcileShipmentManifest({ ...payload, token: apply ? plan.token : '' }, apply);
      if (apply) { setPlan(null); setText(''); setMessage(`已同步 ${result.total_quantity} 件海外在途：国内扣库 ${result.domestic_quantity} 件，老货 ${result.legacy_quantity} 件。`); await onSaved(); }
      else setPlan({ ...result, input: JSON.stringify(payload) });
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); if (apply) setPlan(null); }
    finally { pending.current = false; setBusy(false); }
  };
  return <details className="operations-explanation"><summary>补登 / 更正已发货在途</summary>
    <p>先勾选需要替代的旧单（当前 {replaceIds.length} 单），粘贴完整装箱明细。旧单保留撤回痕迹；按现有可用库存扣出，余量记为老货来源，全部计入海外在途，不计已到仓。</p>
    <label>装箱明细<textarea aria-label="已发货装箱明细" disabled={busy} rows={8} style={{width:'100%',boxSizing:'border-box'}} placeholder="2026-10-07 空运 空2 9900000000000 10" value={text} onChange={e => {setText(e.target.value); setPlan(null);}} /></label>
    <button className="btn" disabled={busy || !text.trim()} onClick={() => run(false)}>{busy ? '处理中...' : '预览库存与在途变化'}</button>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {plan && <><p>共 {plan.boxes} 箱、{plan.total_quantity} 件，国内扣库 {plan.domestic_quantity} 件、老货 {plan.legacy_quantity} 件。替代旧单 {plan.replaced.map((s:any)=>s.shipment_no).join('、') || '无'}。</p>
      <table className="inventory-table"><thead><tr><th>日期 / 箱号</th><th>方式</th><th>SKU</th><th>海外在途</th><th>国内扣库</th><th>老货</th></tr></thead><tbody>{plan.lines.map((x:any,i:number)=><tr key={i}><td>{x.date} / {x.box}</td><td>{x.shipping}</td><td>{x.sku}</td><td>{x.quantity}</td><td>{x.domestic_quantity}</td><td>{x.legacy_quantity}</td></tr>)}</tbody></table>
      <button className="btn btn-primary" disabled={busy || plan.input !== JSON.stringify(input())} onClick={() => run(true)}>确认同步库存和海外在途</button></>}
  </details>;
}
