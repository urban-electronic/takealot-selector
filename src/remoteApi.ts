import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult, ProcurementRecord, PackingProduct, PackingExportPayload, InventoryRow, InventoryAdjustment, ShipmentRecord, Store, StoreInventoryOverview, OperationLog } from './types';

// 远程模式下写操作同步到本地 DB
let _tauriInvoke: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null;
const getInvoke = async () => {
  if (_tauriInvoke) return _tauriInvoke;
  try {
    const mod = await import('@tauri-apps/api/core');
    _tauriInvoke = mod.invoke;
  } catch {
    _tauriInvoke = () => Promise.reject(new Error('not in Tauri context'));
  }
  return _tauriInvoke;
};

/** 静默同步写操作到本地 DB（失败时仅打印日志，不影响远程调用结果） */
const syncToLocal = async (cmd: string, args: Record<string, unknown>): Promise<void> => {
  try {
    const invoke = await getInvoke();
    await invoke(cmd, args);
  } catch (e) {
    console.warn(`[syncToLocal] ${cmd} 失败:`, e);
  }
};

const getBaseUrl = (): string => {
  try {
    return localStorage.getItem('api_base_url') || 'https://takealot-selector-production.up.railway.app';
  } catch {
    return 'https://takealot-selector-production.up.railway.app';
  }
};

const getApiKey = (): string => {
  try {
    return localStorage.getItem('api_key') || '';
  } catch {
    return '';
  }
};

export const getActiveStoreId = (): string => {
  try { return localStorage.getItem('active_store_id') || 'default-store'; }
  catch { return 'default-store'; }
};

export const setActiveStoreId = (storeId: string): void => {
  try { localStorage.setItem('active_store_id', storeId); } catch { /* ignore */ }
};

/** Store-scoped browser state prevents drafts and selections leaking across shops. */
export const getStoreStorageKey = (baseKey: string): string => `${baseKey}:${getActiveStoreId()}`;

export const readStoreStorage = (baseKey: string): string | null => {
  try {
    const scopedKey = getStoreStorageKey(baseKey);
    const scoped = localStorage.getItem(scopedKey);
    if (scoped !== null) return scoped;
    // One-time compatibility migration for the original single-store browser data.
    if (getActiveStoreId() === 'default-store') {
      const legacy = localStorage.getItem(baseKey);
      if (legacy !== null) {
        localStorage.setItem(scopedKey, legacy);
        return legacy;
      }
    }
    return null;
  } catch { return null; }
};

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const baseUrl = getBaseUrl();
  const url = `${baseUrl}${path}`;
  const res = await fetch(url, {
    ...options,
    signal: options.signal ?? AbortSignal.timeout(90000),
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': getApiKey(),
      'X-Store-ID': getActiveStoreId(),
      ...options.headers,
    },
  }).catch((e: unknown) => {
    if (e instanceof DOMException && e.name === 'TimeoutError') throw new Error('请求等待超时，请稍后刷新列表确认结果，避免重复提交。');
    throw e;
  });
  if (!res.ok) {
    // 优先透传后端 detail，避免丢失真实错误原因
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        if (typeof body.detail === 'string') {
          detail = body.detail;
        } else if (Array.isArray(body.detail)) {
          detail = body.detail.map((item: any) => {
            const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : '';
            if (item?.type === 'missing') return `缺少必要参数${field ? `：${field}` : ''}`;
            return item?.msg || '提交的数据格式不正确';
          }).join('；');
        } else {
          detail = '服务器无法识别提交的数据，请刷新页面后重试';
        }
      }
    } catch {
      // 响应体非 JSON 时保持默认错误
    }
    throw new Error(detail);
  }
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  return JSON.parse(text) as T;
};

export const getStores = (): Promise<Store[]> => request<Store[]>('/api/stores');
export const createStore = (data: Partial<Store> & { name: string }): Promise<Store> =>
  request<Store>('/api/stores', { method: 'POST', body: JSON.stringify(data) });
