/**
 * F2/F3 图片降载：经 /api/image-proxy 的 CDN 图片加载失败时，把 <img> 替换为
 * 固定尺寸的占位块。文案如实说明"图暂不可用"（CDN 防盗链拒绝时常见），
 * 而不是留下裂图或误导性的"无图"。
 */

const PLACEHOLDER_TEXT = '图暂不可用';

export const imageErrorPlaceholder = (event: { currentTarget: HTMLImageElement }): void => {
  const img = event.currentTarget;
  if (!img || img.dataset.placeholderDone) return;
  img.dataset.placeholderDone = '1';
  const rect = img.getBoundingClientRect();
  const width = Math.round(rect.width || img.width || 68);
  const height = Math.round(rect.height || img.height || 68);
  const box = document.createElement('div');
  box.textContent = PLACEHOLDER_TEXT;
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', PLACEHOLDER_TEXT);
  box.dataset.placeholderDone = '1';
  box.style.cssText = [
    `width:${width}px`,
    `height:${height}px`,
    'background:#f0f0f0',
    'border-radius:4px',
    'display:flex',
    'align-items:center',
    'justify-content:center',
    'color:#bbb',
    'font-size:11px',
    'flex:none',
    'overflow:hidden',
  ].join(';');
  img.replaceWith(box);
};
