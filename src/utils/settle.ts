/**
 * 并发请求的"分区降级"工具。
 *
 * 背景：Dashboard 并发 5 个接口、Inventory 并发 4 个，原来都用 `Promise.all`，
 * 任一接口失败就整页进入"加载失败"，其余已经成功的数据一起被丢掉。这些区块
 * 之间没有依赖关系（库存挂了不影响发货单展示），应该各降各的级。
 */

/** 把 reject 的原因转成可读文案（Error / 字符串 / 其它）。 */
export function reasonText(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === 'string') return reason;
  try {
    return JSON.stringify(reason);
  } catch {
    return String(reason);
  }
}

/**
 * 处理一个 allSettled 结果：成功就写入状态，失败就记一条带区块名的说明。
 * 返回是否成功，方便调用方决定是否还要走整页错误态。
 */
export function applySettled<T>(
  result: PromiseSettledResult<T>,
  label: string,
  apply: (value: T) => void,
  failures: string[],
): boolean {
  if (result.status === 'fulfilled') {
    apply(result.value);
    return true;
  }
  failures.push(`${label}：${reasonText(result.reason)}`);
  return false;
}

/** 把失败清单拼成一句给用户看的提示；没有失败时返回空串。 */
export function failureSummary(failures: string[], prefix = '部分数据加载失败'): string {
  return failures.length ? `${prefix}：${failures.join('；')}` : '';
}
