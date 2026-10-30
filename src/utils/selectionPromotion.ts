import type { SelectionItem } from '../types';

/** Read the complete selection before any writes, so promotion cannot shift offsets. */
export async function selectedPromotionIds(
  read: (params: { batch_id?: string; only_selected: boolean; limit: number; offset: number }) => Promise<{ total: number; items: SelectionItem[] }>,
  batchId?: string,
): Promise<string[]> {
  const seen = new Set<string>();
  const ids: string[] = [];
  let total: number | undefined;
  for (let offset = 0; total === undefined || offset < total; offset += 300) {
    const result = await read({ batch_id: batchId, only_selected: true, limit: 300, offset });
    if (total !== undefined && total !== result.total) throw Error('勾选记录已变化，请刷新后重试');
    total = result.total;
    if (!Number.isSafeInteger(total) || total < 0 || (offset < total && !result.items.length)) throw Error('勾选记录读取不完整，请刷新后重试');
    for (const item of result.items) {
      if (seen.has(item.id) || !item.selected) throw Error('勾选记录已变化，请刷新后重试');
      seen.add(item.id);
      if (!item.promoted_product_id) ids.push(item.id);
    }
  }
  if (seen.size !== total) throw Error('勾选记录读取不完整，请刷新后重试');
  return ids;
}
