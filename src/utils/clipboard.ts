/**
 * 复制文本到剪贴板。
 *
 * Tauri 的 webview 不保证给 clipboard 权限，`navigator.clipboard` 可能直接抛错，
 * 所以失败后退回 textarea + execCommand。返回是否真的复制成功——调用方要据此
 * 显示「已复制 / 复制失败」，不能假装成功。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 继续走降级路径
  }
  try {
    const box = document.createElement('textarea');
    box.value = text;
    box.setAttribute('readonly', '');
    box.style.position = 'fixed';
    box.style.opacity = '0';
    document.body.appendChild(box);
    box.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(box);
    return ok;
  } catch {
    return false;
  }
}