export const updateStore = (id: string, data: Partial<Store>): Promise<Store> =>
  request<Store>(`/api/stores/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(data) });
export const getStoresInventoryOverview = (): Promise<StoreInventoryOverview[]> =>
  request<StoreInventoryOverview[]>('/api/inventory/stores-overview');

export const openUrl = (url: string): void => {
  if (url && url !== '#') {
    window.open(url, '_blank');
  }
};

// Products
export interface SkuAuditResult {
  source: string; total: number; verified: number; missing: number; mismatch: number; unmatched: number;
  issues: { product_id: string; product_no: number; status: string; current_skus: string[]; reference_skus: string[] }[];
}
export const auditProductSkus = (): Promise<SkuAuditResult> => request<SkuAuditResult>('/api/products/sku-audit');

export const getProducts = (params?: Record<string, string>): Promise<Product[]> => {
  const searchParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        searchParams.set(key, value);
      }
    });
  }
  const qs = searchParams.toString();
  return request<Product[]>(`/api/products${qs ? `?${qs}` : ''}`);
};

export const getProduct = (id: string): Promise<Product> =>
  request<Product>(`/api/products/${encodeURIComponent(id)}`);

export const createProduct = async (data: Partial<Product>): Promise<Product> => {
  const result = await request<Product>('/api/products', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  syncToLocal('sync_product', { data: result as unknown as Record<string, unknown> });
  return result;
};

export const updateProduct = async (id: string, data: Partial<Product>): Promise<Product> => {
  const result = await request<Product>(`/api/products/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  syncToLocal('sync_product', { data: result as unknown as Record<string, unknown> });
  return result;
};

export const deleteProduct = async (id: string): Promise<string> => {
  return request<string>(`/api/products/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
};

export const restoreProduct = (id: string): Promise<string> =>
  request<string>(`/api/products/${encodeURIComponent(id)}/restore`, { method: 'POST' });

export const refreshPrice = (id: string, source: 'official' | 'seller' | 'details' = 'official'): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/products/${encodeURIComponent(id)}/refresh-price?source=${source}`, {
    method: 'POST',
  });

// Scraper - still uses local invoke via Tauri (needs Safari WebView)
export const scrapeTakealot = (productUrl: string): Promise<ScrapeResult> =>
  request<ScrapeResult>('/api/products/scrape-takealot', {
    method: 'POST',
    body: JSON.stringify({ url: productUrl }),
  });

// Dashboard
export const getDashboard = (): Promise<DashboardStats> =>
  request<DashboardStats>('/api/products/stats/dashboard');

// Fee Categories
export const getFeeCategories = (): Promise<FeeCategory[]> =>
  request<FeeCategory[]>('/api/fee-categories');

export const updateFeeCategory = (id: string, data: Partial<FeeCategory>): Promise<FeeCategory> =>
  request<FeeCategory>(`/api/fee-categories/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });

// Fee Mapping Rules
export const getFeeMappingRules = (): Promise<FeeMappingRule[]> =>
  request<FeeMappingRule[]>('/api/fee-mapping-rules');

export const createFeeMappingRule = (data: Partial<FeeMappingRule>): Promise<FeeMappingRule> =>
  request<FeeMappingRule>('/api/fee-mapping-rules', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const updateFeeMappingRule = (id: string, data: Partial<FeeMappingRule>): Promise<FeeMappingRule> =>
  request<FeeMappingRule>(`/api/fee-mapping-rules/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });

// Settings
export const getSettings = (): Promise<Record<string, string>> =>
  request<Record<string, string>>('/api/settings');

export const updateSettings = (data: Record<string, string>): Promise<string> =>
  request<string>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(data),
  });

// Translation
export const translateProductName = (text: string): Promise<{ chinese_name: string }> =>
  request<{ chinese_name: string }>('/api/translate', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });

// Procurement Records
export const listProcurementRecords = (): Promise<ProcurementRecord[]> =>
  request<ProcurementRecord[]>('/api/procurement');

