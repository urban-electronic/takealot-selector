import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import {
  getPackingProducts,
  syncPackingProductsFromCatalog,
  upsertPackingProduct,
  deletePackingProduct,
  uploadPackingImage,
  importFromProducts,
  syncPackingImagesFromProducts,
  exportPacking,
  fetchPackingImageBlob,
} from '../remoteApi';
import type { PackingProduct, Product, PackingExportPayload } from '../types';

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
    return <span className="packing-noimg">加载中...</span>;
  }
  if (state === 'empty') {
    return <span className="packing-noimg">待补图</span>;
  }
  if (state === 'failed' || !src) {
    return <span className="packing-noimg">图加载失败</span>;
  }
  return <img className="packing-img" src={src} alt={alt || filename} />;
}

const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

function packingSpec(product: PackingProduct, rows: PackingProduct[]): string {
  const label = product.variant_label || product.name_zh || '';
  const direct = label.match(/\b(?:\d+\s*(?:GB|TB)|XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|\d+(?:\.\d+)?\s*(?:cm|mm|inch|英寸|码))\b/i);
  if (direct) return direct[0].replace(/\s+/g, '').toUpperCase();
  const color = label.match(/(藏蓝|军绿|卡其|墨绿|深蓝|浅蓝|天蓝|玫红|粉红|黑色|白色|红色|蓝色|绿色|黄色|紫色|粉色|灰色|棕色|咖色|黑|白|红|蓝|绿|黄|紫|粉|灰|棕)$/);
  if (color) return color[1];
  const group = rows.filter(row => row.variant_group && row.variant_group === product.variant_group).map(row => row.variant_label || row.name_zh || '');
  if (group.length > 1) {
    let prefix = group[0];
    while (prefix && !group.every(item => item.startsWith(prefix))) prefix = prefix.slice(0, -1);
    const distinct = label.slice(prefix.length).replace(/^[\s\-_/]+|[\s\-_/]+$/g, '');
    if (distinct) return distinct.slice(0, 12);
  }
  return product.is_primary_variant ? '主款' : '待确认';
}

const PACKING_DRAFT_KEY = 'packingDraftV1';
const PRODUCT_SELECTED_SKUS_KEY = 'productListSelectedSkus';

type PackingLines = Record<string, { cartons: string; count: string }>;
type PackingExportForm = { date: string; mark: string; shipping: string; address: string };
type PackingDraft = { selected: string[]; lines: PackingLines; exportForm: Partial<PackingExportForm>; draftKey: string };
const newDraftKey = () => globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function readPackingDraft(): PackingDraft {
  try {
    const parsed = JSON.parse(localStorage.getItem(PACKING_DRAFT_KEY) || '{}');
    return {
      selected: Array.isArray(parsed.selected) ? parsed.selected.filter((v: unknown): v is string => typeof v === 'string') : [],
      lines: parsed.lines && typeof parsed.lines === 'object' ? parsed.lines as PackingLines : {},
      exportForm: parsed.exportForm && typeof parsed.exportForm === 'object' ? parsed.exportForm as Partial<PackingExportForm> : {},
      draftKey: typeof parsed.draftKey === 'string' && parsed.draftKey ? parsed.draftKey : newDraftKey(),
    };
  } catch {
    return { selected: [], lines: {}, exportForm: {}, draftKey: newDraftKey() };
  }
}

