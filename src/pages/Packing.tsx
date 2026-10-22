import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { createPortal } from 'react-dom';
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

type PackingLines = Record<string, { cartons: string; count: string }>;
type PackingExportForm = { date: string; mark: string; shipping: string; address: string; record_shipment?: boolean };
type PackingDraft = {
  selected: string[];
  lines: PackingLines;
  exportForm: Partial<PackingExportForm>;
  draftKey: string;
  cartons: PackingCarton[];
};
const newDraftKey = () => globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function readPackingDraft(): PackingDraft {
  try {
    const parsed = JSON.parse(readStoreStorage(PACKING_DRAFT_KEY) || '{}');
    return {
      selected: Array.isArray(parsed.selected) ? parsed.selected.filter((v: unknown): v is string => typeof v === 'string') : [],
      lines: parsed.lines && typeof parsed.lines === 'object' ? parsed.lines as PackingLines : {},
      exportForm: parsed.exportForm && typeof parsed.exportForm === 'object' ? parsed.exportForm as Partial<PackingExportForm> : {},
      draftKey: typeof parsed.draftKey === 'string' && parsed.draftKey ? parsed.draftKey : newDraftKey(),
      cartons: Array.isArray(parsed.cartons) ? parsed.cartons as PackingCarton[] : [],
    };
  } catch {
    return { selected: [], lines: {}, exportForm: {}, draftKey: newDraftKey(), cartons: [] };
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
  const [printOpen, setPrintOpen] = useState(false);

  // 事后查箱：箱子封上以后就只剩「某天一共几箱」这一个印象，
  // 「昨天第 5 箱装的是什么」只能回查这张表。
  const [cartonQueryOpen, setCartonQueryOpen] = useState(false);
  const [cartonQueryDate, setCartonQueryDate] = useState('');
  const [cartonQuerySku, setCartonQuerySku] = useState('');
  const [cartonQueryRows, setCartonQueryRows] = useState<PackingCartonRecord[]>([]);
  const [cartonQueryTotal, setCartonQueryTotal] = useState(0);
  const [cartonQueryLoading, setCartonQueryLoading] = useState(false);
  const [cartonQueryError, setCartonQueryError] = useState('');
  const [cartonQueryPicked, setCartonQueryPicked] = useState<Set<string>>(new Set());
  // 重印用：带着记录里的 seq/total，箱唛上的「第 N 箱 / 共 M 箱」不会被重排
  const [reprintRows, setReprintRows] = useState<PackingCartonRecord[]>([]);

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
    }));
  }, [selected, lines, exportForm, draftKey, cartons]);

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
  // 「一箱一个 SKU」是常态：按箱数自动展开成 C01..Cn，不用手填一遍。
  // 混装（一箱多 SKU）只能人工加箱、逐行选 SKU 填数量。
  const expandCartons = () => {
    const next: PackingCarton[] = [];
    selectedRows.forEach(p => {
      const cartonsCount = parseInt(lines[p.sku]?.cartons || '0', 10) || 0;
      const perCount = parseInt(lines[p.sku]?.count || '', 10) || 0;
      for (let i = 0; i < cartonsCount; i += 1) {
        next.push({ box_no: `C${String(next.length + 1).padStart(2, '0')}`, note: '', items: [{ sku: p.sku, quantity: String(perCount || '') }] });
      }
    });
    setCartons(next);
  };

  const addCarton = () => {
    setCartons(prev => [...prev, { box_no: `C${String(prev.length + 1).padStart(2, '0')}`, note: '', items: [{ sku: '', quantity: '' }] }]);
  };

  const patchCarton = (index: number, patch: Partial<PackingCarton>) => {
    setCartons(prev => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };

  const removeCarton = (index: number) => {
    setCartons(prev => prev.filter((_, i) => i !== index));
  };

  const patchCartonItem = (index: number, itemIndex: number, patch: Partial<PackingCarton['items'][number]>) => {
    setCartons(prev => prev.map((c, i) => (i === index
      ? { ...c, items: c.items.map((it, j) => (j === itemIndex ? { ...it, ...patch } : it)) }
      : c)));
  };

  const addCartonItem = (index: number) => {
    setCartons(prev => prev.map((c, i) => (i === index ? { ...c, items: [...c.items, { sku: '', quantity: '' }] } : c)));
  };

  const removeCartonItem = (index: number, itemIndex: number) => {
    setCartons(prev => prev.map((c, i) => (i === index
      ? { ...c, items: c.items.filter((_, j) => j !== itemIndex) }
      : c)));
  };

  // 箱内合计 vs 装箱单合计：对不上就是「箱子里写 10 件、账上扣 12 件」，
  // 比没有记录更糟。这里实时算出来给用户看，导出前还会再拦一次。
  const cartonTotals = useMemo(() => {
    const got: Record<string, number> = {};
    cartons.forEach(c => c.items.forEach(it => {
      const sku = (it.sku || '').trim();
      if (!sku) return;
      got[sku] = (got[sku] || 0) + (parseInt(it.quantity || '0', 10) || 0);
    }));
    const want: Record<string, number> = {};
    selectedRows.forEach(p => {
      want[p.sku] = (parseInt(lines[p.sku]?.cartons || '0', 10) || 0) * (parseInt(lines[p.sku]?.count || '0', 10) || 0);
    });
    const skus = Array.from(new Set([...Object.keys(got), ...Object.keys(want)]));
    return skus.map(sku => ({ sku, got: got[sku] || 0, want: want[sku] || 0 }));
  }, [cartons, selectedRows, lines]);

  const cartonMismatch = cartonTotals.filter(row => row.got !== row.want);
  const cartonPieces = (c: PackingCarton) =>
    c.items.reduce((s, it) => s + (parseInt(it.quantity || '0', 10) || 0), 0);
  const cartonTotalPieces = cartons.reduce((sum, c) => sum + cartonPieces(c), 0);

  // ---- 事后查箱 ----
  const cartonKey = (r: PackingCartonRecord) => `${r.shipment_id}#${r.seq}`;

  const runCartonQuery = async (date: string, sku: string) => {
    setCartonQueryLoading(true);
    setCartonQueryError('');
    try {
      const res = await getPackingCartons({ date, sku: sku.trim(), limit: 500 });
      setCartonQueryRows(res.cartons || []);
      setCartonQueryTotal(res.total_cartons || 0);
      setCartonQueryPicked(new Set());
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

  const toggleCartonPick = (key: string) => {
    setCartonQueryPicked(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
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

  const printPickedCartons = () => {
    const picked = cartonQueryRows.filter(r => cartonQueryPicked.has(cartonKey(r)));
    if (!picked.length) return;
    // 两个 overlay 同时挂 body 会一起进打印流，先关掉草稿那份
    setPrintOpen(false);
    setReprintRows(picked);
  };

  const totalCartons = useMemo(
    () => selectedRows.reduce((sum, p) => sum + (parseInt(lines[p.sku]?.cartons || '0', 10) || 0), 0),
    [selectedRows, lines],
  );
  const totalCount = useMemo(
    () => selectedRows.reduce((sum, p) => {
      const count = parseInt(lines[p.sku]?.count || '', 10) || p.default_count || 0;
      const cartons = parseInt(lines[p.sku]?.cartons || '1', 10) || 1;
      return sum + count * cartons;
    }, 0),
    [selectedRows, lines],
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
    const rows = selectedRows.filter(p => {
      const cartons = parseInt(lines[p.sku]?.cartons || '0', 10);
      const count = parseInt(lines[p.sku]?.count || '', 10);
      return cartons >= 1 && count >= 1;
    });
    if (rows.length === 0) {
      setError('请至少勾选 1 个产品，并填写有效的箱数和每箱件数（≥1）后再导出');
      return;
    }
    if (selectedRows.length !== rows.length) {
      setError('部分勾选产品箱数/每箱件数无效（需 ≥1 的正整数），已自动跳过这些行');
    }
    // 逐箱清单和装箱单必须是同一票货：合计对不上就不让导，否则箱唛会印错。
    if (cartons.length) {
      const bad = cartonTotals.filter(row => row.got !== row.want);
      if (bad.length) {
        const detail = bad.slice(0, 3)
          .map(row => `${products.find(p => p.sku === row.sku)?.name_zh || row.sku} 箱内 ${row.got} / 装箱单 ${row.want}`)
          .join('；');
        setError(`逐箱合计与装箱单不一致：${detail}。请先调整逐箱清单，或点「按 SKU 展开」重来`);
        return;
      }
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
        items: rows.map(p => ({
          sku: p.sku,
          cartons: lines[p.sku]?.cartons || '1',
          count: lines[p.sku]?.count || String(p.default_count || 1),
        })),
        // 没填逐箱清单就不传，后端按「一箱一个 SKU」自动展开
        cartons: cartons.length
          ? cartons.map(c => ({
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
      setDraftKey(newDraftKey());
      setImportMissing([]);
      localStorage.removeItem(getStoreStorageKey(PACKING_DRAFT_KEY));
      localStorage.removeItem(getStoreStorageKey(PRODUCT_SELECTED_SKUS_KEY));
      const sp = new URLSearchParams(searchParams);
      sp.delete('import');
      setSearchParams(sp, { replace: true });
      await loadProducts();
      setExportForm(prev => ({ ...prev, record_shipment: false }));
      setMsg(exportForm.record_shipment ? '导出成功，已登记发货并扣库存' : '导出成功，装箱单已留档，库存未变化，可在 ERP 模板窗口继续套入');
    } catch (e: any) {
      setError(`导出失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setExporting(false);
    }
  };

  const openErpTemplate = async () => {
    setTemplateOpen(true);
    setTemplateLoading(true);
    setTemplateError('');
    setSelectedShipmentIds([]);
    try {
      const rows = await getShipments();
      setTemplateShipments(rows
        .filter(row => row.status === 'confirmed' || row.status === 'clearance' || row.status === 'exported')
        .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '') || b.shipment_no.localeCompare(a.shipment_no)));
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
      const blob = await exportInboundTemplate(chosen.map(row => row.id));
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `ERP入仓模板_${todayStr()}.xlsx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setTemplateOpen(false);
      const totalItems = chosen.reduce((sum, row) => sum + row.total_quantity, 0);
      setMsg(`ERP 模板已生成：${chosen.length} 张装箱单、${chosen.length} 个箱子、${totalItems} 件商品`);
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
          <button className="btn btn-outline" onClick={openErpTemplate}>
            ERP 模板
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
      {msg && <div className="alert alert-success" style={{ marginBottom: 12 }}>{msg}</div>}
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
                  const cartons = parseInt(lines[p.sku]?.cartons || '1', 10) || 1;
                  const perCount = parseInt(lines[p.sku]?.count || '', 10) || p.default_count || 0;
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
                        <div className="packing-line-total"><span>小计</span><strong>{perCount * cartons} 件</strong></div>
                      </div>
                    </div>
                  );
                })}
          </div>
        )}
      </div>

      {/* 逐箱清单：箱子封上以后外观一样，「第几箱装了什么」只能靠这份记录 */}
      <div className="card packing-carton-card">
        <div className="packing-carton-head">
          <h3 style={{ margin: 0 }}>逐箱清单</h3>
          <span className="packing-carton-total">{cartons.length} 箱 · {cartonTotalPieces} 件</span>
        </div>
        <small className="packing-carton-hint">
          箱子封上就分不出哪箱是什么了，这里记下「第几箱装了什么」，箱唛按它印。
          一箱一个 SKU 点「按 SKU 展开」就行；混装就新建一箱、逐行加产品。
        </small>
        <div className="packing-carton-actions">
          <button className="btn btn-outline btn-sm" onClick={expandCartons} disabled={!selectedRows.length} title="按已选产品的箱数×每箱件数，展开成 C01、C02…">
            按 SKU 展开
          </button>
          <button className="btn btn-outline btn-sm" onClick={addCarton}>+ 新建一箱</button>
          <button className="btn btn-outline btn-sm" onClick={() => { setReprintRows([]); setPrintOpen(true); }} disabled={!cartons.length}>打印箱唛</button>
        </div>
        {cartons.length === 0 ? (
          <div className="packing-carton-empty">
            还没有逐箱记录。导出时留空的话，会按「一箱一个 SKU」自动生成，事后也能查。
          </div>
        ) : (
          <div className="packing-carton-list">
            {cartons.map((c, i) => (
              <div className="packing-carton-item" key={i}>
                <div className="packing-carton-row">
                  <label>箱号
                    <input value={c.box_no} onChange={e => patchCarton(i, { box_no: e.target.value })} />
                  </label>
                  <label>备注
                    <input value={c.note} onChange={e => patchCarton(i, { note: e.target.value })} placeholder="如：大箱 / 易碎" />
                  </label>
                  <span className="packing-carton-seq">第 {i + 1} 箱 / 共 {cartons.length} 箱</span>
                  <button className="packing-remove-btn" onClick={() => removeCarton(i)} title="删除这一箱">×</button>
                </div>
                {c.items.map((it, j) => (
                  <div className="packing-carton-line" key={j}>
                    <select value={it.sku} onChange={e => patchCartonItem(i, j, { sku: e.target.value })}>
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
                      onChange={e => patchCartonItem(i, j, { quantity: e.target.value })}
                    />
                    <button className="packing-remove-btn" onClick={() => removeCartonItem(i, j)} title="移除这一行">×</button>
                  </div>
                ))}
                <button className="btn btn-outline btn-sm" onClick={() => addCartonItem(i)}>+ 这箱再加一个产品</button>
              </div>
            ))}
          </div>
        )}
        {cartonMismatch.length > 0 && (
          <div className="packing-carton-warn" role="status">
            合计对不上：{cartonMismatch.slice(0, 4).map(row => (
              `${products.find(p => p.sku === row.sku)?.name_zh || row.sku} 箱内 ${row.got} / 装箱单 ${row.want}`
            )).join('；')}
            {cartonMismatch.length > 4 ? ` 等 ${cartonMismatch.length} 项` : ''}
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
          <span style={{ fontSize: 13, color: 'var(--text-subtle)' }}>将导出 {selectedRows.length} 行装箱单数据（带产品图）</span>
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
                    ? `共 ${cartonQueryTotal} 箱${cartonQuerySku.trim() ? `（只列出装了 ${cartonQuerySku.trim()} 的箱，箱唛上的「共 N 箱」仍是整票总数）` : ''}`
                    : '这一天没有装箱记录'}
              </div>
            )}

            {cartonQueryRows.length > 0 && (
              <div className="carton-query-body">
                {cartonQueryGroups.map(g => {
                  const pickedCount = g.rows.filter(r => cartonQueryPicked.has(cartonKey(r))).length;
                  return (
                    <div className="carton-query-group" key={g.shipment_no}>
                      <div className="carton-query-group-head">
                        <strong>{g.shipment_no || '未编号'}</strong>
                        <span>{g.date}</span>
                        {g.mark ? <span className="carton-query-mark">{g.mark}</span> : null}
                        <span className="carton-query-group-count">{g.rows.length} 箱</span>
                        <button
                          className="btn btn-outline btn-sm"
                          onClick={() => setCartonQueryPicked(prev => {
                            const next = new Set(prev);
                            const all = pickedCount === g.rows.length;
                            g.rows.forEach(r => { if (all) next.delete(cartonKey(r)); else next.add(cartonKey(r)); });
                            return next;
                          })}
                        >
                          {pickedCount === g.rows.length ? '取消本票' : '全选本票'}
                        </button>
                      </div>
                      <div className="carton-query-list">
                        {g.rows.map(r => {
                          const key = cartonKey(r);
                          const checked = cartonQueryPicked.has(key);
                          const pieces = r.items.reduce((s, it) => s + it.quantity, 0);
                          return (
                            <label className={`carton-query-row${checked ? ' is-picked' : ''}`} key={key}>
                              <input type="checkbox" checked={checked} onChange={() => toggleCartonPick(key)} />
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
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="carton-query-foot">
              <span>已选 {cartonQueryPicked.size} 箱</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-outline btn-sm" onClick={() => setCartonQueryPicked(new Set())} disabled={!cartonQueryPicked.size}>清空选择</button>
                <button className="btn btn-primary btn-sm" onClick={printPickedCartons} disabled={!cartonQueryPicked.size}>
                  重印选中箱唛
                </button>
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
            <h3 style={{ margin: '0 0 8px' }}>导出 ERP 入仓模板</h3>
            <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6 }}>
              选择已留档的装箱单，海运、空运分别生成。每张单对应 1 箱；标识号为 1、2…，自定义箱号为 01、02…（ERP 要求至少 2 个字符）。生成模板不扣库存。
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
                {templateExporting ? '生成中…' : '下载填好的模板'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 箱唛：贴在箱子外面的那张纸。用 HTML 打印而不是生成 PDF ——
          箱唛必须能印中文，而前端那个零依赖 PDF 生成器只支持 ASCII（中文会变 ?）。
          挂到 body 而不是留在 #root 里：打印时要把 #root 整块 display:none，
          否则应用那一长条布局会跟着分页，5 张箱唛能印出 14 页。 */}
      {printOpen && createPortal(
        <div className="carton-print-overlay">
          <div className="carton-print-toolbar">
            <strong>箱唛预览 · {cartons.length} 张</strong>
            <small>打印时选 100×70mm 标签纸、缩放 100%，一张一页</small>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={() => window.print()}>打印</button>
              <button className="btn btn-outline" onClick={() => setPrintOpen(false)}>关闭</button>
            </div>
          </div>
          <div className="carton-print-sheet">
            {cartons.map((c, i) => (
              <div className="carton-label" key={i}>
                <div className="carton-label-head">
                  <span className="carton-label-no">{c.box_no || `C${String(i + 1).padStart(2, '0')}`}</span>
                  <span className="carton-label-seq">第 {i + 1} 箱 / 共 {cartons.length} 箱</span>
                </div>
                <div className="carton-label-meta">
                  <span>{exportForm.date || todayStr()}</span>
                  {exportForm.mark ? <span>{exportForm.mark}</span> : null}
                  {c.note ? <span className="carton-label-note">{c.note}</span> : null}
                </div>
                <div className="carton-label-items">
                  {c.items.filter(it => (it.sku || '').trim()).map((it, j) => {
                    const product = products.find(p => p.sku === it.sku);
                    return (
                      <div className="carton-label-item" key={j}>
                        <div className="carton-label-text">
                          <strong>{product?.name_zh || product?.name_en || it.sku}</strong>
                          <small>{it.sku}</small>
                        </div>
                        <div className="carton-label-qty">×{it.quantity || 0}</div>
                      </div>
                    );
                  })}
                </div>
                <div className="carton-label-foot">
                  {c.items.filter(it => (it.sku || '').trim()).length} 个产品 · {cartonPieces(c)} 件
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}

      {/* 箱唛重印：从历史记录出图，箱号与「第 N 箱 / 共 M 箱」照抄当时的值，
          不能按本次选中的张数重排 —— 重排后同一箱会有两个箱号。 */}
      {reprintRows.length > 0 && createPortal(
        <div className="carton-print-overlay">
          <div className="carton-print-toolbar">
            <strong>箱唛重印 · {reprintRows.length} 张</strong>
            <small>箱号与「第几箱 / 共几箱」沿用装箱时的记录</small>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={() => window.print()}>打印</button>
              <button className="btn btn-outline" onClick={() => setReprintRows([])}>关闭</button>
            </div>
          </div>
          <div className="carton-print-sheet">
            {reprintRows.map(r => (
              <div className="carton-label" key={cartonKey(r)}>
                <div className="carton-label-head">
                  <span className="carton-label-no">{r.box_no || `C${String(r.seq).padStart(2, '0')}`}</span>
                  <span className="carton-label-seq">第 {r.seq} 箱 / 共 {r.total} 箱</span>
                </div>
                <div className="carton-label-meta">
                  <span>{r.shipment_date}</span>
                  {r.mark ? <span>{r.mark}</span> : null}
                  {r.note ? <span className="carton-label-note">{r.note}</span> : null}
                </div>
                <div className="carton-label-items">
                  {r.items.map((it, j) => (
                    <div className="carton-label-item" key={j}>
                      <div className="carton-label-text">
                        <strong>{it.name_zh || it.name_en || it.sku}</strong>
                        <small>{it.sku}</small>
                      </div>
                      <div className="carton-label-qty">×{it.quantity}</div>
                    </div>
                  ))}
                </div>
                <div className="carton-label-foot">
                  {r.items.length} 个产品 · {r.items.reduce((s, it) => s + it.quantity, 0)} 件
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
