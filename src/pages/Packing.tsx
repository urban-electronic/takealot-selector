import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import {
  getPackingProducts,
  upsertPackingProduct,
  deletePackingProduct,
  uploadPackingImage,
  importFromProducts,
  exportPacking,
  fetchPackingImageBlob,
} from '../remoteApi';
import type { PackingProduct, Product, PackingExportPayload } from '../types';

// 装箱单图片：img 标签无法带 X-API-Key header，改用 fetch blob + objectURL（带全局缓存）
const blobUrlCache = new Map<string, string>();

function PackingImage({ filename, alt }: { filename: string; alt?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ok' | 'empty' | 'failed'>('loading');

  useEffect(() => {
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
        if (!cancelled) setState('failed');
      });
    return () => {
      cancelled = true;
    };
  }, [filename]);

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

export default function Packing() {
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();

  // 装箱单库
  const [products, setProducts] = useState<PackingProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');

  // 产品库筛选
  const [searchText, setSearchText] = useState('');
  const [fltElectric, setFltElectric] = useState('');
  const [fltMagnetic, setFltMagnetic] = useState('');
  const [fltMaterial, setFltMaterial] = useState('');

  // 本次装箱单行
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lines, setLines] = useState<Record<string, { cartons: string; count: string }>>({});

  // 导出设置
  const [exportForm, setExportForm] = useState({ date: todayStr(), mark: '', shipping: '', address: '' });
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
  const [importResults, setImportResults] = useState<Product[]>([]);
  const [importSearching, setImportSearching] = useState(false);
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  // 文件输入
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const loadProducts = useCallback(async () => {
    try {
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

  // 从选品库导入弹层搜索（debounce）
  useEffect(() => {
    if (!importOpen) return;
    if (importSearch.trim().length < 1) {
      setImportResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      setImportSearching(true);
      try {
        const results = await api.getProducts({ search: importSearch.trim() });
        setImportResults(results || []);
      } catch {
        setImportResults([]);
      } finally {
        setImportSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [importSearch, importOpen]);

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
      setMsg('导出成功，请检查下载的 Excel');
    } catch (e: any) {
      setError(`导出失败：${typeof e === 'string' ? e : e.message || '未知错误'}`);
    } finally {
      setExporting(false);
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
    <div className="page">
      {/* 头部 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16 }}>
          <h1 style={{ margin: 0 }}>装箱单</h1>
          <span style={{ fontSize: 13, color: '#888' }}>
            装箱单库 {products.length} 条 · 本次已选 {selectedRows.length} 条
            {selectedRows.length > 0 && <>（共 {totalCartons} 箱 / {totalCount} 件）</>}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-outline" onClick={() => { setImportOpen(true); setImportSelected(new Set()); }}>
            从选品库导入
          </button>
          <button className="btn btn-primary" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {msg && <div className="alert alert-success" style={{ marginBottom: 12 }}>{msg}</div>}

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

      {/* 装箱单库 */}
      <div className="card" style={{ marginBottom: 20, padding: 16 }}>
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>装箱单产品库</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <input
            type="text"
            placeholder="搜索 SKU / 名称 / 材质..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid #ccc', width: 240 }}
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
          <div style={{ overflowX: 'auto' }}>
            <table className="product-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #ddd', textAlign: 'left' }}>
                  <th style={{ padding: '8px 10px', width: 40 }}></th>
                  <th style={{ padding: '8px 10px', width: 72 }}>图片</th>
                  <th style={{ padding: '8px 10px' }}>SKU</th>
                  <th style={{ padding: '8px 10px' }}>中文名</th>
                  <th style={{ padding: '8px 10px' }}>英文名</th>
                  <th style={{ padding: '8px 10px', width: 56 }}>单位</th>
                  <th style={{ padding: '8px 10px', width: 64 }}>重量(kg)</th>
                  <th style={{ padding: '8px 10px', width: 72 }}>材质</th>
                  <th style={{ padding: '8px 10px', width: 56 }}>带电</th>
                  <th style={{ padding: '8px 10px', width: 56 }}>含磁</th>
                  <th style={{ padding: '8px 10px', width: 220 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredProducts.map(p => (
                  <tr key={p.sku} style={{ borderBottom: '1px solid #eee' }}>
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
                        <PackingImage filename={p.image_file} alt={p.sku} />
                      </div>
                    </td>
                    <td style={{ padding: '8px 10px', fontWeight: 600 }}>{p.sku}</td>
                    <td style={{ padding: '8px 10px' }}>{p.name_zh || '-'}</td>
                    <td style={{ padding: '8px 10px', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name_en || '-'}</td>
                    <td style={{ padding: '8px 10px' }}>{p.unit || '个'}</td>
                    <td style={{ padding: '8px 10px' }}>{p.weight || '-'}</td>
                    <td style={{ padding: '8px 10px' }}>{p.material || '-'}</td>
                    <td style={{ padding: '8px 10px' }}>{p.electric || '-'}</td>
                    <td style={{ padding: '8px 10px' }}>{p.magnetic || '-'}</td>
                    <td style={{ padding: '8px 10px' }}>
                      <button className="btn btn-sm" onClick={() => openEdit(p)} style={{ marginRight: 4 }}>编辑</button>
                      <button
                        className="btn btn-sm"
                        onClick={() => fileInputRefs.current[p.sku]?.click()}
                        style={{ marginRight: 4 }}
                      >
                        {p.image_file ? '换图' : '传图'}
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

      {/* 本次装箱单行 */}
      <div className="card" style={{ marginBottom: 20, padding: 16 }}>
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>本次装箱单行</h3>
        {selectedRows.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#888', padding: 24 }}>
            尚未选择产品，请在上方产品库勾选，或通过产品列表「加入装箱单」直达。
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #ddd', textAlign: 'left' }}>
                  <th style={{ padding: '8px 12px' }}>SKU</th>
                  <th style={{ padding: '8px 12px' }}>产品名称</th>
                  <th style={{ padding: '8px 12px', width: 120 }}>箱数</th>
                  <th style={{ padding: '8px 12px', width: 140 }}>每箱件数</th>
                  <th style={{ padding: '8px 12px', width: 100 }}>件数小计</th>
                  <th style={{ padding: '8px 12px', width: 80 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {selectedRows.map(p => {
                  const cartons = parseInt(lines[p.sku]?.cartons || '1', 10) || 1;
                  const perCount = parseInt(lines[p.sku]?.count || '', 10) || p.default_count || 0;
                  return (
                    <tr key={p.sku} style={{ borderBottom: '1px solid #eee' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 600 }}>{p.sku}</td>
                      <td style={{ padding: '8px 12px' }}>{p.name_zh || p.name_en || '-'}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          type="number"
                          min={1}
                          value={lines[p.sku]?.cartons ?? '1'}
                          onChange={e => setLine(p.sku, 'cartons', e.target.value)}
                          style={{ width: 80, padding: '4px 8px' }}
                        />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          type="number"
                          min={1}
                          placeholder={p.default_count ? `默认 ${p.default_count}` : '每箱件数'}
                          value={lines[p.sku]?.count ?? ''}
                          onChange={e => setLine(p.sku, 'count', e.target.value)}
                          style={{ width: 100, padding: '4px 8px' }}
                        />
                      </td>
                      <td style={{ padding: '8px 12px' }}>{perCount * cartons}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <button className="btn btn-sm btn-danger" onClick={() => toggleSelect(p.sku)}>移除</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 导出设置 */}
      <div className="card" style={{ padding: 16 }}>
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>导出设置</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', maxWidth: 720 }}>
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
          <button className="btn btn-primary" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
          <span style={{ fontSize: 13, color: '#888' }}>将导出 {selectedRows.length} 行装箱单数据（带产品图）</span>
        </div>
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
            <input
              type="text"
              placeholder="搜索选品产品（SKU / 名称）..."
              value={importSearch}
              onChange={e => setImportSearch(e.target.value)}
              style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid #ccc', width: '100%', boxSizing: 'border-box', marginBottom: 12 }}
              autoFocus
            />
            {importSearching && <div style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>搜索中...</div>}
            {importResults.length === 0 && importSearch.trim() ? (
              <div style={{ textAlign: 'center', color: '#888', padding: 20 }}>未找到匹配的选品产品</div>
            ) : (
              <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid #eee', borderRadius: 4 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #ddd', textAlign: 'left' }}>
                      <th style={{ padding: '6px 10px', width: 40 }}></th>
                      <th style={{ padding: '6px 10px' }}>SKU</th>
                      <th style={{ padding: '6px 10px' }}>产品名称</th>
                      <th style={{ padding: '6px 10px' }}>运输方式</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importResults.map(p => {
                      const pid = String(p.id);
                      const checked = importSelected.has(pid);
                      return (
                        <tr key={pid} style={{ borderBottom: '1px solid #eee' }}>
                          <td style={{ padding: '6px 10px', textAlign: 'center' }}>
                            <input
                              type="checkbox"
                              checked={checked}
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
                          <td style={{ padding: '6px 10px', fontWeight: 600 }}>{p.sku || '-'}</td>
                          <td style={{ padding: '6px 10px' }}>{p.chinese_product_name || p.product_name || '-'}</td>
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
