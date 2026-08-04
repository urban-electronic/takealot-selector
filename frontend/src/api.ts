import type { Product, ScrapeResult, FeeCategory, FeeMappingRule, DashboardStats } from './types';
import { API_BASE } from './config';

const BASE = `${API_BASE}/api`;

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || '请求失败');
  }
  return res.json();
}

// Products
export const getProducts = (params?: Record<string, string>) => {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return request<Product[]>(`/products${qs}`);
};

export const getProduct = (id: string) => request<Product>(`/products/${id}`);

export const createProduct = (data: Partial<Product>) =>
  request<Product>('/products', { method: 'POST', body: JSON.stringify(data) });

export const updateProduct = (id: string, data: Partial<Product>) =>
  request<Product>(`/products/${id}`, { method: 'PATCH', body: JSON.stringify(data) });

export const deleteProduct = (id: string) =>
  request<{ detail: string }>(`/products/${id}`, { method: 'DELETE' });

export const refreshPrice = (id: string) =>
  request<any>(`/products/${id}/refresh-price`, { method: 'POST' });

// Scraper
export const scrapeTakealot = (url: string) =>
  request<ScrapeResult>('/products/scrape-takealot', { method: 'POST', body: JSON.stringify({ url }) });

// Dashboard
export const getDashboard = () => request<DashboardStats>('/products/stats/dashboard');

// Fee Categories
export const getFeeCategories = () => request<FeeCategory[]>('/fee-categories');

export const updateFeeCategory = (id: string, data: Partial<FeeCategory>) =>
  request<FeeCategory>(`/fee-categories/${id}`, { method: 'PATCH', body: JSON.stringify(data) });

// Fee Mapping Rules
export const getFeeMappingRules = () => request<FeeMappingRule[]>('/fee-mapping-rules');

export const createFeeMappingRule = (data: Partial<FeeMappingRule>) =>
  request<FeeMappingRule>('/fee-mapping-rules', { method: 'POST', body: JSON.stringify(data) });

export const updateFeeMappingRule = (id: string, data: Partial<FeeMappingRule>) =>
  request<FeeMappingRule>(`/fee-mapping-rules/${id}`, { method: 'PATCH', body: JSON.stringify(data) });

// Settings
export const getSettings = () => request<Record<string, string>>('/settings');

export const updateSettings = (data: Record<string, string>) =>
  request<{ detail: string }>('/settings', { method: 'PATCH', body: JSON.stringify(data) });

// Translation
export const translateProductName = (text: string) =>
  request<{ chinese_name: string }>('/products/translate', { method: 'POST', body: JSON.stringify({ text }) });
