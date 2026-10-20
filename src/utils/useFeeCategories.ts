/**
 * 费率品类下拉的数据源。
 *
 * 为什么单独抽出来：`/api/fee-categories` 读失败时，新建产品页与详情页原来都是
 * `.catch(() => {})` —— 下拉只剩「-- 请选择 --」，用户看到的是「没有品类可选」，
 * 而不是「这一次没读到」。同一个接口在设置页是会报错的（`Settings.tsx` 用
 * Promise.all + setError），同一份数据在两个地方给出两种结论。
 *
 * 这里只把「读失败」变成一个可显示的状态，由页面决定怎么呈现并提供重试；
 * 不改变任何读取口径，也不兜造数据。
 */
import { useCallback, useEffect, useState } from 'react';
import type { FeeCategory } from '../types';

export const FEE_CATEGORY_LOAD_ERROR = '费率品类读取失败，下拉是空的——不是没有品类，请重试';

export interface FeeCategorySource {
  getFeeCategories: () => Promise<FeeCategory[]>;
}

export interface FeeCategoryState {
  categories: FeeCategory[];
  /** 空串表示最近一次读取成功。 */
  error: string;
  loading: boolean;
  reload: () => void;
}

export function useFeeCategories(source: FeeCategorySource): FeeCategoryState {
  const [categories, setCategories] = useState<FeeCategory[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // 用 Promise.resolve().then 包一层：读取函数同步抛错时也要落进 catch，
    // 否则异常会从 effect 里冒出去，界面依旧什么提示都没有。
    Promise.resolve()
      .then(() => source.getFeeCategories())
      .then((rows) => {
        if (cancelled) return;
        setCategories(Array.isArray(rows) ? rows : []);
        setError('');
      })
      .catch(() => {
        // 已经拿到的列表保留：重试失败不该把上一次成功的结果也清空。
        if (!cancelled) setError(FEE_CATEGORY_LOAD_ERROR);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [source, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  return { categories, error, loading, reload };
}
