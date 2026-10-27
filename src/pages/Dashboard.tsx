import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import ReplenishmentAlerts from '../operations/ReplenishmentAlerts';
import { auditProductSkus, getInventory, getShipments, updateProduct } from '../remoteApi';
import type { SkuAuditResult } from '../remoteApi';
import type { DashboardStats, InventoryRow, ProcurementRecord, Product, ShipmentRecord } from '../types';
import { formatPercent } from '../types';
import { missingSkuTasks } from '../utils/dashboardTasks';
import { isPlaceholderLabel, skuVariants, variantLabels } from '../utils/variants';
import { applySettled, failureSummary, reasonText } from '../utils/settle';
import { EmptyState, SkDashboard } from '../LoadStates';

export default function Dashboard() {
  const api = useApi();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [procurements, setProcurements] = useState<ProcurementRecord[]>([]);
  const [shipments, setShipments] = useState<ShipmentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [partialError, setPartialError] = useState('');
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
    const prompt = '确认把产品 ' + issue.product_no + ' 的 SKU 改为卖家导出表中的值？\n\n'
      + issue.reference_skus.join('、') + '\n\n修改后原有 SKU 会被替换。';
    if (!nextSku || !window.confirm(prompt)) return;
    setFixingProductId(issue.product_id); setAuditError(''); setAuditMessage('');
    try {
      await updateProduct(issue.product_id, { sku: nextSku });
      setAuditMessage('产品 ' + issue.product_no + ' 已按卖家表修正 SKU');
      setAudit(await auditProductSkus());
    } catch (e: unknown) {
      setAuditError(e instanceof Error ? e.message : String(e));
    } finally {
      setFixingProductId('');
    }
  };

  useEffect(() => {
    // 分区降级：主页统计失败才走整页错误态，其余四个区块各自降级
    Promise.allSettled([api.getDashboard(), getInventory(), getShipments(), api.listProcurementRecords(), api.getProducts()])
      .then(([dashboard, inventoryRows, shipmentRows, procurementRows, productRows]) => {
        if (dashboard.status === 'rejected') {
          setError(reasonText(dashboard.reason));
          return;
        }
        setStats(dashboard.value);
        const failures: string[] = [];
        applySettled(inventoryRows, '库存', setInventory, failures);
        applySettled(shipmentRows, '发货单', setShipments, failures);
        applySettled(procurementRows, '采购记录', setProcurements, failures);
        applySettled(productRows, '产品列表', setProducts, failures);
        setPartialError(failureSummary(failures));
      })
      .finally(() => setLoading(false));
  }, [api]);

  const overview = useMemo(() => {
    const skuOwners = new Map<string, Set<string>>();
    let unresolvedMultiSkuProducts = 0;
    products.forEach(product => {
      // 与 ProductList 共用同一套切分规则，避免"列表里能认出、主页认不出"的口径分裂
      const tokens = skuVariants(product.sku);
      const labels = variantLabels(product);
      if (tokens.length > 1 && labels.some(isPlaceholderLabel)) unresolvedMultiSkuProducts += 1;
      new Set(tokens).forEach(sku => {
        if (!skuOwners.has(sku)) skuOwners.set(sku, new Set());
        skuOwners.get(sku)!.add(product.id);
      });
    });
    return {
      available: inventory.filter(row => row.sku).reduce((sum, row) => sum + row.available, 0),
      inTransitUnits: procurements.filter(row => row.status === 'in_transit').reduce((sum, row) => sum + (Number(row.quantity) || 0), 0),
      confirmedShipments: shipments.filter(row => row.status === 'confirmed'),
      missingSku: missingSkuTasks(inventory, products).blocking,
      missingImage: products.filter(row => !row.product_image_url && !row.product_image_path).length,
      unresolvedMultiSkuProducts,
      duplicateSkuProducts: new Set(Array.from(skuOwners.values()).filter(ids => ids.size > 1).flatMap(ids => Array.from(ids))).size,
      negative: inventory.filter(row => row.sku && row.available < 0).length,
    };
  }, [inventory, products, procurements, shipments]);

  const taskGroups = [
    { label: '影响库存与发货', items: [
      { count: overview.missingSku, label: '产品缺少 SKU', detail: '补充 SKU 后才能准确匹配库存和装箱单', to: '/products?missing_field=sku&task=补充SKU', tone: 'red' },
      { count: overview.duplicateSkuProducts, label: 'SKU 重复冲突', detail: '同一个 SKU 对应多个产品，需确认归属', to: '/products?duplicate_sku=1&task=处理重复SKU', tone: 'red' },
      { count: overview.negative, label: '库存数量异常', detail: '核对发货记录或进行库存调整', to: '/inventory?stock=negative', tone: 'orange' },
    ] },
    { label: '待完善产品资料', items: [
      { count: stats?.data_incomplete || 0, label: '产品资料待补充', detail: '补齐采购成本、尺寸或物流信息', to: '/products?selection_status=数据待补充&task=补充产品资料', tone: 'orange' },
      { count: stats?.category_pending || 0, label: '产品品类待确认', detail: '确认 Fee 品类后才能准确测算利润', to: '/products?selection_status=待确认品类&task=确认产品品类', tone: 'blue' },
      { count: overview.unresolvedMultiSkuProducts, label: '产品规格待确认', detail: '检查尚未对应真实颜色或尺码的 SKU', to: '/products?multi_sku=1&task=确认未识别规格', tone: 'blue' },
      { count: overview.missingImage, label: '产品缺少图片', detail: '补充图片后可用于产品识别和装箱核对', to: '/products?missing_field=image&task=补充产品图片', tone: 'purple' },
    ] },
  ];
  const visibleTaskGroups = taskGroups
    .map(group => ({ ...group, items: group.items.filter(task => task.count > 0) }))
    .filter(group => group.items.length > 0);
  const issueCount = visibleTaskGroups.reduce((total, group) => total + group.items.reduce((subtotal, task) => subtotal + task.count, 0), 0);

  if (loading) return <SkDashboard />;
  if (error) return <div className="alert alert-error">主页加载失败：{error}</div>;
  if (!stats) return <EmptyState title="还没有可汇总的数据" description="工作台的数字来自产品、库存和发货记录。先去产品列表加几个产品，这里就会自己长出来。" />;

  return (
    <div className="dashboard-page dashboard-simple">
      <div className="dashboard-heading">
        <div><h1>运营工作台</h1><p>待处理事项、选品表现和近期发货，一页掌握。</p></div>
      </div>

      <section className="dashboard-summary-metrics" aria-label="经营概览">
        <Link to="/products" className="dashboard-summary-card"><span>产品总数</span><strong>{stats.total}</strong><small>{stats.qualified} 个合格选品</small></Link>
        <Link to="/inventory" className="dashboard-summary-card"><span>可用库存</span><strong>{overview.available}</strong><small>{overview.negative ? overview.negative + ' 个 SKU 数量异常' : '库存状态正常'}</small></Link>
        <Link to="/procurement" className="dashboard-summary-card"><span>采购在途</span><strong>{overview.inTransitUnits}</strong><small>件等待入库确认</small></Link>
      </section>

      <ReplenishmentAlerts />

      <div className="dashboard-grid">
      <section className="dashboard-panel dashboard-tasks">
        <div className="dashboard-panel-title">
          <div><h2>待处理事项 {issueCount > 0 && <span className="dashboard-issue-count">{issueCount} 项</span>}</h2><p>{issueCount ? '优先显示会影响库存、发货和利润判断的问题' : '目前没有需要处理的问题'}</p></div>
          <button className="btn btn-outline btn-sm" disabled={auditing} onClick={checkSkus} title="按已导入的 Takealot 卖家导出表核对 SKU，不会自动修改资料">
            {auditing ? '核对中...' : '核对 SKU'}
          </button>
        </div>

        {partialError && <div className="alert alert-error">{partialError}</div>}
        {auditError && <div className="alert alert-error">核对失败：{auditError} <button className="btn btn-sm" onClick={checkSkus} disabled={auditing}>重新检查</button></div>}
        {auditMessage && <div className="alert alert-success">{auditMessage}</div>}
        {audit && <div className="sku-audit-panel">
          <div className="sku-audit-heading">
            <div><strong>SKU 核对结果</strong><span>对照产品库与 {audit.source}，结果仅供确认。</span></div>
            <div className="sku-audit-summary"><span className="ok">{audit.verified} 个一致</span>{audit.mismatch > 0 && <span className="warn">{audit.mismatch} 个需要核对</span>}{audit.missing > 0 && <span className="danger">{audit.missing} 个缺少 SKU</span>}</div>
          </div>
          {audit.issues.filter(item => item.status === 'mismatch').length > 0 && <div className="sku-audit-help">
            <strong>需要人工确认</strong><span>请确认系统记录与卖家导出表的差异；自动修正会替换原有 SKU。</span>
          </div>}
          <div className="sku-audit-issues">
            {audit.issues.filter(item => item.status === 'mismatch').map(item => <div className="sku-audit-issue" key={item.product_id}>
              <div className="sku-audit-product"><strong>产品 {item.product_no}</strong><span>SKU 不一致</span></div>
              <div className="sku-audit-values"><div><small>系统当前记录</small><span>{item.current_skus.join('、') || '未填写'}</span></div><b>→</b><div><small>卖家表建议值</small><span>{item.reference_skus.join('、') || '未找到'}</span></div></div>
              <div className="sku-audit-actions"><Link className="btn btn-sm" to={'/products/' + item.product_id}>打开产品核对</Link><button className="btn btn-primary btn-sm" disabled={fixingProductId === item.product_id || item.reference_skus.length === 0} onClick={() => applyReferenceSku(item)}>{fixingProductId === item.product_id ? '修正中...' : '按卖家表修正'}</button></div>
            </div>)}
          </div>
          {audit.missing > 0 && <div className="sku-audit-footer"><span>另有 {audit.missing} 个产品没有填写 SKU。</span><Link to="/products?missing_field=sku&task=补充SKU">去补充 SKU →</Link></div>}
          {audit.mismatch === 0 && audit.missing === 0 && <div className="sku-audit-clear">SKU 核对完成，没有发现差异。</div>}
        </div>}

        {visibleTaskGroups.length === 0 ? (
          <div className="dashboard-all-clear"><strong>当前无待处理问题</strong><span>有新的资料或库存问题时，会自动显示在这里。</span></div>
        ) : (
          <div className="dashboard-task-sections">
            {visibleTaskGroups.map(group => <div className="dashboard-task-section" key={group.label}>
              <div className="dashboard-task-section-title"><strong>{group.label}</strong></div>
              <div className="dashboard-task-grid">{group.items.map(task => <Link to={task.to} key={task.label} className={'dashboard-task ' + task.tone}>
                <strong>{task.count}</strong><span>{task.label}</span><small>{task.detail}</small><b>去处理 →</b>
              </Link>)}</div>
            </div>)}
          </div>
        )}
      </section>

      <aside className="dashboard-sidebar">
        <section className="dashboard-panel dashboard-profit">
          <div className="dashboard-panel-title"><div><h2>选品表现</h2><p>快速查看产品池整体情况</p></div><Link to="/products">查看产品</Link></div>
          <div className="dashboard-profit-row">
            <div><span>平均利润率</span><strong>{formatPercent(stats.avg_profit_margin)}</strong></div>
            <div><span>合格选品</span><strong>{stats.qualified}</strong></div>
            <div><span>不建议</span><strong>{stats.not_recommended}</strong></div>
          </div>
          <Link to="/products" className="dashboard-best"><span>利润率最高</span><strong>{stats.top_product_name || '暂无数据'}</strong><em>{formatPercent(stats.top_profit_margin)}</em></Link>
        </section>

        <section className="dashboard-panel dashboard-recent">
          <div className="dashboard-panel-title"><div><h2>最近发货</h2><p>显示最近 3 张已确认发货单</p></div><Link to="/inventory">全部记录</Link></div>
          {overview.confirmedShipments.length === 0 ? <div className="dashboard-empty">暂无已确认发货记录</div> : (
            <div className="dashboard-recent-list">{overview.confirmedShipments.slice(0, 3).map(row => <div className="dashboard-recent-item" key={row.id}>
              <div><strong>{row.shipment_no}</strong><span>{row.shipment_date}</span></div>
              <div><b>{row.total_quantity} 件</b><small>{row.total_skus} 个 SKU</small></div>
            </div>)}</div>
          )}
        </section>
      </aside>
      </div>
    </div>
  );
}
