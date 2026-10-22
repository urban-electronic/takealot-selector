import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import {
  getPackingProducts,
  getInventory,
  syncPackingProductsFromCatalog,
  upsertPackingProduct,
  deletePackingProduct,
  uploadPackingImage,
  importFromProducts,
  syncPackingImagesFromProducts,
  syncPackingVariantsFromTakealot,
  exportPacking,
  fetchPackingImageBlob,
  getShipments,
  exportInboundTemplate,
  getPackingCartons,
  getStoreStorageKey,
  readStoreStorage,
} from '../remoteApi';
import type { PackingProduct, Product, PackingCarton, PackingCartonRecord, PackingExportPayload, ShipmentRecord, InventoryRow } from '../types';
import { safeSetItem } from '../utils/storage';
import { todayLocal } from '../utils/datetime';
import { CATALOG_CHANGED } from '../utils/catalogEvents';

// 装箱单图片：img 标签无法带 X-API-Key header，改用 fetch blob + objectURL（带全局缓存）
const blobUrlCache = new Map<string, string>();

function PackingImage({ filename, fallbackUrl, alt }: { filename: string; fallbackUrl?: string; alt?: string }) {
  const api = useApi();
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ok' | 'empty' | 'failed'>('loading');

  useEffect(() => {
    if (!filename && fallbackUrl) {
      setSrc(api.getImageUrl(fallbackUrl));
      setState('ok');
      return;
    }
    if (!filename) {
      setSrc(null);
      setState('empty');
      return;
    }
    const cached = blobUrlCache.get(filename);
    if (cached) {
      setSrc(cached);
      setState('ok');
      return;
    }
    let cancelled = false;
    setSrc(null);
    setState('loading');
    fetchPackingImageBlob(filename)
      .then(blob => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        blobUrlCache.set(filename, url);
        setSrc(url);
        setState('ok');
      })
      .catch(() => {
        if (cancelled) return;
        if (fallbackUrl) {
          setSrc(api.getImageUrl(fallbackUrl));
          setState('ok');
        } else {
          setState('failed');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, fallbackUrl, filename]);

  if (state === 'loading') {
    return <span className="packing-noimg">加载中</span>;
  }
  if (state === 'empty') {
    return <span className="packing-noimg">待补图</span>;
  }
  if (state === 'failed' || !src) {
    // 46px 的缩略图里放不下「图加载失败」，换成短文案，完整原因放 title。
    return <span className="packing-noimg" title="图片加载失败">无图</span>;
  }
  return <img className="packing-img" src={src} alt={alt || filename} loading="lazy" onError={() => setState('failed')} />;
}

// 装箱日期/文件名里的日期一律走业务时区（Asia/Shanghai），不要用浏览器本地时区。
// 这里原来有一份自己的 `todayStr()`（getFullYear/getMonth/getDate），在非东八区
// 的机器上会与采购/库存页的默认日期差一天，同一个装箱单两个日期。
const todayStr = () => todayLocal();

const hasChinese = (text: string) => /[\u3400-\u9fff]/.test(text);

/** 纯尺码/容量（M、2XL、7.0 cm、128GB）本来就不该有中文，不该被标成「待改」。 */
const SIZE_TOKEN = /^(?:x{0,3}[slm]|[2-6]xl|\d+(?:\.\d+)?\s*(?:cm|mm|m|kg|g|ml|l|gb|tb|inch|pcs|pack|个|只|件|片)|\d+(?:\.\d+)?\s*[x×*]\s*\d+(?:\.\d+)?\s*(?:['\"”″]|cm|mm|inch)?)$/i;

/**
 * 变体中文名。线上存量里混着「7.0·cm」「Color·daisy」这类历史写法：
 * 中间点是早期把「值 + 单位」拼在一起留下的，读起来像乱码。
 * 这里只做显示归一化（中间点变空格、压缩空白），不改库里的值。
 */
function variantLabel(product: PackingProduct): string {
  const raw = (product.variant_label || product.name_zh || '').trim();
  return raw.replace(/[·・]/g, ' ').replace(/\s+/g, ' ').trim();
}

/** 该不该提示「中文名待改」：没有中文、又确实是文字（不是尺码），才提示。 */
function needsChineseReview(label: string): boolean {
  if (!label || hasChinese(label)) return false;
  if (SIZE_TOKEN.test(label.replace(/\s+/g, ''))) return false;
  return /[a-z]/i.test(label);
}

const PACKING_DRAFT_KEY = 'packingDraftV1';
const PRODUCT_SELECTED_SKUS_KEY = 'productListSelectedSkus';

// OMS（鲲鹏 JF WMS）的箱唛一张只放得下 5 行 SKU：表头 + 5 行刚好贴到 100mm 页底，
// 第 6 行起会另起一页，而那一页没有箱号也没有条码。装的时候就提示，别等印完才发现。
const MAX_SKUS_PER_BOX = 5;

type PackingLines = Record<string, { cartons: string; count: string }>;
type PackingExportForm = { date: string; mark: string; shipping: string; address: string; record_shipment?: boolean };
type PackingDraft = {
  selected: string[];
  lines: PackingLines;
  exportForm: Partial<PackingExportForm>;
  draftKey: string;
  cartons: PackingCarton[];
  // 逐箱清单是「自动生成」还是「手工调整过」。
  // false（默认）= 上方箱数 × 每箱件数就是唯一录入处，箱子由它实时生成；
  // true = 有人动过逐箱清单（混装、拆箱），从此以逐箱清单为准，上方只显示汇总。
  cartonsManual: boolean;
  // OMS（鲲鹏/JF WMS）的入库单号。箱唛号 = 入库单号 + '-' + 3 位箱序，
  // 与 OMS「入库单管理」里生成的箱唛号一致，两张纸才能贴在同一个箱子上。
  inboundNo: string;
};
const newDraftKey = () => globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function readPackingDraft(): PackingDraft {
  try {
    const parsed = JSON.parse(readStoreStorage(PACKING_DRAFT_KEY) || '{}');
    const savedCartons: PackingCarton[] = Array.isArray(parsed.cartons) ? parsed.cartons : [];
    return {
      selected: Array.isArray(parsed.selected) ? parsed.selected.filter((v: unknown): v is string => typeof v === 'string') : [],
      lines: parsed.lines && typeof parsed.lines === 'object' ? parsed.lines as PackingLines : {},
      exportForm: parsed.exportForm && typeof parsed.exportForm === 'object' ? parsed.exportForm as Partial<PackingExportForm> : {},
      draftKey: typeof parsed.draftKey === 'string' && parsed.draftKey ? parsed.draftKey : newDraftKey(),
      cartons: savedCartons,
      // 老草稿没有这个字段：手里已经有逐箱记录，说明是手工录的，按手工模式接着用。
      cartonsManual: typeof parsed.cartonsManual === 'boolean' ? parsed.cartonsManual : savedCartons.length > 0,
      inboundNo: typeof parsed.inboundNo === 'string' ? parsed.inboundNo : '',
    };
  } catch {
    return { selected: [], lines: {}, exportForm: {}, draftKey: newDraftKey(), cartons: [], cartonsManual: false, inboundNo: '' };
  }
}

export default function Packing() {
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const savedDraft = useMemo(readPackingDraft, []);

  // 装箱单库
  const [products, setProducts] = useState<PackingProduct[]>([]);
  const [inventoryBySku, setInventoryBySku] = useState<Record<string, InventoryRow>>({});
  const [inventoryState, setInventoryState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  // 提示条上的直达按钮。带 `for` 是为了自动过期：别处 setMsg 换了文案，按钮就自己消失，
  // 不然会留下一个指向旧动作的按钮。
  const [msgAction, setMsgAction] = useState<{ for: string; label: string; onClick: () => void } | null>(null);
  const [catalogStats, setCatalogStats] = useState<{ total: number; noSku: number; duplicates: number } | null>(null);

  // 产品库筛选
  const [searchText, setSearchText] = useState('');
  const [fltElectric, setFltElectric] = useState('');
  const [fltMagnetic, setFltMagnetic] = useState('');
  const [fltMaterial, setFltMaterial] = useState('');

  // 本次装箱单行
  const [selected, setSelected] = useState<Set<string>>(() => new Set(savedDraft.selected));
  const [lines, setLines] = useState<PackingLines>(() => savedDraft.lines);
  // 逐箱清单：一箱一条。箱子封上以后外观一样，「第几箱装了什么」只能靠这份记录。
  const [cartons, setCartons] = useState<PackingCarton[]>(() => savedDraft.cartons);
  // 手工模式：false 时箱子由「箱数 × 每箱件数」实时生成，逐箱清单只读；
  // 一旦动过逐箱清单就转 true，此后以逐箱清单为准，上方只显示汇总。
  const [cartonsManual, setCartonsManual] = useState(savedDraft.cartonsManual);
  // OMS 入库单号（如 INH1501522）。填了它，箱号就是 OMS 的箱唛号 INH1501522-001，
  // 与 OMS「入库单管理」打印的箱唛逐箱对应；不填就还是 C01、C02。
  const [inboundNo, setInboundNo] = useState(savedDraft.inboundNo);

  // 事后查箱：箱子封上以后就只剩「某天一共几箱」这一个印象，
  // 「昨天第 5 箱装的是什么」只能回查这张表。
  const [cartonQueryOpen, setCartonQueryOpen] = useState(false);
  const [cartonQueryDate, setCartonQueryDate] = useState('');
  const [cartonQuerySku, setCartonQuerySku] = useState('');
  const [cartonQueryRows, setCartonQueryRows] = useState<PackingCartonRecord[]>([]);
  const [cartonQueryTotal, setCartonQueryTotal] = useState(0);
  const [cartonQueryLoading, setCartonQueryLoading] = useState(false);
  const [cartonQueryError, setCartonQueryError] = useState('');

  // 导出设置
  const [exportForm, setExportForm] = useState<PackingExportForm>({ date: todayStr(), mark: '', shipping: '', address: '', ...savedDraft.exportForm, record_shipment: false });
  const [draftKey, setDraftKey] = useState(savedDraft.draftKey);
  const [exporting, setExporting] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [templateShipments, setTemplateShipments] = useState<ShipmentRecord[]>([]);
  const [selectedShipmentIds, setSelectedShipmentIds] = useState<string[]>([]);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [templateExporting, setTemplateExporting] = useState(false);
  const [templateError, setTemplateError] = useState('');

  // URL import=sku1,sku2
  const urlImportSkus = useMemo(() => {
    const raw = searchParams.get('import') || '';
    return raw.split(',').map(s => s.trim()).filter(Boolean);
  }, [searchParams]);
  const [importMissing, setImportMissing] = useState<string[]>([]);
  const [oneClickImporting, setOneClickImporting] = useState(false);

  // 编辑弹层
  const [editSku, setEditSku] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<PackingProduct>>({});

  // 删除确认
  const [deleteSku, setDeleteSku] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ sku: string; x: number; y: number } | null>(null);

  // 三个弹层都写了 aria-modal="true"，那就得能被 Esc 关掉、关掉之前背后不滚动 ——
  // 产品列表、补货页的弹层都是这么做的，这里一个都没做：键盘用户关不掉，
  // 窄屏上手指划的其实是背后的装箱表。按「最上面那层」关：确认删除 > 编辑产品 > 查箱子。
  const openDialog = deleteSku ? 'delete' : editSku ? 'edit' : cartonQueryOpen ? 'query' : '';
  useEffect(() => {
    if (!openDialog) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (openDialog === 'delete') setDeleteSku(null);
      else if (openDialog === 'edit') setEditSku(null);
      else setCartonQueryOpen(false);
    };
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', close);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener('keydown', close);
    };
  }, [openDialog]);

  // 从选品库导入弹层
  const [importOpen, setImportOpen] = useState(false);
  const [importSearch, setImportSearch] = useState('');
  const [importCategory, setImportCategory] = useState('');
  const [importResults, setImportResults] = useState<Product[]>([]);
  const [importSearching, setImportSearching] = useState(false);
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  // 按 SKU 从选品库匹配图片
  const [syncingImages, setSyncingImages] = useState(false);
  const [syncingVariants, setSyncingVariants] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // 文件输入
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const loadProducts = useCallback(async () => {
    setInventoryState('loading');
    void getInventory().then(rows => {
      setInventoryBySku(Object.fromEntries(rows.filter(row => row.sku).map(row => [row.sku, row])));
      setInventoryState('ready');
    }).catch(() => {
      setInventoryBySku({});
      setInventoryState('failed');
    });
    try {
      const sync = await syncPackingProductsFromCatalog();
      setCatalogStats({ total: sync.total_products, noSku: sync.skipped_no_sku, duplicates: sync.duplicate_sku });
      const data = await getPackingProducts();
      setProducts(data);
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '加载装箱单库失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  // 产品列表里改了中文名/SKU 之后，装箱单不能等整页刷新才变。
  // catalog:changed 是本标签页内的即时通知；窗口重新获得焦点时兜住其它标签页的改动
  // （sync 会遍历整个产品库，加 60 秒节流，避免来回切窗口就同步一次）。
  useEffect(() => {
    const reload = () => { void loadProducts(); };
    let lastSync = Date.now();
    const onFocus = () => {
      if (Date.now() - lastSync < 60000) return;
      lastSync = Date.now();
      reload();
    };
    window.addEventListener(CATALOG_CHANGED, reload);
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener(CATALOG_CHANGED, reload);
      window.removeEventListener('focus', onFocus);
    };
  }, [loadProducts]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener('click', close);
    window.addEventListener('scroll', close, true);
    return () => { window.removeEventListener('click', close); window.removeEventListener('scroll', close, true); };
  }, [contextMenu]);

  useEffect(() => {
    // 每次勾选/改数量都会写；配额满时若抛出会让装箱单整页崩掉
    safeSetItem(getStoreStorageKey(PACKING_DRAFT_KEY), JSON.stringify({
      selected: Array.from(selected),
      lines,
      exportForm,
      draftKey,
      cartons,
      cartonsManual,
      inboundNo,
    }));
  }, [selected, lines, exportForm, draftKey, cartons, cartonsManual, inboundNo]);

  // URL import 自动勾选 + 缺失 SKU 提示一键导入
  useEffect(() => {
    if (loading || urlImportSkus.length === 0) return;
    setSelected(prev => {
      const next = new Set(prev);
      let changed = false;
      urlImportSkus.forEach(s => {
        if (!next.has(s)) {
          next.add(s);
          changed = true;
        }
      });
      return changed ? next : prev;
    });
    const missing = urlImportSkus.filter(s => !products.some(p => p.sku === s));
    setImportMissing(missing);
    if (missing.length === 0 && searchParams.get('import')) {
      const sp = new URLSearchParams(searchParams);
      sp.delete('import');
      setSearchParams(sp, { replace: true });
    }
  }, [loading, urlImportSkus, products, searchParams, setSearchParams]);

  // 打开弹层即加载完整选品库，搜索和类目在前端筛选，避免遗漏未被搜索到的类目。
  useEffect(() => {
    if (!importOpen) return;
    let cancelled = false;
    (async () => {
      setImportSearching(true);
      try {
        const results = await api.getProducts();
        if (!cancelled) setImportResults(results || []);
      } catch {
        if (!cancelled) setImportResults([]);
      } finally {
        if (!cancelled) setImportSearching(false);
      }
    })();
    return () => { cancelled = true; };
  }, [api, importOpen]);

  const importCategories = useMemo(() => Array.from(new Set(
    importResults.map(p => p.fee_category).filter((v): v is string => Boolean(v)),
  )).sort((a, b) => a.localeCompare(b)), [importResults]);

  const filteredImportResults = useMemo(() => {
    const kw = importSearch.trim().toLowerCase();
    return importResults.filter(p => {
      if (importCategory && p.fee_category !== importCategory) return false;
      if (!kw) return true;
      return `${p.sku || ''} ${p.product_name || ''} ${p.chinese_product_name || ''} ${p.tsin || ''} ${p.fee_category || ''}`
        .toLowerCase().includes(kw);
    });
  }, [importResults, importSearch, importCategory]);

  const existingPackingSkus = useMemo(() => new Set(products.map(p => p.sku)), [products]);
  const selectableImportResults = useMemo(
    () => filteredImportResults.filter(p => p.sku && !existingPackingSkus.has(p.sku)),
    [filteredImportResults, existingPackingSkus],
  );

  const keywordMatches = useMemo(() => {
    const kw = searchText.trim().toLowerCase();
    return new Set(products.filter(p => !kw || `${p.sku} ${p.name_zh} ${p.name_en} ${p.material} ${p.brand}`.toLowerCase().includes(kw)).map(p => p.sku));
  }, [products, searchText]);

  const filteredProducts = useMemo(() => {
    // Keep group context so expanding a search result can reveal its siblings.
    const matchingGroups = new Set(products.filter(p => keywordMatches.has(p.sku) && p.variant_group).map(p => p.variant_group));
    return products.filter(p => {
      if (!keywordMatches.has(p.sku) && !matchingGroups.has(p.variant_group)) return false;
      if (fltElectric && p.electric !== fltElectric) return false;
      if (fltMagnetic && p.magnetic !== fltMagnetic) return false;
      if (fltMaterial && p.material !== fltMaterial) return false;
      return true;
    });
  }, [products, keywordMatches, fltElectric, fltMagnetic, fltMaterial]);

  const visibleProducts = useMemo(() => filteredProducts.filter(row => !row.variant_group || row.is_primary_variant || expandedGroups.has(row.variant_group) || (searchText.trim() && keywordMatches.has(row.sku))), [filteredProducts, expandedGroups, searchText, keywordMatches]);

  const materials = useMemo(() => {
    const set = new Set<string>();
    products.forEach(p => { if (p.material) set.add(p.material); });
    return Array.from(set).sort();
  }, [products]);

  const toggleSelect = (sku: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(sku)) {
        next.delete(sku);
      } else {
        next.add(sku);
      }
      return next;
    });
  };

  const selectedRows = useMemo(
    () => selected.size === 0 ? [] : Array.from(selected).map(sku => products.find(p => p.sku === sku)).filter((p): p is PackingProduct => Boolean(p)),
    [selected, products],
  );

  const setLine = (sku: string, key: 'cartons' | 'count', value: string) => {
    setLines(prev => {
      const cur = prev[sku] || { cartons: '1', count: '' };
      return { ...prev, [sku]: { ...cur, [key]: value } };
    });
  };

  // ---- 逐箱清单 ----
  // 箱唛号：填了入库单号就照 OMS 的格式（INH1501522-001），否则退回 C01。
  // OMS 的箱唛示例图里，条码下面的那串就是「箱唛号」，红框标着的。
  const boxNoFor = (index: number) => {
    const prefix = inboundNo.trim();
    return prefix ? `${prefix}-${String(index + 1).padStart(3, '0')}` : `C${String(index + 1).padStart(2, '0')}`;
  };

  // 箱数 / 每箱件数的**唯一**口径。显示（小计、合计）、按 SKU 展开、箱内合计校验、
  // 导出过滤与导出内容必须共用这两个函数：原来各写各的（输入框 `?? '1'`、合计 `|| '0'`、
  // 导出却要求**手填过**），于是一个「刚勾上、还没动过」的行界面上写着「1 箱 · 小计 10 件」，
  // 点导出却被判成无效整行跳过，提示还让用户去填那些看起来已经填好的框。
  const lineCartons = (p: PackingProduct): number => {
    const raw = lines[p.sku]?.cartons;
    if (raw === undefined) return 1; // 没动过这一行：输入框显示的就是 1
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : 0; // 清空了 = 0，导出时点名，不静默当 1
  };
  const lineCount = (p: PackingProduct): number => {
    const raw = lines[p.sku]?.count;
    if (raw !== undefined && raw.trim() !== '') {
      const n = parseInt(raw, 10);
      return Number.isFinite(n) && n > 0 ? n : 0;
    }
    return p.default_count || 0; // 留空按产品库的默认件数（输入框 placeholder 写的就是它）
  };

  // 「一箱一个 SKU」是常态：箱子由上面填的箱数 × 每箱件数**实时算出来**，不用手填一遍。
  // 以前这里是个「按 SKU 展开」按钮，展开完还得再手录一遍逐箱清单 ——
  // 同一份数量存两处，改一边另一边就悄悄过期，混装时更是根本对不上（2 箱 20+15 件
  // 凑不出「箱数 × 每箱件数」）。现在只留一个录入处，逐箱清单是它的视图。
  const autoCartons = useMemo<PackingCarton[]>(() => {
    const next: PackingCarton[] = [];
    selectedRows.forEach(p => {
      const boxes = lineCartons(p);
      const per = lineCount(p);
      // 导不出去的行不展开：否则箱里写着这个 SKU、导出却把整行跳过，后端只能报
      // 「第 N 箱里有不在本次装箱单中的 SKU」——用户看着明明勾了，却被告知不在单里。
      if (boxes < 1 || per < 1) return;
      for (let i = 0; i < boxes; i += 1) {
        next.push({ box_no: boxNoFor(next.length), note: '', items: [{ sku: p.sku, quantity: String(per) }] });
      }
    });
    return next;
  }, [selectedRows, lines, inboundNo]);

  // 真正拿去导出、算合计的那一份：手工模式用逐箱清单，否则用自动生成的。
  const planCartons = cartonsManual ? cartons : autoCartons;

  // 第一次动逐箱清单时，先把自动生成的那份落成真数据再改，否则一动手就全没了。
  const editCartons = (fn: (prev: PackingCarton[]) => PackingCarton[]) => {
    if (!cartonsManual) {
      setCartonsManual(true);
      setCartons(fn(autoCartons));
      return;
    }
    setCartons(prev => fn(prev));
  };

  // 回到「按箱数 × 每箱件数自动生成」：混装拆过以后想重来，不用一箱箱删。
  const rebuildCartons = () => {
    setCartonsManual(false);
    setCartons([]);
  };

  const addCarton = () => {
    editCartons(prev => [...prev, { box_no: boxNoFor(prev.length), note: '', items: [{ sku: '', quantity: '' }] }]);
  };

  // 改了入库单号，手工模式的箱号要跟着改；自动模式的箱号本来就是算出来的，不用动。
  const applyInboundNo = (value: string) => {
    setInboundNo(value);
    if (!cartonsManual) return;
    const prefix = value.trim();
    setCartons(prev => prev.map((c, i) => ({
      ...c,
      box_no: prefix ? `${prefix}-${String(i + 1).padStart(3, '0')}` : `C${String(i + 1).padStart(2, '0')}`,
    })));
  };

  const patchCarton = (index: number, patch: Partial<PackingCarton>) => {
    editCartons(prev => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };

  const removeCarton = (index: number) => {
    editCartons(prev => prev.filter((_, i) => i !== index));
  };

  const patchCartonItem = (index: number, itemIndex: number, patch: Partial<PackingCarton['items'][number]>) => {
    editCartons(prev => prev.map((c, i) => (i === index
      ? { ...c, items: c.items.map((it, j) => (j === itemIndex ? { ...it, ...patch } : it)) }
      : c)));
  };

  const addCartonItem = (index: number) => {
    editCartons(prev => prev.map((c, i) => (i === index ? { ...c, items: [...c.items, { sku: '', quantity: '' }] } : c)));
  };

  const removeCartonItem = (index: number, itemIndex: number) => {
    editCartons(prev => prev.map((c, i) => (i === index
      ? { ...c, items: c.items.filter((_, j) => j !== itemIndex) }
      : c)));
  };

  const cartonPieces = (c: PackingCarton) =>
    c.items.reduce((s, it) => s + (parseInt(it.quantity || '0', 10) || 0), 0);
  const cartonTotalPieces = planCartons.reduce((sum, c) => sum + cartonPieces(c), 0);

  // 某个 SKU 实际装了几箱、共几件 —— 手工模式下「已选产品」那行显示的就是这个。
  const skuBoxSummary = (sku: string) => {
    let boxes = 0;
    let pieces = 0;
    planCartons.forEach(c => {
      const row = c.items.find(it => (it.sku || '').trim() === sku);
      if (!row) return;
      boxes += 1;
      pieces += parseInt(row.quantity || '0', 10) || 0;
    });
    return { boxes, pieces };
  };

  // 逐箱清单和已选产品必须一一对上：勾了却没装进任何一箱（漏装）、
  // 或箱里塞了本单外的 SKU。箱子封上以后才发现，就只能补发了。
  const cartonCoverage = useMemo(() => {
    const packed: Record<string, number> = {};
    planCartons.forEach(c => c.items.forEach(it => {
      const sku = (it.sku || '').trim();
      if (!sku) return;
      packed[sku] = (packed[sku] || 0) + (parseInt(it.quantity || '0', 10) || 0);
    }));
    const wanted = new Set(selectedRows.map(p => p.sku));
    return {
      unpacked: selectedRows.filter(p => !packed[p.sku]),
      extra: Object.keys(packed).filter(sku => !wanted.has(sku)),
    };
  }, [planCartons, selectedRows]);

  // ---- 事后查箱 ----
  const cartonKey = (r: PackingCartonRecord) => `${r.shipment_id}#${r.seq}`;

  const runCartonQuery = async (date: string, sku: string) => {
    setCartonQueryLoading(true);
    setCartonQueryError('');
    try {
      const res = await getPackingCartons({ date, sku: sku.trim(), limit: 500 });
      setCartonQueryRows(res.cartons || []);
      setCartonQueryTotal(res.total_cartons || 0);
    } catch (e) {
      setCartonQueryError((e as Error)?.message || '查询失败');
      setCartonQueryRows([]);
      setCartonQueryTotal(0);
    } finally {
      setCartonQueryLoading(false);
    }
  };

  const openCartonQuery = () => {
    const d = exportForm.date || todayStr();
    setCartonQueryOpen(true);
    setCartonQueryDate(d);
    setCartonQuerySku('');
    void runCartonQuery(d, '');
  };

  // 同一票货的箱放一起，否则按日期查出来是一长条，看不出哪几箱是一趟的。
  const cartonQueryGroups = useMemo(() => {
    const map = new Map<string, { shipment_no: string; date: string; mark: string; rows: PackingCartonRecord[] }>();
    cartonQueryRows.forEach(r => {
      let g = map.get(r.shipment_id);
      if (!g) {
        g = { shipment_no: r.shipment_no, date: r.shipment_date, mark: r.mark, rows: [] };
        map.set(r.shipment_id, g);
      }
      g.rows.push(r);
    });
    return Array.from(map.values());
  }, [cartonQueryRows]);

  // 箱数/件数一律以 planCartons 为准：自动模式下它就是「箱数 × 每箱件数」算出来的，
  // 手工模式下它就是逐箱清单本身，两个口径不会各说一套。
  const totalCartons = planCartons.length;
  const totalCount = cartonTotalPieces;
  // 「将导出 N 行」与实际导出必须是同一批：导不出去的行在这里就排掉，
  // 免得界面上写 3 行、文件里只有 2 行。
  // 手工模式下箱数/每箱件数不参与计算，判据换成「有没有装进箱子」。
  const exportableRows = useMemo(
    () => (cartonsManual
      ? selectedRows.filter(p => skuBoxSummary(p.sku).pieces >= 1)
      : selectedRows.filter(p => lineCartons(p) >= 1 && lineCount(p) >= 1)),
    [selectedRows, lines, cartonsManual, planCartons],
  );

  // 一键导入（URL import 缺失的 SKU）
  const handleOneClickImport = async () => {
    if (importMissing.length === 0) return;
    setOneClickImporting(true);
    setMsg('');
    setError('');
    try {
      const all = await api.getProducts({ limit: '500' });
      const idBySku = new Map<string, string>();
      all.forEach(p => {
        if (p.sku) idBySku.set(String(p.sku), String(p.id));
      });
      const ids = importMissing
        .map(s => idBySku.get(s))
        .filter((x): x is string => !!x);
      if (ids.length === 0) {
        setMsg('选品库中未找到这些 SKU，请用“从选品库导入”手动搜索添加');
        setImportMissing([]);
        return;
      }
      const res = await importFromProducts(ids);
      await loadProducts();
      setMsg(`一键导入完成：新增 ${res.imported} 条，跳过 ${res.skipped} 条${res.failed.length ? `，失败 ${res.failed.length} 条` : ''}`);
    } catch (e: any) {
      setError(`一键导入失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setOneClickImporting(false);
    }
  };

  // 编辑保存
  const openEdit = (p: PackingProduct) => {
    setEditForm({ ...p });
    setEditSku(p.sku);
  };

  const saveEdit = async () => {
    if (!editSku) return;
    try {
      await upsertPackingProduct({
        sku: editSku,
        name_zh: editForm.name_zh || '',
        name_en: editForm.name_en || '',
        unit: editForm.unit || '个',
        weight: editForm.weight || '',
        material: editForm.material || '',
        brand: editForm.brand || '',
        battery: editForm.battery || '',
        electric: editForm.electric || '',
        magnetic: editForm.magnetic || '',
        default_count: editForm.default_count ?? null,
      });
      setEditSku(null);
      await loadProducts();
    } catch (e: any) {
      setError(`保存失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    }
  };

  const confirmDelete = async () => {
    if (!deleteSku) return;
    try {
      await deletePackingProduct(deleteSku);
      setDeleteSku(null);
      setSelected(prev => {
        const next = new Set(prev);
        next.delete(deleteSku);
        return next;
      });
      await loadProducts();
    } catch (e: any) {
      setError(`删除失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    }
  };

  // 上传图片
  const handleFile = (sku: string, file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1] || '';
      try {
        await uploadPackingImage(sku, base64);
        setMsg('图片上传成功');
        await loadProducts();
      } catch (e: any) {
        setError(`图片上传失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
      }
    };
    reader.onerror = () => setError('读取图片失败');
    reader.readAsDataURL(file);
  };

  const clearImage = async (sku: string) => {
    try {
      await uploadPackingImage(sku, '', true);
      setMsg('已移除图片');
      await loadProducts();
    } catch (e: any) {
      setError(`移除图片失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    }
  };

  // 导出
  const handleExport = async () => {
    const rows = exportableRows;
    if (rows.length === 0) {
      setError(cartonsManual
        ? '勾选的产品还没装进任何一箱，请先在逐箱清单里补上件数'
        : '请至少勾选 1 个产品，并填写有效的箱数和每箱件数（≥1）后再导出');
      return;
    }
    if (selectedRows.length !== rows.length) {
      setError(cartonsManual
        ? '部分勾选产品还没装进任何一箱，已自动跳过这些行'
        : '部分勾选产品箱数/每箱件数无效（需 ≥1 的正整数），已自动跳过这些行');
    }
    // 勾了却一箱没装 = 箱子封上才发现少装，只能补发；导出前必须拦住。
    if (cartonCoverage.unpacked.length) {
      const detail = cartonCoverage.unpacked.slice(0, 3)
        .map(p => p.name_zh || p.sku).join('；');
      setError(`这些产品还没装进任何一箱：${detail}。补上件数，或点「按产品资料重排」重来`);
      return;
    }
    setExporting(true);
    setError('');
    try {
      const payload: PackingExportPayload = {
        date: exportForm.date,
        mark: exportForm.mark,
        shipping: exportForm.shipping,
        address: exportForm.address,
        draft_key: draftKey,
        record_shipment: exportForm.record_shipment === true,
        items: rows.map(p => {
          if (!cartonsManual) {
            return { sku: p.sku, cartons: String(lineCartons(p)), count: String(lineCount(p)) };
          }
          // 手工模式：箱数按实际装了几箱，每箱件数只是展示用的均值 ——
          // 后端以逐箱清单为准算总件数，混装（同一 SKU 各箱数量不同）也不会被卡。
          const { boxes, pieces } = skuBoxSummary(p.sku);
          const boxCount = Math.max(1, boxes);
          return { sku: p.sku, cartons: String(boxCount), count: String(Math.max(1, Math.ceil(pieces / boxCount))) };
        }),
        // 逐箱清单一律传上去：自动模式下它带着箱唛号，装箱单 A 列才和 OMS 的箱唛对得上。
        cartons: planCartons.length
          ? planCartons.map(c => ({
            box_no: c.box_no,
            note: c.note,
            items: c.items.filter(it => (it.sku || '').trim()).map(it => ({ sku: it.sku.trim(), quantity: it.quantity || '1' })),
          }))
          : undefined,
      };
      const blob = await exportPacking(payload);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `装箱单_${exportForm.date || todayStr()}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setSelected(new Set());
      setLines({});
      setCartons([]);
      setCartonsManual(false);
      setDraftKey(newDraftKey());
      setImportMissing([]);
      localStorage.removeItem(getStoreStorageKey(PACKING_DRAFT_KEY));
      localStorage.removeItem(getStoreStorageKey(PRODUCT_SELECTED_SKUS_KEY));
      const sp = new URLSearchParams(searchParams);
      sp.delete('import');
      setSearchParams(sp, { replace: true });
      await loadProducts();
      setExportForm(prev => ({ ...prev, record_shipment: false }));
      // 导完草稿就清空了，最容易漏掉「去 OMS 建入库单」这一步。
      // 直接把入口给出来，并默认勾上刚导出的这一票。
      const boxNote = cartonsManual ? '' : `（按「一箱一个 SKU」自动生成 ${planCartons.length} 箱）`;
      const okMsg = exportForm.record_shipment
        ? '导出成功，已登记发货并扣库存'
        : '导出成功，装箱单已留档，库存未变化';
      const fullMsg = `${okMsg}${boxNote}`;
      setMsg(fullMsg);
      setMsgAction({
        for: fullMsg,
        label: '导出 OMS 入仓模板',
        onClick: () => { setMsgAction(null); openErpTemplate(true); },
      });
    } catch (e: any) {
      setError(`导出失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setExporting(false);
    }
  };

  const openErpTemplate = async (preselectNewest = false) => {
    setTemplateOpen(true);
    setTemplateLoading(true);
    setTemplateError('');
    setSelectedShipmentIds([]);
    try {
      const rows = await getShipments();
      const usable = rows
        .filter(row => row.status === 'confirmed' || row.status === 'clearance' || row.status === 'exported')
        .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '') || b.shipment_no.localeCompare(a.shipment_no));
      setTemplateShipments(usable);
      // 刚从装箱页导出的那一票就排在最前，默认勾上它，省得再找一遍
      if (preselectNewest && usable.length) setSelectedShipmentIds([usable[0].id]);
    } catch (e: any) {
      setTemplateError(typeof e === 'string' ? e : e.message || '装箱单记录加载失败');
    } finally {
      setTemplateLoading(false);
    }
  };

  const handleExportErpTemplate = async () => {
    const chosen = templateShipments
      .filter(row => selectedShipmentIds.includes(row.id))
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '') || a.shipment_no.localeCompare(b.shipment_no));
    if (chosen.length === 0) {
      setTemplateError('请至少选择 1 张有效装箱单');
      return;
    }
    if (chosen.some(row => row.shipping.includes('海')) && chosen.some(row => row.shipping.includes('空'))) {
      setTemplateError('海运和空运请分别勾选、分别生成模板');
      return;
    }
    setTemplateExporting(true);
    setTemplateError('');
    try {
      const { blob, summary } = await exportInboundTemplate(chosen.map(row => row.id));
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `ERP入仓模板_${todayStr()}.xlsx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setTemplateOpen(false);
      // 按箱生成：一箱一个标识号，同一箱的 SKU 共用标识号 —— OMS 导入后
      // 建出来的箱子数就是 summary.cartons，箱唛也照这个印。
      const detail = summary
        ? `${summary.cartons} 箱、${summary.pieces} 件、${summary.rows} 行 SKU`
        : `${chosen.length} 张装箱单`;
      const overflow = summary?.overflow?.length
        ? `；${summary.overflow.map(o => `箱 ${o.box_no} 装了 ${o.skus} 个 SKU`).join('、')}，OMS 箱唛一张只放得下 5 行，这几箱会多印一页且那一页没有箱号`
        : '';
      setMsg(`OMS 入仓文件已生成：${detail}。到 OMS「入库单 → 导入」上传，生成入库单后直接打箱唛${overflow}`);
    } catch (e: any) {
      setTemplateError(typeof e === 'string' ? e : e.message || 'ERP 模板生成失败');
    } finally {
      setTemplateExporting(false);
    }
  };

  // 按 SKU 从选品库匹配图片（后端抓取转存，仅处理待补图）
  const handleSyncImages = async () => {
    setSyncingImages(true);
    setMsg('');
    setError('');
    try {
      const res = await syncPackingImagesFromProducts(200);
      await loadProducts();
      const failNote = res.failed.length ? `，失败 ${res.failed.length} 条（${res.failed.slice(0, 3).map(f => `${f.sku}:${f.reason}`).join('；')}${res.failed.length > 3 ? '…' : ''}）` : '';
      if (res.updated === 0 && res.failed.length === 0) {
        setMsg('未找到可匹配的图片：选品库中没有这些 SKU 的图片记录，或装箱单库已无待补图');
      } else {
        setMsg(`SKU 图片匹配完成：成功补图 ${res.updated} 条（本批处理 ${res.total_pending} 条待补图）${failNote}`);
      }
    } catch (e: any) {
      setError(`图片匹配失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setSyncingImages(false);
    }
  };

  const handleSyncOfficialVariants = async () => {
    setSyncingVariants(true); setMsg(''); setError('');
    try {
      const res = await syncPackingVariantsFromTakealot(30);
      await loadProducts();
      setMsg(`官网规格核对完成：检查 ${res.groups_checked} 组，确认 ${res.matched} 个 SKU 规格，匹配 ${res.images} 张独立图片；${res.unresolved.length} 组需人工确认。`);
    } catch (e: any) {
      setError(`官网规格核对失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally { setSyncingVariants(false); }
  };

  // 从选品库导入执行
  const confirmImport = async () => {
    const ids = Array.from(importSelected);
    if (ids.length === 0) {
      setError('请先勾选要导入的选品产品');
      return;
    }
    setImporting(true);
    setError('');
    try {
      const res = await importFromProducts(ids);
      await loadProducts();
      setMsg(`导入完成：新增 ${res.imported} 条，跳过 ${res.skipped} 条${res.failed.length ? `，失败 ${res.failed.length} 条` : ''}`);
      setImportSelected(new Set());
      setImportOpen(false);
    } catch (e: any) {
      setError(`导入失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setImporting(false);
    }
  };

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div className="page packing-page">
      {/* 头部 */}
      <div className="packing-hero">
        <div>
          <div className="packing-eyebrow">KUNPENG · PACKING LIST</div>
          <h1>装箱单工作台</h1>
          <span className="packing-subtitle">
            选择产品、调整数量，生成带图片的装箱单。
            <small>
            装箱单库 {products.length} 条{catalogStats ? ` / 产品库 ${catalogStats.total ?? '—'} 条` : ''} · 本次已选 {selectedRows.length} 条
            {selectedRows.length > 0 && <>（共 {totalCartons} 箱 / {totalCount} 件）</>}
            </small>
          </span>
        </div>
        <div className="packing-hero-actions">
          <button className="btn btn-outline" onClick={() => { setImportOpen(true); setImportSearch(''); setImportCategory(''); }}>
            新增产品
          </button>
          <button className="btn btn-outline" onClick={() => openErpTemplate()}>
            OMS 入仓
          </button>
          <button className="btn btn-outline" onClick={openCartonQuery} title="查某天装了哪些箱，或某个 SKU 在哪几箱">
            查箱子
          </button>
          <button className="btn packing-export-btn" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {msg && (
        <div className="alert alert-success" style={{ marginBottom: 12 }}>
          {msg}
          {msgAction && msgAction.for === msg && (
            <button className="btn btn-sm btn-primary" style={{ marginLeft: 12 }} onClick={msgAction.onClick}>
              {msgAction.label}
            </button>
          )}
        </div>
      )}
      {catalogStats && (catalogStats.noSku > 0 || catalogStats.duplicates > 0) && (
        <div className="alert" style={{ marginBottom: 12, borderLeft: '4px solid var(--color-warning)', background: 'var(--surface-warning)' }}>
          已同步全部可用 SKU；另有 {catalogStats.noSku} 条产品缺少 SKU
          {catalogStats.duplicates > 0 ? `，${catalogStats.duplicates} 条 SKU 重复` : ''}，无法作为独立装箱产品。
        </div>
      )}

      {/* URL import 缺失提示 */}
      {importMissing.length > 0 && (
        <div className="alert" style={{ marginBottom: 12, borderLeft: '4px solid var(--color-warning)', background: 'var(--surface-warning)' }}>
          <strong>检测到 {importMissing.length} 个来自选品库的 SKU 尚未导入装箱单：</strong>{' '}
          {importMissing.join(', ')}
          <button
            className="btn btn-sm btn-primary"
            style={{ marginLeft: 12 }}
            onClick={handleOneClickImport}
            disabled={oneClickImporting}
          >
            {oneClickImporting ? '导入中...' : '一键导入'}
          </button>
          <button className="btn btn-outline btn-sm" onClick={handleSyncOfficialVariants} disabled={syncingVariants} title="仅在 Takealot 官网返回可验证的 SKU、规格和图片对应关系时更新">
            {syncingVariants ? '官网核对中...' : '官网核对规格与图片'}
          </button>
        </div>
      )}

      <div className="packing-workspace">
      <div className="packing-library-column">
      {/* 装箱单库 */}
      <div className="card packing-library-card">
        <div className="packing-library-head">
          <div>
            <h3 style={{ margin: 0 }}>产品资料</h3>
            <div className="packing-section-note">中文、英文与 SKU 独立展示，支持搜索与多选。</div>
          </div>
          <button
            className="btn btn-outline btn-sm"
            onClick={handleSyncImages}
            disabled={syncingImages}
            title="将装箱单库中“待补图”的产品，按 SKU 从选品库匹配并抓取图片"
          >
            {syncingImages ? '匹配中...' : '按SKU匹配选品库图片'}
          </button>
        </div>
        <div className="packing-filterbar">
          <input
            type="text"
            placeholder="搜索 SKU / 名称 / 材质..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            className="packing-search"
          />
          <select value={fltElectric} onChange={e => setFltElectric(e.target.value)} style={{ padding: '6px 8px' }}>
            <option value="">带电：全部</option>
            <option value="是">带电</option>
            <option value="否">不带电</option>
            <option value="">空值</option>
          </select>
          <select value={fltMagnetic} onChange={e => setFltMagnetic(e.target.value)} style={{ padding: '6px 8px' }}>
            <option value="">含磁：全部</option>
            <option value="是">含磁</option>
            <option value="否">不含磁</option>
          </select>
          <select value={fltMaterial} onChange={e => setFltMaterial(e.target.value)} style={{ padding: '6px 8px' }}>
            <option value="">材质：全部</option>
            {materials.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>

        {filteredProducts.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--text-subtle)', padding: 24 }}>
            暂无产品，点击右上角「从选品库导入」添加。
          </div>
        ) : (
          <div className="packing-table-scroll">
            <table className="product-table packing-table">
              <colgroup>
                <col style={{ width: 36 }} />
                <col style={{ width: 58 }} />
                <col style={{ width: 150 }} />
                <col />
                <col style={{ width: 148 }} />
              </colgroup>
              <thead>
                <tr>
                  <th aria-label="选择" />
                  <th>图片</th>
                  <th>SKU</th>
                  <th>产品名称</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleProducts.map(p => {
                  const groupCount = p.variant_group ? products.filter(row => row.variant_group === p.variant_group).length : 0;
                  const isVariant = Boolean(p.variant_group);
                  // 多规格行的中文名只是定语（棕色 / 2XL），整串商品名在英文那行；
                  // 单规格行的中文名就是全名。两者不要互相顶替。
                  const zh = isVariant ? variantLabel(p) : (p.name_zh || p.name_en || '-');
                  const zhNeedsReview = isVariant && needsChineseReview(zh);
                  const attrs = [
                    p.unit || '个',
                    p.weight ? `${p.weight} kg` : '',
                    p.material,
                    p.electric === '是' ? '带电' : '',
                    p.magnetic === '是' ? '含磁' : '',
                  ].filter(Boolean);
                  return (
                  <tr key={p.sku} onContextMenu={e => { e.preventDefault(); setContextMenu({ sku: p.sku, x: e.clientX, y: e.clientY }); }} className={isVariant ? (p.is_primary_variant ? 'packing-variant-primary' : 'packing-variant-branch') : undefined}>
                    <td className="packing-col-pick">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${p.sku}`}
                        checked={selected.has(p.sku)}
                        onChange={() => toggleSelect(p.sku)}
                      />
                    </td>
                    <td className="packing-col-image">
                      <div className="packing-thumb">
                        <PackingImage filename={p.image_file} fallbackUrl={p.source_image_url} alt={p.sku} />
                      </div>
                    </td>
                    <td className="packing-col-sku">
                      {isVariant && <span className={p.is_primary_variant ? 'variant-role primary' : 'variant-role'}>{p.is_primary_variant ? '主' : '分支'}</span>}
                      <span className="packing-sku" translate="no">{p.sku}</span>
                    </td>
                    <td className="packing-col-name">
                      <div className="packing-name-row">
                        <strong className="packing-product-name" title={zh}>{zh}</strong>
                        {zhNeedsReview && <span className="packing-name-flag" title="中文字段不是中文，请回产品列表修正">中文名待改</span>}
                        {isVariant && p.is_primary_variant && groupCount > 1 && (
                          <button
                            type="button" className="packing-expand-link"
                            aria-expanded={expandedGroups.has(p.variant_group!)}
                            onClick={() => { const groupId = p.variant_group!; setExpandedGroups(prev => { const next = new Set(prev); if (next.has(groupId)) next.delete(groupId); else next.add(groupId); return next; }); }}
                          >{expandedGroups.has(p.variant_group!) ? '收起规格' : `展开 ${groupCount} 个规格`}</button>
                        )}
                      </div>
                      {p.name_en && p.name_en !== zh && <small className="packing-product-en" title={p.name_en}>{p.name_en}</small>}
                      <div className="packing-attr-line">
                        {attrs.map((a, i) => <span key={i} className={a === '带电' || a === '含磁' ? 'is-flag' : ''}>{a}</span>)}
                      </div>
                    </td>
                    <td className="packing-col-actions">
                      <button className="btn btn-sm" onClick={() => openEdit(p)}>编辑</button>
                      <button className="btn btn-sm" onClick={() => fileInputRefs.current[p.sku]?.click()}>
                        {p.image_file || p.source_image_url ? '换图' : '传图'}
                      </button>
                      {p.image_file && (
                        <button className="btn btn-sm" onClick={() => clearImage(p.sku)}>删图</button>
                      )}
                      <input
                        ref={el => { fileInputRefs.current[p.sku] = el; }}
                        type="file"
                        accept="image/*"
                        style={{ display: 'none' }}
                        onChange={e => {
                          handleFile(p.sku, e.target.files?.[0]);
                          e.target.value = '';
                        }}
                      />
                    </td>
                  </tr>
                )})}
              </tbody>
            </table>
          </div>
        )}
        {contextMenu && <div className="packing-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={e => e.stopPropagation()}>
          <button onClick={() => { setDeleteSku(contextMenu.sku); setContextMenu(null); }}>删除该产品…</button>
          <small>删除前仍会二次确认</small>
        </div>}
      </div>
      </div>

      <aside className="packing-summary-column">
      <div className="packing-summary-banner">
        <span>本次装箱单 · PACKING LIST</span>
        <strong>{selectedRows.length} 件产品</strong>
        <small>{totalCartons} 箱 · {totalCount} 件，草稿自动保存在本机</small>
      </div>
      {/* 本次装箱单行 */}
      <div className="card packing-selected-card">
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>已选产品</h3>
        {selectedRows.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--text-subtle)', padding: 24 }}>
            尚未选择产品，请在上方产品库勾选，或通过产品列表「加入装箱单」直达。
          </div>
        ) : (
          <div className="packing-selected-scroll">
                {selectedRows.map(p => {
                  const boxCount = lineCartons(p);
                  const perCount = lineCount(p);
                  const derived = cartonsManual ? skuBoxSummary(p.sku) : null;
                  return (
                    <div className="packing-selected-item" key={p.sku}>
                      <div className="packing-selected-item-head">
                        <div>
                          <strong title={p.name_en}>{p.variant_group ? variantLabel(p) : (p.name_zh || p.name_en || '-')}</strong>
                          {p.name_en && <small className="packing-selected-en" title={p.name_en}>{p.name_en}</small>}
                          <small>{p.sku}</small>
                          <small>{inventoryState === 'loading' ? '库存读取中…' : inventoryState === 'failed' ? '库存读取失败' : inventoryBySku[p.sku] ? `本地可用 ${inventoryBySku[p.sku].available} 件 · 在途 ${inventoryBySku[p.sku].in_transit} 件` : '该 SKU 未建库存'}</small>
                        </div>
                        <button className="packing-remove-btn" onClick={() => toggleSelect(p.sku)} title="从本次装箱单移除">×</button>
                      </div>
                      {/* 手工调整过逐箱清单以后，这两格就不该再让人填了 ——
                          同一份数量只留一个录入处，不然改了一边另一边就悄悄过期。 */}
                      {derived ? (
                        <div className="packing-line-derived">
                          <span>已装进</span>
                          <strong>{derived.boxes} 箱 · {derived.pieces} 件</strong>
                          <small>由逐箱清单汇总</small>
                        </div>
                      ) : (
                        <div className="packing-selected-controls">
                          <label>箱数
                          <input
                            type="number"
                            min={1}
                            value={lines[p.sku]?.cartons ?? '1'}
                            onChange={e => setLine(p.sku, 'cartons', e.target.value)}
                          />
                          </label>
                          <label>每箱件数
                          <input
                            type="number"
                            min={1}
                            placeholder={p.default_count ? `默认 ${p.default_count}` : '每箱件数'}
                            value={lines[p.sku]?.count ?? ''}
                            onChange={e => setLine(p.sku, 'count', e.target.value)}
                          />
                          </label>
                          <div className="packing-line-total"><span>小计</span><strong>{perCount * boxCount} 件</strong></div>
                        </div>
                      )}
                    </div>
                  );
                })}
          </div>
        )}
      </div>

      {/* 逐箱清单：箱子封上以后外观一样，「第几箱装了什么」只能靠这份记录 */}
      {/* 逐箱清单：箱子封上以后外观一样，「第几箱装了什么」只能靠这份记录。
          默认不录入 —— 上面填完箱数 × 每箱件数，这里就自动长出来。 */}
      <div className="card packing-carton-card">
        <div className="packing-carton-head">
          <h3 style={{ margin: 0 }}>逐箱清单</h3>
          <span className="packing-carton-total">
            {planCartons.length} 箱 · {cartonTotalPieces} 件{cartonsManual ? ' · 手动' : ' · 自动'}
          </span>
        </div>
        <small className="packing-carton-hint">
          {cartonsManual
            ? '已按手工调整的清单为准。要回到「箱数 × 每箱件数」自动生成，点「按产品资料重排」。'
            : '不用填。上面填完「箱数 × 每箱件数」，这里会自动长出箱子；要混装或拆箱再点「手动调整」。'}
          {' '}这份清单会原样变成 OMS 入库单：导出装箱单后点「导出 OMS 入仓模板」，
          在 OMS 里导入就能建入库单、打箱唛，不用再手录一遍 SKU。
        </small>
        {/* 箱唛号 = 入库单号 + 3 位箱序，与 OMS「入库单管理」里打印的箱唛逐箱对应。
            不填也能用，只是箱号退回 C01、C02。 */}
        <div className="packing-carton-inbound">
          <label>
            OMS 入库单号
            <input
              value={inboundNo}
              onChange={e => applyInboundNo(e.target.value)}
              placeholder="如 INH1501522"
              title="填了它，箱号就是 OMS 的箱唛号 INH1501522-001，和 OMS 打出来的箱唛一张对一张"
            />
          </label>
          <span className="packing-carton-inbound-hint">
            {inboundNo.trim()
              ? `箱唛号：${inboundNo.trim()}-001 … ${inboundNo.trim()}-${String(Math.max(planCartons.length, 1)).padStart(3, '0')}`
              : '填上后箱号自动变成 OMS 的箱唛号，贴在箱子上能和 OMS 的箱唛对上'}
          </span>
        </div>
        <div className="packing-carton-actions">
          {cartonsManual ? (
            <>
              <button className="btn btn-outline btn-sm" onClick={addCarton}>+ 新建一箱</button>
              <button className="btn btn-outline btn-sm" onClick={rebuildCartons} title="丢掉手工调整，回到「箱数 × 每箱件数」自动生成">
                按产品资料重排
              </button>
            </>
          ) : (
            <button
              className="btn btn-outline btn-sm"
              onClick={() => { setCartons(autoCartons); setCartonsManual(true); }}
              disabled={!selectedRows.length}
              title="接管这份自动生成的清单，之后可以拆箱、一箱装多个产品"
            >
              手动调整（混装 / 拆箱）
            </button>
          )}
        </div>
        {planCartons.length === 0 ? (
          <div className="packing-carton-empty">
            还没有箱子。选好产品、填上箱数和每箱件数，这里会按「一箱一个 SKU」自动长出来。
          </div>
        ) : (
          <div className="packing-carton-list">
            {planCartons.map((c, i) => (
              <div className="packing-carton-item" key={i}>
                <div className="packing-carton-row">
                  <label>箱号
                    <input value={c.box_no} disabled={!cartonsManual} onChange={e => patchCarton(i, { box_no: e.target.value })} />
                  </label>
                  <label>备注
                    <input value={c.note} disabled={!cartonsManual} onChange={e => patchCarton(i, { note: e.target.value })} placeholder="如：大箱 / 易碎" />
                  </label>
                  <span className="packing-carton-seq">第 {i + 1} 箱 / 共 {planCartons.length} 箱</span>
                  {cartonsManual && <button className="packing-remove-btn" onClick={() => removeCarton(i)} title="删除这一箱">×</button>}
                </div>
                {/* OMS 的箱唛一张只放得下 5 行 SKU，第 6 行起会另起一页且那页没有箱号。
                    装的时候就提示，别等打印出来才发现有一页认不出是哪箱。
                    单独占一行 —— 挤在「第 N 箱」旁边会把删除按钮顶出去。 */}
                {c.items.length > MAX_SKUS_PER_BOX && (
                  <div className="packing-carton-overflow" title={`OMS 箱唛一张只放得下 ${MAX_SKUS_PER_BOX} 行 SKU，这箱 ${c.items.length} 个，会多印一页且那一页没有箱号`}>
                    这箱 {c.items.length} 个 SKU，超出 OMS 箱唛的 {MAX_SKUS_PER_BOX} 行 —— 会多印一页，那一页没有箱号
                  </div>
                )}
                {c.items.map((it, j) => (
                  <div className="packing-carton-line" key={j}>
                    <select value={it.sku} disabled={!cartonsManual} onChange={e => patchCartonItem(i, j, { sku: e.target.value })}>
                      <option value="">选择产品…</option>
                      {selectedRows.map(p => (
                        <option key={p.sku} value={p.sku}>{p.name_zh || p.name_en || p.sku}</option>
                      ))}
                    </select>
                    <input
                      type="number"
                      min={1}
                      placeholder="数量"
                      value={it.quantity}
                      disabled={!cartonsManual}
                      onChange={e => patchCartonItem(i, j, { quantity: e.target.value })}
                    />
                    {cartonsManual && <button className="packing-remove-btn" onClick={() => removeCartonItem(i, j)} title="移除这一行">×</button>}
                  </div>
                ))}
                {cartonsManual && (
                  <button className="btn btn-outline btn-sm" onClick={() => addCartonItem(i)}>+ 这箱再加一个产品</button>
                )}
              </div>
            ))}
          </div>
        )}
        {cartonTotalPieces > 0 && cartonCoverage.unpacked.length > 0 && (
          <div className="packing-carton-warn" role="status">
            还没装进任何一箱：{cartonCoverage.unpacked.slice(0, 4).map(p => p.name_zh || p.sku).join('；')}
            {cartonCoverage.unpacked.length > 4 ? ` 等 ${cartonCoverage.unpacked.length} 项` : ''}
          </div>
        )}
        {cartonCoverage.extra.length > 0 && (
          <div className="packing-carton-warn" role="status">
            箱里有不在本次装箱单的产品：{cartonCoverage.extra.slice(0, 4).join('；')}
            {cartonCoverage.extra.length > 4 ? ` 等 ${cartonCoverage.extra.length} 项` : ''}
          </div>
        )}
      </div>

      {/* 导出设置 */}
      <div className="card packing-export-card">
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>出货信息</h3>
        <div className="packing-export-grid">
          <div>
            <label>日期</label>
            <input type="date" value={exportForm.date} onChange={e => setExportForm(prev => ({ ...prev, date: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
          </div>
          <div>
            <label>运输方式</label>
            <input type="text" placeholder="如：快递 / 空运 / 海运" value={exportForm.shipping} onChange={e => setExportForm(prev => ({ ...prev, shipping: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
          </div>
          <div>
            <label>唛头</label>
            <input type="text" value={exportForm.mark} onChange={e => setExportForm(prev => ({ ...prev, mark: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
          </div>
          <div>
            <label>收货地址</label>
            <input type="text" value={exportForm.address} onChange={e => setExportForm(prev => ({ ...prev, address: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
          </div>
        </div>
        <label style={{ display: 'block', marginTop: 12 }}><input type="checkbox" checked={exportForm.record_shipment === true} onChange={e => setExportForm(prev => ({ ...prev, record_shipment: e.target.checked }))} /> 登记发货并扣库存</label>
        <small style={{ display: 'block', marginTop: 6, color: 'var(--text-muted)' }}>{exportForm.record_shipment ? '按每个 SKU 的发货数量检查并核减本地库存。' : '仅生成文件并留档，可重复使用导出功能，库存未建档也可导出。'}</small>
        <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn packing-export-btn packing-export-wide" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
          <span style={{ fontSize: 13, color: 'var(--text-subtle)' }}>将导出 {exportableRows.length} 行装箱单数据（带产品图）</span>
        </div>
      </div>
      </aside>
      </div>

      {/* 查箱子：事后回查「某天装了哪些箱」/「这个 SKU 在哪几箱」。
          箱子封上以后外观一样，这里是唯一能反查的地方。 */}
      {cartonQueryOpen && (
        <div className="packing-modal-mask" onClick={e => { if (e.target === e.currentTarget) setCartonQueryOpen(false); }}>
          <div className="packing-modal carton-query-modal" role="dialog" aria-modal="true" aria-label="查箱子">
            <div className="carton-query-head">
              <div>
                <h3 style={{ margin: 0 }}>查箱子</h3>
                <small>某天装了哪些箱，或某个 SKU 在哪几箱。</small>
              </div>
              <button className="btn btn-outline btn-sm" onClick={() => setCartonQueryOpen(false)}>关闭</button>
            </div>

            <div className="carton-query-filters">
              <label>发货日期
                <input type="date" value={cartonQueryDate} onChange={e => setCartonQueryDate(e.target.value)} />
              </label>
              <label>SKU（留空看当天全部）
                <input
                  value={cartonQuerySku}
                  onChange={e => setCartonQuerySku(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void runCartonQuery(cartonQueryDate, cartonQuerySku); }}
                  placeholder="如 A-1"
                />
              </label>
              <button className="btn btn-primary btn-sm" onClick={() => void runCartonQuery(cartonQueryDate, cartonQuerySku)} disabled={cartonQueryLoading}>
                {cartonQueryLoading ? '查询中…' : '查询'}
              </button>
            </div>

            {cartonQueryError && <div className="alert alert-error">{cartonQueryError}</div>}

            {!cartonQueryError && (
              <div className="carton-query-summary">
                {cartonQueryLoading
                  ? '查询中…'
                  : cartonQueryRows.length
                    ? (cartonQuerySku.trim()
                      // 按 SKU 筛时后端返回的 total_cartons 只是命中数，说「共 N 箱」会自相矛盾
                      ? `找到 ${cartonQueryRows.length} 箱装了 ${cartonQuerySku.trim()}（箱唛上的「共 N 箱」仍是整票总数）`
                      : `共 ${cartonQueryTotal} 箱`)
                    : '这一天没有装箱记录'}
              </div>
            )}

            {cartonQueryRows.length > 0 && (
              <div className="carton-query-body">
                {cartonQueryGroups.map(g => {
                  return (
                    <div className="carton-query-group" key={g.shipment_no}>
                      <div className="carton-query-group-head">
                        <strong>{g.shipment_no || '未编号'}</strong>
                        <span>{g.date}</span>
                        {g.mark ? <span className="carton-query-mark">{g.mark}</span> : null}
                        <span className="carton-query-group-count">{g.rows.length} 箱</span>
                      </div>
                      <div className="carton-query-list">
                        {g.rows.map(r => {
                          const key = cartonKey(r);
                          const pieces = r.items.reduce((s, it) => s + it.quantity, 0);
                          return (
                            <div className="carton-query-row" key={key}>
                              <span className="carton-query-no">{r.box_no || `C${String(r.seq).padStart(2, '0')}`}</span>
                              <span className="carton-query-seq">第 {r.seq} 箱 / 共 {r.total} 箱</span>
                              <span className="carton-query-items">
                                {r.items.map((it, j) => (
                                  <span className="carton-query-item" key={j}>
                                    {it.name_zh || it.name_en || it.sku}
                                    <small>{it.sku}</small>
                                    <b>×{it.quantity}</b>
                                  </span>
                                ))}
                              </span>
                              <span className="carton-query-pieces">{pieces} 件</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="carton-query-foot">
              <span>{cartonQueryRows.length ? `${cartonQueryRows.length} 箱` : ''}</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-outline btn-sm" onClick={() => setCartonQueryOpen(false)}>关闭</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 编辑弹层 */}
      {editSku && (
        <div className="packing-modal-mask">
          <div className="packing-modal" role="dialog" aria-modal="true" aria-label="编辑装箱产品">
            <h3 className="dialog-title">编辑产品（{editSku}）</h3>
            <div className="dialog-form-grid">
              <div>
                <label>中文名</label>
                <input type="text" value={editForm.name_zh || ''} onChange={e => setEditForm(prev => ({ ...prev, name_zh: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>英文名</label>
                <input type="text" value={editForm.name_en || ''} onChange={e => setEditForm(prev => ({ ...prev, name_en: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>单位</label>
                <input type="text" value={editForm.unit || '个'} onChange={e => setEditForm(prev => ({ ...prev, unit: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>重量 (kg)</label>
                <input type="text" value={editForm.weight || ''} onChange={e => setEditForm(prev => ({ ...prev, weight: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>材质</label>
                <input type="text" value={editForm.material || ''} onChange={e => setEditForm(prev => ({ ...prev, material: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>品牌</label>
                <input type="text" value={editForm.brand || ''} onChange={e => setEditForm(prev => ({ ...prev, brand: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>电池</label>
                <input type="text" value={editForm.battery || ''} onChange={e => setEditForm(prev => ({ ...prev, battery: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label>带电</label>
                <select value={editForm.electric || ''} onChange={e => setEditForm(prev => ({ ...prev, electric: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box', padding: '4px 8px' }}>
                  <option value="">未设置</option>
                  <option value="是">是</option>
                  <option value="否">否</option>
                </select>
              </div>
              <div>
                <label>含磁</label>
                <select value={editForm.magnetic || ''} onChange={e => setEditForm(prev => ({ ...prev, magnetic: e.target.value }))} style={{ width: '100%', boxSizing: 'border-box', padding: '4px 8px' }}>
                  <option value="">未设置</option>
                  <option value="是">是</option>
                  <option value="否">否</option>
                </select>
              </div>
              <div>
                <label>默认每箱件数</label>
                <input type="number" min={1} value={editForm.default_count ?? ''} onChange={e => setEditForm(prev => ({ ...prev, default_count: e.target.value ? parseInt(e.target.value, 10) : null }))} style={{ width: '100%', boxSizing: 'border-box' }} />
              </div>
            </div>
            <div className="dialog-actions">
              <button className="btn" onClick={() => setEditSku(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveEdit}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteSku && (
        <div className="packing-modal-mask">
          <div className="packing-modal" role="dialog" aria-modal="true" aria-label="确认删除装箱产品" style={{ maxWidth: 420 }}>
            <h3 className="dialog-title">确认删除</h3>
            <p style={{ margin: '0 0 20px', color: 'var(--text-muted)' }}>确定从装箱单库删除「{deleteSku}」吗？此操作不可恢复。</p>
            <div className="dialog-actions">
              <button className="btn" onClick={() => setDeleteSku(null)}>取消</button>
              <button className="btn btn-danger" onClick={confirmDelete}>确认删除</button>
            </div>
          </div>
        </div>
      )}

      {/* 从选品库导入弹层 */}
      {importOpen && (
        <div className="packing-modal-mask">
          <div className="packing-modal" style={{ maxWidth: 720 }}>
            <h3 style={{ margin: '0 0 12px' }}>从选品库导入</h3>
            <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-subtle)' }}>
              勾选选品产品后导入装箱单库（SKU 已存在则自动跳过，不会覆盖已有编辑；图片自动抓取转存）。
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(160px, 240px)', gap: 8, marginBottom: 10 }}>
              <input
                type="text"
                placeholder="搜索 SKU / 中文品名 / 英文品名 / TSIN / 类目"
                value={importSearch}
                onChange={e => setImportSearch(e.target.value)}
                style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid var(--color-border)', width: '100%', boxSizing: 'border-box' }}
                autoFocus
              />
              <select value={importCategory} onChange={e => setImportCategory(e.target.value)}>
                <option value="">全部类目（{importCategories.length}）</option>
                {importCategories.map(category => <option key={category} value={category}>{category}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 13, color: 'var(--text-muted)' }}>
              <span>产品库 {importResults.length} 条 · 当前结果 {filteredImportResults.length} 条 · 可导入 {selectableImportResults.length} 条</span>
              <button className="btn btn-outline" style={{ marginLeft: 'auto', padding: '3px 9px' }} disabled={selectableImportResults.length === 0} onClick={() => {
                setImportSelected(prev => new Set([...prev, ...selectableImportResults.map(p => String(p.id))]));
              }}>选择当前全部</button>
            </div>
            {importSearching && <div style={{ fontSize: 13, color: 'var(--text-subtle)', marginBottom: 8 }}>正在加载完整产品库...</div>}
            {!importSearching && filteredImportResults.length === 0 ? (
              <div style={{ textAlign: 'center', color: 'var(--text-subtle)', padding: 20 }}>未找到匹配的选品产品</div>
            ) : (
              <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 4 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 10px', width: 40 }}></th>
                      <th style={{ padding: '6px 10px' }}>SKU</th>
                      <th style={{ padding: '6px 10px' }}>产品名称</th>
                      <th style={{ padding: '6px 10px' }}>类目</th>
                      <th style={{ padding: '6px 10px' }}>运输方式</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredImportResults.map(p => {
                      const pid = String(p.id);
                      const checked = importSelected.has(pid);
                      const disabled = !p.sku || existingPackingSkus.has(p.sku);
                      return (
                        <tr key={pid} style={{ borderBottom: '1px solid var(--color-border)' }}>
                          <td style={{ padding: '6px 10px', textAlign: 'center' }}>
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={disabled}
                              onChange={() => {
                                setImportSelected(prev => {
                                  const next = new Set(prev);
                                  if (next.has(pid)) next.delete(pid); else next.add(pid);
                                  return next;
                                });
                              }}
                              style={{ width: 'auto', accentColor: 'var(--color-primary, #1677ff)' }}
                            />
                          </td>
                          <td style={{ padding: '6px 10px', fontWeight: 600 }}>{p.sku || '无 SKU'}</td>
                          <td style={{ padding: '6px 10px' }}>{p.chinese_product_name || p.product_name || '-'}</td>
                          <td style={{ padding: '6px 10px' }}>{p.fee_category || '-'}</td>
                          <td style={{ padding: '6px 10px' }}>{p.shipping_method || '-'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <span style={{ fontSize: 13, color: 'var(--text-subtle)', marginRight: 'auto', alignSelf: 'center' }}>
                已选 {importSelected.size} 项
              </span>
              <button className="btn" onClick={() => setImportOpen(false)} disabled={importing}>取消</button>
              <button className="btn btn-primary" onClick={confirmImport} disabled={importing || importSelected.size === 0}>
                {importing ? '导入中...' : '导入到装箱单'}
              </button>
            </div>
          </div>
        </div>
      )}
      {templateOpen && (
        <div className="packing-modal-mask">
          <div className="packing-modal" style={{ maxWidth: 720 }}>
            <h3 style={{ margin: '0 0 8px' }}>导出 OMS 入仓文件</h3>
            <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6 }}>
              按<b>箱</b>生成，数据来自逐箱清单：标识号相同的行 = 同一个箱子，箱子数和每箱件数就是装箱时录的那份。
              在 OMS「入库单」里导入这份文件，建完单直接打箱唛，不用再手录 SKU。海运、空运请分别生成。生成文件不扣库存。
            </p>
            {templateError && <div className="alert alert-error" style={{ marginBottom: 10 }}>{templateError}</div>}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 13, color: 'var(--text-muted)' }}>
              <span>{templateLoading ? '正在读取装箱单…' : `可选 ${templateShipments.length} 张 · 已选 ${selectedShipmentIds.length} 张`}</span>
              <button className="btn btn-outline" style={{ marginLeft: 'auto', padding: '3px 9px' }} disabled={templateLoading || templateShipments.length === 0}
                onClick={() => setSelectedShipmentIds(templateShipments.map(row => row.id))}>全选</button>
              <button className="btn btn-outline" style={{ padding: '3px 9px' }} disabled={selectedShipmentIds.length === 0}
                onClick={() => setSelectedShipmentIds([])}>清空</button>
            </div>
            <div style={{ maxHeight: 380, overflow: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
              {templateLoading ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>正在读取装箱单…</div>
              ) : templateShipments.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>暂无可用装箱单</div>
              ) : templateShipments.map(row => (
                <label key={row.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px', borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={selectedShipmentIds.includes(row.id)}
                    onChange={() => setSelectedShipmentIds(prev => prev.includes(row.id) ? prev.filter(id => id !== row.id) : [...prev, row.id])}
                    style={{ width: 'auto', accentColor: 'var(--color-primary, #1677ff)' }} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <strong style={{ display: 'block', color: 'var(--text-strong)' }}>{row.shipment_no}</strong>
                    <small style={{ color: 'var(--text-muted)' }}>{row.shipment_date} · {row.shipping || '未填运输方式'} · {row.total_skus} 个 SKU{row.status === 'clearance' ? ' · 老库存清理' : row.status === 'exported' ? ' · 仅导出·未扣库存' : ''}</small>
                  </span>
                  <span style={{ color: 'var(--text-body)', fontSize: 13, whiteSpace: 'nowrap' }}>{row.total_quantity} 件</span>
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <button className="btn" onClick={() => setTemplateOpen(false)} disabled={templateExporting}>取消</button>
              <button className="btn btn-primary" onClick={handleExportErpTemplate} disabled={templateLoading || templateExporting || selectedShipmentIds.length === 0}>
                {templateExporting ? '生成中…' : '下载 OMS 入仓文件'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
