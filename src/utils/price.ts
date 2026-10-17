/**
 * 南非兰特（ZAR）价格文案解析。
 *
 * 背景：Takealot 页面上的价格文案同时存在两种分隔符用法，且千分位可能是空格：
 *
 *   "R 1 299"        空格千分位
 *   "R 1 299,00"     空格千分位 + 逗号小数
 *   "R 1,299.00"     逗号千分位 + 点小数
 *   "R 299,00"       逗号小数（无千分位）
 *   "R299.00"        点小数（无千分位）
 *
 * 旧实现有两处（`utils/scraper.ts` 的 parsePrice、`takealotPageImport.ts` 的
 * captureTakealotPage）都把逗号一律当千分位删掉，于是 "R 299,00" 被解析成 29900
 * ——放大 100 倍，而且是个"看起来合理的大数"，人工很难一眼发现。
 *
 * 判定规则（先取出第一个数字片段，再判断分隔符各自扮演什么角色）：
 *
 *  1. 逗号与点同时出现 → 靠后的那个是小数点，另一个是千分位。
 *  2. 只有一种分隔符：
 *     - 出现 2 次及以上 → 全部是千分位（"1,234,567"）。
 *     - 只出现 1 次 → 其后恰好 3 位、且整数部分为 1–3 位 → 千分位（"1,299"）；
 *       其后 1–2 位 → 小数点（"299,00" / "299.9"）；
 *       其余（0 位或 4 位以上）→ 按千分位处理。
 *  3. 没有分隔符 → 直接按整数解析。
 *
 * 实现约束：本函数**必须自包含**，不能引用模块作用域里的其它函数。
 * 原因是 `makeTakealotBookmarklet` 会用 `parseZarPrice.toString()` 把源码
 * 注入书签脚本，被引用的外部标识符在打包压缩后不会随源码一起过去。
 */
export function parseZarPrice(text: string | null | undefined): number | null {
  if (!text) return null;

  // 只取第一个「数字开头、数字结尾」的片段，避免把后面的 "Save R 200" 拼进价格
  const match = text.match(/\d[\d\s.,]*\d|\d/);
  if (!match) return null;

  const token = match[0].replace(/\s/g, '').replace(/[.,]+$/, '');
  if (!token) return null;

  const lastComma = token.lastIndexOf(',');
  const lastDot = token.lastIndexOf('.');
  let decimalSeparator: string | null = null;

  if (lastComma >= 0 && lastDot >= 0) {
    decimalSeparator = lastComma > lastDot ? ',' : '.';
  } else if (lastComma >= 0 || lastDot >= 0) {
    const separator = lastComma >= 0 ? ',' : '.';
    const index = token.lastIndexOf(separator);
    const occurrences = token.split(separator).length - 1;
    if (occurrences === 1) {
      const digitsAfter = token.length - index - 1;
      if (digitsAfter === 3 && /^\d{1,3}$/.test(token.slice(0, index))) {
        decimalSeparator = null;
      } else if (digitsAfter >= 1 && digitsAfter <= 2) {
        decimalSeparator = separator;
      }
    }
  }

  let normalized: string;
  if (decimalSeparator) {
    const thousandsSeparator = decimalSeparator === ',' ? '.' : ',';
    normalized = token.split(thousandsSeparator).join('').replace(decimalSeparator, '.');
  } else {
    normalized = token.replace(/[.,]/g, '');
  }

  const value = Number.parseFloat(normalized);
  return Number.isFinite(value) ? value : null;
}
