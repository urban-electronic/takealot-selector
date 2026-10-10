import type { InventoryRow, Product } from '../types';

export function missingSkuTasks(inventory: InventoryRow[], products: Product[]) {
  const byId = new Map(products.map(product => [product.id, product]));
  const missing = inventory.filter(row => !row.sku?.trim());
  const blocking = missing.filter(row => {
    const product = byId.get(row.product_id.split(':')[0]);
    return !!(row.purchased || row.in_transit || row.adjusted || row.shipped || row.available)
      || !!(product?.link_status && product.link_status !== '未购买');
  });
  return { blocking: blocking.length, unpurchased: missing.length - blocking.length };
}
