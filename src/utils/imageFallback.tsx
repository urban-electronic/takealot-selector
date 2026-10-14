/**
 * F2/F3 图片降载：经 /api/image-proxy 的 CDN 图片加载失败时，由 React 状态渲染
 * 固定尺寸的占位块。文案如实说明"图暂不可用"（CDN 防盗链拒绝时常见），
 * 而不是留下裂图或误导性的"无图"。
 *
 * 实现约束（评审红线）：不使用 replaceWith / innerHTML 直接替换 React 管理的
 * DOM 节点——占位块完全由 React 渲染，卸载随组件自然清理；src 变化（修改 URL /
 * 列表刷新 / 路由重入）时重置失败状态，让新地址重新尝试加载。
 */
import { useEffect, useState } from 'react';
import type { CSSProperties, FC, HTMLAttributeReferrerPolicy } from 'react';

const PLACEHOLDER_TEXT = '图暂不可用';

export interface ProductImageProps {
  src: string;
  alt?: string;
  className?: string;
  style?: CSSProperties;
  loading?: 'lazy' | 'eager';
  referrerPolicy?: HTMLAttributeReferrerPolicy;
}

export const ProductImage: FC<ProductImageProps> = ({
  src,
  alt = '',
  className,
  style,
  loading = 'lazy',
  referrerPolicy,
}) => {
  // null = 未失败；{w,h} = 失败时测得的原图布局尺寸（占位块沿用，版面不跳变）
  const [failedSize, setFailedSize] = useState<{ w: number; h: number } | null>(null);

  // src 变化时重置失败状态：修改 URL / 切换列表 / 路由卸载重建后重新加载
  useEffect(() => {
    setFailedSize(null);
  }, [src]);

  if (failedSize) {
    return (
      <div
        role="img"
        aria-label={PLACEHOLDER_TEXT}
        title={PLACEHOLDER_TEXT}
        data-image-fallback="1"
        style={{
          width: failedSize.w,
          height: failedSize.h,
          background: '#f0f0f0',
          borderRadius: 4,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#bbb',
          fontSize: 11,
          flex: 'none',
          overflow: 'hidden',
        }}
      >
        {PLACEHOLDER_TEXT}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      className={className}
      style={style}
      loading={loading}
      referrerPolicy={referrerPolicy}
      onError={(event) => {
        if (failedSize) return;
        const img = event.currentTarget;
        const rect = img.getBoundingClientRect();
        const w = Math.round(rect.width || img.width || 68);
        const h = Math.round(rect.height || img.height || 68);
        setFailedSize({ w, h });
      }}
    />
  );
};
