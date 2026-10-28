import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-shell';
import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult, ProcurementRecord, RecommendationResult } from './types';
import { recommendProducts } from './utils/productRecommend';

/** 在系统默认浏览器中打开外部 URL，Tauri 中 target="_blank" 不生效 */
export const openUrl = (url: string): void => {
  if (!url || url === '#') return;
  const isTauri =
    typeof window !== 'undefined' && !!(window as any).__TAURI_INTERNALS__;
  if (isTauri) {
    open(url).catch(() => {});
  } else {
    // 纯浏览器（网页版 GitHub Pages）无 Tauri 桥接，open() 为 undefined 会被吞错，
    // 改用 window.open 新标签打开，避免点击无反应
    window.open(url, '_blank', 'noopener,noreferrer');
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
    customSku: params?.custom_sku === '1' ? true : null,
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

export const restoreProduct = (id: string): Promise<string> =>
  invoke('restore_product', { id }) as Promise<string>;

export const refreshPrice = (id: string, _source?: 'official' | 'seller' | 'details'): Promise<Record<string, unknown>> =>
  invoke('refresh_price', { id }) as Promise<Record<string, unknown>>;

/**
 * 详情页推荐（同类商品 / 配套周边）。本地数据源没有服务端，就在前端按同一套口径算：
 * 拉一次全量商品 + 采购记录，交给 utils/productRecommend。
 * 拉取失败不抛——详情页主体比推荐重要，推荐挂了不该把整页弄红。
 */
export const getRecommendations = async (
  id: string,
  options?: { similarLimit?: number; complementLimit?: number },
): Promise<RecommendationResult> => {
  const [anchor, pool, records] = await Promise.all([
    getProduct(id),
    getProducts(),
    listProcurementRecords().catch(() => [] as ProcurementRecord[]),
  ]);
  return recommendProducts(anchor, pool, records, {
    similarLimit: options?.similarLimit,
    complementLimit: options?.complementLimit,
  });
};

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

// Settings
export const getSettings = (): Promise<Record<string, string>> =>
  invoke('get_settings') as Promise<Record<string, string>>;

export const updateSettings = (data: Record<string, string>): Promise<string> =>
  invoke('update_settings', { data }) as Promise<string>;

// Translation
export const translateProductName = (text: string): Promise<{ chinese_name: string }> =>
  invoke('translate_product_name', { text }) as Promise<{ chinese_name: string }>;

// Procurement Records
export const listProcurementRecords = (): Promise<ProcurementRecord[]> =>
  invoke('list_procurement_records') as Promise<ProcurementRecord[]>;

export const createProcurementRecord = (data: Partial<ProcurementRecord>): Promise<ProcurementRecord> =>
  invoke('create_procurement_record', { data }) as Promise<ProcurementRecord>;

export const updateProcurementRecord = (id: string, data: Partial<ProcurementRecord>): Promise<ProcurementRecord> =>
  invoke('update_procurement_record', { id, data }) as Promise<ProcurementRecord>;

export const deleteProcurementRecord = (id: string): Promise<string> =>
  invoke('delete_procurement_record', { id }) as Promise<string>;

export const searchProductsByNo = async (keyword: string): Promise<Product[]> => {
  const no = parseInt(keyword, 10);
  // 与 remoteApi 版保持一致：非法输入与"查无此商品"都返回空数组，
  // 避免把 null 包成 [null] 后让调用方渲染 p.product_no 时白屏。
  if (!Number.isFinite(no) || no <= 0) return [];
  const result = await invoke('search_products_by_no', { productNo: no });
  return result ? [result as Product] : [];
};

/** 本地模式：Tauri WebView 请求特征被 Takealot CDN 放行，直接返回原图地址 */
// 本地/桌面模式图片直连，没有代理也就没有缩放；保留 width 参数是为了两套
// API 签名一致（调用点不区分数据源）。
export const getImageUrl = (url: string, _width?: number): string => url;
