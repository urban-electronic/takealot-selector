/**
 * 详情页的「站外货源」区：1688 / Takealot 上看到的东西，**不在自己仓库里**。
 *
 * 和 ProductRecommendations 的分工：
 * - 那个是「自家库里的邻居」——同一品类、属性接近、买过一起；
 * - 这个是「站外看到的」——1688 的进货价、Takealot 上别人卖多少钱。
 *   自家库里根本没有这些，所以必须单独一块，不能混在一起展示（混了会让人
 *   以为这些也是自己上架过的商品）。
 *
 * 两个平台**不做实时抓取**：Takealot 的搜索页返回 Cloudflare 人机校验、
 * 1688 返回跳转壳，绕过去不是本项目要做的事。所以这里的数据靠登记，
 * 界面上把「为什么挂在这条商品下面」和「去站外搜」都摆出来。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useApi } from './DataSourceContext';
import type { ExternalOffer, ExternalOfferInput, ExternalOfferResult, ExternalPlatform } from './types';
import { formatPrice } from './types';

const PLATFORM_LABEL: Record<string, string> = {
  '1688': '1688',
  takealot: 'Takealot',
};

const PLATFORM_HINT: Record<string, string> = {
  '1688': '采购侧 · 人民币批发价',
  takealot: '零售侧 · 兰特售价',
};

/** 站外搜索入口：本机浏览器能正常打开，程序里不去抓（会被校验挡住） */
function searchUrl(platform: ExternalPlatform, keyword: string): string {
  const q = encodeURIComponent(keyword);
  return platform === '1688'
    ? `https://s.1688.com/selloffer/offer_search.htm?keywords=${q}`
    : `https://www.takealot.com/all?q=${q}`;
}

function Badge({ platform }: { platform: string }) {
  const is1688 = platform === '1688';
  // 配色走 ui-tokens.css 的平台令牌，不在组件里写裸色值（换主题时只改一处）
  const tone = is1688
    ? { color: 'var(--platform-1688-text)', background: 'var(--platform-1688-bg)', borderColor: 'var(--platform-1688-border)' }
    : { color: 'var(--platform-takealot-text)', background: 'var(--platform-takealot-bg)', borderColor: 'var(--platform-takealot-border)' };
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 600,
        lineHeight: 1.5,
        padding: '1px 6px',
        borderRadius: 4,
        flex: 'none',
        borderStyle: 'solid',
        borderWidth: 1,
        ...tone,
      }}
    >
      {PLATFORM_LABEL[platform] || platform}
    </span>
  );
}

function OfferCard({
  offer,
  onArchive,
  onOpen,
}: {
  offer: ExternalOffer;
  onArchive: (id: string) => void;
  onOpen: (url: string) => void;
}) {
  const currency = offer.currency === 'CNY' ? 'CNY' : 'ZAR';
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        padding: 10,
        borderRadius: 10,
        border: '1px solid var(--color-border)',
        background: 'var(--color-card)',
        minWidth: 0,
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <Badge platform={offer.platform} />
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--color-text)',
              lineHeight: 1.4,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={offer.title || offer.title_cn}
          >
            {offer.title_cn || offer.title || '（未填标题）'}
          </span>
        </div>

        <div style={{ fontSize: 12, marginTop: 4, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'baseline' }}>
          <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>
            {offer.price != null ? formatPrice(offer.price, currency) : '价格未填'}
          </span>
          {offer.moq != null && <span style={{ color: 'var(--text-muted)' }}>起订 {offer.moq} 件</span>}
          <span style={{ color: 'var(--text-muted)' }}>{PLATFORM_HINT[offer.platform] || ''}</span>
        </div>

        {(offer.usage || offer.scenario) && (
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.6 }}>
            {offer.usage ? `用途：${offer.usage}` : ''}
            {offer.usage && offer.scenario ? ' · ' : ''}
            {offer.scenario ? `场景：${offer.scenario}` : ''}
          </div>
        )}
        {offer.reason && (
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2, lineHeight: 1.6 }}>
            理由：{offer.reason}
          </div>
        )}

        {offer.matched.length > 0 && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6 }}>为什么挂在这里：</span>
            {offer.matched.map((word) => (
              <span
                key={word}
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
                {word}
              </span>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <button type="button" className="btn btn-outline btn-sm" onClick={() => onOpen(offer.url)}>
            打开链接
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onArchive(offer.id)}
            title="只是从这里下线，记录本身保留"
          >
            移除
          </button>
        </div>
      </div>
    </div>
  );
}

