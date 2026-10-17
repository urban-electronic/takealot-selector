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
