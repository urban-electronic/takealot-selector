/**
 * 多规格（SKU 分支）解析。
 *
 * 背景：SKU 与规格名以分隔符拼接后存进 `products.sku` / `products.chinese_product_name`
 * 两个字段，没有独立的分支表。此前 ProductList 按 `/[\s,，;；/]+/` 切分，
 * 而 Dashboard 只按 `/[\r\n]+/` 切分，两边规则不一致：
 * 用「修改分支」保存的多规格产品（写入时用空格 join），在主页会被永久判定为
 * "规格待确认"，用户按提示去修又修不好。
 *
 * 约定：切分规则只在这里定义一份，写入侧与读取侧都用 `join(' ')` / 本模块解析。
 */
import type { Product } from '../types';

/** SKU 字段里的分支列表。分隔符同时接受空白、中英文逗号、分号。 */
export function skuVariants(sku: string | null | undefined): string[] {
  return (sku || '')
    .split(/[\s,，;；]+/)
    .map(value => value.trim())
    .filter(Boolean);
}

/**
 * 与 SKU 顺序一一对应的规格名。
 * 数量对不上时降级为「主商品 / 规格待识别」，供界面提示人工核对。
 */
export function variantLabels(product: Pick<Product, 'sku' | 'chinese_product_name'>): string[] {
  const skus = skuVariants(product.sku);
  const labels = (product.chinese_product_name || '')
    .split(/[\s,，;；/]+/)
    .map(value => value.trim())
    .filter(Boolean);
  if (labels.length === skus.length) return labels;
  return skus.map((_, index) => (index === 0 ? product.chinese_product_name || '主商品' : '规格待识别'));
}

/** 分支名是否仍是占位值（未对应到真实颜色/尺码/型号）。 */
export function isPlaceholderLabel(label: string): boolean {
  return /待识别|待确认|历史分支/.test(label);
}
