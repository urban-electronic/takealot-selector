import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createInventoryAdjustment, getImageUrl, getInventory, getInventoryAdjustments, getShipments, reverseInventoryAdjustment, voidShipment } from '../remoteApi';
import type { InventoryAdjustment, InventoryRow, ShipmentRecord } from '../types';

type Tab = 'inventory' | 'shipments' | 'adjustments';
const today = () => new Date().toISOString().slice(0, 10);
const draftKey = () => globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}`;

export default function Inventory() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<InventoryRow[]>([]);
  const [shipments, setShipments] = useState<ShipmentRecord[]>([]);
  const [adjustments, setAdjustments] = useState<InventoryAdjustment[]>([]);
  const [tab, setTab] = useState<Tab>('inventory');
  const [search, setSearch] = useState('');
  const [stockFilter, setStockFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [adjustSku, setAdjustSku] = useState('');
  const [form, setForm] = useState({ mode: 'increase', quantity: '', reason: '盘点调整', notes: '', occurred_at: today() });

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [inventory, shipmentRows, adjustmentRows] = await Promise.all([getInventory(), getShipments(), getInventoryAdjustments()]);
      setRows(inventory); setShipments(shipmentRows); setAdjustments(adjustmentRows);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return rows.filter(row => {
      if (stockFilter === 'positive' && row.available <= 0) return false;
      if (stockFilter === 'zero' && row.available !== 0) return false;
      if (stockFilter === 'negative' && row.available >= 0) return false;
      return !keyword || `${row.sku} ${row.name} ${row.name_en}`.toLowerCase().includes(keyword);
    }).sort((a, b) => b.available - a.available || (a.product_no ?? 0) - (b.product_no ?? 0));
  }, [rows, search, stockFilter]);

  const submitAdjustment = async () => {
    const quantity = Number.parseInt(form.quantity, 10);
    if (!adjustSku || !Number.isInteger(quantity) || quantity < 1) { setError('请选择 SKU，并填写大于 0 的整数数量'); return; }
    try {
      await createInventoryAdjustment({ sku: adjustSku, quantity_delta: form.mode === 'increase' ? quantity : -quantity, reason: form.reason, notes: form.notes, occurred_at: form.occurred_at });
      setAdjustSku(''); setForm(prev => ({ ...prev, quantity: '', notes: '' })); setMessage('库存调整已记录'); await load();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const withdrawShipment = async (shipment: ShipmentRecord) => {
    if (!window.confirm(`确认撤回 ${shipment.shipment_no}？库存会自动返还，并恢复为可修改草稿。`)) return;
    try {
      await voidShipment(shipment.id, '装箱单有误，撤回修改');
      localStorage.setItem('packingDraftV1', JSON.stringify({
        selected: shipment.lines.map(line => line.sku),
        lines: Object.fromEntries(shipment.lines.map(line => [line.sku, { cartons: String(line.cartons), count: String(line.count_per_carton) }])),
        exportForm: { date: shipment.shipment_date, mark: shipment.mark, shipping: shipment.shipping, address: shipment.address },
        draftKey: draftKey(),
      }));
      navigate('/packing');
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const totalAvailable = rows.reduce((sum, row) => sum + row.available, 0);
  const lowStock = rows.filter(row => row.sku && row.available <= 0).length;

  return (
    <div className="page inventory-page">
      <div className="inventory-heading">
        <div><h1>库存管理</h1><p>自 2026-09-24 起重新计算 · 库存余额、人工调整和发货记录统一留痕，可撤回，不覆盖历史。</p></div>
        <div className="inventory-kpis">
          <span><strong>{totalAvailable}</strong> 可用库存</span>
          <span><strong>{shipments.filter(x => x.status === 'confirmed').length}</strong> 已发货单</span>
          <span className={lowStock ? 'warning' : ''}><strong>{lowStock}</strong> 零/负库存</span>
        </div>
      </div>
      <div className="inventory-tabs">
        <button className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>库存余额</button>
        <button className={tab === 'shipments' ? 'active' : ''} onClick={() => setTab('shipments')}>发货记录</button>
        <button className={tab === 'adjustments' ? 'active' : ''} onClick={() => setTab('adjustments')}>调整流水</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {message && <div className="alert alert-success">{message}</div>}

      {tab === 'inventory' && <>
        <div className="inventory-toolbar">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索 SKU、中文或英文品名" />
          <select value={stockFilter} onChange={e => setStockFilter(e.target.value)}>
            <option value="all">全部库存</option><option value="positive">有库存</option><option value="zero">零库存</option><option value="negative">负库存</option>
          </select>
        </div>
        {adjustSku && <div className="inventory-adjust-strip">
          <strong>调整 {adjustSku}</strong>
          <select value={form.mode} onChange={e => setForm(prev => ({ ...prev, mode: e.target.value }))}><option value="increase">增加库存</option><option value="decrease">减少库存</option></select>
          <input type="number" min="1" placeholder="数量" value={form.quantity} onChange={e => setForm(prev => ({ ...prev, quantity: e.target.value }))} />
          <input value={form.reason} onChange={e => setForm(prev => ({ ...prev, reason: e.target.value }))} placeholder="原因" />
          <input type="date" value={form.occurred_at} onChange={e => setForm(prev => ({ ...prev, occurred_at: e.target.value }))} />
          <input value={form.notes} onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))} placeholder="备注（可选）" />
          <button className="btn btn-primary" onClick={submitAdjustment}>确认记录</button><button className="btn" onClick={() => setAdjustSku('')}>取消</button>
        </div>}
        <div className="inventory-table-wrap"><table className="inventory-table">
          <thead><tr><th>产品</th><th>SKU</th><th>采购入库</th><th>人工调整</th><th>已发货</th><th>可用库存</th><th>操作</th></tr></thead>
          <tbody>{filtered.map(row => <tr key={row.product_id}>
            <td><div className="inventory-product">{row.image_url ? <img src={getImageUrl(row.image_url)} alt="" /> : <span className="inventory-no-image">无图</span>}<div><strong>{row.name}</strong><small>{row.name_en}</small></div></div></td>
            <td className="mono">{row.sku || <span className="muted">缺少 SKU</span>}</td>
            <td>{row.purchased}</td><td>{row.adjusted > 0 ? `+${row.adjusted}` : row.adjusted}</td><td>{row.shipped}</td>
            <td><strong className={row.available <= 0 ? 'stock-danger' : 'stock-value'}>{row.available}</strong></td>
            <td><button className="btn btn-sm" disabled={!row.sku} onClick={() => setAdjustSku(row.sku)}>调整库存</button></td>
          </tr>)}</tbody>
        </table></div>
      </>}

      {tab === 'shipments' && <div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>发货单号</th><th>发货日期</th><th>SKU 数</th><th>总件数</th><th>运输方式</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{shipments.map(row => <tr key={row.id}>
          <td className="mono">{row.shipment_no}</td><td>{row.shipment_date}</td><td>{row.total_skus}</td><td>{row.total_quantity}</td><td>{row.shipping || '-'}</td>
          <td><span className={row.status === 'confirmed' ? 'status-confirmed' : 'status-void'}>{row.status === 'confirmed' ? '已发货' : '已撤回'}</span></td>
          <td>{row.status === 'confirmed' ? <button className="btn btn-sm" onClick={() => withdrawShipment(row)}>撤回并修改</button> : <span className="muted">{row.void_reason}</span>}</td>
        </tr>)}</tbody>
      </table></div>}

      {tab === 'adjustments' && <div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>日期</th><th>SKU</th><th>数量变化</th><th>原因</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>{adjustments.map(row => <tr key={row.id}>
          <td>{row.occurred_at}</td><td className="mono">{row.sku}</td><td className={row.quantity_delta > 0 ? 'stock-value' : 'stock-danger'}>{row.quantity_delta > 0 ? '+' : ''}{row.quantity_delta}</td>
          <td>{row.reason}</td><td>{row.notes || '-'}</td><td>{row.reversed_by ? <span className="muted">已撤销</span> : <button className="btn btn-sm" onClick={async () => { await reverseInventoryAdjustment(row.id); await load(); }}>撤销</button>}</td>
        </tr>)}</tbody>
      </table></div>}
      {loading && <div className="loading">正在更新库存...</div>}
    </div>
  );
}
