/**
 * 详情页底部的「同类商品 / 配套周边」推荐区。
 *
 * 三件必须让人看见的事：
 * 1. **为什么推这条**——每张卡片下面列命中的依据（同一费率类别 / 标题重合几个词 /
 *    常见配套 / 曾同批采购），看不到理由的推荐没人敢照着往下选。
 * 2. **推的是什么口径**——底部「推荐依据」展开后有策略、权重、阈值、数量上限、
 *    候选数与去重规则，跟后端 meta 一一对应，不是写死的一段文案。
 * 3. **没推是有原因的**——空态写清「没有达到阈值的推荐」，而不是留一片空白
 *    让人以为坏了。
 *
 * 数据来源见 DataSourceContext：远程走 `/api/products/{id}/recommendations`，
 * 本地走 utils/productRecommend 的同口径实现。
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from './DataSourceContext';
import type { RecommendationItem, RecommendationResult } from './types';
import { formatPercent, formatPrice } from './types';
import { ProductImage } from './utils/imageFallback';

/** 缩略图宽度：卡片里只有 72px，取 160 够二倍屏，别再把原图拉下来 */
const THUMB_WIDTH = 160;

const percent = (ratio: number) => `${Math.round(ratio * 100)}%`;

function ItemCard({
  item,
  imageUrl,
  onOpen,
}: {
  item: RecommendationItem;
  imageUrl: string;
  onOpen: (id: string) => void;
}) {
  const [hover, setHover] = useState(false);
  // 卡片整体是个 <button>：键盘可达、有原生焦点圈。里面只用 <span>（button 内
  // 只允许短语内容），靠 display 排版，避免把 <div>/<ul> 塞进按钮里。
  return (
    <button
      type="button"
      onClick={() => onOpen(item.product_id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={item.name || item.chinese_name}
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        textAlign: 'left',
        padding: 10,
        borderRadius: 10,
        border: `1px solid ${hover ? 'var(--color-primary)' : 'var(--color-border)'}`,
        background: 'var(--color-card)',
        cursor: 'pointer',
        minWidth: 0,
      }}
    >
      <span style={{ width: 72, height: 72, flex: 'none', borderRadius: 8, overflow: 'hidden', background: 'var(--surface-muted)', display: 'block' }}>
        {item.image_url ? (
          <ProductImage
            src={imageUrl}
            alt={item.name || item.chinese_name || '商品图'}
            style={{ width: 72, height: 72, objectFit: 'cover', display: 'block' }}
          />
        ) : null}
      </span>
      <span style={{ minWidth: 0, flex: 1, display: 'block' }}>
        <span
          style={{
            display: '-webkit-box',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--color-text)',
            lineHeight: 1.4,
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {item.chinese_name || item.name || '（无标题）'}
        </span>
        <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
          {item.product_no != null ? `#${item.product_no}` : '—'}
          {item.sku ? ` · ${item.sku}` : ''}
          {item.plid ? ` · PLID ${item.plid}` : ''}
        </span>
        <span style={{ display: 'flex', fontSize: 12, marginTop: 4, gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>{formatPrice(item.price_zar, 'ZAR')}</span>
          <span style={{ color: 'var(--text-muted)' }}>利润率 {formatPercent(item.profit_margin)}</span>
        </span>
        {item.reasons.length > 0 && (
          <span style={{ display: 'flex', marginTop: 6, flexWrap: 'wrap', gap: 4 }}>
            {item.reasons.map((reason) => (
              <span
                key={reason}
                style={{
                  fontSize: 11,
                  lineHeight: 1.5,
                  padding: '1px 6px',
                  borderRadius: 999,
                  background: 'var(--surface-muted)',
                  color: 'var(--text-muted)',
                  border: '1px solid var(--color-border)',
                }}
              >
                {reason}
              </span>
            ))}
          </span>
        )}
      </span>
    </button>
  );
}

function Column({
  title,
  hint,
  items,
  imageUrl,
  onOpen,
  emptyText,
}: {
  title: string;
  hint: string;
  items: RecommendationItem[];
  imageUrl: (url: string) => string;
  onOpen: (id: string) => void;
  emptyText: string;
}) {
  return (
    <div style={{ flex: '1 1 320px', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 14, color: 'var(--color-text)' }}>{title}</strong>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{hint}</span>
      </div>
      {items.length ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
          {items.map((item) => (
            <ItemCard
              key={item.product_id}
              item={item}
              imageUrl={imageUrl(item.image_url)}
              onOpen={onOpen}
            />
          ))}
        </div>
      ) : (
        <div
          style={{
            fontSize: 12,
            color: 'var(--text-muted)',
            padding: '12px 14px',
            borderRadius: 10,
            border: '1px dashed var(--color-border)',
            lineHeight: 1.6,
          }}
        >
          {emptyText}
        </div>
      )}
    </div>
  );
}

export default function ProductRecommendations({ productId }: { productId: string }) {
  const api = useApi();
  const navigate = useNavigate();
  const [result, setResult] = useState<RecommendationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showMeta, setShowMeta] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    api.getRecommendations(productId)
      .then((data) => { if (alive) setResult(data); })
      // 推荐挂了不牵连详情页主体：这里只降级成本区域的提示
      .catch((e: Error) => { if (alive) setError(e?.message || '推荐加载失败'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, productId, nonce]);

  const meta = result?.meta;
  const total = (result?.similar.length || 0) + (result?.complement.length || 0);

  return (
    <div className="card" data-recommendation-block="1">
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <span>同类商品与配套周边</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10, fontWeight: 400 }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {loading ? '计算中…' : `共 ${total} 条`}
          </span>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setNonce((n) => n + 1)}
            disabled={loading}
          >
            重新计算
          </button>
        </span>
      </div>

      {error ? (
        <div style={{ fontSize: 13, color: 'var(--color-danger)', lineHeight: 1.6 }}>
          推荐暂时算不出来：{error}
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            推荐只影响这一块，商品详情与编辑不受影响；可以点「重新计算」再试一次。
          </div>
        </div>
      ) : loading && !result ? (
        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>正在按品类、价格带、配套关系与历史采购共现计算…</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <Column
              title="同类商品"
              hint={meta ? `同一品类/属性接近，最多 ${meta.limits.similar} 条` : ''}
              items={result?.similar || []}
              imageUrl={(url) => api.getImageUrl(url, THUMB_WIDTH)}
              onOpen={(id) => navigate(`/products/${id}`)}
              emptyText="没有达到阈值的同类商品。库里同品类且属性接近的还没录入，或者分数低于阈值——宁可不推，也不拿不相干的凑数。"
            />
            <Column
              title="配套周边"
              hint={meta ? `配件/互补品，最多 ${meta.limits.complement} 条` : ''}
              items={result?.complement || []}
              imageUrl={(url) => api.getImageUrl(url, THUMB_WIDTH)}
              onOpen={(id) => navigate(`/products/${id}`)}
              emptyText="没有识别到配套商品。判据是标题里的配件词（发网、电池、滤芯…）与静态组合表，只看标题、不猜。"
            />
          </div>

          {meta && (
            <div style={{ marginTop: 14, borderTop: '1px solid var(--color-border)', paddingTop: 10 }}>
              <button
                type="button"
                onClick={() => setShowMeta((v) => !v)}
                style={{
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  cursor: 'pointer',
                  fontSize: 12,
                  color: 'var(--color-primary)',
                }}
              >
                {showMeta ? '收起推荐依据' : '推荐依据（来源、权重、排序、去重、上限）'}
              </button>
              {showMeta && (
                <table style={{ marginTop: 8, fontSize: 12 }}>
                  <tbody>
                    <tr>
                      <td style={{ fontWeight: 600, width: 96, verticalAlign: 'top' }}>数据来源</td>
                      <td>本店在架商品库（{meta.candidate_count} 件）+ 采购记录的同批共现；只读，不改任何数据</td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>打分维度</td>
                      <td>
                        品类关联 {percent(meta.weights.category)} · 属性相似 {percent(meta.weights.attribute)} ·
                        搭配互补 {percent(meta.weights.complement)} · 行为共现 {percent(meta.weights.behavior)}
                      </td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>排序依据</td>
                      <td>加权总分从高到低；同分按商品编号倒序（新录入的排前面）</td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>入榜阈值</td>
                      <td>
                        总分 ≥ {percent(meta.min_score)}。{meta.candidate_count} 件候选里 {meta.scored_count} 件过线，
                        其余不凑数
                      </td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>数量上限</td>
                      <td>同类最多 {meta.limits.similar} 条，配套最多 {meta.limits.complement} 条</td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>去重规则</td>
                      <td>{meta.dedup}</td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600, verticalAlign: 'top' }}>策略</td>
                      <td>{meta.strategy}</td>
                    </tr>
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
