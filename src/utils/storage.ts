/**
 * localStorage 安全写入。
 *
 * 背景：`localStorage.setItem` 在两种常见情况下会抛异常——
 *   1. 配额写满（QuotaExceededError）
 *   2. 隐私模式 / 存储被禁用（SecurityError）
 *
 * 项目里已有十几处写入点，其中若干处在 `useEffect` 里、并且每次勾选或输入都会触发
 * （例如产品列表的勾选集合、装箱草稿）。一旦抛错，effect 直接失败，整页白屏，
 * 而用户看到的现象只是"点一下就崩了"，很难联想到是存储配额。
 *
 * 因此这里统一吞掉异常并返回布尔值：调用方需要感知时可以用返回值，
 * 不需要感知时就不必再包 try/catch。
 */

/** 写入；成功返回 true。失败不抛错。 */
export function safeSetItem(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** 读取；失败返回 null。 */
export function safeGetItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** 删除；失败返回 false。 */
export function safeRemoveItem(key: string): boolean {
  try {
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}
