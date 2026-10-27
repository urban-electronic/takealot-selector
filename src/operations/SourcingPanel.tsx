import { useEffect, useRef, useState } from 'react';
import { updateProduct } from '../remoteApi';
import type { Product } from '../types';
import { normalizePurchaseUrl, purchaseHostLabel, sourcingKeyword, sourcingLinks } from './sourcing';

/** 补货侧栏顶部的「采购货源」块。
 *
 * 场景：运营在补货页看到某商品要补 N 件，下一步就是去 1688 找货/下单。
 * 原来要先关掉侧栏、回产品详情找采购链接，或者自己复制品名去搜。
 *
 * 两条路径都给：已存的采购链接直接打开；没存过的用中文品名去各平台搜。
 * 保存链接就地做，不用跳详情页——但保存只改 purchase_url 这一个字段，
 * 不碰成本与计划参数（后端 PATCH 的 metadata 白名单里必须有 purchase_url，
 * 否则会走"清空 manual_* 再重算"的分支，见 backend/tests/test_metadata_updates.py）。
 */
export default function SourcingPanel({ product }: { product: Product }) {
  const derived = sourcingKeyword(product);
  const [keyword, setKeyword] = useState(derived.keyword);
  const [saved, setSaved] = useState(product.purchase_url ?? '');
  const [draft, setDraft] = useState(product.purchase_url ?? '');
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState('');

  // 侧栏切换商品时重置；父组件会先把 current 置空再取新数据，这里只是兜底，
  // 防止以后有人把侧栏改成"不卸载直接换 product"。
  useEffect(() => {
    const initial = product.purchase_url ?? '';
    setKeyword(sourcingKeyword(product).keyword);
    setSaved(initial);
    setDraft(initial);
    setNote('');
    setProblem('');
  }, [product.id, product.purchase_url, product.chinese_product_name, product.product_name, product.sku]);

  const links = sourcingLinks(keyword);
  const savedUrl = normalizePurchaseUrl(saved);
  const savedLabel = purchaseHostLabel(saved);
  const dirty = draft.trim() !== saved.trim();
  // 提示按「当前输入框里的词」判，不按商品原始字段判：用户手动改成中文之后，
  // 「正在用英文搜」这句就不该再挂着。
  const hasChinese = /[\u4e00-\u9fa5]/.test(keyword);

  const save = async () => {
    if (saving.current) return;
    const trimmed = draft.trim();
    const next = normalizePurchaseUrl(trimmed);
    if (trimmed && !next) {
      setProblem('采购链接要以 http:// 或 https:// 开头');
      setNote('');
      return;
    }
    saving.current = true;
    setBusy(true);
    setProblem('');
    setNote('');
    try {
      const updated = await updateProduct(product.id, { purchase_url: next ?? '' });
      const value = updated.purchase_url ?? '';
      setSaved(value);
      setDraft(value);
      setNote(next ? `已保存采购链接（${purchaseHostLabel(next)}），下次补货直接点开。` : '已清除采购链接。');
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };

  return (
    <section className="card sourcing-panel" aria-labelledby="sourcing-panel-title">
      <div className="sourcing-panel-head">
        <h3 id="sourcing-panel-title">采购货源</h3>
        <small>补货前先去 1688 对货；链接会跟着这个商品一直留着</small>
      </div>

      {savedUrl ? (
        <div className="sourcing-saved">
          <a className="btn btn-primary btn-sm sourcing-link" href={savedUrl} target="_blank" rel="noopener noreferrer">
            打开采购链接 · {savedLabel}
          </a>
        </div>
      ) : (
        <p className="sourcing-empty">
          {saved.trim()
            ? `已存的采购链接不是 http/https 地址（${saved}），请改成完整链接再保存。`
            : '还没有保存采购链接。下面可以直接搜，找到货后把链接粘进来保存。'}
        </p>
      )}

      <div className="sourcing-keyword">
        <label htmlFor="replenishment-sourcing-keyword">搜索关键词</label>
        <input
          id="replenishment-sourcing-keyword"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="中文品名，如 温度控制器"
        />
      </div>
      {!keyword.trim() && derived.source === 'variant_only' && (
        <p className="sourcing-hint">
          这个商品的中文名只有 {derived.variantCount} 个变体标签（{derived.variants.slice(0, 3).join('、')}
          {derived.variantCount > 3 ? '…' : ''}），没有整件的中文品名。填上完整品名再搜，否则搜出来的是别的东西。
        </p>
      )}
      {!keyword.trim() && derived.source === 'none' && (
        <p className="sourcing-hint">这个商品没有中文品名也没有英文标题，先补品名再搜。</p>
      )}
      {!!keyword.trim() && !hasChinese && (
        <p className="sourcing-hint">关键词里没有中文，正在用英文搜。1688 上英文命中率很低，建议改成中文再搜。</p>
      )}

      {!!links.length && (
        <div className="sourcing-platforms">
          {links.map((link) => (
            <a
              key={link.key}
              className="btn btn-outline btn-sm sourcing-link"
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              title={`在${link.label}搜索「${keyword.trim()}」`}
            >
              {link.label} 找货
            </a>
          ))}
        </div>
      )}

      <div className="sourcing-save">
        <label htmlFor="replenishment-sourcing-url">采购链接</label>
        <input
          id="replenishment-sourcing-url"
          type="url"
          inputMode="url"
          disabled={busy}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="粘贴 1688 / 淘宝 / 供应商链接，保存后补货时可直接打开"
        />
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !dirty} onClick={save}>
          {busy ? '保存中…' : draft.trim() ? '保存链接' : '清除链接'}
        </button>
      </div>

      {problem && <p className="sourcing-status sourcing-status-error" role="alert">{problem}</p>}
      {note && <p className="sourcing-status" role="status">{note}</p>}
    </section>
  );
}
