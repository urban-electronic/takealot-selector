/**
 * 数值字段收口。
 *
 * 背景：同一批接口里，`total_amount`、利润率、库存数量等字段有时是 number，
 * 有时被序列化成字符串（"120.00"）。直接使用会有两类静默错误：
 *
 *   1. `sum + value` 变成字符串拼接 —— 0 + "120.00" + "50.00" = "0120.0050.00"
 *   2. `value.toFixed(2)` 直接抛 TypeError —— 整页渲染失败
 *
 * 另外 `value ?? 0` 会把"字段缺失"伪装成 0：缺失利润率被渲染成"0% 低毛利"，
 * 看起来像真实数据。所以这里区分两种语义：
 *   - `toNumber`：缺失/非法 → null，让调用方显式展示"—"
 *   - `toAmount`：求和场景 → 缺失/非法按 0，且保证返回 number
 */

/** 解析为有限数值；缺失或非法返回 null（不返回 0）。 */
export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value.replace(/[\s,]/g, ''));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** 求和/累加用：缺失或非法按 0，保证不会退化成字符串拼接。 */
export function toAmount(value: unknown): number {
  return toNumber(value) ?? 0;
}
