import { useEffect, useState, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import { openUrl } from '../api';
import type { Product } from '../types';
import { formatPrice, formatPercent, SELECTION_STATUS_MAP, SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';

// --- 列定义 ---
interface ColumnDef {
  key: string;
  label: string;
  defaultWidth: number;
}

const allColumns: ColumnDef[] = [
  { key: 'product_no', label: '#', defaultWidth: 50 },
  { key: 'link_status', label: '链接状态', defaultWidth: 80 },
  { key: 'image', label: '图片', defaultWidth: 80 },
  { key: 'product_name', label: '标题', defaultWidth: 200 },
  { key: 'chinese_product_name', label: '中文品名', defaultWidth: 120 },
  { key: 'actual_sale_price_zar', label: '售价', defaultWidth: 100 },
  { key: 'fee_category', label: 'Fee品类', defaultWidth: 140 },
  { key: 'profit_margin', label: '利润率', defaultWidth: 80 },
  { key: 'profit_zar', label: '利润', defaultWidth: 80 },
  { key: 'sku', label: 'SKU', defaultWidth: 140 },
  { key: 'minimum_price_at_20_margin', label: '最低@20%', defaultWidth: 80 },
  { key: 'minimum_price_at_15_margin', label: '最低@15%', defaultWidth: 80 },
  { key: 'shipping_method', label: '运输方式', defaultWidth: 90 },
  { key: 'selection_status', label: '状态', defaultWidth: 90 },
  { key: 'actions', label: '操作', defaultWidth: 80 },
];

const LS_VISIBLE_KEY = 'productListVisibleColumns';
const LS_ORDER_KEY = 'productListColumnOrder';

export default function ProductList() {
  const api = useApi();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const [refreshingIds, setRefreshingIds] = useState<Set<string>>(new Set());

  // 列宽拖拽
  const [colWidths, setColWidths] = useState<Record<string, number>>({});
  const resizeState = useRef<{ colKey: string; startX: number; startWidth: number } | null>(null);

  // 列设置面板
  const [colPanelOpen, setColPanelOpen] = useState(false);
  const colPanelRef = useRef<HTMLDivElement>(null);

  // 列可见性 (localStorage)
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem(LS_VISIBLE_KEY);
      if (saved) return new Set(JSON.parse(saved));
    } catch { /* ignore */ }
    return new Set(allColumns.map(c => c.key));
  });

  // 列顺序 (localStorage) - 迁移旧默认顺序：把 link_status 移到 image 前
  const defaultOrder = allColumns.map(c => c.key);
  const [columnOrder, setColumnOrder] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(LS_ORDER_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        // Migration: if old order has link_status after image, migrate to new default
        const linkIdx = parsed.indexOf('link_status');
        const imgIdx = parsed.indexOf('image');
        if (linkIdx > imgIdx && imgIdx !== -1) {
          return defaultOrder;
        }
        return parsed;
      }
    } catch { /* ignore */ }
    return defaultOrder;
  });

  // 列拖拽排序 (mouse-based to avoid HTML5 drag conflict with Tauri window drag)
  const dragStateRef = useRef<{ colKey: string; startIdx: number; targetIdx: number } | null>(null);
  const [dragTargetIdx, setDragTargetIdx] = useState<number | null>(null);

  const handleColDragStart = (e: React.MouseEvent, colKey: string, idx: number) => {
    // Only drag from the column label area, not resize handle
    if ((e.target as HTMLElement).classList.contains('col-resize-handle')) return;
    e.preventDefault();
    e.stopPropagation();
    dragStateRef.current = { colKey, startIdx: idx, targetIdx: idx };
    setDragTargetIdx(null);

    const handleMouseMove = (ev: MouseEvent) => {
      if (!dragStateRef.current) return;
      const thEls = document.querySelectorAll('.product-table thead th');
      let closestIdx = dragStateRef.current.startIdx;
      let closestDist = Infinity;
      thEls.forEach((th, i) => {
        const rect = th.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const dist = Math.abs(ev.clientX - centerX);
        if (dist < closestDist) {
          closestDist = dist;
          closestIdx = i;
        }
      });
      dragStateRef.current.targetIdx = closestIdx;
      setDragTargetIdx(closestIdx);
    };

    const handleMouseUp = () => {
      if (!dragStateRef.current) return;
      const { colKey: draggedKey, startIdx, targetIdx } = dragStateRef.current;
      if (targetIdx !== startIdx) {
        setColumnOrder(prev => {
          const next = [...prev];
          const visible = next.filter(k => visibleColumns.has(k));
          if (targetIdx >= visible.length) return prev;
          const draggedVisibleIdx = visible.indexOf(draggedKey);
          if (draggedVisibleIdx === -1) return prev;
          visible.splice(draggedVisibleIdx, 1);
          visible.splice(targetIdx, 0, draggedKey);
          // Rebuild full order: visible ones in new order, then hidden ones
          const hidden = next.filter(k => !visibleColumns.has(k));
          const newOrder = [...visible, ...hidden];
          localStorage.setItem(LS_ORDER_KEY, JSON.stringify(newOrder));
          return newOrder;
        });
      }
      dragStateRef.current = null;
      setDragTargetIdx(null);
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'grabbing';
    document.body.style.userSelect = 'none';
  };

  // --- 分页：从 URL 初始化 ---
  const pageFromUrl = parseInt(searchParams.get('page') || '1', 10) || 1;
  const pageSizeFromUrl = parseInt(searchParams.get('pageSize') || '20', 10) || 20;
  const [currentPage, setCurrentPage] = useState(pageFromUrl);
  const [pageSize, setPageSize] = useState(pageSizeFromUrl);

  // filters from URL
  const statusFilter = searchParams.get('selection_status') || '';
  const feeFilter = searchParams.get('fee_category') || '';
  const shippingFilter = (searchParams.get('shipping_method') || '').split(',').filter(Boolean);
  const linkStatusFilter = (searchParams.get('link_status') || '').split(',').filter(Boolean);
  const searchText = searchParams.get('search') || '';

  // 多选下拉状态
  const [multiOpen, setMultiOpen] = useState<string | null>(null);

  // 点击外部关闭多选下拉
  useEffect(() => {
    if (!multiOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('.multi-select-wrapper')) {
        setMultiOpen(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [multiOpen]);

  const toggleMultiFilter = (key: string, val: string) => {
    const current = key === 'shipping_method' ? shippingFilter : linkStatusFilter;
    const next = current.includes(val) ? current.filter(v => v !== val) : [...current, val];
    setFilter(key, next.join(','));
  };

  // --- 列宽拖拽 ---
  const handleMouseDown = (e: React.MouseEvent, colKey: string) => {
    e.preventDefault();
    e.stopPropagation();
    const th = (e.target as HTMLElement).closest('th');
    const startWidth = th?.getBoundingClientRect().width || 100;
    resizeState.current = { colKey, startX: e.clientX, startWidth };

    const handleMouseMove = (ev: MouseEvent) => {
      if (!resizeState.current) return;
      const delta = ev.clientX - resizeState.current.startX;
      const newWidth = Math.max(40, resizeState.current.startWidth + delta);
      setColWidths(prev => ({ ...prev, [colKey]: newWidth }));
    };

    const handleMouseUp = () => {
      resizeState.current = null;
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'col-resize';
  };

  const getColWidth = (colKey: string, defaultWidth: number): number =>
    colWidths[colKey] || defaultWidth;

  // --- 列可见性 ---
  const toggleColumn = (key: string) => {
    setVisibleColumns(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      localStorage.setItem(LS_VISIBLE_KEY, JSON.stringify([...next]));
      return next;
    });
  };

  // 点击外部关闭列设置面板
  useEffect(() => {
    if (!colPanelOpen) return;
    const handler = (e: MouseEvent) => {
      if (colPanelRef.current && !colPanelRef.current.contains(e.target as Node)) {
        setColPanelOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [colPanelOpen]);

  // 实际渲染列（按 columnOrder，过滤 visibleColumns）
  const renderColumns = columnOrder.filter(key => visibleColumns.has(key));
  const colDefMap = new Map(allColumns.map(c => [c.key, c]));

  // --- 数据获取 ---
  const fetchProducts = () => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (statusFilter) params.selection_status = statusFilter;
    if (feeFilter) params.fee_category = feeFilter;
    if (shippingFilter.length > 0) params.shipping_method = shippingFilter.join(',');
    if (linkStatusFilter.length > 0) params.link_status = linkStatusFilter.join(',');
    if (searchText) params.search = searchText;

    api.getProducts(params)
      .then(setProducts)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchProducts();
  }, [statusFilter, feeFilter, shippingFilter.join(','), linkStatusFilter.join(','), searchText]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    // 筛选变更回到第一页
    next.delete('page');
    setSearchParams(next);
    setCurrentPage(1);
  };

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`确定删除「${name}」？`)) return;
    try {
      await api.deleteProduct(id);
      fetchProducts();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleFieldUpdate = async (id: string, field: string, value: any) => {
    try {
      await api.updateProduct(id, { [field]: value });
      setProducts((prev) =>
        prev.map((p) => {
          if (p.id !== id) return p;
          return { ...p, [field]: value };
        })
      );
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleRefreshPrice = async (id: string) => {
    setRefreshingIds((prev) => new Set(prev).add(id));
    try {
      const result: Record<string, any> = await api.refreshPrice(id);
      setProducts((prev) =>
        prev.map((p) => {
          if (p.id !== id) return p;
          return {
            ...p,
            actual_sale_price_zar: result.actual_sale_price_zar ?? p.actual_sale_price_zar,
            product_image_url: result.product_image_url ?? p.product_image_url,
            profit_margin: result.profit_margin ?? p.profit_margin,
            profit_zar: result.profit_zar ?? p.profit_zar,
            total_cost_zar: result.total_cost_zar ?? p.total_cost_zar,
            minimum_price_at_20_margin: result.minimum_price_at_20_margin ?? p.minimum_price_at_20_margin,
            minimum_price_at_15_margin: result.minimum_price_at_15_margin ?? p.minimum_price_at_15_margin,
            selection_status: result.selection_status ?? p.selection_status,
          };
        })
      );
    } catch (e: any) {
      alert(e.message);
    } finally {
      setRefreshingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  // --- 内联编辑 ---
  const [editingPriceId, setEditingPriceId] = useState<string | null>(null);
  const [editingPriceValue, setEditingPriceValue] = useState('');
  const [editingSkuId, setEditingSkuId] = useState<string | null>(null);
  const [editingSkuValue, setEditingSkuValue] = useState('');
  const [editingCnameId, setEditingCnameId] = useState<string | null>(null);
  const [editingCnameValue, setEditingCnameValue] = useState('');

  const startEditPrice = (p: Product) => {
    setEditingPriceId(p.id);
    setEditingPriceValue(p.actual_sale_price_zar != null ? String(p.actual_sale_price_zar) : '');
  };
  const commitEditPrice = async (id: string) => {
    const val = parseFloat(editingPriceValue);
    if (!isNaN(val)) await handleFieldUpdate(id, 'actual_sale_price_zar', val);
    setEditingPriceId(null);
  };
  const startEditSku = (p: Product) => {
    setEditingSkuId(p.id);
    setEditingSkuValue(p.sku || '');
  };
  const commitEditSku = async (id: string) => {
    await handleFieldUpdate(id, 'sku', editingSkuValue || null);
    setEditingSkuId(null);
  };
  const startEditCname = (p: Product) => {
    setEditingCnameId(p.id);
    setEditingCnameValue(p.chinese_product_name || '');
  };
  const commitEditCname = async (id: string) => {
    const trimmed = editingCnameValue.trim().slice(0, 10);
    await handleFieldUpdate(id, 'chinese_product_name', trimmed || null);
    setEditingCnameId(null);
  };

  const feeCategories = [...new Set(products.map((p) => p.fee_category).filter(Boolean))] as string[];

  // --- 分页 ---
  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const pagedProducts = products.slice((safeCurrentPage - 1) * pageSize, safeCurrentPage * pageSize);

  const syncPageToUrl = (page: number, size: number) => {
    const next = new URLSearchParams(searchParams);
    if (page > 1) next.set('page', String(page));
    else next.delete('page');
    if (size !== 20) next.set('pageSize', String(size));
    else next.delete('pageSize');
    setSearchParams(next, { replace: true });
  };

  const handlePageSizeChange = (size: number) => {
    setPageSize(size);
    setCurrentPage(1);
    syncPageToUrl(1, size);
  };

  const goToPage = (page: number) => {
    setCurrentPage(page);
    syncPageToUrl(page, pageSize);
  };

  // --- 单元格渲染 ---
  const renderCell = (p: Product, colKey: string) => {
    const isRefreshing = refreshingIds.has(p.id);
    switch (colKey) {
      case 'product_no':
        return p.product_no;
      case 'image':
        return p.product_image_url ? (
          <img
            src={p.product_image_url}
            alt=""
            referrerPolicy="no-referrer"
            style={{ width: 68, height: 68, objectFit: 'cover', borderRadius: 4 }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
              const parent = (e.target as HTMLImageElement).parentElement;
              if (parent) {
                parent.innerHTML = '<div style="width:68px;height:68px;background:#f0f0f0;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:11px">无图</div>';
              }
            }}
          />
        ) : (
          <div style={{ width: 68, height: 68, background: '#f0f0f0', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', fontSize: 11 }}>无图</div>
        );
      case 'product_name':
        return (
          <span style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>
            {p.takealot_url ? (
              <span
                onClick={() => openUrl(p.takealot_url!)}
                title={p.product_name || ''}
                style={{ textDecoration: 'none', color: 'var(--color-primary)', cursor: 'pointer' }}
              >
                {p.product_name || '(无标题)'}
              </span>
            ) : (
              <span title={p.product_name || ''}>{p.product_name || '(无标题)'}</span>
            )}
          </span>
        );
      case 'chinese_product_name':
        return editingCnameId === p.id ? (
          <input
            type="text" value={editingCnameValue}
            maxLength={10}
            onChange={(e) => setEditingCnameValue(e.target.value)}
            onBlur={() => commitEditCname(p.id)}
            onKeyDown={(e) => { if (e.key === 'Enter') commitEditCname(p.id); if (e.key === 'Escape') setEditingCnameId(null); }}
            autoFocus
            style={{ width: '100%', padding: '2px 4px', fontSize: 12 }}
          />
        ) : (
          <span onDoubleClick={() => startEditCname(p)} title="双击编辑中文品名" style={{ cursor: 'pointer', display: 'inline-block', minWidth: 30, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.chinese_product_name || '-'}
          </span>
        );
      case 'actual_sale_price_zar':
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {editingPriceId === p.id ? (
              <input
                type="number" step="0.01"
                value={editingPriceValue}
                onChange={(e) => setEditingPriceValue(e.target.value)}
                onBlur={() => commitEditPrice(p.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitEditPrice(p.id); if (e.key === 'Escape') setEditingPriceId(null); }}
                autoFocus
                style={{ width: 70, padding: '2px 4px', fontSize: 13 }}
              />
            ) : (
              <span onClick={() => startEditPrice(p)} title="点击编辑售价" style={{ cursor: 'pointer', minWidth: 50, display: 'inline-block' }}>
                {formatPrice(p.actual_sale_price_zar, 'ZAR')}
              </span>
            )}
            <button
              onClick={() => handleRefreshPrice(p.id)}
              disabled={isRefreshing || !p.takealot_url}
              title="刷新 Takealot 售价"
              style={{ fontSize: 11, padding: '1px 5px', cursor: isRefreshing ? 'wait' : 'pointer', opacity: isRefreshing ? 0.5 : 1, border: '1px solid #d9d9d9', borderRadius: 3, background: '#fff', lineHeight: '18px' }}
            >
              {isRefreshing ? '...' : '↻'}
            </button>
          </div>
        );
      case 'fee_category':
        return (
          <select
            value={p.fee_category || ''}
            onChange={(e) => handleFieldUpdate(p.id, 'fee_category', e.target.value || null)}
            style={{ width: '100%', padding: '2px 4px', fontSize: 12 }}
          >
            <option value="">-</option>
            {feeCategories.map((fc) => <option key={fc} value={fc}>{fc}</option>)}
          </select>
        );
      case 'profit_margin':
        return (
          <span className={p.profit_margin != null && p.profit_margin >= 0.25 ? 'high-margin' : p.profit_margin !== null ? 'low-margin' : ''}>
            {formatPercent(p.profit_margin)}
          </span>
        );
      case 'profit_zar':
        return formatPrice(p.profit_zar, 'ZAR');
      case 'sku':
        return editingSkuId === p.id ? (
          <input
            type="text" value={editingSkuValue}
            onChange={(e) => setEditingSkuValue(e.target.value)}
            onBlur={() => commitEditSku(p.id)}
            onKeyDown={(e) => { if (e.key === 'Enter') commitEditSku(p.id); if (e.key === 'Escape') setEditingSkuId(null); }}
            autoFocus
            style={{ width: '100%', padding: '2px 4px', fontSize: 12 }}
          />
        ) : (
          <span onClick={() => startEditSku(p)} title="点击编辑SKU" style={{ cursor: 'pointer', display: 'inline-block', minWidth: 30 }}>
            {p.sku || '-'}
          </span>
        );
      case 'minimum_price_at_20_margin':
        return p.minimum_price_at_20_margin != null ? `R ${p.minimum_price_at_20_margin.toFixed(0)}` : '-';
      case 'minimum_price_at_15_margin':
        return p.minimum_price_at_15_margin != null ? `R ${p.minimum_price_at_15_margin.toFixed(0)}` : '-';
      case 'shipping_method':
        return (
          <select
            value={p.shipping_method || ''}
            onChange={(e) => handleFieldUpdate(p.id, 'shipping_method', e.target.value || null)}
            style={{ width: '100%', padding: '2px 4px', fontSize: 12, whiteSpace: 'nowrap' }}
          >
            <option value="">-</option>
            {SHIPPING_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        );
      case 'link_status':
        return (
          <select
            value={p.link_status || '未购买'}
            onChange={(e) => handleFieldUpdate(p.id, 'link_status', e.target.value)}
            style={{
              width: '100%', padding: '2px 4px', fontSize: 12,
              background: LINK_STATUS_MAP[p.link_status || '未购买']?.color || '#999',
              color: '#fff', border: 'none', borderRadius: 4,
            }}
          >
            {LINK_STATUS_OPTIONS.map((s) => (
              <option key={s} value={s} style={{ background: '#fff', color: '#333' }}>{LINK_STATUS_MAP[s].label}</option>
            ))}
          </select>
        );
      case 'selection_status': {
        const status = SELECTION_STATUS_MAP[p.selection_status];
        const cls = p.selection_status === '合格选品' ? 'qualified' : p.selection_status === '不建议选品' ? 'not-recommended' : p.selection_status === '待确认品类' ? 'pending' : 'incomplete';
        return <span className={`status-badge status-${cls}`}>{status?.label || p.selection_status}</span>;
      }
      case 'actions':
        return (
          <>
            <Link to={`/products/${p.id}`} className="btn btn-outline btn-sm">详情</Link>
            <button className="btn btn-danger btn-sm" style={{ marginLeft: 4 }} onClick={() => handleDelete(p.id, p.product_name || '')}>删除</button>
          </>
        );
      default:
        return null;
    }
  };

  const renderTh = (colKey: string, idx: number) => {
    const def = colDefMap.get(colKey);
    if (!def) return null;
    const w = getColWidth(colKey, def.defaultWidth);
    const isDragTarget = dragTargetIdx === idx;
    return (
      <th
        key={colKey}
        onMouseDown={(e) => handleColDragStart(e, colKey, idx)}
        style={{
          width: w,
          position: 'relative',
          whiteSpace: 'nowrap',
          cursor: 'grab',
          borderLeft: isDragTarget ? '2px solid var(--color-primary)' : undefined,
          transition: 'border-left 0.1s',
          userSelect: 'none',
        }}
      >
        {def.label}
        <div
          onMouseDown={(e) => { e.stopPropagation(); handleMouseDown(e, colKey); }}
          className="col-resize-handle"
        />
      </th>
    );
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2>产品列表 ({products.length})</h2>
        <Link to="/create" className="btn btn-primary">+ 新建产品</Link>
      </div>

      <div className="filters-bar">
        <select value={statusFilter} onChange={(e) => setFilter('selection_status', e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(SELECTION_STATUS_MAP).map(([val, { label }]) => (
            <option key={val} value={val}>{label}</option>
          ))}
        </select>

        <select value={feeFilter} onChange={(e) => setFilter('fee_category', e.target.value)}>
          <option value="">全部品类</option>
          {feeCategories.map((fc) => (
            <option key={fc} value={fc}>{fc}</option>
          ))}
        </select>

        <div className="multi-select-wrapper">
          <div
            className={`multi-select-trigger ${multiOpen === 'shipping' ? 'open' : ''}`}
            onClick={() => setMultiOpen(multiOpen === 'shipping' ? null : 'shipping')}
          >
            {shippingFilter.length === 0 ? '全部运输方式' : shippingFilter.map(v => (
              <span key={v} className="multi-chip">{v}<span className="chip-x" onClick={(e) => { e.stopPropagation(); toggleMultiFilter('shipping_method', v); }}>×</span></span>
            ))}
          </div>
          {multiOpen === 'shipping' && (
            <div className="multi-select-dropdown">
              {SHIPPING_METHODS.map((m) => (
                <label key={m} className="multi-option">
                  <input type="checkbox" checked={shippingFilter.includes(m)} onChange={() => toggleMultiFilter('shipping_method', m)} />
                  {m}
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="multi-select-wrapper">
          <div
            className={`multi-select-trigger ${multiOpen === 'link' ? 'open' : ''}`}
            onClick={() => setMultiOpen(multiOpen === 'link' ? null : 'link')}
          >
            {linkStatusFilter.length === 0 ? '全部链接状态' : linkStatusFilter.map(v => (
              <span key={v} className="multi-chip">{LINK_STATUS_MAP[v]?.label || v}<span className="chip-x" onClick={(e) => { e.stopPropagation(); toggleMultiFilter('link_status', v); }}>×</span></span>
            ))}
          </div>
          {multiOpen === 'link' && (
            <div className="multi-select-dropdown">
              {LINK_STATUS_OPTIONS.map((s) => (
                <label key={s} className="multi-option">
                  <input type="checkbox" checked={linkStatusFilter.includes(s)} onChange={() => toggleMultiFilter('link_status', s)} />
                  {LINK_STATUS_MAP[s].label}
                </label>
              ))}
            </div>
          )}
        </div>

        <input
          type="text"
          placeholder="搜索标题/TSIN/链接..."
          value={searchText}
          onChange={(e) => setFilter('search', e.target.value)}
          style={{ width: 240 }}
        />

        {/* 列设置按钮 */}
        <div className="col-settings-wrapper" ref={colPanelRef}>
          <button
            className="btn btn-outline btn-sm"
            onClick={() => setColPanelOpen(!colPanelOpen)}
          >
            列设置
          </button>
          {colPanelOpen && (
            <div className="col-settings-panel">
              {allColumns.map(c => (
                <label key={c.key} className="col-settings-item">
                  <input
                    type="checkbox"
                    checked={visibleColumns.has(c.key)}
                    onChange={() => toggleColumn(c.key)}
                  />
                  <span>{c.label}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? (
        <div className="loading">加载中...</div>
      ) : products.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 48 }}>
          <p style={{ color: 'var(--color-text-secondary)' }}>暂无产品记录</p>
          <Link to="/create" className="btn btn-primary" style={{ marginTop: 12 }}>新建产品</Link>
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="product-table" style={{ whiteSpace: 'nowrap' }}>
            <thead>
              <tr>
                {renderColumns.map((colKey, idx) => renderTh(colKey, idx))}
              </tr>
            </thead>
            <tbody>
              {pagedProducts.map((p) => {
                const linkBgColor = p.link_status === '已上架' ? '#e3f2fd' : p.link_status === '已购买' ? '#e8f5e9' : p.link_status === '已发货' ? '#fff3e0' : undefined;
                return (
                  <tr key={p.id} style={linkBgColor ? { background: linkBgColor } : undefined}>
                    {renderColumns.map(colKey => (
                      <td key={colKey}>
                        {renderCell(p, colKey)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="pagination-bar">
            <span>共 {products.length} 条，第 {safeCurrentPage}/{totalPages} 页</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <select value={pageSize} onChange={(e) => handlePageSizeChange(Number(e.target.value))}>
                <option value={20}>20条/页</option>
                <option value={50}>50条/页</option>
                <option value={100}>100条/页</option>
              </select>
              <button
                className="btn btn-outline btn-sm"
                disabled={safeCurrentPage <= 1}
                onClick={() => goToPage(safeCurrentPage - 1)}
              >
                上一页
              </button>
              <button
                className="btn btn-outline btn-sm"
                disabled={safeCurrentPage >= totalPages}
                onClick={() => goToPage(safeCurrentPage + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
