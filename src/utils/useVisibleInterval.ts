import { useEffect, useRef } from 'react';

/**
 * 可见性感知的定时轮询。
 *
 * 背景：页面里原有的 setInterval 在标签页隐藏后仍按原频率打接口
 * （最贵的一处是云端浏览器每 2.5 秒拉一张截图），既浪费额度也让后台采集
 * 的限流更容易被触发。
 *
 * 行为：
 * - 页面隐藏时到点不触发；
 * - 回到前台时，若距上次执行已超过一个间隔，立刻补跑一次，避免看到过期数据；
 * - 回调通过 ref 保持最新，调用方不必为它加 useCallback；
 * - enabled=false 时不注册任何定时器（用于"有会话才轮询"这类场景）。
 */
/**
 * 是否确定处于后台。
 *
 * 用 visibilityState === 'hidden' 而不是 document.hidden：
 * jsdom 默认把页面报成 prerender（hidden 为 true），若按 hidden 判断，
 * 测试环境里的轮询会被整体跳过，测试变成"空跑通过"。
 * 只有明确 hidden 才跳过，prerender / visible 都照常轮询。
 */
export function isPageHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

export function useVisibleInterval(callback: () => void, intervalMs: number, enabled = true): void {
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;
    if (typeof document === 'undefined') return;

    let lastRunAt = Date.now();
    const run = () => {
      lastRunAt = Date.now();
      latest.current();
    };

    // 用全局 setInterval 而非 window.setInterval：浏览器里两者等价，
    // 但 jsdom 测试是通过替换 globalThis.setInterval 来驱动轮询的。
    const timer = setInterval(() => {
      if (isPageHidden()) return;
      run();
    }, intervalMs);

    const onVisibilityChange = () => {
      if (isPageHidden()) return;
      if (Date.now() - lastRunAt >= intervalMs) run();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [intervalMs, enabled]);
}
