import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import { auditProductSkus, getInventory, updateProduct } from '../remoteApi';
import type { SkuAuditResult } from '../remoteApi';
import type { DashboardStats, InventoryRow, Product } from '../types';
import { missingSkuTasks } from '../utils/dashboardTasks';

export default function Dashboard() {
  const api = useApi();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
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
    Promise.all([api.getDashboard(), getInventory(), api.getProducts()])
      .then(([dashboard, inventoryRows, productRows]) => {
        setStats(dashboard); setInventory(inventoryRows); setProducts(productRows);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [api]);

  const overview = useMemo(() => {
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
      missingSku: missingSkuTasks(inventory, products).blocking,
      missingImage: products.filter(row => !row.product_image_url && !row.product_image_path).length,
      unresolvedMultiSkuProducts,
      duplicateSkuProducts: new Set(Array.from(skuOwners.values()).filter(ids => ids.size > 1).flatMap(ids => Array.from(ids))).size,
      negative: inventory.filter(row => row.sku && row.available < 0).length,
    };
  }, [inventory, products]);

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
  const issueCount = visibleTaskGroups.reduce((total, group) => total + group.items.length, 0);

  if (loading) return <div className="loading">正在检查待处理事项...</div>;
  if (error) return <div className="alert alert-error">主页加载失败：{error}</div>;
  if (!stats) return <div className="loading">暂无数据</div>;

  return (
    <div className="dashboard-page dashboard-simple">
      <div className="dashboard-heading">
        <div><h1>运营工作台</h1><p>只列出需要你处理的事项。点开即可进入对应页面。</p></div>
      </div>

      <section className="dashboard-panel dashboard-tasks">
        <div className="dashboard-panel-title">
          <div><h2>待处理事项 <span className="dashboard-issue-count">{issueCount}</span></h2><p>{issueCount ? '按影响库存、发货和选品资料排序' : '目前没有需要处理的问题'}</p></div>
          <button className="btn btn-outline btn-sm" disabled={auditing} onClick={checkSkus} title="按已导入的 Takealot 卖家导出表核对 SKU，不会自动修改资料">
            {auditing ? '核对中...' : '核对 SKU'}
          </button>
        </div>

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
    </div>
  );
}
