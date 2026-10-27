// 工作台顶部「补货提醒」的口径。后端 /api/operations/alerts 是按「产品 × 运输方式」
// 逐行返回的（同一个产品可能既有空运行又有海运行），所以：
// ① 产品数必须按 product_id 去重，不能数行；
// ② 缺口按运输方式各自累加，缺数量按 0 兜底（历史行里 quantity 可能是 null）；
// ③ stale / overdue 是后端给出的数据质量标记，补货计划页逐行标了「库存待更新」
//    「到货延期」，工作台这句话原来把它们整个丢掉——同样的数字，一个页面说「这是
//    过期数据算出来的」，另一个页面说得像刚盘点过（ALERTS-STALE-01）。
export type ReplenishmentAlert = {
  product_id: string;
  mode: string;
  quantity?: number | null;
  stale?: boolean;
  overdue?: boolean;
};

export type ReplenishmentSummary = {
  products: number;
  air: number;
  sea: number;
  staleProducts: number;
  overdueProducts: number;
};

const sum = (rows: ReplenishmentAlert[], mode: string) =>
  rows.filter(r => r.mode === mode).reduce((total, r) => total + (Number(r.quantity) || 0), 0);

const countProducts = (rows: ReplenishmentAlert[], flagged: (r: ReplenishmentAlert) => boolean) =>
  new Set(rows.filter(flagged).map(r => r.product_id)).size;

export function summarizeAlerts(rows: ReplenishmentAlert[]): ReplenishmentSummary {
  return {
    products: new Set(rows.map(r => r.product_id)).size,
    air: sum(rows, 'air'),
    sea: sum(rows, 'sea'),
    staleProducts: countProducts(rows, r => Boolean(r.stale)),
    overdueProducts: countProducts(rows, r => Boolean(r.overdue)),
  };
}

/** 「（2 个产品库存待更新、1 个产品到货延期）」；两个标记都没有时返回空串。 */
export function describeCaveats(summary: ReplenishmentSummary): string {
  const parts = [
    summary.staleProducts ? `${summary.staleProducts} 个产品库存待更新` : '',
    summary.overdueProducts ? `${summary.overdueProducts} 个产品到货延期` : '',
  ].filter(Boolean);
  return parts.length ? `（${parts.join('、')}）` : '';
}
