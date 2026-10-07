import { useCallback, useEffect, useMemo, useState } from 'react';
import { createStore, getStores, getStoresInventoryOverview, updateStore } from '../remoteApi';
import type { Store, StoreInventoryOverview } from '../types';

type Props = { onStoresChanged: (stores: Store[]) => void };
type StoreForm = Pick<Store, 'name' | 'platform' | 'owner_name' | 'external_store_ref' | 'sync_method' | 'sync_interval_minutes'>;

const emptyForm = (): StoreForm => ({
  name: '', platform: 'Takealot', owner_name: '', external_store_ref: '',
  sync_method: 'manual', sync_interval_minutes: 60,
});

const syncLabel: Record<string, string> = { manual: '手动维护', import: '文件导入', api: 'API 自动同步' };

export default function Stores({ onStoresChanged }: Props) {
  const [stores, setStores] = useState<Store[]>([]);
  const [overview, setOverview] = useState<StoreInventoryOverview[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<StoreForm>(emptyForm());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [storeRows, overviewRows] = await Promise.all([getStores(), getStoresInventoryOverview()]);
      setStores(storeRows); setOverview(overviewRows); onStoresChanged(storeRows);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, [onStoresChanged]);

  useEffect(() => { load(); }, [load]);

  const totals = useMemo(() => overview.reduce((acc, row) => ({
    available: acc.available + row.available,
    shipped: acc.shipped + row.shipped,
    exceptions: acc.exceptions + row.exception_count,
  }), { available: 0, shipped: 0, exceptions: 0 }), [overview]);

  const beginEdit = (store: Store) => {
    setEditingId(store.id); setShowCreate(false);
    setForm({ name: store.name, platform: store.platform, owner_name: store.owner_name, external_store_ref: store.external_store_ref, sync_method: store.sync_method, sync_interval_minutes: store.sync_interval_minutes });
  };

  const save = async () => {
    if (!form.name.trim()) { setError('请填写店铺名称'); return; }
    setSaving(true); setError(''); setMessage('');
    try {
      if (editingId) await updateStore(editingId, form);
      else await createStore(form);
      setMessage(editingId ? '店铺设置已保存' : '新店铺已建立，可以开始录入该店库存');
      setEditingId(null); setShowCreate(false); setForm(emptyForm()); await load();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };

  const toggleStatus = async (store: Store) => {
    const next = store.status === 'active' ? 'paused' : 'active';
    if (next === 'paused' && !window.confirm(`暂停“${store.name}”后，它不会出现在顶部店铺切换中。历史数据仍会保留，是否继续？`)) return;
    try { await updateStore(store.id, { status: next }); await load(); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return <div className="page stores-page">
    <div className="stores-heading">
      <div><h1>店铺管理</h1><p>统一查看各店库存，并为后续 API 或文件同步预留独立配置。产品资料库仍由所有店铺共用。</p></div>
      <button className="btn btn-primary" onClick={() => { setShowCreate(true); setEditingId(null); setForm(emptyForm()); }}>＋ 新增店铺</button>
    </div>

    <div className="store-kpis">
      <div><small>店铺</small><strong>{stores.filter(x => x.status === 'active').length}</strong><span>正常运营</span></div>
      <div><small>全部可用库存</small><strong>{totals.available}</strong><span>跨店合计</span></div>
      <div><small>累计已发货</small><strong>{totals.shipped}</strong><span>跨店合计</span></div>
      <div className={totals.exceptions ? 'warning' : ''}><small>库存异常</small><strong>{totals.exceptions}</strong><span>零库存或负库存 SKU</span></div>
    </div>

    {error && <div className="alert alert-error">{error}</div>}
    {message && <div className="alert alert-success">{message}</div>}

    {(showCreate || editingId) && <section className="store-editor">
      <div className="store-editor-title"><div><h2>{editingId ? '编辑店铺' : '新增店铺'}</h2><p>阶段二只保存连接方式，不录入密码或密钥。</p></div><button className="btn" onClick={() => { setShowCreate(false); setEditingId(null); }}>取消</button></div>
      <div className="store-form-grid">
        <label>店铺名称<input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="例如：南非二店" /></label>
        <label>平台<select value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}><option>Takealot</option><option>其他平台</option></select></label>
        <label>负责人<input value={form.owner_name} onChange={e => setForm({ ...form, owner_name: e.target.value })} placeholder="可暂不填写" /></label>
        <label>平台店铺识别号<input value={form.external_store_ref} onChange={e => setForm({ ...form, external_store_ref: e.target.value })} placeholder="后续对接时使用" /></label>
        <label>同步方式<select value={form.sync_method} onChange={e => setForm({ ...form, sync_method: e.target.value })}><option value="manual">手动维护</option><option value="import">文件导入</option><option value="api">API 自动同步（预留）</option></select></label>
        <label>同步间隔<select value={form.sync_interval_minutes} disabled={form.sync_method === 'manual'} onChange={e => setForm({ ...form, sync_interval_minutes: Number(e.target.value) })}><option value={30}>30 分钟</option><option value={60}>1 小时</option><option value={360}>6 小时</option><option value={1440}>每天</option></select></label>
      </div>
      <button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? '保存中...' : '保存店铺'}</button>
    </section>}

    <section className="store-table-card">
      <div className="store-table-title"><h2>多店库存总览</h2><span>店铺之间库存完全隔离</span></div>
      <div className="inventory-table-wrap"><table className="inventory-table store-overview-table">
        <thead><tr><th>店铺</th><th>状态</th><th>同步方式</th><th>SKU</th><th>采购入库</th><th>人工调整</th><th>已发货</th><th>可用库存</th><th>异常</th><th>操作</th></tr></thead>
        <tbody>{stores.map(store => {
          const row = overview.find(item => item.store_id === store.id);
          return <tr key={store.id} className={store.status !== 'active' ? 'store-paused-row' : ''}>
            <td><strong>{store.name}</strong><small>{store.owner_name || '未设置负责人'} · {store.platform}</small></td>
            <td><span className={store.status === 'active' ? 'store-status active' : 'store-status'}>{store.status === 'active' ? '运营中' : '已暂停'}</span></td>
            <td>{syncLabel[store.sync_method] || store.sync_method}<small>{store.sync_method === 'manual' ? '按操作实时记录' : `每 ${store.sync_interval_minutes} 分钟`}</small></td>
            <td>{row?.sku_count ?? 0}</td><td>{row?.purchased ?? 0}</td><td>{row?.adjusted ?? 0}</td><td>{row?.shipped ?? 0}</td>
            <td><strong className={(row?.available ?? 0) < 0 ? 'stock-danger' : 'stock-value'}>{row?.available ?? 0}</strong></td>
            <td><strong className={(row?.exception_count ?? 0) ? 'stock-danger' : ''}>{row?.exception_count ?? 0}</strong></td>
            <td><div className="store-actions"><button className="btn btn-sm" onClick={() => beginEdit(store)}>设置</button><button className="btn btn-sm" disabled={store.id === 'default-store'} onClick={() => toggleStatus(store)}>{store.status === 'active' ? '暂停' : '启用'}</button></div></td>
          </tr>;
        })}</tbody>
      </table></div>
      {loading && <div className="loading">正在汇总各店库存...</div>}
    </section>
  </div>;
}
