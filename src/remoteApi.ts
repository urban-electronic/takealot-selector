import type { Product, FeeCategory, FeeMappingRule, DashboardStats, ScrapeResult, ProcurementRecord, PackingProduct, PackingExportPayload, PackingCartonRecord, InventoryRow, InventoryAdjustment, ShipmentRecord, Store, StoreInventoryOverview, OperationLog } from './types';

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
  // 用户在设置页填过地址就用它，这条永远优先。
  try {
    const saved = localStorage.getItem('api_base_url');
    if (saved) return saved;
  } catch {
    // localStorage 不可用（隐私模式等）时按未设置处理
  }
  // 网页版由后端同源提供（见 backend/main.py 末尾的静态托管）：用相对路径，
  // 请求自动打到当前域名。硬编码域名在这里是隐患——换域名、或者域名在某些
  // 网络下不通（GitHub Pages 就是这样）时，页面能打开但所有接口都连不上。
  //
  // 只认构建期开关，不靠 location 判断：Tauri 生产环境的 origin 是
  // http(s)://tauri.localhost，dev 时是 localhost:1420，用 hostname 猜容易误判，
  // 一旦判错桌面版就会去请求它自己那份资源，接口全 404。
  if (import.meta.env.VITE_API_SAME_ORIGIN === '1') return '';
  return 'https://takealot-selector-production.up.railway.app';
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
  if (res.status === 401) {
    // 网页版首次访问时后端要求 API Key，但界面上只会显示「加载失败」，
    // 用户根本不知道要去设置页填。这里发个全局信号，由 App 里的横幅接管引导。
    try { window.dispatchEvent(new Event('api-unauthorized')); } catch { /* 非浏览器环境 */ }
  }
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

export interface SellerApiStatus { configured: boolean; mode: 'product_read_only' }
export const getSellerApiStatus = (): Promise<SellerApiStatus> =>
  request<SellerApiStatus>('/api/integrations/takealot-seller/status');

// 本店在 Takealot 的卖家名：调价判断「Buybox 是不是自己在持位」的唯一依据。
// 只比价格会把对手同价持位误判成本店持位，方向一反就是赔钱。
export interface SellerIdentity {
  name: string;
  source: string;
  candidates: { name: string; skus: number }[];
}
export const getSellerIdentity = (): Promise<SellerIdentity> =>
  request<SellerIdentity>('/api/integrations/takealot-seller/identity');
export const setSellerIdentity = (name: string): Promise<{ name: string; source: string }> =>
  request<{ name: string; source: string }>('/api/integrations/takealot-seller/identity', {
    method: 'PUT',
    body: JSON.stringify({ name }),
  });

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

/** 「为什么找不到」：卖家后台 vs 本地库的三类差异。 */
export interface SellerCoverageProbe {
  tsin: string;
  verdict: 'matched' | 'sku_mismatch' | 'seller_only' | 'local_only' | 'nowhere';
  advice: string;
  seller: { found: boolean; plids: string[]; skus: string[]; title: string; status: string };
  local: {
    found: boolean;
    products: { product_no: number; name: string; sku: string; plid: string }[];
    skus: string[];
  };
}

export interface SellerCoverageResult {
  seller_offer_count: number;
  seller_plid_count: number;
  local_product_count: number;
  local_plid_count: number;
  matched_count: number;
  sku_mismatch_count: number;
  sku_mismatch: { product_id: string; product_no: number; name: string; local_sku: string[]; seller_sku: string[] }[];
  seller_only_count: number;
  seller_only: { plid: string; skus: string[]; title: string; status: string }[];
  local_only_count: number;
  truncated: boolean;
  probes?: SellerCoverageProbe[];
}

export const getSellerCoverage = (tsin?: string): Promise<SellerCoverageResult> =>
  request<SellerCoverageResult>(`/api/products/seller-coverage${tsin ? `?tsin=${encodeURIComponent(tsin)}` : ''}`);

export const getOperationsProfile = (id: string): Promise<any> => request(`/api/operations/${id}`);
export const saveOperationsProfile = (id: string, profile: unknown): Promise<any> => request(`/api/operations/${id}`, { method: 'PUT', body: JSON.stringify(profile) });
export const getOperationsAlerts = (): Promise<any[]> => request('/api/operations/alerts');
export const getOperationsOverview = (): Promise<any> => request('/api/operations/overview');

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

// ---- 多市场参考价 ----
// 淘宝/天猫要登录态、中国官网已停摆，两者都拿不到稳定数据源，所以录入走批量接口；
// 官网直营市场（SG/MY）由后端每天自动重读一次（backend/services/market_price_sync.py）。
export interface MarketPriceCoverage {
  filled: number;
  stale: number;
}

