export interface Product {
  id: string;
  product_no: number | null;
  recorded_at: string | null;
  note: string | null;
  takealot_url: string | null;
  tsin: string | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface ScrapeResult {
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

export const LINK_STATUS_MAP: Record<string, { label: string; color: string }> = {
  '未购买': { label: '未购买', color: '#faad14' },
  '已购买': { label: '已购买', color: '#1677ff' },
  '已上架': { label: '已上架', color: '#52c41a' },
  '已发货': { label: '已发货', color: '#ff9800' },
};

export const SELECTION_STATUS_MAP: Record<string, { label: string; color: string }> = {
  '数据待补充': { label: '数据待补充', color: '#faad14' },
  '待确认品类': { label: '待确认品类', color: '#1677ff' },
  '合格选品': { label: '合格选品', color: '#52c41a' },
  '不建议选品': { label: '不建议选品', color: '#ff4d4f' },
};

export function formatPrice(val: number | null | undefined, currency: 'CNY' | 'ZAR' = 'ZAR'): string {
  if (val == null) return '-';
  const symbol = currency === 'CNY' ? '¥' : 'R';
  return `${symbol} ${val.toFixed(2)}`;
}

export function formatPercent(val: number | null | undefined): string {
  if (val == null) return '-';
  return `${(val * 100).toFixed(2)}%`;
}
