import { Fragment, useEffect, useLayoutEffect, useState, useRef } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useApi, useDataSource } from '../DataSourceContext';
import { openUrl } from '../api';
import { getStoreStorageKey, readStoreStorage, getActiveStoreId, previewSellerAssist, applySellerAssist, getFxRates, getMarketPriceStatus, saveMarketPrices, syncMarketPrices, type SellerAssistResult, type MarketPriceStatus } from '../remoteApi';
import type { Product } from '../types';
import { formatPrice, formatPercent, SELECTION_STATUS_MAP, SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';
import { ProductImage } from '../utils/imageFallback';
import { FALLBACK_FX, MARKET_META, MARKET_ORDER, buildMarketBatch, describeSkipped, formatMarketPrice, formatSyncTime, formatZar, formatZarPrice, fxCaption, marketInsights, parseMarketPrices, staleNote, toZar, visibleMarkets, type MarketCode } from '../utils/marketPrice';
import { useFxRates } from '../utils/useFxRates';
import { beijingClock } from '../utils/datetime';
import { skuVariants, variantLabels } from '../utils/variants';
import { buildLabelBundle, saveLabelBundle } from '../utils/labelPdf';
import { isEan13 } from '../utils/ean';
import { safeSetItem } from '../utils/storage';
import { copyText } from '../utils/clipboard';
import { notifyCatalogChanged } from '../utils/catalogEvents';

// 需要重新抓图的行：缺图，或者分支变体的图片仍是主变体那张（同组同图）。
const imageRefetchPending = (rows: Product[]) => {
  const primaryImage = new Map<string, string>();
  rows.forEach(row => {
    if (row.variant_group && row.is_primary_variant !== false && row.product_image_url) {
      primaryImage.set(row.variant_group, row.product_image_url);
    }
  });
  return rows.filter(row => {
    if (!row.takealot_url || row.product_image_path) return false;
    if (!row.product_image_url) return true;
    return !!(row.variant_group && row.is_primary_variant === false
      && row.product_image_url === primaryImage.get(row.variant_group));
  });
};

// --- 列定义 ---
interface ColumnDef {
  key: string;
  label: string;
  defaultWidth: number;
}

const allColumns: ColumnDef[] = [
  { key: 'select', label: '勾选', defaultWidth: 40 },
  { key: 'product_no', label: '#', defaultWidth: 50 },
  { key: 'link_status', label: '链接状态', defaultWidth: 104 },
  { key: 'image', label: '图片', defaultWidth: 80 },
  { key: 'product_name', label: '标题', defaultWidth: 200 },
  { key: 'chinese_product_name', label: '中文品名', defaultWidth: 120 },
  { key: 'actual_sale_price_zar', label: '售价', defaultWidth: 132 },
  { key: 'market_prices', label: '参考价（R）', defaultWidth: 210 },
  { key: 'fee_category', label: 'Fee品类', defaultWidth: 140 },
  { key: 'competing_sellers_count', label: '竞品卖家数', defaultWidth: 90 },
  { key: 'profit_margin', label: '利润率', defaultWidth: 80 },
  { key: 'profit_zar', label: '利润', defaultWidth: 80 },
  // SKU 列要同时放下 13 位数字和「复制」按钮，140 会挤成两行。
  { key: 'sku', label: 'SKU', defaultWidth: 168 },
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
  useEffect(() => {
    if (!assist) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setAssist(null); };
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', close);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', close); };
  }, [assist]);
  const [assistOnlyTasks, setAssistOnlyTasks] = useState(true);
  const [assistBusy, setAssistBusy] = useState(false);
  const [assistError, setAssistError] = useState('');
  /** 成功类提示单独一个状态：用 role="status" 播报，不会被读屏当成错误 */
  const [assistNotice, setAssistNotice] = useState('');
  const previewAssist = async () => {
    setAssistBusy(true); setAssistError(''); setAssistNotice('');
    try { setAssist(await previewSellerAssist(products.map(p => p.id))); }
    catch (e) { setAssistError(e instanceof Error ? e.message : '读取失败，请重试'); }
    finally { setAssistBusy(false); }
  };
  const applyAssist = async () => {
    if (!assist) return;
    setAssistBusy(true); setAssistError(''); setAssistNotice('');
    try {
      const result = await applySellerAssist(assist.items.filter(i => i.changes.length));
      setAssist(null); fetchProducts();
      setAssistNotice(`已补齐 ${result.fillable} 个产品。尺寸和重量补齐后已重新计算物流费用与利润。`);
    } catch (e) { setAssistError(e instanceof Error ? e.message : '补齐失败，请重新预览'); }
    finally { setAssistBusy(false); }
  };
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const [refreshDetail, setRefreshDetail] = useState<{ id: string; message: string } | null>(null);
  const [refreshMessages, setRefreshMessages] = useState<Record<string, string>>({});
  const batchStop = useRef(false);
  const requestSeq = useRef(0);
  const refreshTimers = useRef<Record<string, number>>({});
  useEffect(() => () => { Object.values(refreshTimers.current).forEach(timer => window.clearTimeout(timer)); }, []);
  const [refreshingIds, setRefreshingIds] = useState<Set<string>>(new Set());
  const [imageBatchProgress, setImageBatchProgress] = useState('');
  const [expandedVariantRows, setExpandedVariantRows] = useState<Set<string>>(new Set());
  const [variantEditor, setVariantEditor] = useState<{ productId: string; index: number; sku: string; label: string; makePrimary: boolean } | null>(null);
  const [savingVariant, setSavingVariant] = useState(false);
  const variantDialogRef = useRef<HTMLDivElement>(null);
  const variantSavingRef = useRef(false);
  variantSavingRef.current = savingVariant;
  const variantEditorOpen = variantEditor !== null;
  useEffect(() => {
    if (!variantEditorOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    const dialog = variantDialogRef.current;
    if (!dialog) return;
    const controls = () => [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')];
    (controls()[0] || dialog).focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (!variantSavingRef.current) setVariantEditor(null);
      } else if (event.key === 'Tab') {
        const items = controls(); const first = items[0]; const last = items[items.length - 1];
        if (!first) { event.preventDefault(); dialog.focus(); }
        else if (!dialog.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener('keydown', handleKey, true);
    return () => { document.removeEventListener('keydown', handleKey, true); if (opener?.isConnected) opener.focus(); };
  }, [variantEditorOpen]);
  const [contextProduct, setContextProduct] = useState<{ id: string; x: number; y: number } | null>(null);
  // 参考价悬停卡：坐标用 fixed 定位，避免被表格滚动容器裁掉
  const [marketTip, setMarketTip] = useState<{ product: Product; x: number; y: number } | null>(null);
  // 参考价批量录入面板。淘宝/中国官网都抓不到，14 个 SKU 一个个点进详情页太慢，
  // 这里一次贴完；SG 是后端自动同步的，面板里只读。
  const [mpPanel, setMpPanel] = useState<{ draft: Record<string, { CN: string; TB: string }> } | null>(null);
  // 面板声明了 aria-modal="true"，就要像「核对本店资料」那个弹窗一样能被 Esc 关掉、
  // 且关掉之前背后不滚动。原来只有点「关闭 ×」或点遮罩两条路：键盘用户按 Esc 没反应，
  // 窄屏上手指划的其实是背后那张产品表（弹窗本身没锁滚动）。
  useEffect(() => {
    if (!mpPanel) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMpPanel(null); };
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', close);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', close); };
  }, [mpPanel]);
  const [mpStatus, setMpStatus] = useState<MarketPriceStatus | null>(null);
  const [mpBusy, setMpBusy] = useState(false);
  const [mpMessage, setMpMessage] = useState('');
  // 兰特汇率：参考价是三地本币，折成 R 才能和 Takealot 售价直接比。
  // 加载器从 remoteApi 注入（而不是让 hook 自己 import），见 useFxRates 的注释。
  const fx = useFxRates({ load: getFxRates, fallback: FALLBACK_FX });
  const [selectedSkus, setSelectedSkus] = useState<Set<string>>(() => {
    try {
      const saved = JSON.parse(readStoreStorage(LS_SELECTED_SKUS_KEY) || '[]');
      return new Set(Array.isArray(saved) ? saved.filter((sku): sku is string => typeof sku === 'string') : []);
    } catch { return new Set(); }
  });

  const [labelLoading, setLabelLoading] = useState(false);
  // 「生成成功、但跳过了几个」不是错误，不该占用页面顶部那条红色告警：
  // `error` 同时是加载失败的位置，红字会让人以为整页没读到。
  const [labelNote, setLabelNote] = useState('');
  // SKU → 产品名，只增不减（新值覆盖旧值，空名不覆盖）。
  //
  // 为什么要缓存而不是下载时临时扫 `products`：勾选存在 localStorage 里、不随筛选
  // 变化，而 `products` 只是当前筛选结果。勾了 3 个再按关键词筛出 1 个，剩下 2 个
  // 在 `products` 里查不到名字，`buildLabelBundle()` 就会退回用 SKU 当产品名——
  // 印出来是「CK2-90151648-1」而不是商品名，而且看不出哪里错。
  const labelNames = useRef(new Map<string, string>());
  useEffect(() => {
    products.forEach((p) => {
      const name = (p.product_name || '').trim();
      if (!name) return; // 改名清空时不要把已有名字擦掉
      skuVariants(p.sku || '').forEach((sku) => labelNames.current.set(sku, name));
    });
  }, [products]);
  // 按钮上写「能真正出标签的数量」，不是勾选总数：不是合法 EAN-13 的 SKU 会被
  // 跳过（见 utils/ean.ts 的说明），写总数的话「勾 3 个只下到 1 张」看起来像文件
  // 丢了。详情页的 labelCount 就是这么算的，两页口径必须一致。
  const labelCount = Array.from(selectedSkus).filter(isEan13).length;

  // 每个 SKU 一个独立 PDF（页面 40×30），多选打包成 zip。
  // 浏览器里直接出（src/utils/labelPdf.ts），不依赖后端部署；条码不合法的 SKU 跳过。
  const downloadLabels = () => {
    const skus = Array.from(selectedSkus);
    if (!skus.length || labelLoading) return;
    setLabelLoading(true);
    setError('');
    setLabelNote('');
    try {
      // 勾选里只有 SKU，产品名从缓存里按 SKU 反查（多规格产品一个品对应多个 SKU）。
      // 用缓存而不是 `products`：被筛选挡掉的行仍然要印出真名字，见 labelNames 的注释。
      const bundle = buildLabelBundle(
        skus.map((sku) => ({ sku, name: labelNames.current.get(sku) || '' })),
        { size: '40x30' },
      );
      if (!bundle.count) {
        setError('选中的 SKU 都不是合法的 EAN-13 条码，生成不了标签');
        return;
      }
      saveLabelBundle(bundle);
      if (bundle.skipped.length) {
        setLabelNote(
          `已生成 ${bundle.count} 张标签；另有 ${bundle.skipped.length} 个 SKU 不是合法 EAN-13，已跳过`,
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成产品标签失败');
    } finally {
      setLabelLoading(false);
    }
  };

  // 在产品库和装箱单之间切换时保留本次勾选，成功导出装箱单后由装箱单页清除。
  useEffect(() => {
    // 每次勾选都会写；配额满时若抛出会让整个列表崩掉
    safeSetItem(getStoreStorageKey(LS_SELECTED_SKUS_KEY), JSON.stringify(Array.from(selectedSkus)));
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
  // 正在进行的拖拽的收尾函数；组件卸载时补调一次，别把监听器和光标留在页面上。
  const dragStop = useRef<(() => void) | null>(null);
  useEffect(() => () => { dragStop.current?.(); }, []);

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
          safeSetItem(LS_ORDER_KEY, JSON.stringify(newOrder));
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
  const maintenanceMode = searchParams.get('maintenance') === '1';
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

    // 拖到一半换页面（或点链接跳走）时 mouseup 永远不会来：
    // 监听器会留在 document 上，body 的光标也会一直是 col-resize。
    // 所以把「收尾」存进 ref，卸载时补一次。
    const stop = () => {
      resizeState.current = null;
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', stop);
      document.body.style.cursor = '';
      dragStop.current = null;
    };
    dragStop.current = stop;

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', stop);
    document.body.style.cursor = 'col-resize';
  };

  const getColWidth = (colKey: string, defaultWidth: number): number =>
    colKey === 'actual_sale_price_zar' ? Math.max(132, colWidths[colKey] || defaultWidth)
      // SKU 列再窄也留得下 13 位数字 + 复制按钮，否则数字会断成两行。
      : colKey === 'sku' ? Math.max(150, colWidths[colKey] || defaultWidth)
      // 链接状态下拉要放下「已购买」三个字 + 箭头，80px 时只剩 48px 文本区，
      // 浏览器会把值裁成「已」，看起来像数据坏了。
      : colKey === 'link_status' ? Math.max(104, colWidths[colKey] || defaultWidth)
      : colWidths[colKey] || defaultWidth;

  // --- 列可见性 ---
  const toggleColumn = (key: string) => {
    setVisibleColumns(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      safeSetItem(LS_VISIBLE_KEY, JSON.stringify([...next]));
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
    // 请求序号：快速切换筛选时，先发的请求可能后返回并覆盖新结果
    const seq = ++requestSeq.current;
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

    return api.getProducts(params)
      .then(rows => { if (seq === requestSeq.current) setProducts(rows); })
      .catch((e: Error) => { if (seq === requestSeq.current) setError(e.message); })
      .finally(() => { if (seq === requestSeq.current) setLoading(false); });
  };

  useEffect(() => {
    fetchProducts();
    // api 随数据源切换而变化（useApi 按 dataSource memo），不放进依赖会导致
    // 切换本地/远程后列表仍是旧数据源的结果
  }, [api, dataSource, statusFilter, feeFilter, shippingFilter.join(','), linkStatusFilter.join(','), searchText, missingField, multiSkuOnly, duplicateSkuOnly, archivedMode]);

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

  const handleFieldUpdate = async (id: string, field: string, value: any, reportError = (message: string) => alert(message)) => {
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
      // 中文品名/SKU 变了，装箱单那边得跟着刷新，否则用户以为改动没生效。
      notifyCatalogChanged([field]);
      return updated;
    } catch (e: any) {
      reportError(e.message);
      return null;
    }
  };
  const handleRefreshPrice = async (id: string, source: 'official' | 'seller' = getActiveStoreId() === 'default-store' ? 'seller' : 'official') => {
    window.clearTimeout(refreshTimers.current[id]);
    setRefreshMessages(prev => ({ ...prev, [id]: '' }));
    setRefreshingIds((prev) => new Set(prev).add(id));
    try {
      const result: Record<string, any> = await api.refreshPrice(id, dataSource === 'remote' ? source : 'official');
      setRefreshMessages(prev => ({ ...prev, [id]: `${result.data_source === 'seller_api' ? '本店报价' : '官网售价'}已更新 · ${beijingClock()}${(result.warnings || []).length ? '；' + result.warnings.join('；') : ''}` }));
      refreshTimers.current[id] = window.setTimeout(() => setRefreshMessages(prev => prev[id]?.includes('已更新') ? { ...prev, [id]: '' } : prev), 6000);
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
      const message = e.message || '读取失败，原售价保留';
      setRefreshMessages(prev => ({ ...prev, [id]: message }));
    } finally {
      setRefreshingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const openMarketPanel = async () => {
    const draft: Record<string, { CN: string; TB: string }> = {};
    products.forEach((p) => {
      const prices = parseMarketPrices(p.market_prices);
      draft[p.id] = {
        CN: prices.CN ? String(prices.CN.price) : '',
        TB: prices.TB ? String(prices.TB.price) : '',
      };
    });
    setMpPanel({ draft });
    setMpMessage('');
    setMpStatus(null);
    try {
      setMpStatus(await getMarketPriceStatus());
    } catch (e: any) {
      setMpMessage(e.message || '读取同步状态失败');
    }
  };

  const saveMarketPanel = async () => {
    if (!mpPanel) return;
    // 无效行（0 / 负数 / 非数字）不静默丢弃：交回去点名，面板保持打开让人改。
    const { items, skipped } = buildMarketBatch(products, mpPanel.draft);
    if (!items.length) {
      setMpMessage(skipped.length
        ? `价格必须大于 0，${describeSkipped(skipped)} 未保存`
        : '没有需要保存的改动');
      return;
    }
    setMpBusy(true);
    try {
      const result = await saveMarketPrices(items);
      fetchProducts();
      notifyCatalogChanged(['market_prices']);
      if (skipped.length) {
        setMpMessage(`已保存 ${result.saved} 条；${describeSkipped(skipped)} 价格无效（需大于 0）未保存`);
      } else {
        setMpPanel(null);
        setMpMessage(`已保存 ${result.saved} 条`);
      }
    } catch (e: any) {
      setMpMessage(e.message || '保存失败');
    } finally {
      setMpBusy(false);
    }
  };

  const runMarketSync = async () => {
    setMpBusy(true);
    try {
      const result = await syncMarketPrices();
      setMpMessage(result.message || '已开始重读官网价');
    } catch (e: any) {
      setMpMessage(e.message || '触发同步失败');
    } finally {
      setMpBusy(false);
    }
  };

  const handleRefetchMissingImages = async () => {
    // 缺图行之外，还要包含「与主变体同图」的分支：它们当初就是被抓成主图的那张，
    // 不重新抓一次永远换不成自己的变体图。
    const pending = imageRefetchPending(products);
    if (pending.length === 0) {
      alert('当前没有需要补充图片的产品：缺图行和沿用主图的分支变体都已处理。');
      return;
    }
    let success = 0; let processed = 0;
    const failures: string[] = [];
    const shared: string[] = [];
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
        else if (product.variant_group && product.is_primary_variant === false) shared.push(`#${product.product_no}`);
        else failures.push(`#${product.product_no}：未获取图片，请人工上传`);
      } catch (e) {
        failures.push(`#${product.product_no}：${e instanceof Error ? e.message : '读取失败'}`);
      } finally {
        setRefreshingIds(prev => { const next = new Set(prev); next.delete(product.id); return next; });
      }
    }
    setImageBatchProgress('');
    await fetchProducts();
    // 结果摘要属于"完成通知"，不是错误：用 status 播报；只有真的失败才另起 error
    setAssistNotice(`图片处理${batchStop.current ? '已停止' : '完成'}：成功 ${success} 个，已处理 ${processed}/${pending.length}，未获取图片 ${processed - success} 个。${shared.length ? `其中 ${shared.join('、')} 官网没有独立变体图，仍沿用主图。` : ''}`);
    setAssistError(failures.length ? `以下产品处理失败：${failures.join('；')}` : '');
  };

  const saveVariant = async () => {
    if (!variantEditor || variantSavingRef.current) return;
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
    variantSavingRef.current = true;
    setSavingVariant(true);
    try {
      const updated = await api.updateProduct(product.id, { sku: skus.join(' '), chinese_product_name: labels.join(' ') });
      setProducts(prev => prev.map(item => item.id === product.id ? updated : item));
      setSelectedSkus(prev => { const next = new Set(prev); if (next.delete(oldSku)) next.add(nextSku); return next; });
      setVariantEditor(null);
      // 分支名就是装箱单里那一行的中文名，改完必须让装箱单重新同步。
      notifyCatalogChanged(['sku', 'chinese_product_name']);
    } catch (e: any) { alert(e.message || '分支保存失败'); }
    finally { variantSavingRef.current = false; setSavingVariant(false); }
  };

  // --- 内联编辑 ---
  // SKU 不在此列：它是外部平台的唯一标识，改了就跟 Takealot 上的商品对不上，
  // 采集和调价会一起失准。这里只给复制，暂时不允许在列表里改。
  const [editingPriceId, setEditingPriceId] = useState<string | null>(null);
  const [editingPriceValue, setEditingPriceValue] = useState('');
  const [editingCnameId, setEditingCnameId] = useState<string | null>(null);
  const [editingCnameValue, setEditingCnameValue] = useState('');
  const [copiedSku, setCopiedSku] = useState<string | null>(null);
  const [copyFailedSku, setCopyFailedSku] = useState<string | null>(null);
  const inlineSession = useRef<string | null>(null);
  const inlineWriting = useRef(false);
  const inlineInput = useRef<HTMLInputElement>(null);
  const [inlineBusy, setInlineBusy] = useState(false);
  const [inlineError, setInlineError] = useState('');
  useEffect(() => { if (inlineError && !inlineBusy) inlineInput.current?.focus(); }, [inlineError, inlineBusy]);
  const beginInline = (id: string, field: string) => {
    if (inlineWriting.current) return false;
    inlineSession.current = `${id}:${field}`; setInlineError('');
    setEditingPriceId(null); setEditingCnameId(null);
    return true;
  };
  const cancelInline = (close: () => void) => {
    if (inlineWriting.current) return;
    inlineSession.current = null; setInlineError(''); close();
  };
  const commitInline = async (id: string, field: string, value: any, close: () => void) => {
    if (inlineWriting.current || inlineSession.current !== `${id}:${field}`) return;
    inlineWriting.current = true; setInlineBusy(true); setInlineError('');
    try {
      const updated = await handleFieldUpdate(id, field, value, message => setInlineError(`保存失败：${message}，回车重试`));
      if (!updated) return;
      inlineSession.current = null; close();
    } finally { inlineWriting.current = false; setInlineBusy(false); }
  };
  const inlineFeedback = <>{inlineBusy && <small role="status">保存中…</small>}{inlineError && <small id="inline-save-error" role="alert" className="text-danger">{inlineError}</small>}</>;

  const startEditPrice = (p: Product) => {
    if (!beginInline(p.id, 'actual_sale_price_zar')) return;
    setEditingPriceId(p.id);
    setEditingPriceValue(p.actual_sale_price_zar != null ? String(p.actual_sale_price_zar) : '');
  };
  const commitEditPrice = async (id: string) => {
    if (inlineWriting.current || inlineSession.current !== `${id}:actual_sale_price_zar`) return;
    const val = parseFloat(editingPriceValue);
    if (!Number.isFinite(val)) { setInlineError('请输入有效售价'); return; }
    await commitInline(id, 'actual_sale_price_zar', val, () => setEditingPriceId(null));
  };
  // 复制 SKU：只写剪贴板，不发请求。成功/失败都在按钮上就地反馈，
  // 不用 toast——用户点完手还在那一格，眼睛也在那一格。
  const copySku = async (sku: string) => {
    const ok = await copyText(sku);
    if (ok) {
      setCopiedSku(sku); setCopyFailedSku(null);
      setTimeout(() => setCopiedSku(current => current === sku ? null : current), 2000);
    } else {
      setCopyFailedSku(sku); setCopiedSku(null);
      setTimeout(() => setCopyFailedSku(current => current === sku ? null : current), 3000);
    }
  };
  const startEditCname = (p: Product) => {
    if (!beginInline(p.id, 'chinese_product_name')) return;
    setEditingCnameId(p.id);
    setEditingCnameValue(p.chinese_product_name || '');
  };
  const commitEditCname = async (id: string) => {
    // 不再 slice(0, 10)：后端列是 String 无长度限制，ProductDetail 对同一字段
    // 用的是 30（单规格）/200（多规格），这里按同一规则对齐，避免静默丢字。
    const trimmed = editingCnameValue.trim();
    await commitInline(id, 'chinese_product_name', trimmed || null, () => setEditingCnameId(null));
  };

  const feeCategories = [...new Set(products.map((p) => p.fee_category).filter(Boolean))] as string[];

  // --- 分页 ---
  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const pagedProducts = products.slice((safeCurrentPage - 1) * pageSize, safeCurrentPage * pageSize);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const resetPageScroll = useRef(false);
  useLayoutEffect(() => {
    if (!resetPageScroll.current || !tableScrollRef.current) return;
    resetPageScroll.current = false;
    const table = tableScrollRef.current;
    table.scrollTop = 0; // Keep horizontal column position, reset only the new page's rows.
    const headerHeight = document.querySelector('.app-header')?.getBoundingClientRect().height || 0;
    window.scrollTo({ top: Math.max(0, window.scrollY + table.getBoundingClientRect().top - headerHeight - 12), behavior: 'auto' });
  }, [currentPage, pageSize]);

  const syncPageToUrl = (page: number, size: number) => {
    const next = new URLSearchParams(searchParams);
    if (page > 1) next.set('page', String(page));
    else next.delete('page');
    if (size !== 20) next.set('pageSize', String(size));
    else next.delete('pageSize');
    setSearchParams(next, { replace: true });
  };

  const handlePageSizeChange = (size: number) => {
    if (size === pageSize) return;
    resetPageScroll.current = true;
    setPageSize(size);
    setCurrentPage(1);
    syncPageToUrl(1, size);
  };

  const goToPage = (page: number) => {
    if (page === safeCurrentPage) return;
    resetPageScroll.current = true;
    setCurrentPage(page);
    syncPageToUrl(page, pageSize);
  };

  // --- 单元格渲染 ---
  const renderCell = (p: Product, colKey: string) => {
    const isRefreshing = refreshingIds.has(p.id);
    switch (colKey) {
      case 'select':
        if (!p.sku) return <span style={{ color: 'var(--text-subtle)', fontSize: 11 }}>无SKU</span>;
        const variants = skuVariants(p.sku);
        const allVariantsChecked = variants.length > 0 && variants.every(sku => selectedSkus.has(sku));
        return (
          <input
            type="checkbox"
            aria-label={`选择产品：${p.sku || p.product_no}`}
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
          <ProductImage
            src={api.getImageUrl(p.product_image_url)}
            referrerPolicy="no-referrer"
            style={{ width: 68, height: 68, objectFit: 'cover', borderRadius: 4 }}
          />
        ) : (
          <div style={{ width: 68, height: 68, background: 'var(--surface-muted)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-subtle)', fontSize: 11 }}>无图</div>
        );
      case 'product_name':
        return (
          <span style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>
            {p.takealot_url ? (
              <a
                href={p.takealot_url}
                target="_blank" rel="noreferrer"
                onClick={e => { if (!e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.button === 0) { e.preventDefault(); void openUrl(p.takealot_url!); } }}
                title={p.product_name || ''}
                style={{ textDecoration: 'none', color: 'var(--color-primary)', cursor: 'pointer' }}
              >
                {p.product_name || '(无标题)'}
              </a>
            ) : (
              <span title={p.product_name || ''}>{p.product_name || '(无标题)'}</span>
            )}
          </span>
        );
      case 'chinese_product_name':
        return editingCnameId === p.id ? (
          <span className="product-inline-editor">
          <input
            ref={inlineInput} disabled={inlineBusy} aria-busy={inlineBusy} aria-invalid={!!inlineError} aria-describedby={inlineError ? 'inline-save-error' : undefined}
            type="text" value={editingCnameValue}
            aria-label={`中文品名：${p.sku}`} name="chinese_product_name" autoComplete="off"
            maxLength={skuVariants(p.sku).length > 1 ? 200 : 30}
            onChange={(e) => setEditingCnameValue(e.target.value)}
            onBlur={() => commitEditCname(p.id)}
            onKeyDown={(e) => { if (e.key === 'Enter') commitEditCname(p.id); if (e.key === 'Escape') cancelInline(() => setEditingCnameId(null)); }}
            autoFocus
            className="cell-control"
          />
          {inlineFeedback}</span>
        ) : (
          <button type="button" disabled={inlineBusy} className="product-cell-edit" aria-label={`编辑中文品名：${p.sku}`} onClick={() => startEditCname(p)} title={p.chinese_product_name || '编辑中文品名'} style={{ minWidth: 30, maxWidth: 120 }}>
            {p.chinese_product_name || '-'}
          </button>
        );
      case 'actual_sale_price_zar':
        return (
          <div className="price-refresh-cell">
            {editingPriceId === p.id ? (
              <span className="product-inline-editor">
              <input
                ref={inlineInput} disabled={inlineBusy} aria-busy={inlineBusy} aria-invalid={!!inlineError} aria-describedby={inlineError ? 'inline-save-error' : undefined}
                type="number" step="0.01"
                aria-label={`售价：${p.sku}`} name="actual_sale_price_zar" autoComplete="off"
                value={editingPriceValue}
                onChange={(e) => setEditingPriceValue(e.target.value)}
                onBlur={() => commitEditPrice(p.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitEditPrice(p.id); if (e.key === 'Escape') cancelInline(() => setEditingPriceId(null)); }}
                autoFocus
                className="cell-control"
                style={{ width: 70 }}
              />
              {inlineFeedback}</span>
            ) : (
              <button type="button" disabled={inlineBusy} className="product-cell-edit" aria-label={`编辑售价：${p.sku}`} onClick={() => startEditPrice(p)} title="编辑售价" style={{ minWidth: 50 }}>
                {formatPrice(p.actual_sale_price_zar, 'ZAR')}
              </button>
            )}
            <button
              onClick={() => handleRefreshPrice(p.id)}
              disabled={isRefreshing || !p.takealot_url}
              className="price-refresh-button"
              aria-busy={isRefreshing}
              aria-label={isRefreshing ? '正在读取售价' : '刷新售价'}
              title={dataSource === 'remote' ? (getActiveStoreId() === 'default-store' ? '刷新本店报价；官网读取在更多菜单中' : '刷新官网售价') : '刷新官网售价'}
            >
              <span aria-hidden="true">↻</span>
            </button>
            {refreshMessages[p.id] && <button type="button" className="price-refresh-status" data-result={refreshMessages[p.id].includes('已更新') ? 'success' : 'error'} aria-label={refreshMessages[p.id].includes('已更新') ? '售价已更新，查看说明' : '读取失败，查看原因'} title={refreshMessages[p.id].includes('已更新') ? '已更新，点击查看说明' : '读取失败，点击查看原因'} onClick={() => setRefreshDetail({ id: p.id, message: refreshMessages[p.id] })}>{refreshMessages[p.id].includes('已更新') ? '✓' : '!'}</button>}
          </div>
        );
      case 'market_prices': {
        const prices = parseMarketPrices(p.market_prices);
        // 一个方框，只装采到的市场（见 visibleMarkets）：新加坡官网价是唯一由服务端
        // 自动重读的权威值，放前面加粗；国内原价 / 到手价是人工录入的参考值，
        // 跟在后面用次要色。采不到的市场不占位。
        const available = visibleMarkets(prices);
        if (!available.length) return <span style={{ color: 'var(--color-text-secondary)' }}>—</span>;
        const [lead, ...rest] = available;
        const leadEntry = prices[lead]!;
        const tag = (code: MarketCode) => (code === 'CN' ? '原' : code === 'TB' ? '到手' : '');
        // 统一折成兰特：三地本币跟 Takealot 售价不同币种，不折算就没法直接比。
        // 汇率缺失时退回原币而不是留空——原币也是真数据，空着等于说「没采到」。
        const money = (code: MarketCode) =>
          formatZarPrice(code, prices[code]!, fx) || formatMarketPrice(code, prices[code]!);
        return (
          <span
            onMouseEnter={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              setMarketTip({ product: p, x: rect.left, y: rect.bottom + 6 });
            }}
            onMouseLeave={() => setMarketTip(null)}
            style={{
              display: 'inline-flex', gap: 6, alignItems: 'center', whiteSpace: 'nowrap',
              padding: '1px 7px', borderRadius: 4,
              border: '0.5px solid var(--color-border)', background: 'var(--surface-muted)',
            }}
          >
            {/* 过期按**单个市场**判，不按整格：原来只有打头的那个市场过期时整格才变淡，
                于是「新加坡价是新的、国内到手价是两周前且已过期」这行看起来完全正常。
                标记用 `staleNote()` 的文字而不是降 opacity：0.6 透明度把 12.5px 的
                #1f2937 混成 3.65:1（低于 4.5 门槛），既难读、又只有看得见的人能收到信号；
                文字标记与批量面板、悬停卡同一口径。 */}
            <span style={{ fontSize: 12.5, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>
              {money(lead)}
              {staleNote(leadEntry) ? <span style={{ color: 'var(--color-text-secondary)', fontSize: 11 }}>{staleNote(leadEntry)}</span> : null}
            </span>
            {rest.length > 0 && (
              <span aria-hidden="true" style={{ width: 1, height: 11, background: 'var(--color-border)' }} />
            )}
            {rest.map((code) => (
              <span
                key={code}
                style={{ fontSize: 11.5, color: 'var(--color-text-secondary)', fontVariantNumeric: 'tabular-nums' }}
              >
                {tag(code)}
                {money(code)}
                {staleNote(prices[code])}
              </span>
            ))}
          </span>
        );
      }
      case 'fee_category':
        return (
          <select
            aria-label={`Fee品类：${p.sku}`}
            value={p.fee_category || ''}
            onChange={(e) => handleFieldUpdate(p.id, 'fee_category', e.target.value || null)}
            className="cell-control"
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
      case 'sku': {
        // SKU 是平台侧的唯一标识：改了它，采集、调价、装箱单就都对不上号，
        // 所以这里只读 + 复制，不给改。多规格时「展开」只是摊开看，不是编辑。
        if (!p.sku) return <span className="sku-cell"><span className="sku-value is-empty">无SKU</span></span>;
        const sku = p.sku;
        const skuOptions = skuVariants(sku);
        const expanded = expandedVariantRows.has(p.id);
        return (
          <span className="sku-cell">
            {skuOptions.length > 1 ? (
              <span className="sku-branch-summary">
                <b translate="no">{skuOptions[0]}</b>
                <button
                  type="button" aria-expanded={expanded}
                  onClick={() => setExpandedVariantRows(prev => { const next = new Set(prev); if (next.has(p.id)) next.delete(p.id); else next.add(p.id); return next; })}
                >{expanded ? '收起规格' : `展开 ${skuOptions.length} 个规格`}</button>
              </span>
            ) : (
              <span className="sku-value" translate="no" title={sku}>{sku}</span>
            )}
            <button
              type="button"
              className={`sku-copy${copiedSku === sku ? ' is-done' : ''}${copyFailedSku === sku ? ' is-failed' : ''}`}
              aria-label={`复制SKU：${sku}`}
              title={skuOptions.length > 1 ? `复制 ${skuOptions.length} 个规格的 SKU（空格分隔）` : '复制 SKU'}
              onClick={() => { void copySku(sku); }}
            >{copyFailedSku === sku ? '复制失败' : copiedSku === sku ? '已复制' : '复制'}</button>
          </span>
        );
      }
      case 'minimum_price_at_20_margin':
        return formatPrice(p.minimum_price_at_20_margin, 'ZAR');
      case 'minimum_price_at_15_margin':
        return formatPrice(p.minimum_price_at_15_margin, 'ZAR');
      case 'shipping_method':
        return (
          <select
            aria-label={`运输方式：${p.sku}`}
            value={p.shipping_method || ''}
            onChange={(e) => handleFieldUpdate(p.id, 'shipping_method', e.target.value || null)}
            className="cell-control"
            style={{ whiteSpace: 'nowrap' }}
          >
            <option value="">-</option>
            {SHIPPING_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        );
      case 'link_status':
        return (
          <select
            aria-label={`链接状态：${p.sku}`}
            value={p.link_status || '未购买'}
            onChange={(e) => handleFieldUpdate(p.id, 'link_status', e.target.value)}
            className="cell-control cell-control--tinted"
            style={{
              background: LINK_STATUS_MAP[p.link_status || '未购买']?.color || 'var(--surface-muted)',
              color: LINK_STATUS_MAP[p.link_status || '未购买']?.text || 'var(--text-neutral-strong)',
              border: 'none', borderRadius: 8,
            }}
          >
            {LINK_STATUS_OPTIONS.map((s) => (
              <option key={s} value={s} style={{ background: 'var(--color-card)', color: 'var(--text-body)' }}>{LINK_STATUS_MAP[s].label}</option>
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
            <button className="btn btn-outline btn-sm" style={{ marginLeft: 4 }} aria-label="更多产品操作" onClick={e => { e.stopPropagation(); setContextProduct({ id: p.id, x: Math.max(8, Math.min(e.clientX, window.innerWidth - 210)), y: Math.max(8, Math.min(e.clientY, window.innerHeight - 210)) }); }}>⋯</button>
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
        <h2>{archivedMode ? '废品库' : maintenanceMode ? '资料维护' : '产品列表'} ({products.length})</h2>
        {maintenanceMode && <div style={{ display: 'flex', gap: 8 }}><button className="btn btn-outline" disabled={assistBusy || loading || !products.length || products.length > 500} title={products.length > 500 ? `当前 ${products.length} 个产品，超过单次核对上限 500 个，请先用筛选缩小范围` : undefined} onClick={previewAssist}>{assistBusy ? '核对中…' : '核对缺字段'}</button><Link to="/products" className="btn btn-outline">返回产品列表</Link></div>}
        {!archivedMode && !maintenanceMode && dataSource === 'remote' && <button className="btn btn-outline seller-assist-shortcut" disabled={assistBusy || loading || !products.length || products.length > 500} title={products.length > 500 ? `当前 ${products.length} 个产品，超过单次核对上限 500 个，请先用筛选缩小范围` : undefined} onClick={previewAssist}>{assistBusy ? '核对中…' : '核对本店资料'}</button>}
        {!archivedMode && !maintenanceMode && dataSource === 'remote' && <button className="btn btn-outline" onClick={openMarketPanel} title="批量录入国内原价 / 到手价（照天猫、京东商品页抄）；新加坡官网价由后端每天自动重读">参考价</button>}
        {archivedMode ? <Link to="/products" className="btn btn-outline">返回产品列表</Link> : <Link to="/create" className="btn btn-primary">+ 新建产品</Link>}
      </div>

      {refreshDetail && <div className="refresh-detail-panel" role="status">
        <div style={{ flex: 1, minWidth: 0 }}><strong>#{products.find(p => p.id === refreshDetail.id)?.product_no ?? ''} 售价刷新结果</strong><p>{refreshDetail.message}</p></div>
        <button className="btn btn-outline btn-sm" onClick={() => setRefreshDetail(null)}>关闭</button>
      </div>}
      {assistNotice && <div role="status" className="product-task-banner">{assistNotice}</div>}
      {assistError && <div role="alert" className="product-task-banner">{assistError}</div>}
      {assist && <div className="seller-assist-backdrop" role="dialog" aria-modal="true" aria-label="卖家资料补齐预览" onClick={e => { if (e.target === e.currentTarget) setAssist(null); }}>
        <div className="seller-assist-dialog">
          <div className="seller-assist-header"><strong>卖家资料核对</strong><button className="btn btn-outline btn-sm" onClick={() => setAssist(null)}>关闭 ×</button></div>
          <div className="seller-assist-body">
          <h3>补齐预览：{assist.fillable} 个产品可以补齐</h3>
          <p>来源：本店卖家 API。仅补空白 SKU、图片、标题、TSIN、尺寸和重量。尺寸或重量补齐后会重新计算费用与利润，人工成本覆盖值保留。</p>
          <label style={{ display: 'block', marginBottom: 12 }}><input type="checkbox" checked={assistOnlyTasks} onChange={e => setAssistOnlyTasks(e.target.checked)} /> 只显示缺字段的产品</label>
          <div className="seller-assist-table-scroll" role="region" aria-label="资料核对明细" tabIndex={0}><table style={{ width: '100%' }}><thead><tr><th>产品</th><th>将补齐</th><th>需要核对 / 人工填写</th></tr></thead><tbody>{assist.items.filter(item => !assistOnlyTasks || item.changes.length || item.manual.length || products.some(p => p.id === item.id && (!p.sku || !p.product_name || (!p.product_image_url && !p.product_image_path) || p.length_mm == null || p.width_mm == null || p.height_mm == null || p.actual_weight_kg == null))).map(item => <tr key={item.id}>
            <td>#{item.product_no} {item.name}<br/><Link to={`/products/${item.id}`} onClick={() => setAssist(null)}>修改资料</Link></td>
            <td>{item.changes.length ? item.changes.map(c => <div key={c.field}>{c.label}：{c.field === 'product_image_url' ? <a href={String(c.value)} target="_blank" rel="noreferrer">查看图片</a> : String(c.value)}</div>) : '无需补齐'}</td>
            <td>{[...item.warnings, ...(item.manual.length ? ['需填写：' + item.manual.join('、')] : [])].map((message, i) => <div key={i}>{message}</div>)}{item.differences.length > 0 && <details><summary>查看已有资料差异 ({item.differences.length})</summary>{item.differences.map((message, i) => <div key={i}>{message}</div>)}</details>}</td>
          </tr>)}</tbody></table></div>
          </div>
          <div className="seller-assist-footer">
            <button className="btn btn-primary" disabled={assistBusy || !assist.fillable} onClick={applyAssist}>确认仅补空白资料 ({assist.fillable})</button>
            <button className="btn btn-outline" disabled={assistBusy} onClick={previewAssist}>重新核对</button>
            <button className="btn btn-outline" onClick={() => setAssist(null)}>关闭</button>
          </div>
        </div>
      </div>}

      {mpPanel && <div className="seller-assist-backdrop" role="dialog" aria-modal="true" aria-label="多市场参考价" onClick={e => { if (e.target === e.currentTarget) setMpPanel(null); }}>
        <div className="seller-assist-dialog">
          <div className="seller-assist-header"><strong>多市场参考价</strong><button className="btn btn-outline btn-sm" onClick={() => setMpPanel(null)}>关闭 ×</button></div>
          <div className="seller-assist-body">
            <p style={{ marginTop: 0 }}>
              新加坡官网价由服务端每天自动重读一次（改价、下架都会跟着变）。国内原价和到手价只能人工录入——
              中国官网（charleskeith.cn）已停摆，淘宝/天猫要登录态，京东搜索也会 302，都不做自动抓取；
              数值可以照天猫、京东的商品页抄，<strong>来源和日期会一并显示</strong>，不会被当成实时价。
            </p>
            {mpStatus && (
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10, fontSize: 12.5, color: 'var(--color-text-secondary)' }}>
                <span>已录价产品 {mpStatus.products_with_price}/{mpStatus.products}</span>
                {MARKET_ORDER.map((code) => (
                  <span key={code}>{MARKET_META[code].label} {mpStatus.coverage[code]?.filled ?? 0}{mpStatus.coverage[code]?.stale ? `（${mpStatus.coverage[code].stale} 条过期）` : ''}</span>
                ))}
                <span>上次同步 {formatSyncTime(mpStatus.last_sync?.at)}（北京时间）</span>
                <span>汇率 {fxCaption(fx)}</span>
                <button className="btn btn-outline btn-sm" disabled={mpBusy} onClick={runMarketSync}>立即重读官网价</button>
              </div>
            )}
            {mpMessage && <div role="status" className="product-task-banner">{mpMessage}</div>}
            <div className="seller-assist-table-scroll" role="region" aria-label="参考价明细" tabIndex={0}>
              <table style={{ width: '100%' }}>
                <thead><tr><th>产品</th><th>国内原价 ¥</th><th>国内到手价 ¥</th><th>新加坡官网 S$（自动）</th></tr></thead>
                <tbody>
                  {products.map((p) => {
                    const prices = parseMarketPrices(p.market_prices);
                    const draft = mpPanel.draft[p.id] || { CN: '', TB: '' };
                    return (
                      <tr key={p.id}>
                        <td>#{p.product_no ?? ''} {p.product_name || p.sku || '(无标题)'}<br /><span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>{p.sku}</span></td>
                        {(['CN', 'TB'] as const).map((market) => {
                          // 边输边给兰特，省得每次都要自己按汇率算一遍。
                          const typed = Number((draft[market] || '').trim());
                          const hint = Number.isFinite(typed) && typed > 0 ? toZar(typed, 'CNY', fx) : null;
                          return (
                            <td key={market}>
                              <input
                                aria-label={`${MARKET_META[market].label}价格：${p.sku || p.product_name}`}
                                inputMode="decimal"
                                value={draft[market]}
                                placeholder="未录入"
                                onChange={(e) => {
                                  const value = e.target.value;
                                  setMpPanel((prev) => prev ? { draft: { ...prev.draft, [p.id]: { ...(prev.draft[p.id] || { CN: '', TB: '' }), [market]: value } } } : prev);
                                }}
                                style={{ width: 96 }}
                              />
                              {hint !== null && (
                                <div style={{ fontSize: 11.5, color: 'var(--color-text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                                  ≈ {formatZar(hint)}
                                </div>
                              )}
                            </td>
                          );
                        })}
                        <td style={{ fontVariantNumeric: 'tabular-nums', color: prices.SG ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>
                          {prices.SG ? formatMarketPrice('SG', prices.SG) : '未采集'}
                          {prices.SG?.at ? <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}> · {prices.SG.at}</span> : null}
                          {/* 这一列只读，但用户是照着它决定国内价填多少的：旧价不标出来，
                              就会拿一个两周前的 S$ 去算加价率。 */}
                          {staleNote(prices.SG) ? <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>{staleNote(prices.SG)}</span> : null}
                          {prices.SG && formatZarPrice('SG', prices.SG, fx) ? (
                            <div style={{ color: 'var(--color-text-secondary)', fontSize: 11.5 }}>≈ {formatZarPrice('SG', prices.SG, fx)}</div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <div className="seller-assist-footer">
            <button className="btn btn-primary" disabled={mpBusy} onClick={saveMarketPanel}>{mpBusy ? '保存中…' : '保存参考价'}</button>
            <button className="btn btn-outline" onClick={() => setMpPanel(null)}>关闭</button>
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
            {imageBatchProgress || `补充缺图 (${imageRefetchPending(products).length})`}
          </button>
          {imageBatchProgress && <button className="btn btn-outline btn-sm" onClick={() => { batchStop.current = true; setImageBatchProgress('正在结束当前产品，随后停止…'); }}>停止后续处理</button>}
        </div>
      )}

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
          className="btn btn-outline btn-sm"
          disabled={labelCount === 0 || labelLoading}
          title={
            selectedSkus.size === 0
              ? '请先勾选产品（需要 13 位条码才能生成 EAN-13 标签）'
              : labelCount === 0
                ? `勾选的 ${selectedSkus.size} 个 SKU 都不是合法 EAN-13 条码，生成不了标签`
                : `为选中的 ${labelCount} 个 SKU 各生成一个 40×30mm 的标签 PDF（多选会打包成 zip），标签打印机直接打`
          }
          onClick={downloadLabels}
        >
          {labelLoading ? '生成中…' : `下载标签${labelCount > 0 ? ` (${labelCount})` : ''}`}
        </button>

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
      {labelNote && <div className="alert alert-info" role="status">{labelNote}</div>}

      {loading ? (
        <div className="loading">加载中...</div>
      ) : products.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 48 }}>
          <p style={{ color: 'var(--color-text-secondary)' }}>暂无产品记录</p>
          <Link to="/create" className="btn btn-primary" style={{ marginTop: 12 }}>新建产品</Link>
        </div>
      ) : (
        <div className="table-wrapper product-list-table-wrapper">
          <div className="product-table-scroll" ref={tableScrollRef}>
            <table className="product-table" style={{ whiteSpace: 'nowrap' }}>
            <thead>
              <tr>
                {renderColumns.map((colKey, idx) => renderTh(colKey, idx))}
              </tr>
            </thead>
            <tbody>
              {pagedProducts.map((p) => {
                // 行底色和下拉框底色必须来自同一张表。原来这里另写了一套 #e3f2fd/#e8f5e9/#fff3e0，
                // 与 types.ts 里按对比度调过的 LINK_STATUS_MAP 不一致：同一个状态两种深浅。
                const linkBgColor = p.link_status && p.link_status !== '未购买' ? LINK_STATUS_MAP[p.link_status]?.color : undefined;
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
                                  {p.product_image_url ? <ProductImage src={api.getImageUrl(p.product_image_url)} referrerPolicy="no-referrer" /> : <i>待补图</i>}
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
              <span className="pagination-jump" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }}>
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
                  className="cell-control"
                  style={{ width: 42, textAlign: 'center' }}
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
        <div ref={variantDialogRef} role="dialog" aria-modal="true" aria-labelledby="variant-editor-title" aria-busy={savingVariant} tabIndex={-1} className="packing-modal variant-editor-modal" onClick={e => e.stopPropagation()}>
          <h3 id="variant-editor-title">修改 SKU 分支</h3>
          <div className="form-group"><label htmlFor="variant-sku">分支 SKU</label><input id="variant-sku" name="variant_sku" autoComplete="off" spellCheck={false} disabled={savingVariant} value={variantEditor.sku} onChange={e => setVariantEditor({ ...variantEditor, sku: e.target.value })} /></div>
          <div className="form-group"><label htmlFor="variant-label">真实规格名称</label><input id="variant-label" name="variant_label" autoComplete="off" disabled={savingVariant} aria-describedby="variant-label-hint" value={variantEditor.label} onChange={e => setVariantEditor({ ...variantEditor, label: e.target.value })} placeholder="例如：军绿色、XL、128GB…" /><small id="variant-label-hint">填写用户能直接辨认的颜色、尺码、容量或型号。</small></div>
          <label className="variant-primary-toggle"><input type="checkbox" disabled={savingVariant} checked={variantEditor.makePrimary} onChange={e => setVariantEditor({ ...variantEditor, makePrimary: e.target.checked })} />设为主款（保存后移动到第一位）</label>
          <div className="modal-actions"><button className="btn btn-outline" disabled={savingVariant} onClick={() => setVariantEditor(null)}>取消</button><button className="btn btn-primary" disabled={savingVariant} onClick={saveVariant}>{savingVariant ? '保存中…' : '保存分支'}</button></div>
        </div>
      </div>}
      {marketTip && (() => {
        const prices = parseMarketPrices(marketTip.product.market_prices);
        // 与列表单元格同一口径：只列采到的市场，采不到的不占行。
        const codes = visibleMarkets(prices);
        const insights = marketInsights(prices);
        // 四列（市场 / 原币 / 兰特 / 来源·日期）比原来宽，窄屏上不能硬撑，
        // 否则会顶出视口；窗口装不下时按窗口宽度收，靠省略号兜住最长的来源列。
        const tipWidth = Math.max(280, Math.min(440, window.innerWidth - 16));
        return (
          <div
            role="tooltip"
            style={{
              position: 'fixed',
              left: Math.max(8, Math.min(marketTip.x, window.innerWidth - tipWidth - 8)),
              top: marketTip.y,
              zIndex: 60,
              width: tipWidth,
              padding: '10px 12px',
              background: 'var(--color-card)',
              border: '0.5px solid var(--color-border)',
              borderRadius: 8,
              boxShadow: 'var(--shadow-lg)',
              fontSize: 12.5,
              color: 'var(--color-text)',
            }}
          >
            <div style={{ fontWeight: 500, marginBottom: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {marketTip.product.product_name || '(无标题)'}
            </div>
            {codes.length === 0 ? (
              <div style={{ color: 'var(--color-text-secondary)' }}>
                还没有采集到价格。新加坡官网价由服务端每天自动重读；国内原价 / 到手价在「参考价」里人工录入。
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                <thead>
                  <tr style={{ color: 'var(--color-text-secondary)', fontSize: 11.5 }}>
                    <th style={{ textAlign: 'left', padding: '2px 8px 2px 0', fontWeight: 400, width: 74 }}>市场</th>
                    <th style={{ textAlign: 'left', padding: '2px 8px 2px 0', fontWeight: 400, width: 68 }}>原币</th>
                    <th style={{ textAlign: 'left', padding: '2px 8px 2px 0', fontWeight: 400, width: 62 }}>兰特</th>
                    <th style={{ textAlign: 'left', padding: '2px 0', fontWeight: 400 }}>来源 · 日期</th>
                  </tr>
                </thead>
                <tbody>
                  {codes.map((code) => {
                    const entry = prices[code]!;
                    const zar = formatZarPrice(code, entry, fx);
                    return (
                      <tr key={code}>
                        <td style={{ padding: '4px 8px 4px 0', borderTop: '0.5px solid var(--color-border)', whiteSpace: 'nowrap' }}>
                          {MARKET_META[code].label}
                        </td>
                        <td style={{ padding: '4px 8px 4px 0', borderTop: '0.5px solid var(--color-border)', fontVariantNumeric: 'tabular-nums', color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>
                          {formatMarketPrice(code, entry)}
                        </td>
                        <td style={{ padding: '4px 8px 4px 0', borderTop: '0.5px solid var(--color-border)', fontVariantNumeric: 'tabular-nums', fontWeight: 500, whiteSpace: 'nowrap' }}>
                          {zar || <span style={{ color: 'var(--color-text-secondary)', fontWeight: 400 }}>无汇率</span>}
                        </td>
                        <td style={{ padding: '4px 0', borderTop: '0.5px solid var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 11.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {entry.src || '手工'}
                          {entry.n ? ` · ${entry.n} 条` : ''}
                          {entry.at ? ` · ${entry.at}` : ''}
                          {staleNote(entry)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {insights.length > 0 && (
              <div style={{ marginTop: 8, paddingTop: 7, borderTop: '0.5px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>
                {insights.map((it) => (
                  <span key={it.label} style={{ marginRight: 12 }}>
                    {it.label} <span style={{ color: 'var(--color-text)', fontWeight: 500 }}>{it.value}</span>
                  </span>
                ))}
              </div>
            )}
            {/* 折算依据要写出来：兰特是算出来的，不写汇率就没法核对。 */}
            <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--color-text-secondary)' }}>
              {fxCaption(fx)}
            </div>
          </div>
        );
      })()}
      {contextProduct && <div className="product-context-menu" style={{ left: contextProduct.x, top: contextProduct.y }} onClick={e => e.stopPropagation()}>
        <button onClick={() => navigate(`/products/${contextProduct.id}?edit=1&from=list`)}>修改资料</button>
        <button disabled={refreshingIds.has(contextProduct.id)} onClick={() => { const id = contextProduct.id; setContextProduct(null); handleRefreshPrice(id, 'official'); }}>读取官网售价</button>
        <button onClick={() => { const product = products.find(p => p.id === contextProduct.id); setContextProduct(null); if (product) handleDelete(product.id, product.product_name || ''); }}>移入废品库</button>
      </div>}
    </div>
  );
}

