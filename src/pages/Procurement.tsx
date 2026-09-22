import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import type { ProcurementRecord, Product } from '../types';

export default function Procurement() {
  const navigate = useNavigate();
  const api = useApi();
  const [records, setRecords] = useState<ProcurementRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState('');

  // form state
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({
    product_no: '',
    product_name: '',
    product_id: '',
    quantity: '',
    total_amount: '',
    notes: '',
    recorded_at: new Date().toISOString().slice(0, 10),
  });

  // delete confirm
  const [deleteTarget, setDeleteTarget] = useState<ProcurementRecord | null>(null);

  // list filter
  const [filterProductNo, setFilterProductNo] = useState('');

  // product search
  const [productSearch, setProductSearch] = useState('');
  const [searchResults, setSearchResults] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);

  const loadRecords = useCallback(async () => {
    try {
      const data = await api.listProcurementRecords();
      setRecords(data);
    } catch (e: any) {
      setError(e.toString());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRecords(); }, [loadRecords]);

  const handleSyncToRemote = async () => {
    const baseUrl = localStorage.getItem('api_base_url') || 'https://takealot-selector-production.up.railway.app';
    if (!baseUrl) { setError('请先设置远程库 API 地址'); return; }
    setSyncing(true);
    setSyncMsg('');
    setError('');
    try {
      const rows = await api.listProcurementRecords();
      const payload = [{ table: 'procurement_records', rows }];
      const res = await fetch(`${baseUrl}/api/migrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSyncMsg(`同步成功！已导出 ${data.imported?.procurement_records ?? rows.length} 条采购记录`);
    } catch (e: any) {
      setError(`同步失败: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  // search products by serial number
  useEffect(() => {
    if (productSearch.length < 1) { setSearchResults([]); return; }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const results = await api.searchProductsByNo(productSearch.trim());
        setSearchResults(results);
      } catch { setSearchResults([]); }
      finally { setSearching(false); }
    }, 300);
    return () => clearTimeout(timer);
  }, [productSearch]);

  const selectProduct = (p: Product) => {
    setForm(prev => ({
      ...prev,
      product_no: String(p.product_no ?? ''),
      product_name: p.product_name ?? '',
      product_id: p.id ?? '',
    }));
    setProductSearch('');
    setSearchResults([]);
  };

  const unitPrice = (() => {
    const qty = parseFloat(form.quantity);
    const total = parseFloat(form.total_amount);
    if (qty > 0 && total > 0) return (total / qty).toFixed(2);
    return '';
  })();

  const resetForm = () => {
    setForm({ product_no: '', product_name: '', product_id: '', quantity: '', total_amount: '', notes: '', recorded_at: new Date().toISOString().slice(0, 10) });
    setEditingId(null);
    setShowForm(false);
    setProductSearch('');
    setSearchResults([]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      product_id: form.product_id,
      product_no: parseInt(form.product_no) || 0,
      product_name: form.product_name,
      quantity: parseInt(form.quantity) || 0,
      total_amount: parseFloat(form.total_amount) || 0,
      notes: form.notes,
      recorded_at: form.recorded_at,
    };
    try {
      if (editingId) {
        await api.updateProcurementRecord(editingId, payload);
      } else {
        await api.createProcurementRecord(payload);
      }
      resetForm();
      await loadRecords();
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '保存失败');
    }
  };

  const startEdit = (r: ProcurementRecord) => {
    setForm({
      product_no: String(r.product_no ?? ''),
      product_name: r.product_name ?? '',
      product_id: r.product_id ?? '',
      quantity: String(r.quantity ?? ''),
      total_amount: String(r.total_amount ?? ''),
      notes: r.notes ?? '',
      recorded_at: r.recorded_at ?? '',
    });
    setEditingId(r.id);
    setShowForm(true);
  };

  const handleDelete = async (r: ProcurementRecord) => {
    setDeleteTarget(r);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await api.deleteProcurementRecord(deleteTarget.id);
      setDeleteTarget(null);
      await loadRecords();
    } catch (e: any) {
      alert(e.toString());
      setDeleteTarget(null);
    }
  };

  const formatPrice = (v: number | null | undefined) =>
    v != null ? `¥${v.toFixed(2)}` : '-';

  const totalProcurement = records.reduce((sum, r) => sum + (r.total_amount ?? 0), 0);

  const filteredRecords = filterProductNo.trim()
    ? records.filter(r => String(r.product_no) === filterProductNo.trim())
    : records;
  const filteredTotal = filteredRecords.reduce((sum, r) => sum + (r.total_amount ?? 0), 0);

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16 }}>
          <h1 style={{ margin: 0 }}>采购记录</h1>
          {records.length > 0 && (
            <span style={{ fontSize: 16, fontWeight: 600, color: '#e74c3c' }}>
              {filterProductNo.trim() ? (
                <>#{filterProductNo.trim()} 累计采购金额 {formatPrice(filteredTotal)}（{filteredRecords.length} 条记录）</>
              ) : (
                <>累计采购金额 {formatPrice(totalProcurement)}（{records.length} 条记录）</>
              )}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-outline" onClick={handleSyncToRemote} disabled={syncing}>
            {syncing ? '同步中...' : '同步到远程'}
          </button>
          <button className="btn btn-primary" onClick={() => { resetForm(); setShowForm(true); }}>
            + 新建记录
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {syncMsg && <div className="alert alert-success" style={{ marginBottom: 12 }}>{syncMsg}</div>}

      {/* form */}
      {showForm && (
        <div className="card" style={{ marginBottom: 20, padding: 20 }}>
          <h3 style={{ marginTop: 0 }}>{editingId ? '编辑采购记录' : '新建采购记录'}</h3>
          <form onSubmit={handleSubmit}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', marginBottom: 16 }}>
              {/* recorded date */}
              <div>
                <label>日期</label>
                <input type="date" value={form.recorded_at}
                  onChange={e => setForm(prev => ({ ...prev, recorded_at: e.target.value }))}
                  required style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div />

              {/* product search */}
              <div style={{ position: 'relative' }}>
                <label>产品序列号 (product_no)</label>
                <input type="text" value={productSearch || form.product_no}
                  placeholder="输入 serial number 搜索..."
                  onChange={e => {
                    const v = e.target.value;
                    setProductSearch(v);
                    setForm(prev => ({ ...prev, product_no: v, product_name: '', product_id: '' }));
                  }}
                  style={{ width: '100%', boxSizing: 'border-box' }} />
                {searchResults.length > 0 && (
                  <div style={{
                    position: 'absolute', zIndex: 10, background: '#fff', border: '1px solid #ddd',
                    borderRadius: 4, maxHeight: 200, overflow: 'auto', width: '100%', boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                  }}>
                    {searchResults.map(p => (
                      <div key={p.id} onClick={() => selectProduct(p)}
                        style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid #eee' }}
                        onMouseEnter={e => (e.currentTarget.style.background = '#f0f0f0')}
                        onMouseLeave={e => (e.currentTarget.style.background = '#fff')}>
                        <strong>#{p.product_no}</strong> {p.product_name}
                      </div>
                    ))}
                  </div>
                )}
                {searching && <small style={{ color: '#888' }}>搜索中...</small>}
              </div>

              {/* product name (auto-filled) */}
              <div>
                <label>产品名称</label>
                <input type="text" value={form.product_name} readOnly
                  style={{ width: '100%', boxSizing: 'border-box', background: '#f5f5f5' }} />
              </div>

              {/* quantity */}
              <div>
                <label>采购数量</label>
                <input type="number" min="1" value={form.quantity}
                  onChange={e => setForm(prev => ({ ...prev, quantity: e.target.value }))}
                  required style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>

              {/* total amount */}
              <div>
                <label>采购总金额</label>
                <input type="number" step="0.01" min="0" value={form.total_amount}
                  onChange={e => setForm(prev => ({ ...prev, total_amount: e.target.value }))}
                  required style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>

              {/* unit price (auto) */}
              <div>
                <label>单价（自动计算）</label>
                <input type="text" value={unitPrice ? `¥${unitPrice}` : ''} readOnly
                  style={{ width: '100%', boxSizing: 'border-box', background: '#f5f5f5' }} />
              </div>

              {/* notes */}
              <div>
                <label>备注（选填）</label>
                <input type="text" value={form.notes}
                  onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))}
                  style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" className="btn btn-primary">{editingId ? '保存修改' : '创建记录'}</button>
              <button type="button" className="btn" onClick={resetForm}>取消</button>
            </div>
          </form>
        </div>
      )}

      {/* delete confirm modal */}
      {deleteTarget && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex',
          alignItems: 'center', justifyContent: 'center', zIndex: 100
        }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 360, boxShadow: '0 4px 16px rgba(0,0,0,0.2)' }}>
            <h3 style={{ margin: '0 0 12px' }}>确认删除</h3>
            <p style={{ margin: '0 0 20px', color: '#555' }}>
              确定删除「{deleteTarget.product_name || deleteTarget.product_no}」的采购记录？
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="btn btn-danger" onClick={confirmDelete}>确认删除</button>
            </div>
          </div>
        </div>
      )}

      {/* list */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <input type="text" placeholder="输入产品序号筛选采购记录..." value={filterProductNo}
          onChange={e => setFilterProductNo(e.target.value)}
          style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid #ccc', width: 240 }} />
        {filterProductNo.trim() && (
          <button className="btn btn-sm" onClick={() => setFilterProductNo('')}>清除筛选</button>
        )}
      </div>
      {filteredRecords.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#888', padding: 40 }}>暂无采购记录，点击上方按钮新建。</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #ddd', textAlign: 'left' }}>
              <th style={{ padding: '8px 12px' }}>日期</th>
              <th style={{ padding: '8px 12px' }}>序列号</th>
              <th style={{ padding: '8px 12px' }}>产品名称</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>采购数量</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>采购总金额</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>单价</th>
              <th style={{ padding: '8px 12px' }}>备注</th>
              <th style={{ padding: '8px 12px' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredRecords.map(r => (
              <tr key={r.id} style={{ borderBottom: '1px solid #eee' }}>
                <td style={{ padding: '8px 12px' }}>{r.recorded_at}</td>
                <td style={{ padding: '8px 12px' }}>{r.product_no}</td>
                <td style={{ padding: '8px 12px' }}>{r.product_name}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{r.quantity}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatPrice(r.total_amount)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatPrice(r.unit_price)}</td>
                <td style={{ padding: '8px 12px', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.notes || '-'}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <button className="btn btn-sm" onClick={() => startEdit(r)} style={{ marginRight: 6 }}>编辑</button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(r)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