export const createProcurementRecord = async (data: Partial<ProcurementRecord>): Promise<ProcurementRecord> => {
  const result = await request<ProcurementRecord>('/api/procurement', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  syncToLocal('create_procurement_record', { data: result as unknown as Record<string, unknown> });
  return result;
};

export const updateProcurementRecord = async (id: string, data: Partial<ProcurementRecord>): Promise<ProcurementRecord> => {
  const result = await request<ProcurementRecord>(`/api/procurement/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  syncToLocal('update_procurement_record', { id, data: result as unknown as Record<string, unknown> });
  return result;
};

export const deleteProcurementRecord = async (id: string): Promise<string> => {
  const result = await request<string>(`/api/procurement/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  syncToLocal('delete_procurement_record', { id });
  return result;
};

export const searchProductsByNo = async (keyword: string): Promise<Product[]> => {
  const no = parseInt(keyword, 10);
  if (!no) return [];
  return request<Product[]>(`/api/procurement/by-no/${no}`);
};

/**
 * 远程模式图片地址：Takealot CDN 对浏览器/非白名单请求一律 403 防盗链，
 * 统一走后端 /api/image-proxy 代理（服务端带 Referer+UA 已验证可正常抓取）。
 */
export const getImageUrl = (url: string): string => {
  if (!url) return url;
  if (!/^https?:\/\//i.test(url)) return url;
  const baseUrl = getBaseUrl();
  // <img> 标签无法带 X-API-Key 头，图片代理改用 query token 鉴权
  const key = (localStorage.getItem('api_key') || '').trim();
  const sep = key ? `&token=${encodeURIComponent(key)}` : '';
  return `${baseUrl}/api/image-proxy?url=${encodeURIComponent(url)}${sep}`;
};

// ---- Packing（装箱单，云端独立鲲鹏库） ----

export interface PackingUpsertInput {
  sku: string;
  name_zh?: string;
  name_en?: string;
  unit?: string;
  weight?: string;
  material?: string;
  brand?: string;
  battery?: string;
  electric?: string;
  magnetic?: string;
  default_count?: number | null;
  template_row?: number | null;
}

export const getPackingProducts = (): Promise<PackingProduct[]> =>
  request<PackingProduct[]>('/api/packing/products');

export const syncPackingProductsFromCatalog = (): Promise<{
  ok: boolean;
  total_products: number;
  packing_products: number;
  imported: number;
  skipped_no_sku: number;
  duplicate_sku: number;
}> => request('/api/packing/sync-from-products', { method: 'POST', body: '{}' });

export const getInventory = (): Promise<InventoryRow[]> =>
  request<InventoryRow[]>('/api/inventory');

export const createInventoryAdjustment = (data: {
  sku: string; quantity_delta: number; reason: string; notes?: string; occurred_at?: string;
}): Promise<{ ok: boolean; id: string; available: number }> =>
  request('/api/inventory/adjustments', { method: 'POST', body: JSON.stringify(data) });

export const getInventoryAdjustments = (): Promise<InventoryAdjustment[]> =>
  request<InventoryAdjustment[]>('/api/inventory/adjustments');

export const getOperationLogs = (): Promise<OperationLog[]> =>
  request<OperationLog[]>('/api/activity?limit=200');

export const reverseInventoryAdjustment = (id: string): Promise<{ ok: boolean }> =>
  request(`/api/inventory/adjustments/${encodeURIComponent(id)}/reverse`, { method: 'POST', body: '{}' });

export const getShipments = (): Promise<ShipmentRecord[]> =>
  request<ShipmentRecord[]>('/api/inventory/shipments');

export const voidShipment = (id: string, reason: string): Promise<{ ok: boolean }> =>
  request(`/api/inventory/shipments/${encodeURIComponent(id)}/void`, {
    method: 'POST', body: JSON.stringify({ reason }),
  });

export const exportInboundTemplate = async (shipmentIds: string[]): Promise<Blob> => {
  const res = await fetch(`${getBaseUrl()}/api/inventory/shipments/inbound-template`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': getApiKey(), 'X-Store-ID': getActiveStoreId() },
    body: JSON.stringify({ shipment_ids: shipmentIds }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const body = await res.json();
      detail = body?.detail || body?.error || detail;
    } catch { /* 保留默认错误 */ }
    throw new Error(detail);
  }
  return await res.blob();
};

export const upsertPackingProduct = async (data: PackingUpsertInput): Promise<{ ok: boolean }> => {
  const result = await request<{ ok: boolean }>('/api/packing/product', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return result;
};

export const deletePackingProduct = (sku: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>('/api/packing/delete', {
    method: 'POST',
    body: JSON.stringify({ sku }),
  });

export const uploadPackingImage = (sku: string, dataBase64: string, remove = false): Promise<{ ok: boolean; filename?: string }> =>
  request<{ ok: boolean; filename?: string }>('/api/packing/image', {
    method: 'POST',
    body: JSON.stringify({ sku, data: dataBase64, remove }),
  });

export const importFromProducts = (productIds: string[]): Promise<{ ok: boolean; imported: number; skipped: number; failed: Array<{ sku: string; reason: string }> }> =>
  request<{ ok: boolean; imported: number; skipped: number; failed: Array<{ sku: string; reason: string }> }>('/api/packing/import-from-products', {
    method: 'POST',
    body: JSON.stringify({ product_ids: productIds }),
  });

/** 装箱单库"待补图"产品按 SKU 从选品库匹配图片（后端抓取转存 SHA256） */
export const syncPackingImagesFromProducts = (limit = 100): Promise<{ ok: boolean; updated: number; total_pending: number; failed: Array<{ sku: string; reason: string }> }> =>
  request<{ ok: boolean; updated: number; total_pending: number; failed: Array<{ sku: string; reason: string }> }>('/api/packing/sync-images-from-products', {
    method: 'POST',
    body: JSON.stringify({ limit }),
  });

export const syncPackingVariantsFromTakealot = (limit = 20): Promise<{ ok: boolean; groups_checked: number; matched: number; images: number; unresolved: Array<{ product_id: string; skus: string[]; reason: string }> }> =>
  request('/api/packing/sync-variants-from-takealot', {
    method: 'POST', body: JSON.stringify({ limit }),
  });

/** 导出装箱单 Excel：返回原始 blob（不走 request 的 JSON 解析） */
export const exportPacking = async (payload: PackingExportPayload): Promise<Blob> => {
  const res = await fetch(`${getBaseUrl()}/api/packing/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': getApiKey(),
      'X-Store-ID': getActiveStoreId(),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      } else if (body && body.error) {
        detail = body.error;
      }
    } catch {
      // 响应体非 JSON 时保持默认错误
    }
    throw new Error(detail);
  }
  return await res.blob();
};

/** 装箱单图片访问（img 标签无法带 X-API-Key header，改用 fetch blob） */
export const fetchPackingImageBlob = async (filename: string): Promise<Blob> => {
  const res = await fetch(`${getBaseUrl()}/api/packing/images/${encodeURIComponent(filename)}`, {
    headers: { 'X-API-Key': getApiKey(), 'X-Store-ID': getActiveStoreId() },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return await res.blob();
};

export interface SellerAssistItem {
  id: string; product_no: number; name: string; token: string;
  changes: { field: string; label: string; value: string | number }[];
  differences: string[]; warnings: string[]; manual: string[];
}
export interface SellerAssistResult { items: SellerAssistItem[]; fillable: number }
export const previewSellerAssist = (ids: string[]) => request<SellerAssistResult>('/api/products/seller-assist/preview', { method: 'POST', body: JSON.stringify({ ids }) });
export const applySellerAssist = (items: SellerAssistItem[]) => request<SellerAssistResult>('/api/products/seller-assist/apply', { method: 'POST', body: JSON.stringify({ ids: items.map(i => i.id), tokens: Object.fromEntries(items.map(i => [i.id, i.token])) }) });
