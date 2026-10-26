import { useState, useEffect, useCallback } from 'react';
import { productLabels } from '../utils/productLabels';
import { toAmount, toNumber } from '../utils/number';
import { useApi, useDataSource } from '../DataSourceContext';
import { migrateToRemote } from '../remoteApi';
import { todayLocal } from '../utils/datetime';
import type { ProcurementRecord, Product } from '../types';

export default function Procurement() {
  const api = useApi();
  const { dataSource } = useDataSource();
  const [productNames, setProductNames] = useState<Record<string, string>>({});
  const [records, setRecords] = useState<ProcurementRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState('');
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [savingNoteIds, setSavingNoteIds] = useState<Set<string>>(new Set());

  // form state
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  /** 当前记录已关联的产品序号；用于判断用户是否改了序列号（改了就提示会解除关联） */
  const [linkedProductNo, setLinkedProductNo] = useState('');
  const [form, setForm] = useState({
    product_no: '',
    product_name: '',
    product_id: '',
    quantity: '',
    total_amount: '',
    notes: '',
    status: 'in_transit',
    recorded_at: todayLocal(),
  });

  // delete confirm
  const [deleteTarget, setDeleteTarget] = useState<ProcurementRecord | null>(null);

  // list filter
  const [filterProductNo, setFilterProductNo] = useState('');

  // product search
  const [productSearch, setProductSearch] = useState('');
  const [searchResults, setSearchResults] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  const loadRecords = useCallback(async () => {
    try {
      const data = await api.listProcurementRecords();
      setRecords(data);
      // 读成功就把上一次的失败提示收掉：这条横幅原来只写不清，用户改对之后
      // 重试成功、列表也刷新了，屏幕上仍挂着「保存失败」。
      setError('');
    } catch (e: any) {
      setError(e.toString());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRecords(); }, [loadRecords]);
  useEffect(() => {
    let active = true;
    api.getProducts().then(products => {
      if (active) setProductNames(Object.fromEntries(products.map(p => [p.id, p.chinese_product_name || ''])));
    }).catch(() => { /* Rule-based labels remain available without the catalog. */ });
    return () => { active = false; };
  }, [api]);

  const handleSyncToRemote = async () => {
    // 远程模式下 listProcurementRecords 读到的已经是远端数据，再 POST 回 /api/migrate
    // 等于把远端数据覆盖回远端，没有意义且可能覆盖并发写入。
    if (dataSource !== 'local') {
      setError('当前是远程数据源，数据已在远端，无需同步。');
      return;
    }
    setSyncing(true);
    setSyncMsg('');
    setError('');
    try {
      const rows = await api.listProcurementRecords();
      const data = await migrateToRemote([{ table: 'procurement_records', rows }]);
      setSyncMsg(`同步成功！已导出 ${data.imported?.procurement_records ?? rows.length} 条采购记录`);
    } catch (e: any) {
      setError(`同步失败: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  // search products by serial number
  useEffect(() => {
    if (productSearch.length < 1) { setSearchResults([]); setSearchError(''); return; }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const results = await api.searchProductsByNo(productSearch.trim());
        setSearchResults(results);
        setSearchError('');
      } catch (e: unknown) {
        // 读失败 ≠ 没有匹配的产品。以前这里只 setSearchResults([])：下拉不出现，
        // 用户看到的就是「搜不到这个序列号」，于是要么放弃关联（记录挂空），
        // 要么以为产品不存在。现在把失败原因摆出来（PROC-SEARCH-ERR-01）。
        setSearchResults([]);
        setSearchError(e instanceof Error ? e.message : String(e));
      }
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
    setLinkedProductNo(String(p.product_no ?? ''));
    setProductSearch('');
    setSearchResults([]);
  };

  const unitPrice = (() => {
    const qty = parseFloat(form.quantity);
    const total = parseFloat(form.total_amount);
    if (qty > 0 && total > 0) return (total / qty).toFixed(2);
    return '';
  })();

  // 序列号被改掉、但产品关联还指着原产品：这时候提交会写进一条
  // 「序列号属于 A、关联却指向 B」的记录，之后补货、成本、毛利全按错的产品算。
  // 所以关联没解决之前不允许提交，而不是只在旁边给一句提示。
  const linkedNo = linkedProductNo.trim();
  const identityMismatch = !!form.product_id && !!linkedNo && form.product_no.trim() !== linkedNo;

  const resetForm = () => {
    setForm({ product_no: '', product_name: '', product_id: '', quantity: '', total_amount: '', notes: '', status: 'in_transit', recorded_at: todayLocal() });
    setEditingId(null);
    setLinkedProductNo('');
    setShowForm(false);
    setProductSearch('');
    setSearchResults([]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (identityMismatch) {
      setError(`序列号 ${form.product_no.trim() || '（空）'} 与已关联的产品 #${linkedNo} 不一致。请重新选择匹配的产品，或点「解除关联」后再保存。`);
      return;
    }
    const payload = {
      product_id: form.product_id,
      product_no: parseInt(form.product_no) || 0,
      product_name: form.product_name,
      quantity: parseInt(form.quantity) || 0,
      total_amount: parseFloat(form.total_amount) || 0,
      notes: form.notes,
      status: form.status as ProcurementRecord['status'],
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
      status: r.status ?? 'received',
      recorded_at: r.recorded_at ?? '',
    });
    setEditingId(r.id);
    setLinkedProductNo(String(r.product_no ?? ''));
    setShowForm(true);
  };

  const handleDelete = async (r: ProcurementRecord) => {
    setDeleteTarget(r);
  };

  const saveInlineNote = async (record: ProcurementRecord) => {
    const nextNote = (noteDrafts[record.id] ?? record.notes ?? '').trim();
    if (nextNote === (record.notes ?? '').trim() || savingNoteIds.has(record.id)) return;
    setSavingNoteIds(prev => new Set(prev).add(record.id));
    try {
      const updated = await api.updateProcurementRecord(record.id, { notes: nextNote });
      setRecords(prev => prev.map(row => row.id === record.id ? updated : row));
      setNoteDrafts(prev => { const next = { ...prev }; delete next[record.id]; return next; });
      setError('');
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '备注保存失败');
    } finally {
      setSavingNoteIds(prev => { const next = new Set(prev); next.delete(record.id); return next; });
    }
  };

  const changeProcurementStatus = async (record: ProcurementRecord, status: ProcurementRecord['status']) => {
    if (status === record.status) return;
    const message = status === 'received'
      ? '确认已到仓？确认后该采购数量会计入可用库存。'
      : status === 'cancelled'
        ? '确认取消这条采购？该数量不会计入库存。'
        : '确认改回在途？已计入的库存数量会自动移出可用库存。';
    if (!window.confirm(message)) return;
    try {
      const updated = await api.updateProcurementRecord(record.id, { status });
      setRecords(prev => prev.map(row => row.id === record.id ? updated : row));
      setError('');
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '采购状态更新失败');
    }
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

  const formatPrice = (v: number | string | null | undefined) => {
    const amount = toNumber(v);
    return amount != null ? `¥${amount.toFixed(2)}` : '-';
  };

  // total_amount 可能是字符串（"120.00"），直接相加会变成字符串拼接
  const totalProcurement = records.reduce((sum, r) => sum + toAmount(r.total_amount), 0);

  const filteredRecords = filterProductNo.trim()
    ? records.filter(r => String(r.product_no) === filterProductNo.trim())
    : records;
  const filteredTotal = filteredRecords.reduce((sum, r) => sum + toAmount(r.total_amount), 0);

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div className="page procurement-page">
      <div className="page-heading">
        <div className="procurement-heading-copy">
          <h1 style={{ margin: 0 }}>采购记录</h1>
          {records.length > 0 && (
            <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-danger)' }}>
              {filterProductNo.trim() ? (
                <>#{filterProductNo.trim()} 累计采购金额 {formatPrice(filteredTotal)}（{filteredRecords.length} 条记录）</>
              ) : (
                <>累计采购金额 {formatPrice(totalProcurement)}（{records.length} 条记录）</>
              )}
            </span>
          )}
        </div>
        <div className="page-actions">
          <button className="btn btn-outline" onClick={handleSyncToRemote} disabled={syncing || dataSource !== 'local'}
            title={dataSource !== 'local' ? '当前是远程数据源，数据已在远端' : undefined}>
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
            <div className="procurement-form-grid">
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
                    // 不再在每次按键时清空 product_id/product_name：编辑已有记录时
                    // 点一下输入框就会把产品关联永久丢掉，且没有任何提示。
                    // 关联只在用户真正选中搜索结果、或点「解除关联」时才变化。
                    setProductSearch(e.target.value);
                    setForm(prev => ({ ...prev, product_no: e.target.value }));
                  }}
                  style={{ width: '100%', boxSizing: 'border-box' }} />
                {searchResults.length > 0 && (
                  <div style={{
                    position: 'absolute', zIndex: 10, background: 'var(--color-card)', border: '1px solid var(--color-border)',
                    borderRadius: 4, maxHeight: 200, overflow: 'auto', width: '100%', boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                  }}>
                    {searchResults.map(p => (
                      <div key={p.id} onClick={() => selectProduct(p)}
                        style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--color-border)' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-hover)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'var(--color-card)')}>
                        <strong>#{p.product_no}</strong> {p.product_name}
                      </div>
                    ))}
                  </div>
                )}
                {searching && <small style={{ color: 'var(--text-subtle)' }}>搜索中...</small>}
                {searchError && (
                  <small role="alert" style={{ color: 'var(--danger-strong)', display: 'block', marginTop: 4 }}>
                    产品搜索失败：{searchError}。序列号仍可手填，已有的产品关联不会被改动。
                  </small>
                )}
                {identityMismatch && (
                  <small role="alert" style={{ color: 'var(--warning-strong)', display: 'block', marginTop: 4 }}>
                    序列号已改为 {form.product_no.trim() || '（空）'}，当前仍关联原产品 #{linkedProductNo}。
                    先解决关联才能保存，否则这条记录会挂到错的产品上。
                    <button type="button" className="btn btn-sm" style={{ marginLeft: 8 }}
                      onClick={() => { setForm(prev => ({ ...prev, product_id: '', product_name: '' })); setLinkedProductNo(''); }}>
                      解除关联
                    </button>
                  </small>
                )}
              </div>

              {/* product name (auto-filled, editable as fallback) */}
              <div>
                <label>产品名称</label>
                <input type="text" value={form.product_name}
                  onChange={e => setForm(prev => ({ ...prev, product_name: e.target.value }))}
                  style={{ width: '100%', boxSizing: 'border-box' }} />
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
                  style={{ width: '100%', boxSizing: 'border-box', background: 'var(--surface-muted)' }} />
              </div>

              {/* notes */}
              <div>
                <label>备注（选填）</label>
                <input type="text" value={form.notes}
                  onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))}
                  style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>入库状态</label>
                <select value={form.status} onChange={e => setForm(prev => ({ ...prev, status: e.target.value }))} style={{ width: '100%' }}>
                  <option value="in_transit">在途（暂不增加库存）</option>
                  <option value="received">已入库（增加可用库存）</option>
                  <option value="cancelled">已取消</option>
                </select>
              </div>
            </div>

            <div className="page-actions">
              <button type="submit" className="btn btn-primary" disabled={identityMismatch}
                title={identityMismatch ? '序列号与已关联产品不一致，请先重新选择产品或解除关联' : ''}>{editingId ? '保存修改' : '创建记录'}</button>
              <button type="button" className="btn" onClick={resetForm}>取消</button>
            </div>
          </form>
        </div>
      )}

      {/* delete confirm modal */}
      {deleteTarget && (
        <div className="app-modal-backdrop">
          <div className="app-modal" role="dialog" aria-modal="true" aria-label="确认删除采购记录">
            <h3 className="dialog-title">确认删除</h3>
            <p style={{ margin: '0 0 20px', color: 'var(--text-muted)' }}>
              确定删除「{deleteTarget.product_name || deleteTarget.product_no}」的采购记录？
            </p>
            <div className="dialog-actions">
              <button className="btn" onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="btn btn-danger" onClick={confirmDelete}>确认删除</button>
            </div>
          </div>
        </div>
      )}

      {/* list */}
      <div className="procurement-filterbar">
        <input type="text" placeholder="输入产品序号筛选采购记录..." value={filterProductNo}
          onChange={e => setFilterProductNo(e.target.value)}
          style={{ padding: '6px 12px', borderRadius: 10, border: '1px solid var(--color-border)', width: 240 }} />
        {filterProductNo.trim() && (
          <button className="btn btn-sm" onClick={() => setFilterProductNo('')}>清除筛选</button>
        )}
      </div>
      {filteredRecords.length === 0 ? (
        <div style={{ textAlign: 'center', color: 'var(--text-subtle)', padding: 40 }}>暂无采购记录，点击上方按钮新建。</div>
      ) : (
        <div className="table-wrapper procurement-table-scroll"><table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid var(--color-border)', textAlign: 'left' }}>
              <th style={{ padding: '8px 12px' }}>日期</th>
              <th style={{ padding: '8px 12px' }}>序列号</th>
              <th style={{ padding: '8px 12px' }}>产品名称</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>采购数量</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>采购总金额</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>单价</th>
              <th style={{ padding: '8px 12px' }}>备注</th>
              <th style={{ padding: '8px 12px' }}>入库状态</th>
              <th style={{ padding: '8px 12px' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredRecords.map(r => (
              <tr key={r.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                {/* recorded_at 正常是 date 输入给的 YYYY-MM-DD；老数据里可能带完整 ISO 时间，
                    直接渲染会在列表里露出一串 T09:00:00Z。只取日期部分。 */}
                <td style={{ padding: '8px 12px' }}>{(r.recorded_at || '').slice(0, 10)}</td>
                <td style={{ padding: '8px 12px' }}>{r.product_no}</td>
                <td style={{ padding: '8px 12px', maxWidth: 360 }} title={r.product_name || ''}>
                  {(() => { const label = productLabels(r.product_name, productNames[r.product_id || '']); return <div className="procurement-product-label"><strong>{label.chinese}{label.features && <span> · {label.features}</span>}</strong><small>{label.english}</small></div>; })()}
                </td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{r.quantity}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatPrice(r.total_amount)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatPrice(r.unit_price)}</td>
                <td style={{ padding: '6px 12px', width: 210 }}>
                  <div className="procurement-note-cell">
                    <input
                      value={noteDrafts[r.id] ?? r.notes ?? ''}
                      placeholder="直接填写备注…"
                      aria-label={`产品 ${r.product_no} 的采购备注`}
                      onChange={event => setNoteDrafts(prev => ({ ...prev, [r.id]: event.target.value }))}
                      onBlur={() => saveInlineNote(r)}
                      onKeyDown={event => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          event.currentTarget.blur();
                        }
                      }}
                    />
                    {savingNoteIds.has(r.id) && <small>保存中</small>}
                  </div>
                </td>
                <td style={{ padding: '6px 12px' }}>
                  <select className={`procurement-status ${r.status || 'received'}`} value={r.status || 'received'} onChange={event => changeProcurementStatus(r, event.target.value as ProcurementRecord['status'])}>
                    <option value="in_transit">在途</option><option value="received">已入库</option><option value="cancelled">已取消</option>
                  </select>
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <button className="btn btn-sm" onClick={() => startEdit(r)} style={{ marginRight: 6 }}>编辑</button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(r)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