export interface MarketPriceStatus {
  store_id: string;
  products: number;
  products_with_price: number;
  coverage: Record<string, MarketPriceCoverage>;
  last_sync: Record<string, unknown> | null;
  interval_hours: number;
  auto_disabled: boolean;
  due: boolean;
  auto_markets: string[];
  manual_only: Record<string, string>;
}

export interface MarketPriceBatchItem {
  sku?: string;
  product_id?: string;
  market: 'CN' | 'SG' | 'TB';
  /** null 表示清除该市场 */
  price?: number | null;
  src?: string;
  n?: number;
  url?: string;
}

export const getMarketPriceStatus = (): Promise<MarketPriceStatus> =>
  request<MarketPriceStatus>('/api/market-prices/status');

export const saveMarketPrices = (items: MarketPriceBatchItem[]): Promise<{ saved: number; products: number; results: Array<Record<string, unknown>> }> =>
  request('/api/market-prices/batch', { method: 'POST', body: JSON.stringify({ items }) });

export const syncMarketPrices = (): Promise<{ started: boolean; message: string }> =>
  request('/api/market-prices/sync', { method: 'POST' });

/**
 * 兰特汇率：`rates[cur]` = 1 单位该货币值多少兰特。
 *
 * 后端缓存 24 小时，拉不到上游时会降级返回旧缓存或内置兜底值，
 * 并用 `stale` / `fallback` 标记出来——前端据此在界面上写清汇率来源。
 */
export interface FxRatePayload {
  base: string;
  rates: Record<string, number>;
  at?: string | null;
  src?: string;
  fetched_at?: string | null;
  stale?: boolean;
  fallback?: boolean;
  error?: string;
}

export const getFxRates = (): Promise<FxRatePayload> =>
  request<FxRatePayload>('/api/market-prices/fx');

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

// 动词必须与后端一致：backend/api/category_routes.py 注册的是
// @router.patch('/fee-categories/{category_id}')，没有 PUT。用 PUT 打过去是
// 405 Method Not Allowed，而设置页的「启用/停用分类」「改成功率」都走这里
// （src/pages/Settings.tsx 的 handleToggleCategory / handleUpdateFeeRate），
// 网页版点一次就报一次错，Tauri 版走 api.ts 的 invoke 不受影响——所以只在
// 网页版复现，很容易被当成「偶发」。
export const updateFeeCategory = (id: string, data: Partial<FeeCategory>): Promise<FeeCategory> =>
  request<FeeCategory>(`/api/fee-categories/${encodeURIComponent(id)}`, {
    method: 'PATCH',
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

// Settings
export const getSettings = (): Promise<Record<string, string>> =>
  request<Record<string, string>>('/api/settings');

export const updateSettings = (data: Record<string, string>): Promise<string> =>
  request<string>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(data),
  });

