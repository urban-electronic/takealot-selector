/**
 * 兰特汇率（展示用）。
 *
 * 四个要点：
 * 1. **先给值，再去拿**。初始值从 localStorage 里读上次的汇率，所以刷新页面不会出现
 *    「先显示 S$99.9、一秒后跳成 R1,282」的闪动。
 * 2. **这是个叶子模块：除了 react，不 import 任何相对路径**。列表页的 jsdom 测试
 *    （`scripts/test-product-pagination.cjs`）用打桩替换 `../remoteApi`，但打桩只作用在
 *    被编译的那一层，嵌套的相对 `.ts` 导入会直接 MODULE_NOT_FOUND。所以加载器和兜底
 *    汇率都由调用方传进来，这里只留 `import type`（编译后不存在）。
 * 3. **拿不到就用兜底**。后端拉不到上游汇率时会自己降级（旧缓存 → 内置常量），
 *    前端再兜一层：没有加载器、或请求失败（离线、桌面版没有后端）时用传进来的兜底值，
 *    保证这一列永远算得出兰特。
 * 4. **模块级缓存**。列表页和详情页都会用到，一次会话里只请求一次；
 *    后端自己还有 24 小时缓存，所以这个请求正常情况下不打上游。
 */

import { useEffect, useState } from 'react';

import type { FxRates } from './marketPrice';

const CACHE_KEY = 'ck:fx:zar';
/** 同一次会话内的复用窗口：来回切页面不必每次都问后端。 */
const MEMO_MS = 10 * 60 * 1000;

export interface FxSource {
  /** 拉汇率；不传就只用缓存/兜底（测试打桩、桌面版没有后端时） */
  load?: () => Promise<FxRates>;
  /** 缓存和请求都拿不到时用的常量汇率 */
  fallback: FxRates;
}

let memo: FxRates | null = null;
let memoAt = 0;

function parseCached(raw: string | null): FxRates | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object') return null;
    const rates = (value as FxRates).rates;
    if (!rates || typeof rates !== 'object') return null;
    const valid = (['SGD', 'CNY'] as const).filter(
      (cur) => typeof rates[cur] === 'number' && (rates[cur] as number) > 0,
    );
    if (!valid.length) return null;
    return { ...(value as FxRates), fallback: false };
  } catch {
    return null;
  }
}

function readLocal(): FxRates | null {
  try {
    return parseCached(localStorage.getItem(CACHE_KEY));
  } catch {
    return null;
  }
}

/** 同步取当前可用的汇率（渲染第一帧就能用，不会先空一帧）。 */
export function currentFxRates(fallback: FxRates): FxRates {
  return memo || readLocal() || fallback;
}

export function useFxRates({ load, fallback }: FxSource): FxRates {
  const [fx, setFx] = useState<FxRates>(() => currentFxRates(fallback));

  useEffect(() => {
    // 没有加载器（测试打桩、或桌面版没有后端）就只用缓存/兜底，不发请求
    if (!load) return;
    if (memo && Date.now() - memoAt < MEMO_MS) return;
    let alive = true;
    load()
      .then((next) => {
        if (!alive || !next || !next.rates) return;
        memo = next;
        memoAt = Date.now();
        setFx(next);
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify(next));
        } catch {
          // 配额满 / 隐私模式：汇率缓存丢了就丢了，下次重新拉，不值得打断页面
        }
      })
      .catch(() => {
        // 离线 / 桌面版没有后端：保留本地缓存或兜底值，不打扰用户
      });
    return () => {
      alive = false;
    };
  }, [load]);

  return fx;
}
