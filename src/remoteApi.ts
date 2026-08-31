import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult, ProcurementRecord } from './types';

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

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const baseUrl = getBaseUrl();
  const url = `${baseUrl}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
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
