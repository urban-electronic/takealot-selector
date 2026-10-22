/**
 * 业务日期工具。
 *
 * 背景：项目此前在采购记录与库存调整里使用 `new Date().toISOString().slice(0, 10)`，
 * 取的是 **UTC** 日期。东八区每天 00:00–08:00 会落到"昨天"，导致采购/库存调整
 * 的默认日期错位一天（账实不符）。
 *
 * 约定：业务日期统一按中国时间（Asia/Shanghai）取，与 App.tsx 双城时钟里
 * "中国"一侧的口径一致，不跟随运行机器的本地时区。
 */

const BUSINESS_TIME_ZONE = 'Asia/Shanghai';

/**
 * 解析后端返回的时间串。
 *
 * 后端的 `created_at` / `updated_at` 是 SQLAlchemy 的 `DateTime`（naive UTC，
 * 默认值 `datetime.utcnow`），经 Pydantic 序列化后是 `2026-10-21T12:00:00`——
 * **不带时区标记**。JS 的 `new Date()` 对这种串按「本地时间」解析，于是同一个
 * 时间戳在东八区浏览器里显示成 12:00，实际是 20:00，整整早 8 小时。
 *
 * 约定：没有时区标记的一律当 UTC（全库没有 naive 本地时间，唯一的
 * `datetime.now()` 只用来拼单号）；带 `Z` 或 `±hh:mm` 的按它自己解析。
 * 解析不了返回 null，由调用方决定显示什么，不抛异常。
 */
export function parseServerTime(value: unknown): Date | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text) return null;
  // 纯日期（YYYY-MM-DD）不能直接补 Z，否则会变成非法串；按 UTC 零点算。
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(text)
    ? `${text}T00:00:00Z`
    : text.replace(' ', 'T');
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  const stamp = new Date(hasZone ? normalized : `${normalized}Z`);
  return Number.isNaN(stamp.getTime()) ? null : stamp;
}

/**
 * 后端时间 → 业务时区（Asia/Shanghai）下的可读时间，不跟随运行机器的时区。
 * 空值/坏值返回 `empty`，让调用方保留自己的占位符。
 */
export function beijingDateTime(value: unknown, empty = '-'): string {
  const stamp = parseServerTime(value);
  if (!stamp) return empty;
  return stamp.toLocaleString('zh-CN', { timeZone: BUSINESS_TIME_ZONE });
}

/**
 * 业务时区下的「时:分」，给「刚刚几点更新的」这类短标签用。
 *
 * 不要用 `new Date().toLocaleTimeString()`：它取的是**运行机器的时区**。
 * 开发机正好是 UTC+8，所以本地看永远正常；换到 UTC 的机器上同一个动作会写
 * 「已更新 · 03:00」而北京时间是 11:00，而全站其它时间列都按北京时间显示，
 * 两处对不上就会被当成「刷新记录错乱了」。空值/坏值返回 `empty`。
 */
export function beijingClock(value: unknown = new Date(), empty = '-'): string {
  const stamp = value instanceof Date ? value : parseServerTime(value);
  if (!stamp || Number.isNaN(stamp.getTime())) return empty;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: BUSINESS_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(stamp);
}

/** 返回业务时区下的 YYYY-MM-DD，可直接用于 <input type="date"> 的 value。 */
export function todayLocal(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: BUSINESS_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const pick = (type: string) => parts.find(part => part.type === type)?.value ?? '';
  const year = pick('year');
  const month = pick('month');
  const day = pick('day');
  if (!year || !month || !day) {
    // Intl 异常时退化为本地日期，宁可差一天也不要返回空字符串把表单打空
    return now.toLocaleDateString('sv-SE');
  }
  return `${year}-${month}-${day}`;
}
