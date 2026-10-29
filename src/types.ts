import { toNumber } from './utils/number';

export interface Product {
  id: string;
  product_no: number | null;
  is_archived?: boolean;
  archived_at?: string | null;
  recorded_at: string | null;
  note: string | null;
  takealot_url: string | null;
  tsin: string | null;
  /** Takealot 商品线 ID（公开，在链接里）。推荐去重与卖家后台对账都用它 */
  plid?: string | null;
  product_name: string | null;
  product_image_url: string | null;
  product_image_path: string | null;
  actual_sale_price_zar: number | null;
  fee_category: string | null;
  fee_category_confirmed: boolean;
  fee_rate_range: string | null;
  success_fee_rate: number | null;
  purchase_url: string | null;
  sku: string | null;
  chinese_product_name: string | null;
  purchase_cost_cny: number | null;
  purchase_shipping_cny: number | null;
  purchase_quantity: number;
  length_mm: number | null;
  width_mm: number | null;
  height_mm: number | null;
  actual_weight_kg: number | null;
  packaging_cost_per_unit_cny: number | null;
  unit_price_cny: number | null;
  shipping_method: string | null;
  inbound_listing_fee_cny: number;
  outbound_operation_fee_cny: number;
  last_mile_delivery_fee_cny: number;
  other_fee_cny: number;
  fulfillment_fee_zar: number;
  // manual cost overrides
  manual_domestic_forwarding_cny?: number | null;
  manual_international_shipping_cny?: number | null;
  manual_overseas_op_cost_cny?: number | null;
  manual_success_fee_zar?: number | null;
  manual_fulfillment_fee_zar?: number | null;
  manual_total_cost_zar?: number | null;
  // market signals
  competing_sellers_count?: number | null;
  stock_remaining?: number | null;
  review_count?: number | null;
  rating_value?: number | null;
  /** 多市场参考价 JSON 文本，见 utils/marketPrice */
  market_prices?: string | null;
  // calculated
  volume_cbm: number | null;
  volumetric_weight_kg: number | null;
  chargeable_weight_kg: number | null;
  domestic_forwarding_cost_per_unit_cny: number | null;
  unit_product_cost_cny: number | null;
  international_shipping_per_unit_cny: number | null;
  cost_to_sa_warehouse_cny: number | null;
  cost_to_sa_warehouse_zar: number | null;
  overseas_warehouse_operation_cost_cny: number | null;
  overseas_warehouse_operation_cost_zar: number | null;
  success_fee_zar: number | null;
  official_total_cost_zar: number | null;
  total_cost_zar: number | null;
  profit_zar: number | null;
  profit_margin: number | null;
  minimum_price_at_20_margin: number | null;
  minimum_price_at_15_margin: number | null;
  link_status: string | null;
  selection_status: string;
  exchange_rate_used: number;
  // 变体分组来自 products 表（backend/models.py），后端一直在返回，只是这里漏了声明。
  // 前端靠它判断「同组同图」要不要重新抓图，缺类型就只能靠 any 绕过去。
  variant_group?: string;
  is_primary_variant?: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ScrapeResult {
  data_source?: 'takealot' | 'offer_catalog' | 'manual' | 'browser_page' | 'seller_api';
  variants?: { sku: string; label: string; image_url: string }[];
  normalized_url: string;
  tsin: string | null;
  product_name: string | null;
  product_image_url: string | null;
  actual_sale_price_zar: number | null;
  in_stock_price: number | null;
  takealot_category_path: string | null;
  recommended_fee_category?: string | null;
  fee_category_confidence?: 'high' | 'medium' | 'low';
  fee_match_reason?: string | null;
  warnings: string[];
  success: boolean;
}

export interface FeeCategory {
  id: string;
  name: string;
  fee_rate_range: string;
  success_fee_rate: number;
  active: boolean;
}

export interface FeeMappingRule {
  id: string;
  takealot_category_pattern: string | null;
  title_keyword_pattern: string | null;
  fee_category: string;
  priority: number;
  active: boolean;
  created_by_user: boolean;
  created_at: string | null;
}

export interface DashboardStats {
  total: number;
  data_incomplete: number;
  category_pending: number;
  qualified: number;
  not_recommended: number;
  avg_profit_margin: number;
  top_product_name: string | null;
  top_profit_margin: number | null;
}

export const SHIPPING_METHODS = ['空运普货', '空运带电', '海运普货', '海运带电'] as const;

export const LINK_STATUS_OPTIONS = ['未购买', '已购买', '已上架', '已发货'] as const;

// 链接状态配色：浅底 + 深字，保证正文级对比度（WCAG AA 4.5:1）。
// 旧版用饱和底色配白字，"未购买/已上架/已发货"实测只有 1.9~2.3:1，黄绿底上几乎看不清。
export const LINK_STATUS_MAP: Record<string, { label: string; color: string; text: string }> = {
  '未购买': { label: '未购买', color: '#fff4d6', text: '#8a5900' }, // 5.46:1
  '已购买': { label: '已购买', color: '#e8f1ff', text: '#0b5fd9' }, // 5.05:1
  '已上架': { label: '已上架', color: '#e8f6ea', text: '#17694a' }, // 5.96:1
  '已发货': { label: '已发货', color: '#ffeddb', text: '#a35400' }, // 4.81:1
};

// ---- 产品推荐（详情页底部的「同类商品 / 配套周边」）----
// 口径与 backend/services/product_recommendation.py 一致；
// 本地数据源在 src/utils/productRecommend.ts 里按同一套常量算一遍。
export interface RecommendationItem {
  product_id: string;
  product_no: number | null;
  name: string;
  chinese_name: string;
  sku: string;
  plid: string;
  image_url: string;
  price_zar: number | null;
  profit_margin: number | null;
  fee_category: string | null;
  takealot_url: string;
  score: number;
  /** 命中的推荐依据，界面上逐条展示——看不到理由的推荐没人敢用 */
  reasons: string[];
}

export interface RecommendationMeta {
  strategy: string;
  weights: { category: number; attribute: number; complement: number; behavior: number };
  dedup: string;
  min_score: number;
  limits: { similar: number; complement: number };
  candidate_count: number;
  scored_count: number;
  anchor: { product_id: string; product_no: number | null; name: string; fee_category: string | null };
}

export interface RecommendationResult {
  similar: RecommendationItem[];
  complement: RecommendationItem[];
  meta: RecommendationMeta;
}

// ---- 站外货源（1688 / Takealot 上看到的、不属于自己仓库的商品）----
// 和上面的 RecommendationItem 是两件事：那个在自家库里找邻居，这个读 external_offers 表。
export type ExternalPlatform = '1688' | 'takealot';

export interface ExternalOffer {
  id: string;
  platform: ExternalPlatform;
  url: string;
  title: string;
  title_cn: string;
  /** 站外标价，未换算。1688 是人民币批发价、Takealot 是兰特零售价 */
  price: number | null;
  currency: string;
  /** 起订量，只有 1688 才有 */
  moq: number | null;
  image_url: string;
  keywords: string[];
  /** 用途 */
  usage: string;
  /** 适用场景 */
  scenario: string;
  /** 选择理由 */
  reason: string;
  /** 命中了商品的哪几个关键词——界面要能回答「为什么挂在这里」 */
  matched: string[];
  is_active?: boolean;
}

export interface ExternalOfferResult {
  sourcing: ExternalOffer[];
  market: ExternalOffer[];
  meta: {
    total: number;
    matched: number;
    unmatched: number;
    limit_per_platform: number;
    match_rule: string;
    sort: string;
  };
}

export interface ExternalOfferInput {
  platform: ExternalPlatform;
  url: string;
  title?: string;
  title_cn?: string;
  price?: number | null;
  moq?: number | null;
  image_url?: string;
  keywords?: string;
  usage?: string;
  scenario?: string;
  reason?: string;
}

export interface ProcurementRecord {
  id: string;
  product_id: string | null;
  product_no: number | null;
  product_name: string | null;
  quantity: number;
  total_amount: number;
  unit_price: number | null;
  notes: string | null;
  status: 'in_transit' | 'received' | 'cancelled';
  status_updated_at?: string | null;
  recorded_at: string;
}

export interface PackingProduct {
  sku: string;
  name_zh: string;
  name_en: string;
  unit: string;
  weight: string;
  material: string;
  brand: string;
  battery: string;
  electric: string;
  magnetic: string;
  template_row: number | null;
  default_count: number | null;
  image_file: string;
  source_image_url?: string;
  variant_group?: string;
  variant_label?: string;
  is_primary_variant?: boolean;
}

export interface PackingExportLine {
  sku: string;
  cartons: string;
  count: string;
}

/**
 * 逐箱清单：一箱一条，回答「第几箱装了什么」。
 *
 * 装箱单（`PackingExportLine`）是按 SKU 汇总的，箱子封上以后外观一样，
 * 靠它查不出「第 3 箱里是什么」。混装时一箱会有多行 items。
 */
export interface PackingCartonItem {
  sku: string;
  quantity: string;
}

export interface PackingCarton {
  box_no: string;
  note: string;
  items: PackingCartonItem[];
}

export interface PackingExportPayload {
  record_shipment?: boolean;
  date: string;
  mark: string;
  shipping: string;
  address: string;
  /** OMS 入库单号。唛头留空时用它，保证「客户唛头」和「箱唛」同一套编号 */
  inbound_no?: string;
  items: PackingExportLine[];
  draft_key?: string;
  /** 不传时后端按「一箱一个 SKU」自动展开；混装必须传 */
  cartons?: PackingCarton[];
}

/** 查询回来的逐箱记录（含发货单号与「共几箱」） */
export interface PackingCartonRecord {
  shipment_id: string;
  shipment_no: string;
  shipment_date: string;
  mark: string;
  seq: number;
  total: number;
  box_no: string;
  note: string;
  items: { sku: string; name_zh: string; name_en: string; quantity: number }[];
}

export interface InventoryRow {
  product_id: string;
  product_no: number | null;
  sku: string;
  name: string;
  name_en: string;
  image_url: string;
  purchased: number;
  in_transit: number;
  overseas_in_transit?: number;
  adjusted: number;
  shipped: number;
  available: number;
  variant_group?: string;
  is_primary_variant?: boolean;
}

export interface OperationLog {
  id: string;
  entity_type: string;
  entity_id: string;
  action: string;
  summary: string;
  details: string;
  created_at: string;
}

export interface InventoryAdjustment {
  id: string;
  sku: string;
  quantity_delta: number;
  reason: string;
  notes: string;
  occurred_at: string;
  reversed_by: string | null;
  created_at: string;
}

export interface ShipmentRecord {
  id: string;
  created_at?: string;
  shipment_no: string;
  shipment_date: string;
  mark: string;
  shipping: string;
  address: string;
  status: 'confirmed' | 'void' | 'clearance' | 'exported' | 'transit';
  version: number;
  void_reason: string;
  total_skus: number;
  total_quantity: number;
  lines: Array<{ sku: string; name: string; cartons: number; count_per_carton: number; total_quantity: number }>;
}

export interface Store {
  id: string;
  name: string;
  platform: string;
  status: string;
  owner_name: string;
  sync_method: string;
  sync_interval_minutes: number;
  external_store_ref: string;
  allow_negative_inventory_shipments: boolean;
  last_synced_at: string | null;
}

export interface StoreInventoryOverview {
  store_id: string;
  store_name: string;
  status: string;
  sku_count: number;
  purchased: number;
  adjusted: number;
  shipped: number;
  available: number;
  exception_count: number;
  last_synced_at: string | null;
}

export const SELECTION_STATUS_MAP: Record<string, { label: string; color: string }> = {
  '数据待补充': { label: '数据待补充', color: '#faad14' },
  '待确认品类': { label: '待确认品类', color: '#1677ff' },
  '合格选品': { label: '合格选品', color: '#52c41a' },
  '不建议选品': { label: '不建议选品', color: '#ff4d4f' },
};

export function formatPrice(val: number | string | null | undefined, currency: 'CNY' | 'ZAR' = 'ZAR'): string {
  const amount = toNumber(val);
  if (amount == null) return '-';
  const symbol = currency === 'CNY' ? '¥' : 'R';
  return `${symbol} ${amount.toFixed(2)}`;
}

export function formatPercent(val: number | string | null | undefined): string {
  const ratio = toNumber(val);
  if (ratio == null) return '-';
  return `${(ratio * 100).toFixed(2)}%`;
}

/* ---------------------------------------------------------------------------
 * 选品池（/selection）
 *
 * 和 `Product` 的区别：这是**待核算的候选**，不是产品主档。只有勾中并
 * 「一键新建产品」之后才会复制成 `Product`。字段名刻意与 `Product` 对齐，
 * 这样两边算利润用的是同一套公式（services/product_calculator.py）。
 * ------------------------------------------------------------------------- */

export type SelectionItemStatus = '待核算' | '合格' | '不合格' | '已建产品';

export interface SelectionItem {
  id: string;
  batch_id: string;
  batch_name: string;
  plid: string;
  tsin: string;
  takealot_url: string;
  product_name: string;
  chinese_product_name: string;
  brand: string;
  product_image_url: string;
  image_urls: string[];
  takealot_category_path: string;
  fee_category: string;
  /** high / medium / low —— 类目是推断出来的，置信度决定要不要人工复核 */
  fee_category_confidence: string;
  fee_category_reason: string;
  actual_sale_price_zar: number | null;
  rating_value: number | null;
  review_count: number | null;
  /** 只有销售报表带来的行才有；竞品表这三项都是 null */
  sales_qty: number | null;
  net_sales_zar: number | null;
  gross_sales_zar: number | null;
  net_sales_cny: number | null;
  /** S / A / B / C；没有销售数据时为空串 */
  tier: string;
  already_in_products: boolean;
  promoted_product_id: string | null;
  length_mm: number | null;
  width_mm: number | null;
  height_mm: number | null;
  actual_weight_kg: number | null;
  /**
   * 重尺来源，可信度从高到低：
   * product_list（产品列表里已有的）/ seller_api（本店 offer）/
   * catalogue_api（卖家目录接口）/ public_api（公开页，约 10%，仅预填）/
   * manual（人工填，锁定，不再被覆盖）
   */
  dimensions_source: string;
  purchase_url: string;
  purchase_cost_cny: number | null;
  purchase_shipping_cny: number | null;
  purchase_quantity: number;
  unit_price_cny: number | null;
  packaging_cost_per_unit_cny: number | null;
  shipping_method: string | null;
  fulfillment_fee_zar: number | null;
  success_fee_zar: number | null;
  chargeable_weight_kg: number | null;
  unit_product_cost_cny: number | null;
  international_shipping_per_unit_cny: number | null;
  cost_to_sa_warehouse_zar: number | null;
  total_cost_zar: number | null;
  profit_zar: number | null;
  profit_margin: number | null;
  status: SelectionItemStatus;
  selected: boolean;
  enrich_status: string;
  enrich_message: string;
  note: string;
}

export interface SelectionImportResult {
  batch_id: string;
  batch_name: string;
  sheet: string;
  source_rows: number;
  merged_rows: number;
  created: number;
  already_in_products: number;
  skipped: number;
  with_sales_data: number;
  columns: Record<string, number>;
  unknown_headers: string[];
}

export interface SelectionEnrichResult {
  processed: number;
  ok: number;
  failed: number;
  /** 从卖家 API 或目录接口拿到重尺的条数 */
  with_dimensions: number;
  /** 直接从产品主档抄到重尺的条数（自家商品的主路径） */
  from_product_list: number;
  /** 图/类目/价格主档里已经齐全、没再打网络的条数 */
  skipped_network: number;
  remaining: number;
  /** 上游拒绝或限流时停止后续轮次；remaining 仍是实际待处理数。 */
  public_access_blocked?: boolean;
  seller_api: string;
  failures: { id: string; plid: string; tsin: string; message: string }[];
}

export interface SelectionSummary {
  total: number;
  pending: number;
  qualified: number;
  rejected: number;
  promoted: number;
  selected: number;
  duplicates: number;
  with_image: number;
  with_dimensions: number;
  no_plid: number;
}

export interface SelectionBatch {
  batch_id: string;
  batch_name: string;
  count: number;
  created_at: string;
}

export interface SelectionItemPatch {
  product_name?: string;
  chinese_product_name?: string;
  fee_category?: string;
  takealot_url?: string;
  actual_sale_price_zar?: number;
  length_mm?: number;
  width_mm?: number;
  height_mm?: number;
  actual_weight_kg?: number;
  purchase_url?: string;
  purchase_cost_cny?: number;
  purchase_shipping_cny?: number;
  purchase_quantity?: number;
  unit_price_cny?: number;
  packaging_cost_per_unit_cny?: number;
  shipping_method?: string;
  selected?: boolean;
  note?: string;
}

export interface SelectionPromoteResult {
  created: number;
  skipped: number;
  products: { id: string; product_id: string; product_no: string; product_name: string }[];
  skip_details: { id: string; reason: string }[];
}
