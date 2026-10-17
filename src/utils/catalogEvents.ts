/**
 * 商品目录（产品列表）变更广播。
 *
 * 装箱单有自己的库，靠后端 `/api/packing/sync-from-products` 从产品库同步名称。
 * 改了中文品名/ SKU 之后如果不通知，装箱单要等下次整页刷新才变——用户会以为
 * 改动没生效。这里用一个窗口事件把「刚改过目录」这件事说出去，谁关心谁刷新。
 */
export const CATALOG_CHANGED = 'catalog:changed';

/** 会影响装箱单显示的字段：名称、SKU。其它字段（价格、状态）不必触发同步。 */
const CATALOG_FIELDS = new Set(['sku', 'product_name', 'chinese_product_name']);

export function notifyCatalogChanged(fields: string[] = []): void {
  if (fields.length && !fields.some(f => CATALOG_FIELDS.has(f))) return;
  // 这只是「顺带喊一声」，喊不出去也不能连累保存流程：
  // 早期版本直接 new CustomEvent，在没有该构造函数的环境里会抛错，
  // 结果保存成功了却被上层 catch 成「保存失败」，编辑器不关、用户以为没存上。
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
  try {
    const Ctor = window.CustomEvent;
    window.dispatchEvent(Ctor ? new Ctor(CATALOG_CHANGED) : new window.Event(CATALOG_CHANGED));
  } catch {
    /* 广播失败只影响「另一页何时刷新」，不该冒泡到调用方 */
  }
}
