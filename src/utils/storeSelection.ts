import type { Store } from '../types';

/**
 * 页头「店铺」下拉的选项（STORE-SWITCH-01）。
 *
 * 当前店铺必须**始终**出现在选项里：它是每个请求实际带的 X-Store-ID。
 * 原来列表读失败、或者当前店铺刚被暂停时，下拉会退回一条写死的
 * 「自营店铺」——界面显示 A，请求发的却是 B，用户以为换了店其实没换；
 * 当前店铺不在选项里时浏览器还会把下拉显示成空白。
 */
export type StoreOptionState = 'active' | 'paused' | 'unknown';

export interface StoreOption {
  id: string;
  name: string;
  label: string;
  state: StoreOptionState;
}

const FALLBACK_STORE_NAME = '自营店铺';

/**
 * @param stores  /api/stores 的结果；读失败时调用方给空数组
 * @param activeStore 当前生效的店铺 id（localStorage 里那个）
 * @param failed  店铺列表是否读取失败。只在失败时给当前店铺加「未在店铺列表中」，
 *                否则首次加载（还没返回）的瞬间也会显示这句吓人的话。
 */
export function storeOptions(stores: Store[] | null | undefined, activeStore: string, failed = false): StoreOption[] {
  const rows = Array.isArray(stores) ? stores : [];
  const options: StoreOption[] = rows
    .filter(row => row.status === 'active')
    .map(row => ({ id: row.id, name: row.name, label: row.name, state: 'active' as StoreOptionState }));
  if (options.some(option => option.id === activeStore)) return options;
  const current = rows.find(row => row.id === activeStore);
  const name = current?.name || (activeStore === 'default-store' ? FALLBACK_STORE_NAME : activeStore);
  if (current) {
    // 店铺还在库里，只是被暂停了：留着它并说明，别让下拉悄悄换到别的店。
    options.push({ id: activeStore, name, label: `${name}（已暂停）`, state: 'paused' });
  } else {
    options.push({ id: activeStore, name, label: failed ? `${name}（未在店铺列表中）` : name, state: 'unknown' });
  }
  return options;
}

/** 店铺列表读失败时页头的提示：说清现在按哪个店在跑、哪些动作没发生。 */
export function storeLoadNotice(reason: string, currentLabel: string): string {
  const detail = (reason || '').trim() || '原因未知';
  return `店铺列表读取失败：${detail}。当前按「${currentLabel}」继续——切换店铺不可用，打开应用自动采集本次未提交。`;
}
