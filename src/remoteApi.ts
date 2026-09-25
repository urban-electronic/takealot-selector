import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult, ProcurementRecord, PackingProduct, PackingExportPayload } from './types';

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

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const baseUrl = getBaseUrl();
  const url = `${baseUrl}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': getApiKey(),
      ...options.headers,
    },
  });
  if (!res.ok) {
    // 优先透传后端 detail，避免丢失真实错误原因
    let detail = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
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

export const openUrl = (url: string): void => {
  if (url && url !== '#') {
    window.open(url, '_blank');
  }
};

// Products
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
  // 先获取产品信息以拿到 takealot_url
  const product = await getProduct(id);
  const result = await request<string>(`/api/products/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (product.takealot_url) {
    syncToLocal('sync_delete_product', { takealotUrl: product.takealot_url });
  }
  return result;
};

export const refreshPrice = (id: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/products/${encodeURIComponent(id)}/refresh-price`, {
    method: 'POST',
  });

// Scraper - still uses local invoke via Tauri (needs Safari WebView)
export const scrapeTakealot = (productUrl: string): Promise<ScrapeResult> =>
  request<ScrapeResult>('/api/products/scrape-takealot', {
    method: 'POST',
    body: JSON.stringify({ productUrl }),
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
  return `${baseUrl}/api/image-proxy?url=${encodeURIComponent(url)}`;
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

/** 导出装箱单 Excel：返回原始 blob（不走 request 的 JSON 解析） */
export const exportPacking = async (payload: PackingExportPayload): Promise<Blob> => {
  const res = await fetch(`${getBaseUrl()}/api/packing/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': getApiKey(),
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
    headers: { 'X-API-Key': getApiKey() },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return await res.blob();
};
