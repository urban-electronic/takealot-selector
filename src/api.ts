import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-shell';
import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult } from './types';
export const IMAGE_PROXY_BASE = '';

/** 在系统默认浏览器中打开外部 URL，Tauri 中 target="_blank" 不生效 */
export const openUrl = (url: string): void => {
  if (url && url !== '#') {
    open(url).catch(() => {});
  }
};

// Products
export const getProducts = (params?: Record<string, string>): Promise<Product[]> =>
  invoke('get_products', {
    selectionStatus: params?.selection_status ?? null,
    feeCategory: params?.fee_category ?? null,
    shippingMethod: params?.shipping_method ? params.shipping_method.split(',').filter(Boolean) : null,
    linkStatus: params?.link_status ? params.link_status.split(',').filter(Boolean) : null,
    minMargin: params?.min_margin ? Number(params.min_margin) : null,
    maxMargin: params?.max_margin ? Number(params.max_margin) : null,
    search: params?.search ?? null,
    sortBy: params?.sort_by ?? null,
    sortOrder: params?.sort_order ?? null,
  }) as Promise<Product[]>;

export const getProduct = (id: string): Promise<Product> =>
  invoke('get_product', { id }) as Promise<Product>;

export const createProduct = (data: Partial<Product>): Promise<Product> =>
  invoke('create_product', { data }) as Promise<Product>;

export const updateProduct = (id: string, data: Partial<Product>): Promise<Product> =>
  invoke('update_product', { id, data }) as Promise<Product>;

export const deleteProduct = (id: string): Promise<string> =>
  invoke('delete_product', { id }) as Promise<string>;

export const refreshPrice = (id: string): Promise<Record<string, unknown>> =>
  invoke('refresh_price', { id }) as Promise<Record<string, unknown>>;

// Scraper - uses system curl (Apple TLS) via Rust backend to bypass Cloudflare
export const scrapeTakealot = (productUrl: string): Promise<ScrapeResult> =>
  invoke('scrape_takealot', { productUrl }) as Promise<ScrapeResult>;

// Dashboard
export const getDashboard = (): Promise<DashboardStats> =>
  invoke('get_dashboard') as Promise<DashboardStats>;

// Fee Categories
export const getFeeCategories = (): Promise<FeeCategory[]> =>
  invoke('get_fee_categories') as Promise<FeeCategory[]>;

export const updateFeeCategory = (id: string, data: Partial<FeeCategory>): Promise<FeeCategory> =>
  invoke('update_fee_category', { id, data }) as Promise<FeeCategory>;

// Fee Mapping Rules
export const getFeeMappingRules = (): Promise<FeeMappingRule[]> =>
  invoke('get_fee_mapping_rules') as Promise<FeeMappingRule[]>;

export const createFeeMappingRule = (data: Partial<FeeMappingRule>): Promise<FeeMappingRule> =>
  invoke('create_fee_mapping_rule', { data }) as Promise<FeeMappingRule>;

export const updateFeeMappingRule = (id: string, data: Partial<FeeMappingRule>): Promise<FeeMappingRule> =>
  invoke('update_fee_mapping_rule', { id, data }) as Promise<FeeMappingRule>;

// Settings
export const getSettings = (): Promise<Record<string, string>> =>
  invoke('get_settings') as Promise<Record<string, string>>;

export const updateSettings = (data: Record<string, string>): Promise<string> =>
  invoke('update_settings', { data }) as Promise<string>;

// Translation
export const translateProductName = (text: string): Promise<{ chinese_name: string }> =>
  invoke('translate_product_name', { text }) as Promise<{ chinese_name: string }>;