function Column({
  title,
  hint,
  offers,
  emptyText,
  onArchive,
  onOpen,
}: {
  title: string;
  hint: string;
  offers: ExternalOffer[];
  emptyText: string;
  onArchive: (id: string) => void;
  onOpen: (url: string) => void;
}) {
  return (
    <div style={{ flex: '1 1 320px', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 14, color: 'var(--color-text)' }}>{title}</strong>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{hint}</span>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>共 {offers.length} 条</span>
      </div>
      {offers.length ? (
        // minmax 的列宽必须用 min(…, 100%)：320px 窄屏时容器只有 262px，
        // 写死 280px 会让每一列都撑破容器（文字重叠审计会报「内容比盒子宽」）。
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(260px, 100%), 1fr))', gap: 8 }}>
          {offers.map((offer) => (
            <OfferCard key={offer.id} offer={offer} onArchive={onArchive} onOpen={onOpen} />
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

const EMPTY_FORM: ExternalOfferInput = {
  platform: '1688',
  url: '',
  title: '',
  title_cn: '',
  price: null,
  moq: null,
  keywords: '',
  usage: '',
  scenario: '',
  reason: '',
};

export default function ExternalOffers({
  productId,
  productName,
  productNameCn,
}: {
  productId: string;
  productName?: string;
  productNameCn?: string;
}) {
  const api = useApi();
  const [result, setResult] = useState<ExternalOfferResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [nonce, setNonce] = useState(0);

  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<ExternalOfferInput>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  // 外跳搜索词：可编辑，默认取商品名。商品的中文名常常只是「假发」这种大类词，
  // 拿它直接去 1688 搜会出来一整屏不相干的东西，所以要让人能改成「发网」。
  const [searchCn, setSearchCn] = useState('');
  const [searchEn, setSearchEn] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    api.getExternalOffers(productId)
      .then((data) => { if (alive) setResult(data); })
      .catch((e: Error) => { if (alive) setError(e?.message || '站外货源读取失败'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, productId, nonce]);

  const archive = useCallback(async (id: string) => {
    setNotice('');
    try {
      await api.archiveExternalOffer(id);
      setNotice('已从这里下线（记录保留，可在站外货源管理里恢复）');
      setNonce((n) => n + 1);
    } catch (e) {
      setError((e as Error)?.message || '移除失败');
    }
  }, [api]);

  const submit = useCallback(async () => {
    setFormError('');
    if (!form.url.trim()) {
      setFormError('链接必填');
      return;
    }
    if (!String(form.keywords || '').trim()) {
      // 没有关键词就挂不到任何商品下面，录了也是白录
      setFormError('关键词必填：用它决定这条货源出现在哪些商品下面');
      return;
    }
    setSaving(true);
    try {
      await api.createExternalOffer({
        ...form,
        url: form.url.trim(),
        price: form.price === null || Number.isNaN(form.price) ? null : form.price,
        moq: form.moq === null || Number.isNaN(form.moq) ? null : form.moq,
      });
      setForm(EMPTY_FORM);
      setFormOpen(false);
      setNotice('已添加');
      setNonce((n) => n + 1);
    } catch (e) {
      setFormError((e as Error)?.message || '添加失败');
    } finally {
      setSaving(false);
    }
  }, [api, form]);

  // 搜索关键词：1688 用中文名（没有就退回英文），Takealot 用英文名
  const keywords = useMemo(() => {
    const cn = (productNameCn || '').trim();
    const en = (productName || '').trim();
    return { cn: cn || en, en: en || cn };
  }, [productName, productNameCn]);

  const total = (result?.sourcing.length || 0) + (result?.market.length || 0);

  // 商品换了就把搜索词重置成新商品的名字（否则会带着上一件商品的词）
  useEffect(() => {
    setSearchCn(keywords.cn || '');
    setSearchEn(keywords.en || '');
  }, [keywords.cn, keywords.en]);

  return (
    <div className="card" data-external-offers="1">
      <div
        className="card-title"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span>站外货源</span>
          <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-muted)' }}>
            1688 / Takealot 上看到的商品，不在你自己的仓库里
          </span>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10, fontWeight: 400 }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{loading ? '读取中…' : `共 ${total} 条`}</span>
          <button type="button" className="btn btn-outline btn-sm" onClick={() => setFormOpen((v) => !v)}>
            {formOpen ? '收起' : '登记一条'}
          </button>
        </span>
      </div>

      {notice && (
        <div style={{ fontSize: 12, color: 'var(--color-primary)', marginBottom: 8 }}>{notice}</div>
      )}

      {formOpen && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(200px, 100%), 1fr))',
            gap: 8,
            padding: 12,
            marginBottom: 12,
            borderRadius: 10,
            border: '1px solid var(--color-border)',
            background: 'var(--surface-muted)',
          }}
        >
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            平台
            <select
              value={form.platform}
              onChange={(e) => setForm({ ...form, platform: e.target.value as ExternalPlatform })}
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            >
              <option value="1688">1688（人民币批发价）</option>
              <option value="takealot">Takealot（兰特售价）</option>
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', gridColumn: '1 / -1' }}>
            商品链接（必填，域名要跟平台对上）
            <input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder={form.platform === '1688' ? 'https://detail.1688.com/offer/....html' : 'https://www.takealot.com/.../PLID...'}
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            标题（中文）
            <input
              value={form.title_cn || ''}
              onChange={(e) => setForm({ ...form, title_cn: e.target.value })}
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            标题（原文）
            <input
              value={form.title || ''}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            价格（{form.platform === '1688' ? '¥' : 'R'}）
            <input
              type="number"
              min="0"
              step="0.01"
              value={form.price ?? ''}
              onChange={(e) => setForm({ ...form, price: e.target.value === '' ? null : Number(e.target.value) })}
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          {form.platform === '1688' && (
            <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
              起订量
              <input
                type="number"
                min="0"
                step="1"
                value={form.moq ?? ''}
                onChange={(e) => setForm({ ...form, moq: e.target.value === '' ? null : Number(e.target.value) })}
                style={{ display: 'block', width: '100%', marginTop: 4 }}
              />
            </label>
          )}
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', gridColumn: '1 / -1' }}>
            关键词（必填，逗号分隔；英文按整词、中文按子串命中商品标题）
            <input
              value={form.keywords || ''}
              onChange={(e) => setForm({ ...form, keywords: e.target.value })}
              placeholder="例如：wig cap,发网"
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            用途
            <input
              value={form.usage || ''}
              onChange={(e) => setForm({ ...form, usage: e.target.value })}
              placeholder="戴假发前包住真发"
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
            适用场景
            <input
              value={form.scenario || ''}
              onChange={(e) => setForm({ ...form, scenario: e.target.value })}
              placeholder="lace front、日常佩戴"
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', gridColumn: '1 / -1' }}>
            选择理由
            <input
              value={form.reason || ''}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              placeholder="纯消耗品、复购高、单价低"
              style={{ display: 'block', width: '100%', marginTop: 4 }}
            />
          </label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', gridColumn: '1 / -1' }}>
            <button type="button" className="btn btn-sm" onClick={submit} disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </button>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => { setFormOpen(false); setFormError(''); }}
            >
              取消
            </button>
            {formError && <span style={{ fontSize: 12, color: 'var(--color-danger)' }}>{formError}</span>}
          </div>
        </div>
      )}

      {error ? (
        <div style={{ fontSize: 13, color: 'var(--color-danger)', lineHeight: 1.6 }}>
          站外货源暂时读不到：{error}
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            这块数据只存在服务端，桌面端需要能连上服务端才能看到；它不影响商品详情本身。
            <button
              type="button"
              onClick={() => setNonce((n) => n + 1)}
              style={{ background: 'none', border: 'none', padding: 0, marginLeft: 6, color: 'var(--color-primary)', cursor: 'pointer', fontSize: 12 }}
            >
              重试
            </button>
          </div>
        </div>
      ) : loading && !result ? (
        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>正在按关键词匹配已登记的站外货源…</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <Column
              title="1688 货源"
              hint="进货价参考"
              offers={result?.sourcing || []}
              onArchive={archive}
              onOpen={api.openUrl}
              emptyText={`还没有匹配的 1688 货源。点右上「登记一条」把看中的链接存进来；或者用下面的搜索框去 1688 找——搜到合适的再回来登记。`}
            />
            <Column
              title="Takealot 同类"
              hint="别人卖多少钱"
              offers={result?.market || []}
              onArchive={archive}
              onOpen={api.openUrl}
              emptyText={`还没有匹配的 Takealot 同类。点右上「登记一条」存一条竞品链接；或者用下面的搜索框去看看别人卖多少钱。`}
            />
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            {/* 搜索词可改：商品的中文名经常只是「假发」这种大类词，
                直接拿去 1688 搜会出来一整屏不相干的东西。默认值只是起点。 */}
            <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block' }}>
              1688 搜索词
              <input
                value={searchCn}
                onChange={(e) => setSearchCn(e.target.value)}
                placeholder="例如：发网 / 假发支架"
                style={{ display: 'block', width: 180, marginTop: 4 }}
              />
            </label>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => api.openUrl(searchUrl('1688', searchCn))}
              disabled={!searchCn.trim()}
            >
              去 1688 搜
            </button>
            <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginLeft: 12 }}>
              Takealot 搜索词
              <input
                value={searchEn}
                onChange={(e) => setSearchEn(e.target.value)}
                placeholder="例如：wig cap"
                style={{ display: 'block', width: 240, marginTop: 4 }}
              />
            </label>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => api.openUrl(searchUrl('takealot', searchEn))}
              disabled={!searchEn.trim()}
            >
              去 Takealot 搜
            </button>
          </div>

          {result?.meta && (
            <div
              style={{
                marginTop: 12,
                borderTop: '1px solid var(--color-border)',
                paddingTop: 10,
                fontSize: 12,
                color: 'var(--text-muted)',
                lineHeight: 1.7,
              }}
            >
              已登记 {result.meta.total} 条，本商品命中 {result.meta.matched} 条，未命中{' '}
              {result.meta.unmatched} 条（未命中的不展示，不凑数）。匹配规则：{result.meta.match_rule}；
              排序：{result.meta.sort}；每栏最多 {result.meta.limit_per_platform} 条。
              <br />
              两个平台都不做实时抓取——Takealot 搜索页有人机校验、1688 返回跳转壳，程序里不去绕，
              所以数据靠登记，价格以链接里的实际页面为准。
            </div>
          )}
        </>
      )}
    </div>
  );
}