// Translation
// 翻译接口挂在产品路由下（backend/api/product_routes.py 的 router 前缀是
// /api/products，路由是 @router.post('/translate')），完整路径是
// /api/products/translate。原来这里少了 /products 段，网页版必 404；
// 两个调用点（ProductCreate.tsx 的页面导入与 handleScrape）都把异常吞掉了，
// 于是表现为「自动翻译中文品名」在网页版从来不生效，也不报错。
export const translateProductName = (text: string): Promise<{ chinese_name: string }> =>
  request<{ chinese_name: string }>('/api/products/translate', {
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

export const reconcileShipmentManifest = (data: unknown, apply = false): Promise<any> =>
  request(`/api/inventory/reconciliation/${apply ? 'apply' : 'preview'}`, { method: 'POST', body: JSON.stringify(data) });

export const voidShipment = (id: string, reason: string): Promise<{ ok: boolean }> =>
  request(`/api/inventory/shipments/${encodeURIComponent(id)}/void`, {
    method: 'POST', body: JSON.stringify({ reason }),
  });

/**
 * 把本地表数据导入远端库。
 *
 * 走统一 `request()` 而不是裸 fetch：其余写操作都带 `X-API-Key` / `X-Store-ID`，
 * 这里原来只有 `Content-Type`，鉴权开启后会直接 401，而错误信息只显示 "HTTP 401"。
 */
export const migrateToRemote = (tables: Array<{ table: string; rows: unknown[] }>) =>
  request<{ imported?: Record<string, number> }>('/api/migrate', {
    method: 'POST',
    body: JSON.stringify(tables),
  });

export type InboundSummary = {
  cartons: number;
  pieces: number;
  rows: number;
  /** OMS 箱唛一张只放得下 5 行 SKU，超出的箱会多印一页且那一页没有箱号 */
  overflow: Array<{ box_no: string; skus: number; overflow: number }>;
};

/**
 * 生成 OMS（鲲鹏 JF WMS）建入库单要导入的 xlsx。
 *
 * 按**箱**生成：标识号相同的行 = 同一个箱子。箱子数据来自逐箱清单，
 * 所以装箱时录的「第几箱装了什么」会原样变成 OMS 的入库单，不用再手录一遍。
 */
export const exportInboundTemplate = async (shipmentIds: string[]): Promise<{ blob: Blob; summary: InboundSummary | null }> => {
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
  let summary: InboundSummary | null = null;
  const raw = res.headers.get('X-Inbound-Summary');
  if (raw) {
    try { summary = JSON.parse(raw) as InboundSummary; } catch { /* 老后端没有这个头，按空处理 */ }
  }
  return { blob: await res.blob(), summary };
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

type PackingImportResult = {
  ok: boolean; imported: number; skipped: number;
  /** 真的没导进来（如 SKU 为空）。 */
  failed: Array<{ sku: string; reason: string }>;
  /** 产品已导入、只是图片没抓到 —— 和 failed 分开，否则用户会重复导入。 */
  image_failed: Array<{ sku: string; reason: string }>;
};

export const importFromProducts = (productIds: string[]): Promise<PackingImportResult> =>
  request<PackingImportResult>('/api/packing/import-from-products', {
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

/**
 * 逐箱清单查询：某天装了哪些箱，或反查某个 SKU 在哪几箱。
 * 箱子封上以后外观一样，「哪箱装了什么」只能查这里。
 */
export const getPackingCartons = (
  params: { date?: string; sku?: string; limit?: number } = {},
): Promise<{ date: string; total_cartons: number; cartons: PackingCartonRecord[] }> => {
  const q = new URLSearchParams();
  if (params.date) q.set('date', params.date);
  if (params.sku) q.set('sku', params.sku);
  if (params.limit) q.set('limit', String(params.limit));
  const qs = q.toString();
  return request(`/api/packing/cartons${qs ? `?${qs}` : ''}`);
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
  // current 只在「以卖家后台为准更正 SKU」时出现，表示要被替换掉的旧值。
  changes: { field: string; label: string; value: string | number; current?: string | number }[];
  differences: string[]; warnings: string[]; manual: string[];
  migrated?: Record<string, number>;
}
export interface SellerAssistResult { items: SellerAssistItem[]; fillable: number }
export const previewSellerAssist = (ids: string[]) => request<SellerAssistResult>('/api/products/seller-assist/preview', { method: 'POST', body: JSON.stringify({ ids }) });
export const applySellerAssist = (items: SellerAssistItem[]) => request<SellerAssistResult>('/api/products/seller-assist/apply', { method: 'POST', body: JSON.stringify({ ids: items.map(i => i.id), tokens: Object.fromEntries(items.map(i => [i.id, i.token])) }) });

/** 卖家后台 → 正式产品主档：已有的更正，缺的新建。 */
export interface SellerSyncChange {
  field: string; label: string; value: string | number; current?: string | number;
}
export interface SellerSyncUpdate {
  plid: string; product_id: string; product_no: number; name: string;
  matched_by: 'plid' | 'tsin' | 'sku';
  changes: SellerSyncChange[]; warnings: string[];
}
export interface SellerSyncCreate {
  plid: string; sku: string; tsin: string; product_name: string;
  product_image_url: string; takealot_url: string; actual_sale_price_zar: number | null;
}
export interface SellerSyncSkip { plid: string; skus: string[]; reason: string }
export interface SellerSyncStats {
  seller_offer_count: number; seller_group_count: number; local_product_count: number;
  update_count: number; create_count: number; unchanged_count: number;
  skip_count: number; rename_count: number;
}
export interface SellerSyncResult {
  stats: SellerSyncStats;
  updates: SellerSyncUpdate[];
  creates: SellerSyncCreate[];
  skipped: SellerSyncSkip[];
  token: string;
  truncated: boolean;
  applied?: { created: number; updated: number; renamed: number; moved: Record<string, number> };
}
// plids 是可选的收窄口径：整库同步会一次新增一百多条商品，排查某几个 SKU 时
// 只同步关心的那几条，写进去的范围才和界面上看到的一致。
const sellerSyncBody = (plids?: string[], token?: string) => JSON.stringify(
  plids && plids.length ? (token ? { token, plids } : { plids }) : (token ? { token } : {}),
);

export const previewSellerSync = (plids?: string[]) => request<SellerSyncResult>('/api/products/seller-sync/preview', { method: 'POST', body: sellerSyncBody(plids) });
export const applySellerSync = (token: string, plids?: string[]) => request<SellerSyncResult>('/api/products/seller-sync/apply', { method: 'POST', body: sellerSyncBody(plids, token) });

export const getAutomaticPricing = (): Promise<any> => request('/api/automatic-pricing');
export const configureAutomaticPricing = (product: string, offer: string, rule: unknown): Promise<any> => request(`/api/automatic-pricing/${product}/${offer}`, { method: 'PUT', body: JSON.stringify(rule) });
export const pauseAutomaticPricing = (product: string, offer: string): Promise<any> => request(`/api/automatic-pricing/${product}/${offer}/pause`, { method: 'POST' });

export const reconcileAutomaticPricing = (product: string, offer: string): Promise<any> => request(`/api/automatic-pricing/${product}/${offer}/reconcile`, { method: 'POST' });

export const bulkAutomaticPricing = (payload: unknown): Promise<any> => request('/api/automatic-pricing/bulk', { method: 'POST', body: JSON.stringify(payload) });

export const pauseAllAutomaticPricing = (): Promise<any> => request('/api/automatic-pricing/pause-all', { method: 'POST' });

export const configureBuyboxMonitor = (product:string, offer:string, settings:unknown):Promise<any> => request(`/api/buybox/${product}/${offer}`,{method:'PUT',body:JSON.stringify(settings)});
export const collectBuyboxNow = (product:string, offer:string):Promise<any> => request(`/api/buybox/${product}/${offer}/collect-now`,{method:'POST'});
export const bulkBuyboxMonitor = (payload:unknown):Promise<any> => request('/api/buybox/bulk',{method:'POST',body:JSON.stringify(payload)});

export const collectOnAppOpen = () => request<{queued:number;skipped:number;price_changed:boolean}>('/api/buybox/page-open',{method:'POST'});

// Browser frames and lease tokens never use query strings or localStorage.
export const openCloudBrowser = (product_id: string) => request<{session:string;width:number;height:number}>('/api/buybox-browser/open', {method:'POST',body:JSON.stringify({product_id})});
export const cloudBrowserAction = (session: string, data: Record<string, unknown>) => request('/api/buybox-browser/action', {method:'POST',headers:{'X-Browser-Session':session},body:JSON.stringify(data)});
export const cloudBrowserFrame = async (session: string) => {
  const response=await fetch(`${getBaseUrl()}/api/buybox-browser/frame`, {headers:{'X-API-Key':getApiKey(),'X-Store-ID':getActiveStoreId(),'X-Browser-Session':session},cache:'no-store',signal:AbortSignal.timeout(10000)});
  if(!response.ok) { let detail='云端画面读取失败'; try { detail=(await response.json()).detail||detail; } catch {} throw Object.assign(new Error(detail),{status:response.status}); }
  return response.blob();
};

/**
 * 生成产品标签。默认 single：每个 SKU 一个独立 PDF，页面就是 40×30mm，
 * 可直接丢给标签打印机；多个 SKU 时后端打包成 zip。
 * mode='sheet' 才返回 A4 拼版（45 张/页，带裁切角标）。
 * 传 skus 走列表页批量；传 productIds 走详情页——后端会把该产品的每个 SKU 都拆成一个文件。
 * 只有 13 位合法 EAN-13 的 SKU 才会被画出来，其余后端跳过。
 *
 * 注意：页面上的「下载标签」已经改走 `src/utils/labelPdf.ts`（浏览器里直接出 PDF），
 * 不依赖后端部署。这里保留是因为后端接口还在（供脚本/其它客户端用）；
 * 两边版式参数一致，改一处记得对一下另一处。
 */
export const downloadProductLabels = async (
  skus: string[],
  opts: { size?: '40x30' | '30x40'; copies?: number; productIds?: string[]; mode?: 'single' | 'sheet' } = {},
): Promise<Blob> => {
  const res = await fetch(`${getBaseUrl()}/api/products/labels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': getApiKey(), 'X-Store-ID': getActiveStoreId() },
    body: JSON.stringify({
      skus,
      product_ids: opts.productIds || [],
      size: opts.size || '40x30',
      copies: opts.copies || 1,
      mode: opts.mode || 'single',
    }),
    signal: AbortSignal.timeout(90000),
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
