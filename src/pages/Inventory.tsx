import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createInventoryAdjustment, exportInboundTemplate, getImageUrl, getInventory, getInventoryAdjustments, getOperationLogs, getShipments, getStoreStorageKey, reverseInventoryAdjustment, voidShipment } from '../remoteApi';
import type { InventoryAdjustment, InventoryRow, OperationLog, ShipmentRecord } from '../types';
import { ProductImage } from '../utils/imageFallback';
import { beijingDateTime, todayLocal } from '../utils/datetime';
import { applySettled, failureSummary } from '../utils/settle';

import ShipmentReconciliation from '../operations/ShipmentReconciliation';

type Tab = 'inventory' | 'shipments' | 'adjustments' | 'activity';
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
  const [form, setForm] = useState({ mode: 'increase', quantity: '', reason: '盘点调整', notes: '', occurred_at: todayLocal() });
  const adjustmentPending = useRef(false);
  const [savingAdjustment, setSavingAdjustment] = useState(false);
  const [exportingInbound, setExportingInbound] = useState(false);
  const [selectedShipmentIds, setSelectedShipmentIds] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    // 分区降级：四个区块互不依赖，任一失败不应让其余数据一起消失
    const results = await Promise.allSettled([getInventory(), getShipments(), getInventoryAdjustments(), getOperationLogs()]);
    const failures: string[] = [];
    applySettled(results[0], '库存', setRows, failures);
    applySettled(results[1], '发货单', setShipments, failures);
    applySettled(results[2], '库存调整', setAdjustments, failures);
    applySettled(results[3], '操作日志', setActivity, failures);
    setError(failureSummary(failures));
    setLoading(false);
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
    if (adjustmentPending.current) return;
    const quantity = Number(form.quantity);
    if (!adjustSku || !Number.isSafeInteger(quantity) || quantity < 1) { setError('请选择 SKU，并填写大于 0 的整数数量'); return; }
    adjustmentPending.current = true;
    setSavingAdjustment(true); setError(''); setMessage('');
    try {
      await createInventoryAdjustment({ sku: adjustSku, quantity_delta: form.mode === 'increase' ? quantity : -quantity, reason: form.reason, notes: form.notes, occurred_at: form.occurred_at });
      setAdjustSku(''); setForm(prev => ({ ...prev, quantity: '', notes: '' })); setMessage('库存调整已记录'); await load();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { adjustmentPending.current = false; setSavingAdjustment(false); }
  };

  // 撤回会不会动库存：只有这两种状态扣过库存——confirmed 记在「已发货」，
  // transit 记在「海外在途」。按钮文案、确认提示、撤回后是否恢复装箱草稿
  // 都用这一个判据，三处各写一份的话必然漂：原来按钮只认 confirmed，
  // transit 那行写着「撤回记录」，点下去却把库存退回来、还跳去装箱页恢复草稿。
  const deductsStock = (status: string) => status === 'confirmed' || status === 'transit';

  const withdrawShipment = async (shipment: ShipmentRecord) => {
    const affectsInventory = deductsStock(shipment.status);
    const prompt = affectsInventory
      ? `确认撤回 ${shipment.shipment_no}？库存会自动返还，并恢复为可修改草稿。`
      : `确认撤回装箱单 ${shipment.shipment_no}？库存不会变化。`;
    if (!window.confirm(prompt)) return;
    try {
      await voidShipment(shipment.id, affectsInventory ? '装箱单有误，撤回修改' : '撤回未扣库存的装箱单');
      if (affectsInventory) {
        localStorage.setItem(getStoreStorageKey('packingDraftV1'), JSON.stringify({
          selected: shipment.lines.map(line => line.sku),
          lines: Object.fromEntries(shipment.lines.map(line => [line.sku, { cartons: String(line.cartons), count: String(line.count_per_carton) }])),
          exportForm: { date: shipment.shipment_date, mark: shipment.mark, shipping: shipment.shipping, address: shipment.address },
          draftKey: draftKey(),
        }));
        navigate('/packing');
        return;
      }
      setMessage('装箱单记录已撤回，库存未变化');
      await load();
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
      const { blob, summary } = await exportInboundTemplate(selectedShipmentIds);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `入仓_${selectedShipmentIds.length}个装箱单.xlsx`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      // 按箱生成：summary 里是 OMS 真正会建出来的箱数和件数
      const detail = summary ? `${summary.cartons} 箱、${summary.pieces} 件` : `${selectedShipmentIds.length} 个装箱单`;
      const overflow = summary?.overflow?.length
        ? `；注意 ${summary.overflow.map(o => `箱 ${o.box_no} 有 ${o.skus} 个 SKU`).join('、')}，OMS 箱唛只放得下 5 行，会多印一页且那一页没有箱号`
        : '';
      setMessage(`已生成 OMS 入仓文件：${detail}${overflow}`);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setExportingInbound(false); }
  };

  const totalAvailable = rows.reduce((sum, row) => sum + row.available, 0);
  const totalInTransit = rows.reduce((sum, row) => sum + (row.in_transit || 0), 0);
  const lowStock = rows.filter(row => row.sku && row.available < 0).length;

  return (
    <div className="page inventory-page">
      <div className="inventory-heading">
        <div><h1>库存管理</h1><p>自 2026-09-24 起重新计算 · 库存余额、人工调整和发货记录统一留痕，可撤回，不覆盖历史。</p></div>
        <div className="inventory-kpis">
          <span><strong>{totalAvailable}</strong> 可用库存</span>
          <span><strong>{totalInTransit}</strong> 国内采购在途</span><span><strong>{rows.reduce((sum, row) => sum + (row.overseas_in_transit || 0), 0)}</strong> 海外在途</span>
          <span><strong>{shipments.filter(x => x.status === 'confirmed' || x.status === 'transit').length}</strong> 已发货单</span>
          {lowStock > 0 && <span className="warning"><strong>{lowStock}</strong> 负库存</span>}
        </div>
      </div>
      <div className="inventory-tabs">
        <button className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>库存余额</button>
        <button className={tab === 'shipments' ? 'active' : ''} onClick={() => setTab('shipments')}>装箱 / 发货记录</button>
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
          <select disabled={savingAdjustment} value={form.mode} onChange={e => setForm(prev => ({ ...prev, mode: e.target.value }))}><option value="increase">增加库存</option><option value="decrease">减少库存</option></select>
          <input disabled={savingAdjustment} type="number" min="1" placeholder="数量" value={form.quantity} onChange={e => setForm(prev => ({ ...prev, quantity: e.target.value }))} />
          <input disabled={savingAdjustment} value={form.reason} onChange={e => setForm(prev => ({ ...prev, reason: e.target.value }))} placeholder="原因" />
          <input disabled={savingAdjustment} type="date" value={form.occurred_at} onChange={e => setForm(prev => ({ ...prev, occurred_at: e.target.value }))} />
          <input disabled={savingAdjustment} value={form.notes} onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))} placeholder="备注（可选）" />
          <button className="btn btn-primary" disabled={savingAdjustment} onClick={submitAdjustment}>{savingAdjustment ? '记录中...' : '确认记录'}</button><button className="btn" disabled={savingAdjustment} onClick={() => setAdjustSku('')}>取消</button>
        </div>}
        <div className="inventory-table-wrap"><table className="inventory-table">
          <thead><tr><th>产品</th><th>SKU</th><th>国内采购在途</th><th>海外在途</th><th>采购入库</th><th>人工调整</th><th>已发货</th><th>可用库存</th><th>操作</th></tr></thead>
          <tbody>{filtered.map(row => <tr key={row.product_id}>
            <td><div className="inventory-product">{row.image_url ? <ProductImage src={getImageUrl(row.image_url)} /> : <span className="inventory-no-image">无图</span>}<div><strong>{row.variant_group && <span className={row.is_primary_variant ? 'variant-role primary' : 'variant-role'}>{row.is_primary_variant ? '主' : '分支'}</span>}{row.name}</strong><small>{row.name_en}</small></div></div></td>
            <td className="mono">{row.sku || <span className="muted">缺少 SKU</span>}</td>
            <td className="stock-transit">{row.in_transit || 0}</td><td className="stock-transit">{row.overseas_in_transit || 0}</td><td>{row.purchased}</td><td>{row.adjusted > 0 ? `+${row.adjusted}` : row.adjusted}</td><td>{row.shipped}</td>
            <td><strong className={row.available < 0 ? 'stock-danger' : 'stock-value'}>{row.available}</strong></td>
            <td><button className="btn btn-sm" disabled={!row.sku || savingAdjustment} onClick={() => setAdjustSku(row.sku)}>调整库存</button></td>
          </tr>)}</tbody>
        </table></div>
      </>}

      {tab === 'shipments' && <><ShipmentReconciliation replaceIds={selectedShipmentIds} onSaved={async () => { setSelectedShipmentIds([]); await load(); }} /><div className="inventory-toolbar"><button className="btn btn-primary" disabled={!selectedShipmentIds.length || exportingInbound} onClick={downloadInbound}>{exportingInbound ? '生成中...' : `生成入仓文件（已选 ${selectedShipmentIds.length} 单）`}</button><span className="muted">勾选顺序决定标识号：第 1 单为 1，第 2 单为 2，以此类推</span></div><div className="inventory-table-wrap"><table className="inventory-table">
        <thead><tr><th>选择</th><th>发货单号</th><th>发货日期</th><th>SKU 数</th><th>总件数</th><th>运输方式</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{shipments.map(row => <tr key={row.id}>
          <td>{row.status !== 'void' ? <input type="checkbox" checked={selectedShipmentIds.includes(row.id)} onChange={e => setSelectedShipmentIds(prev => e.target.checked ? [...prev, row.id] : prev.filter(id => id !== row.id))} /> : null}</td>
          <td className="mono">{row.shipment_no}</td><td>{row.shipment_date}</td><td>{row.total_skus}</td><td>{row.total_quantity}</td><td>{row.shipping || '-'}</td>
          <td><span className={row.status === 'void' ? 'status-void' : 'status-confirmed'}>{row.status === 'transit' ? '已发货 · 海外在途' : row.status === 'confirmed' ? '已发货' : row.status === 'clearance' ? '老库存清理·不扣库存' : row.status === 'exported' ? '仅导出·未扣库存' : '已撤回'}</span></td>
          <td>{row.status !== 'void' ? <button className="btn btn-sm" onClick={() => withdrawShipment(row)}>{deductsStock(row.status) ? '撤回并修改' : '撤回记录'}</button> : <span className="muted">{row.void_reason}</span>}</td>
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
          <td>{beijingDateTime(row.created_at)}</td>
          <td>{row.entity_type === 'inventory' ? '库存' : row.entity_type === 'shipment' ? '发货' : row.entity_type}</td>
          <td>{row.action === 'adjust' ? '调整' : row.action === 'reverse' ? '撤销' : row.action === 'void' ? '撤回' : row.action}</td>
          <td>{row.summary}</td>
        </tr>)}</tbody>
      </table></div>}
      {loading && <div className="loading">正在更新库存...</div>}
    </div>
  );
}

