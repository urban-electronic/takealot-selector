import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createInventoryAdjustment, exportInboundTemplate, getImageUrl, getInventory, getInventoryAdjustments, getOperationLogs, getShipments, getStoreStorageKey, reverseInventoryAdjustment, voidShipment } from '../remoteApi';
import type { InventoryAdjustment, InventoryRow, OperationLog, ShipmentRecord } from '../types';

type Tab = 'inventory' | 'shipments' | 'adjustments' | 'activity';
const today = () => new Date().toISOString().slice(0, 10);
const draftKey = () => globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}`;

export default function Inventory() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [rows, setRows] = useState<InventoryRow[]>([]);
  const [shipments, setShipments] = useState<ShipmentRecord[]>([]);
  const [adjustments, setAdjustments] = useState<InventoryAdjustment[]>([]);
  const [activity, setActivity] = useState<OperationLog[]>([]);
  const [tab, setTab] = useState<Tab>('inventory');
  const [search, setSearch] = useState('');
  const [stockFilter, setStockFilter] = useState(() => searchParams.get('stock') || 'all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [adjustSku, setAdjustSku] = useState('');
  const [form, setForm] = useState({ mode: 'increase', quantity: '', reason: '盘点调整', notes: '', occurred_at: today() });
  const [exportingInbound, setExportingInbound] = useState(false);
  const [selectedShipmentIds, setSelectedShipmentIds] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [inventory, shipmentRows, adjustmentRows, activityRows] = await Promise.all([getInventory(), getShipments(), getInventoryAdjustments(), getOperationLogs()]);
      setRows(inventory); setShipments(shipmentRows); setAdjustments(adjustmentRows); setActivity(activityRows);
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
      localStorage.setItem(getStoreStorageKey('packingDraftV1'), JSON.stringify({
        selected: shipment.lines.map(line => line.sku),
        lines: Object.fromEntries(shipment.lines.map(line => [line.sku, { cartons: String(line.cartons), count: String(line.count_per_carton) }])),
        exportForm: { date: shipment.shipment_date, mark: shipment.mark, shipping: shipment.shipping, address: shipment.address },
        draftKey: draftKey(),
      }));
      navigate('/packing');
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const undoAdjustment = async (row: InventoryAdjustment) => {
    if (!window.confirm(`确认撤销 ${row.sku} 的库存调整 ${row.quantity_delta > 0 ? '+' : ''}${row.quantity_delta}？`)) return;
    setError(''); setMessage('');
    try {
      await reverseInventoryAdjustment(row.id);
      setMessage('库存调整已撤销');
      await load();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  };


  const downloadInbound = async () => {
    if (selectedShipmentIds.length === 0) { setError('请先勾选至少一个有效装箱单'); return; }
    setExportingInbound(true); setError('');
    try {
      const blob = await exportInboundTemplate(selectedShipmentIds);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `入仓_${selectedShipmentIds.length}个装箱单.xlsx`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      setMessage(`已按选择顺序生成 ${selectedShipmentIds.length} 个装箱单对应的入仓文件`);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setExportingInbound(false); }
  };

  const totalAvailable = rows.reduce((sum, row) => sum + row.available, 0);
  const totalInTransit = rows.reduce((sum, row) => sum + (row.in_transit || 0), 0);
  const lowStock = rows.filter(row => row.sku && row.available <= 0).length;

  return (
    <div className="page inventory-page">
      <div className="inventory-heading">
        <div><h1>库存管理</h1><p>自 2026-09-24 起重新计算 · 库存余额、人工调整和发货记录统一留痕，可撤回，不覆盖历史。</p></div>
        <div className="inventory-kpis">
          <span><strong>{totalAvailable}</strong> 可用库存</span>
          <span><strong>{totalInTransit}</strong> 在途库存</span>
          <span><strong>{shipments.filter(x => x.status === 'confirmed').length}</strong> 已发货单</span>
          <span className={lowStock ? 'warning' : ''}><strong>{lowStock}</strong> 零/负库存</span>
        </div>
      </div>
      <div className="inventory-tabs">
        <button className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>库存余额</button>
        <button className={tab === 'shipments' ? 'active' : ''} onClick={() => setTab('shipments')}>发货记录</button>
        <button className={tab === 'adjustments' ? 'active' : ''} onClick={() => setTab('adjustments')}>调整流水</button>
        <button className={tab === 'activity' ? 'active' : ''} onClick={() => setTab('activity')}>操作历史</button>
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
          <thead><tr><th>产品</th><th>SKU</th><th>在途</th><th>采购入库</th><th>人工调整</th><th>已发货</th><th>可用库存</th><th>操作</th></tr></thead>
          <tbody>{filtered.map(row => <tr key={row.product_id}>
            <td><div className="inventory-product">{row.image_url ? <img src={getImageUrl(row.image_url)} alt="" /> : <span className="inventory-no-image">无图</span>}<div><strong>{row.variant_group && <span className={row.is_primary_variant ? 'variant-role primary' : 'variant-role'}>{row.is_primary_variant ? '主' : '分支'}</span>}{row.name}</strong><small>{row.name_en}</small></div></div></td>
            <td className="mono">{row.sku || <span className="muted">缺少 SKU</span>}</td>
            <td className="stock-transit">{row.in_transit || 0}</td><td>{row.purchased}</td><td>{row.adjusted > 0 ? `+${row.adjusted}` : row.adjusted}</td><td>{row.shipped}</td>
            <td><strong className={row.available <= 0 ? 'stock-danger' : 'stock-value'}>{row.available}</strong></td>
            <td><button className="btn btn-sm" disabled={!row.sku} onClick={() => setAdjustSku(row.sku)}>调整库存</button></td>
          </tr>)}</tbody>
        </table></div>
      </>}

      {tab === 'shipments' && <><div className="inventory-toolbar"><button className="btn btn-primary" disabled={!selectedShipmentIds.length || exportingInbound} onClick={downloadInbound}>{exportingInbound ? '生成中...' : `生成入仓文件（已选 ${selectedShipmentIds.length} 单）`}</button><span className="muted">勾选顺序决定标识号：第 1 单为 1，第 2 单为 2，以此类推</span></div><div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>选择</th><th>发货单号</th><th>发货日期</th><th>SKU 数</th><th>总件数</th><th>运输方式</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{shipments.map(row => <tr key={row.id}>
          <td>{row.status === 'confirmed' ? <input type="checkbox" checked={selectedShipmentIds.includes(row.id)} onChange={e => setSelectedShipmentIds(prev => e.target.checked ? [...prev, row.id] : prev.filter(id => id !== row.id))} /> : null}</td>
          <td className="mono">{row.shipment_no}</td><td>{row.shipment_date}</td><td>{row.total_skus}</td><td>{row.total_quantity}</td><td>{row.shipping || '-'}</td>
          <td><span className={row.status === 'confirmed' ? 'status-confirmed' : 'status-void'}>{row.status === 'confirmed' ? '已发货' : '已撤回'}</span></td>
          <td>{row.status === 'confirmed' ? <button className="btn btn-sm" onClick={() => withdrawShipment(row)}>撤回并修改</button> : <span className="muted">{row.void_reason}</span>}</td>
        </tr>)}</tbody>
      </table></div></>}

      {tab === 'adjustments' && <div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>日期</th><th>SKU</th><th>数量变化</th><th>原因</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>{adjustments.map(row => <tr key={row.id}>
          <td>{row.occurred_at}</td><td className="mono">{row.sku}</td><td className={row.quantity_delta > 0 ? 'stock-value' : 'stock-danger'}>{row.quantity_delta > 0 ? '+' : ''}{row.quantity_delta}</td>
          <td>{row.reason}</td><td>{row.notes || '-'}</td><td>{row.reversed_by ? <span className="muted">已撤销</span> : <button className="btn btn-sm" onClick={() => undoAdjustment(row)}>撤销</button>}</td>
        </tr>)}</tbody>
      </table></div>}
      {tab === 'activity' && <div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>时间</th><th>类型</th><th>操作</th><th>内容</th></tr></thead>
        <tbody>{activity.map(row => <tr key={row.id}>
          <td>{row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '-'}</td>
          <td>{row.entity_type === 'inventory' ? '库存' : row.entity_type === 'shipment' ? '发货' : row.entity_type}</td>
          <td>{row.action === 'adjust' ? '调整' : row.action === 'reverse' ? '撤销' : row.action === 'void' ? '撤回' : row.action}</td>
          <td>{row.summary}</td>
        </tr>)}</tbody>
      </table></div>}
      {loading && <div className="loading">正在更新库存...</div>}
    </div>
  );
}
