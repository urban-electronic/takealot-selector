import type { InventoryRow, Product } from '../types';

export function missingSkuTasks(inventory: InventoryRow[], products: Product[]) {
  const byId = new Map(products.map(product => [product.id, product]));
  const missing = inventory.filter(row => !row.sku?.trim());
  const blocking = missing.filter(row => {
    // product_id 可能是 null/undefined（接口少字段），直接 split 会抛 TypeError
    const productId = row.product_id ? row.product_id.split(':')[0] : '';
    const product = productId ? byId.get(productId) : undefined;
    return !!(row.purchased || row.in_transit || row.adjusted || row.shipped || row.available)
      || !!(product?.link_status && product.link_status !== '未购买');
  });
  return { blocking: blocking.length, unpurchased: missing.length - blocking.length };
}
