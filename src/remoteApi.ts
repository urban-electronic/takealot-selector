import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult } from './types';

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

export const createProduct = (data: Partial<Product>): Promise<Product> =>
  request<Product>('/api/products', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const updateProduct = (id: string, data: Partial<Product>): Promise<Product> =>
  request<Product>(`/api/products/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });

export const deleteProduct = (id: string): Promise<string> =>
  request<string>(`/api/products/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });

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
