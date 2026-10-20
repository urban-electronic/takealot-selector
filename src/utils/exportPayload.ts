/**
 * 「一键导出到云端」的 payload 组装。
 *
 * 为什么单独抽出来：后端 `/api/migrate`（`backend/main.py` 的 `bulk_migrate`）会**先
 * DELETE 本次 payload 涉及的每一张表**（`payload_tables` 决定清哪几张），再逐行 INSERT。
 * 于是「读不到」和「本地确实是空的」在后端看来完全一样：都发一张 `rows: []` 过去，
 * 云端那张表就被清空，而界面照样写「导出成功！fee_mapping_rules: 0」。
 *
 * 所以这里把两者分开：读不到的表**整表省略**，后端根本不会碰它；
 * 读到了但确实是 0 条，照常发空数组（那是用户的真实状态，该同步）。
 */

export type ExportTable = { table: string; rows: unknown[] };

export type ExportSource = {
  products: unknown[];
  feeCategories: unknown[];
  /** `null` 表示读取失败（不是「0 条」）；空数组表示本地确实没有规则。 */
  feeRules: unknown[] | null;
  settings: Record<string, string>;
};

export function buildExportPayload(source: ExportSource): { payload: ExportTable[]; skipped: string[] } {
  const payload: ExportTable[] = [
    { table: 'products', rows: source.products },
    { table: 'fee_categories', rows: source.feeCategories },
  ];
  const skipped: string[] = [];
  if (source.feeRules === null) {
    skipped.push('fee_mapping_rules');
  } else {
    payload.push({ table: 'fee_mapping_rules', rows: source.feeRules });
  }
  payload.push({
    table: 'system_settings',
    rows: Object.entries(source.settings).map(([key, value]) => ({ key, value })),
  });
  return { payload, skipped };
}