export default function Packing() {
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const savedDraft = useMemo(readPackingDraft, []);

  // 装箱单库
  const [products, setProducts] = useState<PackingProduct[]>([]);
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

  // 导出设置
  const [exportForm, setExportForm] = useState<PackingExportForm>({ date: todayStr(), mark: '', shipping: '', address: '', ...savedDraft.exportForm });
  const [draftKey, setDraftKey] = useState(savedDraft.draftKey);
  const [exporting, setExporting] = useState(false);

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

  // 文件输入
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const loadProducts = useCallback(async () => {
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

  useEffect(() => {
    localStorage.setItem(PACKING_DRAFT_KEY, JSON.stringify({
      selected: Array.from(selected),
      lines,
      exportForm,
      draftKey,
    }));
  }, [selected, lines, exportForm, draftKey]);

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

  const filteredProducts = useMemo(() => {
    const kw = searchText.trim().toLowerCase();
    return products.filter(p => {
      if (kw) {
        const hay = `${p.sku} ${p.name_zh} ${p.name_en} ${p.material} ${p.brand}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      if (fltElectric && p.electric !== fltElectric) return false;
      if (fltMagnetic && p.magnetic !== fltMagnetic) return false;
      if (fltMaterial && p.material !== fltMaterial) return false;
      return true;
    });
  }, [products, searchText, fltElectric, fltMagnetic, fltMaterial]);

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
    () => selected.size === 0 ? [] : products.filter(p => selected.has(p.sku)),
    [selected, products],
  );

  const setLine = (sku: string, key: 'cartons' | 'count', value: string) => {
    setLines(prev => {
      const cur = prev[sku] || { cartons: '1', count: '' };
      return { ...prev, [sku]: { ...cur, [key]: value } };
    });
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
    setExporting(true);
    setError('');
    try {
      const payload: PackingExportPayload = {
        date: exportForm.date,
        mark: exportForm.mark,
        shipping: exportForm.shipping,
        address: exportForm.address,
        draft_key: draftKey,
        items: rows.map(p => ({
          sku: p.sku,
          cartons: lines[p.sku]?.cartons || '1',
          count: lines[p.sku]?.count || String(p.default_count || 1),
        })),
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
      setDraftKey(newDraftKey());
      setImportMissing([]);
      localStorage.removeItem(PACKING_DRAFT_KEY);
      localStorage.removeItem(PRODUCT_SELECTED_SKUS_KEY);
      const sp = new URLSearchParams(searchParams);
      sp.delete('import');
      setSearchParams(sp, { replace: true });
      await loadProducts();
      setMsg('导出成功，已刷新装箱单库并清空本次勾选，请检查下载的 Excel');
    } catch (e: any) {
      setError(`导出失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setExporting(false);
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
            装箱单库 {products.length} 条{catalogStats ? ` / 产品库 ${catalogStats.total} 条` : ''} · 本次已选 {selectedRows.length} 条
            {selectedRows.length > 0 && <>（共 {totalCartons} 箱 / {totalCount} 件）</>}
            </small>
          </span>
        </div>
        <div className="packing-hero-actions">
          <button className="btn btn-outline" onClick={() => { setImportOpen(true); setImportSearch(''); setImportCategory(''); }}>
            新增产品
          </button>
          <button className="btn packing-export-btn" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {msg && <div className="alert alert-success" style={{ marginBottom: 12 }}>{msg}</div>}
      {catalogStats && (catalogStats.noSku > 0 || catalogStats.duplicates > 0) && (
        <div className="alert" style={{ marginBottom: 12, borderLeft: '4px solid #faad14', background: '#fffbe6' }}>
          已同步全部可用 SKU；另有 {catalogStats.noSku} 条产品缺少 SKU
          {catalogStats.duplicates > 0 ? `，${catalogStats.duplicates} 条 SKU 重复` : ''}，无法作为独立装箱产品。
        </div>
      )}

      {/* URL import 缺失提示 */}
      {importMissing.length > 0 && (
        <div className="alert" style={{ marginBottom: 12, borderLeft: '4px solid #faad14', background: '#fffbe6' }}>
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
        </div>
      )}

      <div className="packing-workspace">
      <div className="packing-library-column">
      {/* 装箱单库 */}
      <div className="card packing-library-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
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
          <div style={{ textAlign: 'center', color: '#888', padding: 24 }}>
            暂无产品，点击右上角「从选品库导入」添加。
          </div>
        ) : (
          <div className="packing-table-scroll">
            <table className="product-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #ddd', textAlign: 'left' }}>
                  <th style={{ padding: '8px 10px', width: 40 }}></th>
                  <th style={{ padding: '8px 10px', width: 72 }}>图片</th>
                  <th style={{ padding: '8px 10px' }}>SKU</th>
                  <th style={{ padding: '8px 10px' }}>产品名称</th>
                  <th style={{ padding: '8px 10px', width: 230 }}>产品属性</th>
                  <th style={{ padding: '8px 10px', width: 190 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredProducts.map(p => (
                  <tr key={p.sku} className={p.variant_group ? (p.is_primary_variant ? 'packing-variant-primary' : 'packing-variant-branch') : ''} style={{ borderBottom: '1px solid #eee' }}>
                    <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                      <input
                        type="checkbox"
                        checked={selected.has(p.sku)}
                        onChange={() => toggleSelect(p.sku)}
                        style={{ width: 'auto', accentColor: 'var(--color-primary, #1677ff)' }}
                      />
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <div style={{ width: 56, height: 56, border: '1px solid #eee', borderRadius: 4, overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <PackingImage filename={p.image_file} fallbackUrl={p.source_image_url} alt={p.sku} />
                      </div>
                    </td>
                    <td style={{ padding: '8px 10px', fontWeight: 600 }}>
                      {p.variant_group && <span className={p.is_primary_variant ? 'variant-role primary' : 'variant-role'}>{p.is_primary_variant ? '主' : '分支'}</span>}
                      {p.sku}
                      {p.variant_group && <span className="packing-spec-box">{packingSpec(p, products)}</span>}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <strong className="packing-product-name">{p.name_zh || p.name_en || '-'}</strong>
                      {p.name_en && p.name_en !== p.name_zh && <small className="packing-product-en">{p.name_en}</small>}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <div className="packing-attribute-list">
                        <span>{p.unit || '个'}</span>
                        {p.weight && <span>{p.weight} kg</span>}
                        {p.material && <span>{p.material}</span>}
                        {p.electric && <span>带电 {p.electric}</span>}
                        {p.magnetic && <span>含磁 {p.magnetic}</span>}
                      </div>
                    </td>
                    <td className="packing-row-actions" style={{ padding: '8px 10px' }}>
                      <button className="btn btn-sm" onClick={() => openEdit(p)} style={{ marginRight: 4 }}>编辑</button>
                      <button
                        className="btn btn-sm"
                        onClick={() => fileInputRefs.current[p.sku]?.click()}
                        style={{ marginRight: 4 }}
                      >
                        {p.image_file || p.source_image_url ? '换图' : '传图'}
                      </button>
                      {p.image_file && (
                        <button className="btn btn-sm" onClick={() => clearImage(p.sku)} style={{ marginRight: 4 }}>删图</button>
                      )}
                      <button className="btn btn-sm btn-danger" onClick={() => setDeleteSku(p.sku)}>删除</button>
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
                ))}
              </tbody>
            </table>
          </div>
        )}
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
          <div style={{ textAlign: 'center', color: '#888', padding: 24 }}>
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
                        <div><strong>{p.name_zh || p.name_en || '-'}</strong><small>{p.sku}</small></div>
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
        <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn packing-export-btn packing-export-wide" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
          <span style={{ fontSize: 13, color: '#888' }}>将导出 {selectedRows.length} 行装箱单数据（带产品图）</span>
        </div>
      </div>
      </aside>
      </div>

      {/* 编辑弹层 */}
      {editSku && (
        <div className="packing-modal-mask">
          <div className="packing-modal">
            <h3 style={{ margin: '0 0 12px' }}>编辑产品（{editSku}）</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px' }}>
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
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <button className="btn" onClick={() => setEditSku(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveEdit}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteSku && (
        <div className="packing-modal-mask">
          <div className="packing-modal" style={{ maxWidth: 420 }}>
            <h3 style={{ margin: '0 0 12px' }}>确认删除</h3>
            <p style={{ margin: '0 0 20px', color: '#555' }}>确定从装箱单库删除「{deleteSku}」吗？此操作不可恢复。</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
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
            <p style={{ margin: '0 0 12px', fontSize: 13, color: '#888' }}>
              勾选选品产品后导入装箱单库（SKU 已存在则自动跳过，不会覆盖已有编辑；图片自动抓取转存）。
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(160px, 240px)', gap: 8, marginBottom: 10 }}>
              <input
                type="text"
                placeholder="搜索 SKU / 中文品名 / 英文品名 / TSIN / 类目"
                value={importSearch}
                onChange={e => setImportSearch(e.target.value)}
                style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid #ccc', width: '100%', boxSizing: 'border-box' }}
                autoFocus
              />
              <select value={importCategory} onChange={e => setImportCategory(e.target.value)}>
                <option value="">全部类目（{importCategories.length}）</option>
                {importCategories.map(category => <option key={category} value={category}>{category}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 13, color: '#666' }}>
              <span>产品库 {importResults.length} 条 · 当前结果 {filteredImportResults.length} 条 · 可导入 {selectableImportResults.length} 条</span>
              <button className="btn btn-outline" style={{ marginLeft: 'auto', padding: '3px 9px' }} disabled={selectableImportResults.length === 0} onClick={() => {
                setImportSelected(prev => new Set([...prev, ...selectableImportResults.map(p => String(p.id))]));
              }}>选择当前全部</button>
            </div>
            {importSearching && <div style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>正在加载完整产品库...</div>}
            {!importSearching && filteredImportResults.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#888', padding: 20 }}>未找到匹配的选品产品</div>
            ) : (
              <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid #eee', borderRadius: 4 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #ddd', textAlign: 'left' }}>
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
                        <tr key={pid} style={{ borderBottom: '1px solid #eee' }}>
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
              <span style={{ fontSize: 13, color: '#888', marginRight: 'auto', alignSelf: 'center' }}>
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
    </div>
  );
}
