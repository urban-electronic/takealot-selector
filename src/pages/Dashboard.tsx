import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import { getInventory, getShipments } from '../remoteApi';
import type { DashboardStats, InventoryRow, ProcurementRecord, Product, ShipmentRecord } from '../types';
import { formatPercent } from '../types';

export default function Dashboard() {
  const api = useApi();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [shipments, setShipments] = useState<ShipmentRecord[]>([]);
  const [procurements, setProcurements] = useState<ProcurementRecord[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [officialCheck, setOfficialCheck] = useState('');

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
    let multiSkuProducts = 0;
    products.forEach(product => {
      const tokens = (product.sku || '').split(/[\s,，;；]+/).map(value => value.trim()).filter(Boolean);
      if (tokens.length > 1) multiSkuProducts += 1;
      new Set(tokens).forEach(sku => {
        if (!skuOwners.has(sku)) skuOwners.set(sku, new Set());
        skuOwners.get(sku)!.add(product.id);
      });
    });
    return {
      available: validInventory.reduce((sum, row) => sum + row.available, 0),
      zeroOrNegative: validInventory.filter(row => row.available <= 0).length,
      missingSku: inventory.filter(row => !row.sku).length,
      missingImage: products.filter(row => !row.product_image_url && !row.product_image_path).length,
      multiSkuProducts,
      duplicateSkuProducts: new Set(Array.from(skuOwners.values()).filter(ids => ids.size > 1).flatMap(ids => Array.from(ids))).size,
      confirmedShipments: shipments.filter(row => row.status === 'confirmed'),
      purchasedUnits: procurements.reduce((sum, row) => sum + (Number(row.quantity) || 0), 0),
    };
  }, [inventory, shipments, procurements, products]);

  const tasks = [
    { step: 1, count: overview.missingSku, label: '产品缺少 SKU', detail: 'SKU 是库存与装箱匹配的基础', to: '/products?missing_field=sku&task=第1步：补充SKU', tone: 'red' },
    { step: 2, count: stats?.data_incomplete || 0, label: '产品资料待补充', detail: '补齐成本、尺寸和物流信息', to: '/products?selection_status=数据待补充&task=第2步：补充产品资料', tone: 'orange' },
    { step: 3, count: stats?.category_pending || 0, label: '品类待确认', detail: '确认 Fee 品类后重新计算利润', to: '/products?selection_status=待确认品类&task=第3步：确认产品品类', tone: 'blue' },
    { step: 4, count: overview.multiSkuProducts, label: '多款 SKU 待确认', detail: '区分同商品的颜色、款式分支', to: '/products?multi_sku=1&task=第4步：确认主商品与款式分支', tone: 'blue' },
    { step: 5, count: overview.duplicateSkuProducts, label: '重复 SKU 冲突', detail: '同一 SKU 出现在多个商品中', to: '/products?duplicate_sku=1&task=第5步：处理重复SKU', tone: 'red' },
    { step: 6, count: overview.missingImage, label: '产品缺少图片', detail: '完整产品库检查，补图后同步至装箱单', to: '/products?missing_field=image&task=第6步：补充产品图片', tone: 'purple' },
    { step: 7, count: overview.zeroOrNegative, label: '零库存产品', detail: '补录采购或进行库存调整', to: '/inventory?stock=zero', tone: 'purple' },
  ];

  const runOfficialCheck = async () => {
    const candidates = products.filter(product => product.takealot_url && (!product.product_image_url || !product.product_name || product.actual_sale_price_zar == null)).slice(0, 30);
    if (!candidates.length) { setOfficialCheck('没有可由官网自动修复的项目'); return; }
    let repaired = 0;
    for (let index = 0; index < candidates.length; index += 1) {
      setOfficialCheck(`官网自检 ${index + 1}/${candidates.length}`);
      try {
        const result = await api.refreshPrice(candidates[index].id);
        setProducts(prev => prev.map(product => product.id === candidates[index].id ? { ...product, ...result } as Product : product));
        repaired += 1;
      } catch { /* 单项失败保留在任务列表中 */ }
    }
    setOfficialCheck(`官网自检完成：更新 ${repaired} 项`);
  };

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
        <Link to="/procurement"><span>累计采购件数</span><strong>{overview.purchasedUnits}</strong><small>{procurements.length} 条采购记录</small></Link>
        <Link to="/inventory"><span>有效发货单</span><strong>{overview.confirmedShipments.length}</strong><small>{overview.confirmedShipments.reduce((s, x) => s + x.total_quantity, 0)} 件已发货</small></Link>
      </section>

      <div className="dashboard-grid">
        <section className="dashboard-panel dashboard-tasks">
          <div className="dashboard-panel-title"><div><h2>待处理事项</h2><p>优先解决会阻塞采购、库存和发货的问题。</p></div><button className="btn btn-outline btn-sm" disabled={officialCheck.startsWith('官网自检 ')} onClick={runOfficialCheck}>{officialCheck || '官网自检可修复项'}</button></div>
          <div className="dashboard-task-grid">
            {tasks.map(task => <Link to={task.to} key={task.label} className={`dashboard-task ${task.tone}`}>
              <em>第 {task.step} 步</em><strong>{task.count}</strong><span>{task.label}</span><small>{task.detail}</small><b>进入处理 →</b>
            </Link>)}
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
            <table><thead><tr><th>单号</th><th>日期</th><th>SKU</th><th>件数</th><th>状态</th></tr></thead>
              <tbody>{shipments.slice(0, 6).map(row => <tr key={row.id}>
                <td>{row.shipment_no}</td><td>{row.shipment_date}</td><td>{row.total_skus}</td><td>{row.total_quantity}</td>
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
