/**
 * 兰特汇率（展示用）。
 *
 * 三个要点：
 * 1. **先给值，再去拿**。初始值从 localStorage 里读上次的汇率，所以刷新页面不会出现
 *    「先显示 S$99.9、一秒后跳成 R1,282」的闪动。
 * 2. **拿不到就用兜底**。后端拉不到上游汇率时会自己降级（旧缓存 → 内置常量），
 *    前端再兜一层：请求本身失败（离线、桌面版没有后端）时用 `FALLBACK_FX`，
 *    保证这一列永远算得出兰特。
 * 3. **模块级缓存**。列表页和详情页都会用到，一次会话里只请求一次；
 *    后端自己还有 24 小时缓存，所以这个请求正常情况下不打上游。
 */

import { useEffect, useState } from 'react';

import { FALLBACK_FX, type FxRates } from './marketPrice';
import { getFxRates } from '../remoteApi';
import { safeSetItem } from './storage';

const CACHE_KEY = 'ck:fx:zar';
/** 同一次会话内的复用窗口：来回切页面不必每次都问后端。 */
const MEMO_MS = 10 * 60 * 1000;

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
    const stamp = Date.parse(value.fetched_at || '');
    return { ...(value as FxRates), stale: Boolean(value.stale) ||
      !Number.isFinite(stamp) || Date.now() - stamp >= 24 * 60 * 60 * 1000 };
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
export function currentFxRates(): FxRates {
  return memo || readLocal() || FALLBACK_FX;
}

export function useFxRates(): FxRates {
  const [fx, setFx] = useState<FxRates>(currentFxRates);

  useEffect(() => {
    let alive = true;
    if (memo && Date.now() - memoAt < MEMO_MS) return;
    getFxRates()
      .then((next) => {
        if (!alive || !next || !next.rates) return;
        memo = next;
        memoAt = Date.now();
        setFx(next);
        safeSetItem(CACHE_KEY, JSON.stringify(next));
      })
      .catch(() => {
        // 离线 / 桌面版没有后端：保留本地缓存或兜底值，不打扰用户
      });
    return () => {
      alive = false;
    };
  }, []);

  return fx;
}
