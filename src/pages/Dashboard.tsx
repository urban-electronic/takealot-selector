import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import { auditProductSkus, getInventory, getShipments, updateProduct } from '../remoteApi';
import type { SkuAuditResult } from '../remoteApi';
import type { DashboardStats, InventoryRow, ProcurementRecord, Product, ShipmentRecord } from '../types';
import { formatPercent } from '../types';
import { missingSkuTasks } from '../utils/dashboardTasks';

export default function Dashboard() {
  const api = useApi();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [shipments, setShipments] = useState<ShipmentRecord[]>([]);
  const [procurements, setProcurements] = useState<ProcurementRecord[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [auditing, setAuditing] = useState(false);
  const [audit, setAudit] = useState<SkuAuditResult | null>(null);
  const [auditError, setAuditError] = useState('');
  const [fixingProductId, setFixingProductId] = useState('');
  const [auditMessage, setAuditMessage] = useState('');
  const checkSkus = async () => {
    setAuditing(true); setAuditError(''); setAuditMessage('');
    try { setAudit(await auditProductSkus()); }
    catch (e: unknown) { setAuditError(e instanceof Error ? e.message : String(e)); }
    finally { setAuditing(false); }
  };

  const applyReferenceSku = async (issue: SkuAuditResult['issues'][number]) => {
    const nextSku = issue.reference_skus.join('\n');
    if (!nextSku || !window.confirm(`确认把产品 ${issue.product_no} 的 SKU 改为卖家导出表中的值？\n\n${issue.reference_skus.join('、')}\n\n修改后原有 SKU 会被替换。`)) return;
    setFixingProductId(issue.product_id); setAuditError(''); setAuditMessage('');
    try {
      await updateProduct(issue.product_id, { sku: nextSku });
      setAuditMessage(`产品 ${issue.product_no} 已按卖家表修正 SKU`);
      setAudit(await auditProductSkus());
    } catch (e: unknown) {
      setAuditError(e instanceof Error ? e.message : String(e));
    } finally {
      setFixingProductId('');
    }
  };

  useEffect(() => {
    Promise.all([api.getDashboard(), getInventory(), getShipments(), api.listProcurementRecords(), api.getProducts()])
      .then(([dashboard, inventoryRows, shipmentRows, procurementRows, productRows]) => {
        setStats(dashboard); setInventory(inventoryRows); setShipments(shipmentRows); setProcurements(procurementRows); setProducts(productRows);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [api]);

  const overview = useMemo(() => {
    const validInventory = inventory.filter(row => row.sku);
    const skuOwners = new Map<string, Set<string>>();
    let unresolvedMultiSkuProducts = 0;
    products.forEach(product => {
      const tokens = (product.sku || '').split(/[\s,，;；]+/).map(value => value.trim()).filter(Boolean);
      const labels = (product.chinese_product_name || '').split(/[\r\n]+/).map(value => value.trim()).filter(Boolean);
      if (tokens.length > 1 && (labels.length !== tokens.length || labels.some(label => /待识别|待确认|历史分支/.test(label)))) unresolvedMultiSkuProducts += 1;
      new Set(tokens).forEach(sku => {
        if (!skuOwners.has(sku)) skuOwners.set(sku, new Set());
        skuOwners.get(sku)!.add(product.id);
      });
    });
    return {
      available: validInventory.reduce((sum, row) => sum + row.available, 0),
      negative: validInventory.filter(row => row.available < 0).length,
      missingSku: missingSkuTasks(inventory, products).blocking,
      unpurchasedMissingSku: missingSkuTasks(inventory, products).unpurchased,
      missingImage: products.filter(row => !row.product_image_url && !row.product_image_path).length,
      unresolvedMultiSkuProducts,
      duplicateSkuProducts: new Set(Array.from(skuOwners.values()).filter(ids => ids.size > 1).flatMap(ids => Array.from(ids))).size,
      confirmedShipments: shipments.filter(row => row.status === 'confirmed'),
      purchasedUnits: procurements.reduce((sum, row) => sum + (Number(row.quantity) || 0), 0),
      inTransitUnits: procurements.filter(row => row.status === 'in_transit').reduce((sum, row) => sum + (Number(row.quantity) || 0), 0),
    };
  }, [inventory, shipments, procurements, products]);

  const taskGroups = [
    { label: '阻塞处理', detail: '会影响装箱、库存或发货', items: [
      { count: overview.missingSku, label: '产品缺少 SKU', detail: 'SKU 是库存与装箱匹配的基础', to: '/products?missing_field=sku&task=补充SKU', tone: 'red' },
      { count: overview.duplicateSkuProducts, label: '重复 SKU 冲突', detail: '同一 SKU 出现在多个商品中', to: '/products?duplicate_sku=1&task=处理重复SKU', tone: 'red' },
    ] },
    { label: '资料完善', detail: '不阻塞操作，可逐步处理', items: [
      { count: overview.unpurchasedMissingSku, label: '未购选品尚无 SKU', detail: '未购买且无库存活动；采购或上架前再补充', to: '/products?missing_field=sku&task=未购选品SKU', tone: 'blue' },
      { count: stats?.data_incomplete || 0, label: '产品资料待补充', detail: '补齐成本、尺寸和物流信息', to: '/products?selection_status=数据待补充&task=补充产品资料', tone: 'orange' },
      { count: stats?.category_pending || 0, label: '品类待确认', detail: '只显示真正没有有效 Fee 类型的产品', to: '/products?selection_status=待确认品类&task=确认产品品类', tone: 'blue' },
      { count: overview.unresolvedMultiSkuProducts, label: '规格仍待识别', detail: '已识别真实颜色、尺码的产品不会计入', to: '/products?multi_sku=1&task=确认未识别规格', tone: 'blue' },
      { count: overview.missingImage, label: '产品缺少图片', detail: '补图后同步至装箱单', to: '/products?missing_field=image&task=补充产品图片', tone: 'purple' },
    ] },
    { label: '运营提醒', detail: '属于库存状态，不算资料错误', items: [
      { count: overview.inTransitUnits, label: '在途采购', detail: '到仓确认后才增加可用库存', to: '/procurement', tone: 'orange' },
      { count: overview.negative, label: '负库存', detail: '核对发货或进行盘点调整', to: '/inventory?stock=negative', tone: 'purple' },
    ] },
  ];
  const visibleTaskGroups = taskGroups
    .map(group => ({ ...group, items: group.items.filter(task => task.count > 0) }))
    .filter(group => group.items.length > 0);

  if (loading) return <div className="loading">正在汇总经营数据...</div>;
  if (error) return <div className="alert alert-error">仪表盘加载失败：{error}</div>;
  if (!stats) return <div className="loading">暂无数据</div>;

  return (
    <div className="dashboard-page">
      <div className="dashboard-heading">
        <div><h1>运营工作台</h1><p>把需要处理的问题、库存、采购和发货集中到一个页面。</p></div>
        <div className="dashboard-actions">
          <Link to="/create" className="btn btn-primary">新建产品</Link>
          <Link to="/packing" className="btn btn-outline">创建装箱单</Link>
        </div>
      </div>

      <section className="dashboard-metrics">
        <Link to="/products"><span>产品总数</span><strong>{stats.total}</strong><small>{stats.qualified} 个合格选品</small></Link>
        <Link to="/inventory"><span>可用库存</span><strong>{overview.available}</strong><small>自 2026-09-24 起计算</small></Link>
        <Link to="/procurement"><span>采购状态</span><strong>{overview.inTransitUnits}</strong><small>在途 · 累计采购 {overview.purchasedUnits} 件</small></Link>
        <Link to="/inventory"><span>有效发货单</span><strong>{overview.confirmedShipments.length}</strong><small>{overview.confirmedShipments.reduce((s, x) => s + x.total_quantity, 0)} 件已发货</small></Link>
      </section>

      <div className="dashboard-grid">
        <section className="dashboard-panel dashboard-tasks">
          <div className="dashboard-panel-title"><div><h2>待处理事项</h2><p>优先解决会阻塞采购、库存和发货的问题。</p></div><button className="btn btn-outline btn-sm" disabled={auditing} onClick={checkSkus} title="按已导入的Takealot卖家导出表核对SKU，不修改产品或库存">{auditing ? '核对中...' : 'SKU 自检'}</button></div>
          {auditError && <div className="alert alert-error">处理失败：{auditError} <button className="btn btn-sm" onClick={checkSkus} disabled={auditing}>重新检查</button></div>}
          {auditMessage && <div className="alert alert-success">{auditMessage}</div>}
          {audit && <div className="sku-audit-panel">
            <div className="sku-audit-heading">
              <div><strong>SKU 核对结果</strong><span>这是资料核对提示，不是程序故障。系统正在比较产品库与 {audit.source}。</span></div>
              <div className="sku-audit-summary"><span className="ok">{audit.verified} 个一致</span>{audit.mismatch > 0 && <span className="warn">{audit.mismatch} 个需要核对</span>}{audit.missing > 0 && <span className="danger">{audit.missing} 个缺少 SKU</span>}</div>
            </div>
            {audit.issues.filter(item => item.status === 'mismatch').length > 0 && <div className="sku-audit-help">
              <strong>为什么会出现？</strong><span>这些产品在系统里保存了多个 SKU，但卖家导出表只确认了其中一部分。请判断多出的 SKU 是旧数据，还是仍在使用的颜色/规格。</span>
            </div>}
            <div className="sku-audit-issues">
              {audit.issues.filter(item => item.status === 'mismatch').map(item => <div className="sku-audit-issue" key={item.product_id}>
                <div className="sku-audit-product"><strong>产品 {item.product_no}</strong><span>SKU 记录不一致</span></div>
                <div className="sku-audit-values"><div><small>系统当前记录</small><span>{item.current_skus.join('、') || '未填写'}</span></div><b>→</b><div><small>卖家表建议值</small><span>{item.reference_skus.join('、') || '未找到'}</span></div></div>
                <div className="sku-audit-actions"><Link className="btn btn-sm" to={`/products/${item.product_id}`}>打开产品核对</Link><button className="btn btn-primary btn-sm" disabled={fixingProductId === item.product_id || item.reference_skus.length === 0} onClick={() => applyReferenceSku(item)}>{fixingProductId === item.product_id ? '修正中...' : '按卖家表修正'}</button></div>
              </div>)}
            </div>
            {audit.missing > 0 && <div className="sku-audit-footer"><span>另有 {audit.missing} 个产品没有填写 SKU。</span><Link to="/products?missing_field=sku&task=补充SKU">去补充 SKU →</Link></div>}
            {audit.mismatch === 0 && audit.missing === 0 && <div className="sku-audit-clear">SKU 核对完成，当前没有需要处理的问题。</div>}
          </div>}
          <div className="dashboard-task-sections">
            {visibleTaskGroups.length === 0 ? <div className="dashboard-all-clear"><strong>当前无待处理问题</strong><span>出现新的资料、库存或发货异常后，会自动显示在这里。</span></div> : visibleTaskGroups.map(group => <div className="dashboard-task-section" key={group.label}>
              <div className="dashboard-task-section-title"><strong>{group.label}</strong><span>{group.detail}</span></div>
              <div className="dashboard-task-grid">{group.items.map(task => <Link to={task.to} key={task.label} className={`dashboard-task ${task.tone}`}>
                <em>待处理</em><strong>{task.count}</strong><span>{task.label}</span><small>{task.detail}</small><b>进入处理 →</b>
              </Link>)}</div>
            </div>)}
          </div>
        </section>

        <section className="dashboard-panel dashboard-profit">
          <div className="dashboard-panel-title"><div><h2>选品质量</h2><p>快速判断产品池健康度。</p></div><Link to="/products">查看产品</Link></div>
          <div className="dashboard-profit-row">
            <div><span>平均利润率</span><strong>{formatPercent(stats.avg_profit_margin)}</strong></div>
            <div><span>合格选品</span><strong>{stats.qualified}</strong></div>
            <div><span>不建议选品</span><strong>{stats.not_recommended}</strong></div>
          </div>
          <div className="dashboard-best"><span>当前最高利润率产品</span><strong>{stats.top_product_name || '暂无'}</strong><em>{formatPercent(stats.top_profit_margin)}</em></div>
        </section>

        <section className="dashboard-panel dashboard-recent">
          <div className="dashboard-panel-title"><div><h2>最近发货</h2><p>已确认装箱单会自动进入这里并扣减库存。</p></div><Link to="/inventory">全部记录</Link></div>
          {shipments.length === 0 ? <div className="dashboard-empty">暂无发货记录</div> : (
            <table className="dashboard-recent-table"><colgroup><col className="col-order" /><col className="col-date" /><col className="col-sku" /><col className="col-name" /><col className="col-qty" /><col className="col-status" /></colgroup><thead><tr><th>单号</th><th>日期</th><th>SKU</th><th>中文品名</th><th>数量</th><th>状态</th></tr></thead>
              <tbody>{shipments.slice(0, 6).map(row => <tr key={row.id}>
                <td>{row.shipment_no}</td><td>{row.shipment_date}</td>
                <td colSpan={3} className="dashboard-shipment-group-cell"><div className="dashboard-shipment-group">
                  {row.lines.map((line, index) => <div className="dashboard-shipment-line" key={`${line.sku}-${index}`}>
                    <span className="dashboard-line-sku">{line.sku}</span>
                    <span className="dashboard-line-name" title={line.name}>{line.name.length > 10 ? `${line.name.slice(0, 10)}…` : line.name}</span>
                    <span className="dashboard-line-quantity">{line.total_quantity}</span>
                  </div>)}
                </div></td>
                <td><span className={row.status === 'confirmed' ? 'status-confirmed' : 'status-void'}>{row.status === 'confirmed' ? '已发货' : '已撤回'}</span></td>
              </tr>)}</tbody>
            </table>
          )}
        </section>

        <section className="dashboard-panel dashboard-shortcuts">
          <div className="dashboard-panel-title"><div><h2>快捷操作</h2><p>直接进入高频工作。</p></div></div>
          <Link to="/products"><strong>产品列表</strong><span>筛选、维护与加入装箱单</span></Link>
          <Link to="/procurement"><strong>采购入库</strong><span>新增采购记录并增加库存</span></Link>
          <Link to="/inventory"><strong>库存调整</strong><span>盘点增减、发货撤回与流水</span></Link>
          <Link to="/packing"><strong>装箱发货</strong><span>确认数量、生成 Excel 并扣库存</span></Link>
        </section>
      </div>
    </div>
  );
}
