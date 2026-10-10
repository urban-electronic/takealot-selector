import { Fragment, useEffect, useState, useRef } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useApi, useDataSource } from '../DataSourceContext';
import { openUrl } from '../api';
import { getStoreStorageKey, readStoreStorage, getActiveStoreId, previewSellerAssist, applySellerAssist, type SellerAssistResult } from '../remoteApi';
import type { Product } from '../types';
import { formatPrice, formatPercent, SELECTION_STATUS_MAP, SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';

// --- 列定义 ---
interface ColumnDef {
  key: string;
  label: string;
  defaultWidth: number;
}

const allColumns: ColumnDef[] = [
  { key: 'select', label: '勾选', defaultWidth: 40 },
  { key: 'product_no', label: '#', defaultWidth: 50 },
  { key: 'link_status', label: '链接状态', defaultWidth: 80 },
  { key: 'image', label: '图片', defaultWidth: 80 },
  { key: 'product_name', label: '标题', defaultWidth: 200 },
  { key: 'chinese_product_name', label: '中文品名', defaultWidth: 120 },
  { key: 'actual_sale_price_zar', label: '售价', defaultWidth: 100 },
  { key: 'fee_category', label: 'Fee品类', defaultWidth: 140 },
  { key: 'competing_sellers_count', label: '竞品卖家数', defaultWidth: 90 },
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
const LS_SELECTED_SKUS_KEY = 'productListSelectedSkus';

/** Validate saved column order against canonical allColumns keys:
 *  - Remove stale keys no longer in allColumns
 *  - Append missing new keys to the end */
function validateColumnOrder(saved: string[], canonical: string[]): string[] {
  const canonicalSet = new Set(canonical);
  const cleaned = saved.filter(k => canonicalSet.has(k));
  const cleanedSet = new Set(cleaned);
  const missing = canonical.filter(k => !cleanedSet.has(k));
  return [...cleaned, ...missing];
}

function skuVariants(sku: string | null): string[] {
  return (sku || '').split(/[\s,，;；]+/).map(value => value.trim()).filter(Boolean);
}

function variantLabels(product: Product): string[] {
  const skus = skuVariants(product.sku);
  const labels = (product.chinese_product_name || '').split(/[\s,，;；/]+/).map(value => value.trim()).filter(Boolean);
  return labels.length === skus.length ? labels : skus.map((_, index) => index === 0 ? (product.chinese_product_name || '主商品') : '规格待识别');
}

function variantKind(label: string): string {
  if (/(黑|白|红|蓝|绿|黄|紫|粉|灰|棕|咖|卡其|军绿|藏蓝|颜色|色$)/.test(label)) return '颜色';
  if (/(^|\s)(XS|S|M|L|XL|XXL)(\s|$)|码|尺寸|厘米|cm|mm|英寸|inch/i.test(label)) return '尺寸';
  if (/\d+\s*(个|只|件|包|片|套|pcs|pack)/i.test(label)) return '数量';
  if (/型号|版本|代|款/.test(label)) return '型号';
  return '款式';
}

function variantSpec(label: string, allLabels: string[]): string {
  const direct = label.match(/\b(?:\d+\s*(?:GB|TB)|XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|\d+(?:\.\d+)?\s*(?:cm|mm|inch|英寸|码))\b/i);
  if (direct) return direct[0].replace(/\s+/g, '').toUpperCase();
  const color = label.match(/(藏蓝|军绿|卡其|墨绿|深蓝|浅蓝|天蓝|玫红|粉红|黑色|白色|红色|蓝色|绿色|黄色|紫色|粉色|灰色|棕色|咖色|黑|白|红|蓝|绿|黄|紫|粉|灰|棕)$/);
  if (color) return color[1];
  if (allLabels.length > 1) {
    let prefix = allLabels[0];
    while (prefix && !allLabels.every(item => item.startsWith(prefix))) prefix = prefix.slice(0, -1);
    const distinct = label.slice(prefix.length).replace(/^[\s\-_/]+|[\s\-_/]+$/g, '');
    if (distinct) return distinct.slice(0, 12);
  }
  return label.slice(0, 12) || '待确认';
}

export default function ProductList() {
  const api = useApi();
  const { dataSource } = useDataSource();
  const [assist, setAssist] = useState<SellerAssistResult | null>(null);
  const [assistOnlyTasks, setAssistOnlyTasks] = useState(true);
  const [assistBusy, setAssistBusy] = useState(false);
  const [assistError, setAssistError] = useState('');
  const previewAssist = async () => {
    setAssistBusy(true); setAssistError('');
    try { setAssist(await previewSellerAssist(products.map(p => p.id))); }
    catch (e) { setAssistError(e instanceof Error ? e.message : '读取失败，请重试'); }
    finally { setAssistBusy(false); }
  };
  const applyAssist = async () => {
    if (!assist) return;
    setAssistBusy(true); setAssistError('');
    try {
      const result = await applySellerAssist(assist.items.filter(i => i.changes.length));
      setAssist(null); fetchProducts();
      alert(`已补齐 ${result.fillable} 个产品。尺寸和重量补齐后已重新计算物流费用与利润。`);
    } catch (e) { setAssistError(e instanceof Error ? e.message : '补齐失败，请重新预览'); }
    finally { setAssistBusy(false); }
  };
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const [priceSource, setPriceSource] = useState<'official' | 'seller'>(getActiveStoreId() === 'default-store' ? 'seller' : 'official');
  const [refreshDetail, setRefreshDetail] = useState<{ id: string; message: string } | null>(null);
  const [refreshMessages, setRefreshMessages] = useState<Record<string, string>>({});
  const batchStop = useRef(false);
  const [refreshingIds, setRefreshingIds] = useState<Set<string>>(new Set());
  const [imageBatchProgress, setImageBatchProgress] = useState('');
  const [expandedVariantRows, setExpandedVariantRows] = useState<Set<string>>(new Set());
  const [variantEditor, setVariantEditor] = useState<{ productId: string; index: number; sku: string; label: string; makePrimary: boolean } | null>(null);
  const [savingVariant, setSavingVariant] = useState(false);
  const [contextProduct, setContextProduct] = useState<{ id: string; x: number; y: number } | null>(null);
  const [selectedSkus, setSelectedSkus] = useState<Set<string>>(() => {
    try {
      const saved = JSON.parse(readStoreStorage(LS_SELECTED_SKUS_KEY) || '[]');
      return new Set(Array.isArray(saved) ? saved.filter((sku): sku is string => typeof sku === 'string') : []);
    } catch { return new Set(); }
  });

  // 在产品库和装箱单之间切换时保留本次勾选，成功导出装箱单后由装箱单页清除。
  useEffect(() => {
    localStorage.setItem(getStoreStorageKey(LS_SELECTED_SKUS_KEY), JSON.stringify(Array.from(selectedSkus)));
  }, [selectedSkus]);

  useEffect(() => {
    if (!contextProduct) return;
    const close = () => setContextProduct(null);
    window.addEventListener('click', close);
    window.addEventListener('scroll', close, true);
    return () => { window.removeEventListener('click', close); window.removeEventListener('scroll', close, true); };
  }, [contextProduct]);

  // 列宽拖拽
  const [colWidths, setColWidths] = useState<Record<string, number>>({});
  const resizeState = useRef<{ colKey: string; startX: number; startWidth: number } | null>(null);

  // 列设置面板
  const [colPanelOpen, setColPanelOpen] = useState(false);
  const colPanelRef = useRef<HTMLDivElement>(null);

  // 列可见性 (localStorage) — 校验迁移：过滤僵尸key、补全缺失key
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(() => {
    const canonical = allColumns.map(c => c.key);
    try {
      const saved = localStorage.getItem(LS_VISIBLE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        const validated = validateColumnOrder(parsed, canonical);
        localStorage.setItem(LS_VISIBLE_KEY, JSON.stringify(validated));
        return new Set(validated);
      }
    } catch { /* ignore */ }
    return new Set(canonical);
  });

  // 列顺序 (localStorage) — 通用校验：过滤僵尸key、补全缺失key
  const [columnOrder, setColumnOrder] = useState<string[]>(() => {
    const canonical = allColumns.map(c => c.key);
    try {
      const saved = localStorage.getItem(LS_ORDER_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        const validated = validateColumnOrder(parsed, canonical);
        localStorage.setItem(LS_ORDER_KEY, JSON.stringify(validated));
        return validated;
      }
    } catch { /* ignore */ }
    return canonical;
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
  const [jumpPage, setJumpPage] = useState('');

  // filters from URL
  const statusFilter = searchParams.get('selection_status') || '';
  const feeFilter = searchParams.get('fee_category') || '';
  const shippingFilter = (searchParams.get('shipping_method') || '').split(',').filter(Boolean);
  const linkStatusFilter = (searchParams.get('link_status') || '').split(',').filter(Boolean);
  const searchText = searchParams.get('search') || '';
  const missingField = searchParams.get('missing_field') || '';
  const multiSkuOnly = searchParams.get('multi_sku') === '1';
  const duplicateSkuOnly = searchParams.get('duplicate_sku') === '1';
  const taskMode = searchParams.get('task') || '';
  const archivedMode = searchParams.get('archived') === '1';
  const [searchInput, setSearchInput] = useState(searchText);

  // URL 与输入框双向同步；输入停止 300ms 后再请求，避免每个按键都刷新列表
  useEffect(() => {
    setSearchInput(searchText);
  }, [searchText]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (searchInput !== searchText) setFilter('search', searchInput.trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput, searchText]);

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
    if (missingField) params.missing_field = missingField;
    if (multiSkuOnly) params.multi_sku = '1';
    if (duplicateSkuOnly) params.duplicate_sku = '1';
    if (archivedMode) params.archived = 'true';

    api.getProducts(params)
      .then(setProducts)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchProducts();
  }, [statusFilter, feeFilter, shippingFilter.join(','), linkStatusFilter.join(','), searchText, missingField, multiSkuOnly, duplicateSkuOnly, archivedMode]);

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
    if (!window.confirm(`确定将「${name}」移入废品库？产品序号会永久保留。`)) return;
    try {
      await api.deleteProduct(id);
      fetchProducts();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleRestore = async (id: string) => {
    try {
      await api.restoreProduct(id);
      fetchProducts();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleFieldUpdate = async (id: string, field: string, value: any) => {
    try {
      const updated = await api.updateProduct(id, { [field]: value });
      setProducts((prev) => prev
        .map((p) => p.id === id ? updated : p)
        .filter((p) => {
          if (missingField === 'sku') return !p.sku;
          if (missingField === 'image') return !p.product_image_url;
          if (missingField === 'chinese_name') return !p.chinese_product_name;
          if (missingField === 'shipping') return !p.shipping_method;
          if (statusFilter) return p.selection_status === statusFilter;
          return true;
        }));
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleRefreshPrice = async (id: string) => {
    setRefreshMessages(prev => ({ ...prev, [id]: '' }));
    setRefreshingIds((prev) => new Set(prev).add(id));
    try {
      const result: Record<string, any> = await api.refreshPrice(id, dataSource === 'remote' ? priceSource : 'official');
      setRefreshMessages(prev => ({ ...prev, [id]: `${result.data_source === 'seller_api' ? '本店报价' : '官网售价'}已更新 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}${(result.warnings || []).length ? '；' + result.warnings.join('；') : ''}` }));
      setProducts((prev) =>
        prev.map((p) => {
          if (p.id !== id) return p;
          return {
            ...p,
            sku: result.sku ?? p.sku,
            fee_category: result.fee_category ?? p.fee_category,
            fee_category_confirmed: result.fee_category_confirmed ?? p.fee_category_confirmed,
            product_name: result.product_name ?? p.product_name,
            chinese_product_name: result.chinese_product_name ?? p.chinese_product_name,
            actual_sale_price_zar: result.actual_sale_price_zar ?? p.actual_sale_price_zar,
            product_image_url: result.product_image_url ?? p.product_image_url,
            profit_margin: result.profit_margin ?? p.profit_margin,
            profit_zar: result.profit_zar ?? p.profit_zar,
            total_cost_zar: result.total_cost_zar ?? p.total_cost_zar,
            minimum_price_at_20_margin: result.minimum_price_at_20_margin ?? p.minimum_price_at_20_margin,
            minimum_price_at_15_margin: result.minimum_price_at_15_margin ?? p.minimum_price_at_15_margin,
            selection_status: result.selection_status ?? p.selection_status,
            competing_sellers_count: result.competing_sellers_count != null ? result.competing_sellers_count : p.competing_sellers_count,
            stock_remaining: result.stock_remaining != null ? result.stock_remaining : p.stock_remaining,
            review_count: result.review_count != null ? result.review_count : p.review_count,
            rating_value: result.rating_value != null ? result.rating_value : p.rating_value,
          };
        })
      );
    } catch (e: any) {
      setRefreshMessages(prev => ({ ...prev, [id]: e.message || '读取失败，原售价保留' }));
    } finally {
      setRefreshingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleRefetchMissingImages = async () => {
    const pending = products.filter(product => !product.product_image_url && !product.product_image_path && product.takealot_url);
    if (pending.length === 0) {
      alert('当前缺图产品没有可用的 Takealot 官网链接。');
      return;
    }
    let success = 0; let processed = 0;
    const failures: string[] = [];
    batchStop.current = false;
    for (let index = 0; index < pending.length; index += 1) {
      if (batchStop.current) break;
      const product = pending[index];
      processed += 1;
      setImageBatchProgress(`正在补充图片 ${index + 1}/${pending.length}`);
      setRefreshingIds(prev => new Set(prev).add(product.id));
      try {
        const result: Record<string, any> = await api.refreshPrice(product.id, 'details');
        if (result.product_image_url) success += 1;
        else failures.push(`#${product.product_no}：未获取图片，请人工上传`);
      } catch (e) {
        failures.push(`#${product.product_no}：${e instanceof Error ? e.message : '读取失败'}`);
      } finally {
        setRefreshingIds(prev => { const next = new Set(prev); next.delete(product.id); return next; });
      }
    }
    setImageBatchProgress('');
    await fetchProducts();
    setAssistError(`图片处理${batchStop.current ? '已停止' : '完成'}：成功 ${success} 个，已处理 ${processed}/${pending.length}，未获取图片 ${processed - success} 个。${failures.join('；')}`);
  };

  const saveVariant = async () => {
    if (!variantEditor) return;
    const product = products.find(item => item.id === variantEditor.productId);
    if (!product) return;
    const skus = skuVariants(product.sku);
    const labels = variantLabels(product);
    const nextSku = variantEditor.sku.trim();
    const nextLabel = variantEditor.label.trim();
    if (!nextSku || !nextLabel) { alert('SKU 和规格名称都不能为空'); return; }
    if (skus.some((sku, index) => index !== variantEditor.index && sku === nextSku)) { alert('该 SKU 已存在于当前产品分支中'); return; }
    const oldSku = skus[variantEditor.index];
    skus[variantEditor.index] = nextSku;
    labels[variantEditor.index] = nextLabel;
    if (variantEditor.makePrimary && variantEditor.index > 0) {
      skus.unshift(...skus.splice(variantEditor.index, 1));
      labels.unshift(...labels.splice(variantEditor.index, 1));
    }
    setSavingVariant(true);
    try {
      const updated = await api.updateProduct(product.id, { sku: skus.join(' '), chinese_product_name: labels.join(' ') });
      setProducts(prev => prev.map(item => item.id === product.id ? updated : item));
      setSelectedSkus(prev => { const next = new Set(prev); if (next.delete(oldSku)) next.add(nextSku); return next; });
      setVariantEditor(null);
    } catch (e: any) { alert(e.message || '分支保存失败'); }
    finally { setSavingVariant(false); }
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
      case 'select':
        if (!p.sku) return <span style={{ color: '#ccc', fontSize: 11 }}>无SKU</span>;
        const variants = skuVariants(p.sku);
        const allVariantsChecked = variants.length > 0 && variants.every(sku => selectedSkus.has(sku));
        return (
          <input
            type="checkbox"
            checked={allVariantsChecked}
            onChange={() => {
              setSelectedSkus(prev => {
                const next = new Set(prev);
                variants.forEach(sku => {
                  if (allVariantsChecked) next.delete(sku); else next.add(sku);
                });
                return next;
              });
            }}
            title={variants.length > 1 ? '选择或取消该产品的全部颜色分支' : '勾选后可加入装箱单'}
            style={{ width: 'auto', accentColor: 'var(--color-primary, #1677ff)', cursor: 'pointer' }}
          />
        );
      case 'product_no':
        return p.product_no;
      case 'image':
        return p.product_image_url ? (
          <img
            src={api.getImageUrl(p.product_image_url)}
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
          <div className="price-refresh-cell">
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
              title={dataSource === 'remote' ? (priceSource === 'seller' ? '刷新本店报价（不是官网最低价）' : '刷新官网售价') : '刷新官网售价'}
              style={{ fontSize: 11, padding: '1px 5px', cursor: isRefreshing ? 'wait' : 'pointer', opacity: isRefreshing ? 0.5 : 1, border: '1px solid #d9d9d9', borderRadius: 3, background: '#fff', lineHeight: '18px' }}
            >
              {isRefreshing ? '读取中' : '↻'}
            </button>
            {refreshMessages[p.id] && <button type="button" className="price-refresh-status" title="点击查看刷新结果和说明" onClick={() => setRefreshDetail({ id: p.id, message: refreshMessages[p.id] })}>{refreshMessages[p.id].includes('已更新') ? '已更新 ⓘ' : '读取失败 ⓘ'}</button>}
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
      case 'competing_sellers_count':
        return p.competing_sellers_count != null ? p.competing_sellers_count : '-';
      case 'profit_margin':
        return (
          <span className={p.profit_margin != null && p.profit_margin >= 0.25 ? 'high-margin' : p.profit_margin !== null ? 'low-margin' : ''}>
            {formatPercent(p.profit_margin)}
          </span>
        );

      case 'profit_zar':
        return formatPrice(p.profit_zar, 'ZAR');
      case 'sku':
        const skuOptions = skuVariants(p.sku);
        if (skuOptions.length > 1) {
          const expanded = expandedVariantRows.has(p.id);
          return <span className="sku-branch-summary"><b>{skuOptions[0]}</b><button type="button" onClick={() => setExpandedVariantRows(prev => { const next = new Set(prev); if (next.has(p.id)) next.delete(p.id); else next.add(p.id); return next; })}>{expanded ? '收起规格' : `展开 ${skuOptions.length} 个规格`}</button></span>;
        }
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
        if (archivedMode) {
          return <button className="btn btn-primary btn-sm" onClick={() => handleRestore(p.id)}>恢复产品</button>;
        }
        return (
          <>
            <Link to={`/products/${p.id}${taskMode ? '?edit=1&from=task' : ''}`} className={`btn btn-sm ${taskMode ? 'btn-primary' : 'btn-outline'}`}>{taskMode ? '修改资料' : '详情'}</Link>
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
    if (colKey === 'select') {
      const pageSkus = pagedProducts.flatMap(p => skuVariants(p.sku));
      const allChecked = pageSkus.length > 0 && pageSkus.every(s => selectedSkus.has(s));
      return (
        <th
          key={colKey}
          style={{ width: 40, position: 'relative', whiteSpace: 'nowrap', userSelect: 'none', textAlign: 'center' }}
        >
          <input
            type="checkbox"
            checked={allChecked}
            onChange={(e) => {
              setSelectedSkus(prev => {
                const next = new Set(prev);
                if (e.target.checked) {
                  pageSkus.forEach(s => next.add(s));
                } else {
                  pageSkus.forEach(s => next.delete(s));
                }
                return next;
              });
            }}
            title="全选当前页"
            style={{ width: 'auto', accentColor: 'var(--color-primary, #1677ff)', cursor: 'pointer' }}
          />
        </th>
      );
    }
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
    <div className="product-list-page">
      <div className="product-list-header">
        <h2>{archivedMode ? '废品库' : '产品列表'} ({products.length})</h2>
        {archivedMode ? <Link to="/products" className="btn btn-outline">返回产品列表</Link> : <Link to="/create" className="btn btn-primary">+ 新建产品</Link>}
      </div>

      {!archivedMode && dataSource === 'remote' && <div className="product-task-banner">
        <div><strong>卖家资料核对与补齐</strong><span>核对当前列表，预览后仅补空白资料；已有内容保留。采购成本、品类和运输方式请人工填写。</span></div>
        <button className="btn btn-outline" disabled={assistBusy || !products.length || products.length > 500} onClick={previewAssist}>{assistBusy ? '正在处理…' : '核对与补齐资料'}</button>
      </div>}
      {refreshDetail && <div className="refresh-detail-panel" role="status">
        <div style={{ flex: 1, minWidth: 0 }}><strong>#{products.find(p => p.id === refreshDetail.id)?.product_no ?? ''} 售价刷新结果</strong><p>{refreshDetail.message}</p></div>
        <button className="btn btn-outline btn-sm" onClick={() => setRefreshDetail(null)}>关闭</button>
      </div>}
      {assistError && <div role="alert" className="product-task-banner">{assistError}</div>}
      {assist && <div role="dialog" aria-modal="true" aria-label="卖家资料补齐预览" style={{ position: 'fixed', inset: 0, background: '#0008', zIndex: 2000, display: 'grid', placeItems: 'center' }}>
        <div style={{ background: 'var(--color-bg, white)', padding: 24, borderRadius: 12, width: 'min(1000px, 94vw)', maxHeight: '85vh', overflow: 'auto' }}>
          <h3>补齐预览：{assist.fillable} 个产品可以补齐</h3>
          <p>来源：本店卖家 API。仅补空白 SKU、图片、标题、TSIN、尺寸和重量。尺寸或重量补齐后会重新计算费用与利润，人工成本覆盖值保留。</p>
          <label style={{ display: 'block', marginBottom: 12 }}><input type="checkbox" checked={assistOnlyTasks} onChange={e => setAssistOnlyTasks(e.target.checked)} /> 只显示需要处理的产品（可补齐、待核对或需人工填写）</label>
          <table style={{ width: '100%' }}><thead><tr><th>产品</th><th>将补齐</th><th>需要核对 / 人工填写</th></tr></thead><tbody>{assist.items.filter(item => !assistOnlyTasks || item.changes.length || item.differences.length || item.warnings.length || item.manual.length).map(item => <tr key={item.id}>
            <td>#{item.product_no} {item.name}<br/><Link to={`/products/${item.id}`} onClick={() => setAssist(null)}>修改资料</Link></td>
            <td>{item.changes.length ? item.changes.map(c => <div key={c.field}>{c.label}：{c.field === 'product_image_url' ? <a href={String(c.value)} target="_blank" rel="noreferrer">查看图片</a> : String(c.value)}</div>) : '无需补齐'}</td>
            <td>{[...item.warnings, ...item.differences, ...(item.manual.length ? ['需填写：' + item.manual.join('、')] : [])].map((message, i) => <div key={i}>{message}</div>)}</td>
          </tr>)}</tbody></table>
          <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
            <button className="btn btn-primary" disabled={assistBusy || !assist.fillable} onClick={applyAssist}>确认仅补空白资料 ({assist.fillable})</button>
            <button className="btn btn-outline" disabled={assistBusy} onClick={previewAssist}>重新核对</button>
            <button className="btn btn-outline" disabled={assistBusy} onClick={() => setAssist(null)}>关闭</button>
          </div>
        </div>
      </div>}

      {archivedMode && <div className="product-task-banner"><div><strong>这里保存已删除产品</strong><span>原序号永久保留；恢复后仍使用原序号，新建产品继续按历史最大序号往后增加。</span></div></div>}

      {taskMode && (
        <div className="product-task-banner">
          <div>
            <strong>智能任务模式：{taskMode}</strong>
            <span>当前只显示需要处理的产品。点击每行右侧“修改资料”进入完整编辑界面；SKU 和售价也可直接点击表格字段修改。</span>
          </div>
          <Link to="/products" className="btn btn-outline btn-sm">退出任务模式</Link>
        </div>
      )}

      {missingField === 'image' && (
        <div className="image-recovery-bar">
          <div><strong>图片与资料补充</strong><span>优先读取本店卖家资料，未匹配时读取官网；不修改售价和采购成本。</span></div>
          <button className="btn btn-primary btn-sm" disabled={!!imageBatchProgress} onClick={handleRefetchMissingImages}>
            {imageBatchProgress || `补充缺图 (${products.filter(p => !p.product_image_url && p.takealot_url).length})`}
          </button>
          {imageBatchProgress && <button className="btn btn-outline btn-sm" onClick={() => { batchStop.current = true; setImageBatchProgress('正在结束当前产品，随后停止…'); }}>停止后续处理</button>}
        </div>
      )}

      {dataSource === 'remote' && !archivedMode && <div className="product-task-banner">
        <label>售价刷新来源：<select value={priceSource} onChange={e => setPriceSource(e.target.value as 'official' | 'seller')} disabled={refreshingIds.size > 0}>
          <option value="seller">本店报价（卖家 API）</option><option value="official">官网售价（网页读取）</option>
        </select></label><span>本店报价与官网售价不同；来源不会自动切换。点击售价可人工修改。</span>
      </div>}
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
          className="product-search-input"
          placeholder="搜索中文品名/英文品名/TSIN/链接..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label="搜索中文品名、英文品名、TSIN或链接"
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

        <button
          className="btn btn-primary btn-sm"
          disabled={selectedSkus.size === 0}
          title={selectedSkus.size === 0 ? '请先勾选产品（需有 SKU）' : `将选中的 ${selectedSkus.size} 个产品加入装箱单`}
          onClick={() => {
            const skus = Array.from(selectedSkus);
            navigate(`/packing?import=${encodeURIComponent(skus.join(','))}`);
          }}
        >
          加入装箱单{selectedSkus.size > 0 ? ` (${selectedSkus.size})` : ''}
        </button>
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
        <div className="table-wrapper product-list-table-wrapper">
          <div className="product-table-scroll">
            <table className="product-table" style={{ whiteSpace: 'nowrap' }}>
            <thead>
              <tr>
                {renderColumns.map((colKey, idx) => renderTh(colKey, idx))}
              </tr>
            </thead>
            <tbody>
              {pagedProducts.map((p) => {
                const linkBgColor = p.link_status === '已上架' ? '#e3f2fd' : p.link_status === '已购买' ? '#e8f5e9' : p.link_status === '已发货' ? '#fff3e0' : undefined;
                const branches = skuVariants(p.sku);
                const labels = variantLabels(p);
                return (
                  <Fragment key={p.id}>
                    <tr onContextMenu={e => { e.preventDefault(); setContextProduct({ id: p.id, x: e.clientX, y: e.clientY }); }} className={branches.length > 1 ? 'product-parent-row' : ''} style={linkBgColor ? { background: linkBgColor } : undefined}>
                      {renderColumns.map(colKey => (
                        <td key={colKey}>{renderCell(p, colKey)}</td>
                      ))}
                    </tr>
                    {branches.length > 1 && expandedVariantRows.has(p.id) && (
                      <tr className="product-branch-row">
                        <td colSpan={renderColumns.length}>
                          <div className="product-branch-rail">
                            {branches.map((sku, index) => (
                              <div key={sku} className={`product-branch-item ${index === 0 ? 'primary' : ''}`}>
                                <input type="checkbox" checked={selectedSkus.has(sku)} onChange={() => setSelectedSkus(prev => {
                                  const next = new Set(prev);
                                  if (next.has(sku)) next.delete(sku); else next.add(sku);
                                  return next;
                                })} />
                                <span className="branch-image">
                                  {p.product_image_url ? <img src={api.getImageUrl(p.product_image_url)} alt="" referrerPolicy="no-referrer" /> : <i>待补图</i>}
                                </span>
                                <span className={`branch-role ${index === 0 ? 'primary' : ''}`}>{index === 0 ? '主' : variantKind(labels[index])}</span>
                                <code>{sku}</code>
                                <span className="branch-names"><strong>{labels[index]}</strong><small>{p.product_name || '英文品名待回抓'}</small></span>
                                <span className="branch-spec">{variantSpec(labels[index], labels)}</span>
                                <button type="button" className="btn btn-outline btn-sm branch-edit-btn" onClick={() => setVariantEditor({ productId: p.id, index, sku, label: labels[index], makePrimary: index === 0 })}>修改分支</button>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
            </table>
          </div>
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
                onClick={() => goToPage(1)}
              >
                首页
              </button>
              <button
                className="btn btn-outline btn-sm"
                disabled={safeCurrentPage <= 1}
                onClick={() => goToPage(safeCurrentPage - 1)}
              >
                上一页
              </button>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }}>
                到第
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  value={jumpPage}
                  onChange={(e) => setJumpPage(e.target.value.replace(/\D/g, ''))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const n = parseInt(jumpPage, 10);
                      if (n >= 1 && n <= totalPages) { goToPage(n); setJumpPage(''); }
                    }
                  }}
                  style={{ width: 42, padding: '2px 4px', textAlign: 'center', fontSize: 13 }}
                />
                页
                <button
                  className="btn btn-outline btn-sm"
                  style={{ padding: '2px 8px', fontSize: 12 }}
                  onClick={() => {
                    const n = parseInt(jumpPage, 10);
                    if (n >= 1 && n <= totalPages) { goToPage(n); setJumpPage(''); }
                  }}
                >
                  跳转
                </button>
              </span>
              <button
                className="btn btn-outline btn-sm"
                disabled={safeCurrentPage >= totalPages}
                onClick={() => goToPage(safeCurrentPage + 1)}
              >
                下一页
              </button>
              <button
                className="btn btn-outline btn-sm"
                disabled={safeCurrentPage >= totalPages}
                onClick={() => goToPage(totalPages)}
              >
                尾页
              </button>
            </div>
          </div>
        </div>
      )}
      {variantEditor && <div className="packing-modal-mask" onClick={() => !savingVariant && setVariantEditor(null)}>
        <div className="packing-modal variant-editor-modal" onClick={e => e.stopPropagation()}>
          <h3>修改 SKU 分支</h3>
          <div className="form-group"><label>分支 SKU</label><input value={variantEditor.sku} onChange={e => setVariantEditor({ ...variantEditor, sku: e.target.value })} /></div>
          <div className="form-group"><label>真实规格名称</label><input value={variantEditor.label} onChange={e => setVariantEditor({ ...variantEditor, label: e.target.value })} placeholder="例如：军绿色、XL、128GB" /><small>填写用户能直接辨认的颜色、尺码、容量或型号。</small></div>
          <label className="variant-primary-toggle"><input type="checkbox" checked={variantEditor.makePrimary} onChange={e => setVariantEditor({ ...variantEditor, makePrimary: e.target.checked })} />设为主款（保存后移动到第一位）</label>
          <div className="modal-actions"><button className="btn btn-outline" disabled={savingVariant} onClick={() => setVariantEditor(null)}>取消</button><button className="btn btn-primary" disabled={savingVariant} onClick={saveVariant}>{savingVariant ? '保存中...' : '保存分支'}</button></div>
        </div>
      </div>}
      {contextProduct && <div className="product-context-menu" style={{ left: contextProduct.x, top: contextProduct.y }} onClick={e => e.stopPropagation()}>
        <button onClick={() => navigate(`/products/${contextProduct.id}?edit=1&from=list`)}>修改资料</button>
        <button onClick={() => navigate(`/products/${contextProduct.id}`)}>查看详情</button>
      </div>}
    </div>
  );
}
