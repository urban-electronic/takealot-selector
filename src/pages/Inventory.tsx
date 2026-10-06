import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import type { ProcurementRecord, Product } from '../types';

type InventoryRow = {
  product: Product;
  inventory: number;
  procurementCount: number;
  lastProcurementAt: string;
};

function shortName(product: Product): string {
  return product.chinese_product_name?.trim()
    || product.product_name?.trim()
    || '未命名产品';
}

export default function Inventory() {
  const api = useApi();
  const [products, setProducts] = useState<Product[]>([]);
  const [records, setRecords] = useState<ProcurementRecord[]>([]);
  const [search, setSearch] = useState('');
  const [stockFilter, setStockFilter] = useState<'all' | 'in_stock' | 'out_of_stock'>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getProducts(), api.listProcurementRecords()])
      .then(([productRows, procurementRows]) => {
        if (cancelled) return;
        setProducts(productRows || []);
        setRecords(procurementRows || []);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [api]);

  const rows = useMemo<InventoryRow[]>(() => {
    const byId = new Map<string, ProcurementRecord[]>();
    const byNo = new Map<number, ProcurementRecord[]>();
    records.forEach(record => {
      if (record.product_id) {
        const group = byId.get(record.product_id) || [];
        group.push(record);
        byId.set(record.product_id, group);
      } else if (record.product_no != null) {
        const group = byNo.get(record.product_no) || [];
        group.push(record);
        byNo.set(record.product_no, group);
      }
    });
    return products.map(product => {
      const matches = byId.get(product.id)
        || (product.product_no != null ? byNo.get(product.product_no) : undefined)
        || [];
      return {
        product,
        inventory: matches.reduce((sum, record) => sum + Math.max(0, Number(record.quantity) || 0), 0),
        procurementCount: matches.length,
        lastProcurementAt: matches.reduce(
          (latest, record) => record.recorded_at > latest ? record.recorded_at : latest,
          '',
        ),
      };
    });
  }, [products, records]);

  const filteredRows = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return rows.filter(row => {
      if (stockFilter === 'in_stock' && row.inventory <= 0) return false;
      if (stockFilter === 'out_of_stock' && row.inventory > 0) return false;
      if (!keyword) return true;
      const product = row.product;
      return `${product.sku || ''} ${shortName(product)} ${product.product_name || ''} ${product.tsin || ''}`
        .toLowerCase().includes(keyword);
    });
  }, [rows, search, stockFilter]);

  const totalInventory = useMemo(() => rows.reduce((sum, row) => sum + row.inventory, 0), [rows]);
  const stockedProducts = useMemo(() => rows.filter(row => row.inventory > 0).length, [rows]);

  if (loading) return <div className="loading">库存加载中...</div>;

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
        <div>
          <h1 style={{ margin: 0 }}>库存</h1>
          <div style={{ color: '#777', fontSize: 13, marginTop: 6 }}>
            {products.length} 个产品 · {stockedProducts} 个有库存 · 库存总量 {totalInventory}
          </div>
        </div>
        <div style={{ color: '#8a6d1d', background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, padding: '7px 10px', fontSize: 12 }}>
          当前库存按采购记录累计；后续接入出库记录后可自动计算实时结余。
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>库存加载失败：{error}</div>}

      <div className="card" style={{ padding: 12, marginBottom: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 1fr) minmax(150px, 220px)', gap: 10 }}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索 SKU、中文/英文品名、TSIN"
            style={{ width: '100%', boxSizing: 'border-box' }}
          />
          <select value={stockFilter} onChange={e => setStockFilter(e.target.value as typeof stockFilter)}>
            <option value="all">全部库存状态</option>
            <option value="in_stock">仅有库存</option>
            <option value="out_of_stock">仅零库存</option>
          </select>
        </div>
      </div>

      <div className="card" style={{ overflow: 'hidden' }}>
        <div style={{ maxHeight: 'calc(100vh - 270px)', minHeight: 280, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: '#fafafa' }}>
              <tr style={{ borderBottom: '1px solid #ddd' }}>
                <th style={{ padding: '10px 12px', textAlign: 'left', width: 70 }}>序号</th>
                <th style={{ padding: '10px 12px', textAlign: 'left', width: 180 }}>SKU</th>
                <th style={{ padding: '10px 12px', textAlign: 'left' }}>品名</th>
                <th style={{ padding: '10px 12px', textAlign: 'right', width: 140 }}>库存量</th>
                <th style={{ padding: '10px 12px', textAlign: 'right', width: 120 }}>采购记录</th>
                <th style={{ padding: '10px 12px', textAlign: 'left', width: 130 }}>最近入库</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map(row => (
                <tr key={row.product.id} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '9px 12px', color: '#888' }}>{row.product.product_no ?? '-'}</td>
                  <td style={{ padding: '9px 12px', fontWeight: 600 }}>{row.product.sku || '-'}</td>
                  <td style={{ padding: '9px 12px' }}>
                    <Link to={`/products/${row.product.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
                      {shortName(row.product)}
                    </Link>
                  </td>
                  <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                    <strong style={{ fontSize: 18, color: row.inventory > 0 ? '#1677ff' : '#999' }}>{row.inventory}</strong>
                  </td>
                  <td style={{ padding: '9px 12px', textAlign: 'right' }}>{row.procurementCount}</td>
                  <td style={{ padding: '9px 12px', color: '#666' }}>{row.lastProcurementAt || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {filteredRows.length === 0 && (
            <div style={{ textAlign: 'center', color: '#888', padding: 40 }}>没有符合条件的库存产品</div>
          )}
        </div>
      </div>
    </div>
  );
}
