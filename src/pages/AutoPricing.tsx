import { useEffect, useState } from 'react';
import { getAutomaticPricing, configureAutomaticPricing, pauseAutomaticPricing, reconcileAutomaticPricing, bulkAutomaticPricing, pauseAllAutomaticPricing } from '../remoteApi';
export default function AutoPricing() {
  const [rows, setRows] = useState<any[]>([]); const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [search, setSearch] = useState('');
  const [configured, setConfigured] = useState(false);
  const [selected, setSelected] = useState<string[]>([]); const [group, setGroup] = useState('all'); const [groupName, setGroupName] = useState('');
  const key = (r: any) => `${r.product_id}:${r.offer_id}`;
  const refresh = () => getAutomaticPricing().then(d => { setRows(d.rows); setConfigured(d.configured); });
  useEffect(() => { refresh().catch(e => setError(e.message)); const t = setInterval(() => refresh().catch(e => setError(e.message)), 30000); return () => clearInterval(t); }, []);
  const act = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'reconcile') await reconcileAutomaticPricing(r.product_id, r.offer_id);
      else if (action === 'pause') await pauseAutomaticPricing(r.product_id, r.offer_id);
      else {
        const draft = { ...r.rule, ...drafts[key(r)] };
        const useSystem = drafts[key(r)]?.use_system_min ?? r.rule.use_system_min ?? true;
        const min = useSystem ? r.suggested_min : Number(draft.min_price);
        if (!min || !Number.isInteger(min)) throw new Error('系统底价缺失，请补全成本或手工输入该SKU最低价。');
        if (draft.max_price && !Number.isInteger(Number(draft.max_price))) throw new Error('最高价必须为整数，也可留空使用智能上限。');
        await configureAutomaticPricing(r.product_id, r.offer_id, { min_price: useSystem ? null : min, use_system_min: useSystem, smart_raise: (draft.strategy || 'takealot_buybox') === 'takealot_buybox', max_price: draft.max_price ? Number(draft.max_price) : null, strategy: draft.strategy || 'takealot_buybox', lead_amount: 1, cooldown_minutes: 10, max_daily_changes: 10, enabled: action === 'start' ? true : !!r.rule.enabled });
        setDrafts(prev => { const next = { ...prev }; delete next[key(r)]; return next; });
      }
      await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const emergencyStop = async () => { setBusy('all'); try { await pauseAllAutomaticPricing(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } };
  const bulk = async (action: string) => {
    if (!selected.length) { setError('请先勾选需要操作的SKU。'); return; }
    setBusy('bulk'); setError('');
    try {
      const chosen = rows.filter(r => selected.includes(key(r)));
      await bulkAutomaticPricing({ action, group: groupName, items: chosen.map(r => ({ product_id: r.product_id, offer_id: r.offer_id })) });
      await refresh(); setSelected([]);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const visible = rows.filter(r => (group === 'all' || r.group === group) && `${r.name} ${r.sku}`.toLowerCase().includes(search.toLowerCase()));
  const input = (r: any, field: string, label: string) => <input type="number" min="1" step="1" aria-label={`${r.sku}${label}`} placeholder={field === 'min_price' ? `自动 ${r.suggested_min ?? '待补'}` : '智能上限'} style={{ width: 105 }} value={drafts[key(r)]?.[field] ?? (field === 'min_price' && r.rule.use_system_min !== false ? r.suggested_min ?? '' : r.rule[field] ?? '')} onChange={e => setDrafts({ ...drafts, [key(r)]: { ...drafts[key(r)], [field]: e.target.value, ...(field === 'min_price' ? { use_system_min: e.target.value === '' } : {}) } })} />;
  const relative = (v: string) => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
  return <div><div className="dashboard-heading"><div><h1>自动调价</h1><p>PricePilot 现货 Buybox 规则 · 每个SKU独立触发</p></div><button className="btn btn-outline" onClick={() => refresh().catch(e => setError(e.message))}>刷新</button></div>
    <div className="card"><div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}><strong>运行中 {rows.filter(r => r.rule.enabled).length} 个SKU / {rows.length} 个SKU</strong><button className="btn btn-danger" disabled={!!busy || !rows.some(r => r.rule.enabled)} onClick={emergencyStop}>紧急停止全部调价</button></div><strong>默认读取系统20%利润底价；点击“启动调价”才会开始实际改价。</strong><p>最高价可留空，不必随便填大数。未持有只降不升；持有可小步回弹。确认本店现货、竞品均非现货时，约3%试涨，12小时观察一次，累计不超过启用参考价20%；48小时无新成交时回退。低于最低价放弃竞争，冷却10分钟，每日最多10次；采集失败保持现价，改价结果不明时自动暂停。</p>{!configured && <p>卖家接口尚未配置，不能启动。</p>}</div>
    {error && <div role="alert" className="alert alert-error">{error}</div>}
    <div className="card"><div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}><select aria-label="SKU分类筛选" value={group} onChange={e => { setGroup(e.target.value); setSelected([]); }}><option value="all">全部分类</option>{[...new Set(rows.map(r => r.group))].map(g => <option key={g} value={g}>{g}</option>)}</select><input placeholder="分类名称，如宠物用品" value={groupName} onChange={e => setGroupName(e.target.value)} /><button className="btn btn-outline" disabled={!!busy || !selected.length} onClick={() => bulk('group')}>选中SKU归类</button><button className="btn btn-primary" disabled={!!busy || !selected.length || !configured} onClick={() => bulk('start')}>一键启动选中 {selected.length} 个SKU</button><button className="btn btn-outline" disabled={!!busy || !selected.length} onClick={() => bulk('pause')}>暂停选中SKU</button></div><input aria-label="搜索调价商品" placeholder="商品名称 / SKU" value={search} onChange={e => setSearch(e.target.value)} style={{ marginBottom: 16 }} /><div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th><input type="checkbox" aria-label="勾选当前筛选的全部SKU" checked={visible.length > 0 && visible.every(r => selected.includes(key(r)))} onChange={e => setSelected(e.target.checked ? visible.map(key) : [])} /></th><th>商品 / SKU</th><th>Winner · Buybox</th><th>Check · 现货竞品</th><th>Price change · 最近评估</th><th>Status</th><th>Stock</th><th>本店售价</th><th>Auto-pricing · 价格区间 R</th><th>策略 / 触发</th></tr></thead><tbody>{visible.map(r => <tr key={key(r)}><td><input type="checkbox" aria-label={`选择SKU ${r.sku}`} checked={selected.includes(key(r))} onChange={e => setSelected(e.target.checked ? [...selected, key(r)] : selected.filter(k => k !== key(r)))} /></td><td style={{ minWidth: 230, maxWidth: 350 }}><a href={r.url} target="_blank" rel="noreferrer">{r.name}</a><small style={{ display: 'block' }}>{r.sku} · {r.group}</small></td><td>{r.rule.last_evaluated_at ? r.rule.holding_buybox ? '✓ 持有' : '未持有' : '待采集'}<small style={{ display: 'block' }}>R {r.rule.buybox_price ?? '—'}</small></td><td>R {r.rule.competitor_price ?? '—'}</td><td style={{ minWidth: 220 }}>{r.rule.last_result || '未触发'}<small style={{ display: 'block' }}>评估：{relative(r.rule.last_evaluated_at)}</small><small style={{ display: 'block' }}>改价：{relative(r.rule.last_changed_at)}</small></td><td>{r.rule.write_pending ? '待核验 / 已暂停' : r.rule.enabled ? '运行中' : '关闭'}</td><td>{r.stock ?? '—'}</td><td>R {r.price ?? '—'}</td><td style={{ whiteSpace: 'nowrap' }}>{input(r, 'min_price', '最低')} — {input(r, 'max_price', '最高')}<small style={{ display: 'block' }}>底价来源：{(drafts[key(r)]?.use_system_min ?? r.rule.use_system_min ?? true) ? '系统利润底价' : '手动'}；最高可留空</small><button className="btn btn-outline btn-sm" disabled={!!busy} onClick={() => act(r, 'save')}>保存</button></td><td style={{ minWidth: 180 }}><select value={drafts[key(r)]?.strategy ?? r.rule.strategy ?? 'takealot_buybox'} onChange={e => setDrafts({ ...drafts, [key(r)]: { ...drafts[key(r)], strategy: e.target.value } })}><option value="takealot_buybox">智能抢 Buybox / 试涨</option><option value="follow_lowest">跟随现货最低价</option></select><button className={`btn ${r.rule.enabled ? 'btn-outline' : 'btn-primary'}`} disabled={!!busy || !configured} onClick={() => act(r, r.rule.write_pending ? 'reconcile' : r.rule.enabled ? 'pause' : 'start')}>{busy === key(r) ? '处理中…' : r.rule.write_pending ? '核验现价' : r.rule.enabled ? '暂停调价' : '启动调价'}</button></td></tr>)}</tbody></table></div>{!rows.length && <p>等待卖家商品同步，未匹配的SKU不能自动改价。</p>}</div>
  </div>;
}
