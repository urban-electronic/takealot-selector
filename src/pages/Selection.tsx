/**
 * 选品池 —— 「上传表 → 抓主图/类目 → 填重尺和成本 → 一键进产品列表」
 *
 * 和「新建产品」的分工
 * --------------------
 * 老流程是：筛链接 → 识图找供货商 → 大致核算 → 新建产品 → 填链接和重尺 → 保存。
 * 那一步「新建产品」其实是**记录**，筛选全在别处做，两边不通。
 * 这个页面把筛选和记录接在一起：候选先进选品池（不进 `products`，不污染库存和
 * 装箱），核算通过的勾中后才复制成正式产品。
 *
 * 三个入口都能吃
 * --------------
 * - 只有链接（竞品店铺导出）：PLID 从链接里取，主图和类目走公开接口
 * - 只有 TSIN（Takealot 销售报表）：卖家 API 反查 PLID，重尺也能一起带出来
 * - 链接 + 销售数据（自己整理的表）：档位按帕累托算，和原来那份分析表一致
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ProductImage } from '../utils/imageFallback';
import {
  bulkPatchSelection,
  deleteSelectionBatch,
  enrichSelection,
  getFeeCategories,
  getImageUrl,
  getSelectionBatches,
  getSelectionItems,
  getSelectionSummary,
  importSelectionTable,
  patchSelectionItem,
  probeSelectionCatalogue,
  probeSelectionPublicApi,
  promoteSelection,
  selectQualifiedSelection,
} from '../remoteApi';
import { useFeeCategories } from '../utils/useFeeCategories';
import { SHIPPING_METHODS, formatPercent, formatPrice } from '../types';
import type { CatalogueProbeReport, PublicApiProbeReport } from '../remoteApi';
import type {
  FeeCategory,
  SelectionBatch,
  SelectionEnrichResult,
  SelectionImportResult,
  SelectionItem,
  SelectionItemPatch,
  SelectionSummary,
} from '../types';

const PAGE_SIZE = 24;

const TIER_STYLE: Record<string, { label: string; className: string }> = {
  S: { label: 'S 档', className: 'tier-s' },
  A: { label: 'A 档', className: 'tier-a' },
  B: { label: 'B 档', className: 'tier-b' },
  C: { label: 'C 档', className: 'tier-c' },
};

const DIMENSION_SOURCE_LABEL: Record<string, string> = {
  product_list: '产品列表已有',
  seller_api: '卖家后台',
  catalogue_api: '卖家目录',
  public_api: '公开页预填',
  manual: '人工填写',
};

const STATUS_OPTIONS = ['待核算', '合格', '不合格', '已建产品'];

// useFeeCategories 的 effect 依赖 source 本身，传对象字面量会每渲染一次就重读一遍，
// 所以放在模块作用域固定住。
const FEE_CATEGORY_SOURCE = { getFeeCategories };

/** 数值输入：本地保留字符串，失焦或回车才提交，避免每敲一个字符发一次请求。 */
function NumberCell({
  label, value, unit, step, onCommit, disabled, hint,
}: {
  label: string;
  value: number | null;
  unit?: string;
  step?: number;
  onCommit: (next: number | null) => void;
  disabled?: boolean;
  /** 字段由别的字段算出来时，在标签后标一句来源（如「= 单价×数量」）。 */
  hint?: string;
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value));
  useEffect(() => { setDraft(value == null ? '' : String(value)); }, [value]);

  const commit = () => {
    const text = draft.trim();
    if (text === '') {
      if (value != null) onCommit(null);
      return;
    }
    const parsed = Number(text.replace(',', '.'));
    if (!Number.isFinite(parsed)) { setDraft(value == null ? '' : String(value)); return; }
    if (parsed !== value) onCommit(parsed);
  };

  return (
    <label className="selection-field">
      <span>
        {label}{unit ? <i>{unit}</i> : null}
        {hint ? <i title={hint}>{hint}</i> : null}
      </span>
      <input
        type="number"
        inputMode="decimal"
        step={step ?? 1}
        value={draft}
        disabled={disabled}
        title={hint}
        onChange={event => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={event => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}
      />
    </label>
  );
}

function MarginBadge({ item }: { item: SelectionItem }) {
  if (item.promoted_product_id) return <span className="selection-margin is-done">已建产品</span>;
  if (item.profit_margin == null) return <span className="selection-margin is-pending">待补数据</span>;
  const ok = item.profit_margin >= 0.15;
  return (
    <span className={`selection-margin ${ok ? 'is-ok' : 'is-bad'}`}>
      {ok ? '利润率' : '利润率'} {formatPercent(item.profit_margin)}
    </span>
  );
}

function SelectionCard({
  item, busy, onPatch, onToggle, feeCategories,
}: {
  item: SelectionItem;
  busy: boolean;
  onPatch: (patch: SelectionItemPatch) => void;
  onToggle: () => void;
  feeCategories: FeeCategory[];
}) {
  const tier = TIER_STYLE[item.tier];
  const locked = Boolean(item.promoted_product_id);
  const dimsMissing = item.length_mm == null || item.width_mm == null
    || item.height_mm == null || item.actual_weight_kg == null;
  // 供货单价填了就以它为准：采购价 = 单价 × 数量。后端 _calc 里是同一个口径，
  // 这里算一份只为把值立刻显示出来，不用等一次往返。
  const derivedCost = item.unit_price_cny == null
    ? null
    : Math.round(item.unit_price_cny * (item.purchase_quantity || 4) * 100) / 100;

  return (
    <article className={`selection-card ${item.selected ? 'is-selected' : ''} ${locked ? 'is-locked' : ''}`}>
      <header>
        <label className="selection-pick">
          {/* 无障碍名直接挂在 input 上。原来是一个 .visually-hidden 的 span 装
              「选中 + 商品名」——sr-only 本身没问题，但那段长英文标题会被版式审计
              判成「文字被裁且无省略号」，还把卡片的 scrollWidth 撑宽 20px。 */}
          <input
            type="checkbox"
            checked={item.selected}
            disabled={locked}
            onChange={onToggle}
            aria-label={`选中 ${item.product_name || item.plid}`}
          />
        </label>
        {tier ? <span className={`selection-tier ${tier.className}`}>{tier.label}</span> : null}
        {item.already_in_products ? <span className="selection-flag is-dup">已在产品列表</span> : null}
        {item.status === '不合格' ? <span className="selection-flag is-bad">不建议</span> : null}
      </header>

      <div className="selection-thumb">
        {item.product_image_url
          ? <ProductImage src={getImageUrl(item.product_image_url, 260)} alt={item.product_name} loading="lazy" />
          : <div className="selection-thumb-empty">{item.enrich_status === 'failed' ? '没抓到主图' : '未抓取'}</div>}
      </div>

      <h3 title={item.product_name}>{item.product_name || item.chinese_product_name || '（没有商品名）'}</h3>

      <div className="selection-meta">
        {item.tsin ? <span>TSIN {item.tsin}</span> : null}
        {item.plid ? <span>PLID {item.plid}</span> : null}
        {item.review_count != null ? <span>{item.review_count} 条评价</span> : null}
        {item.rating_value != null ? <span>★ {item.rating_value.toFixed(1)}</span> : null}
      </div>

      <div className="selection-price">
        <strong>{formatPrice(item.actual_sale_price_zar, 'ZAR')}</strong>
        {item.sales_qty ? <span>{item.sales_qty} 件 · 净销售 {formatPrice(item.net_sales_zar, 'ZAR')}</span> : null}
      </div>

      <div className="selection-category">
        <span className={`selection-chip confidence-${item.fee_category_confidence || 'low'}`}
          title={item.fee_category_reason || item.takealot_category_path}>
          {item.fee_category || '类目待定'}
        </span>
        {item.takealot_category_path
          ? <small title={item.takealot_category_path}>{item.takealot_category_path.split(' > ').slice(-2).join(' > ')}</small>
          : null}
      </div>

      <div className="selection-grid">
        <NumberCell label="长" unit="mm" value={item.length_mm} disabled={locked}
          onCommit={next => onPatch({ length_mm: next })} />
        <NumberCell label="宽" unit="mm" value={item.width_mm} disabled={locked}
          onCommit={next => onPatch({ width_mm: next })} />
        <NumberCell label="高" unit="mm" value={item.height_mm} disabled={locked}
          onCommit={next => onPatch({ height_mm: next })} />
        <NumberCell label="重量" unit="kg" step={0.01} value={item.actual_weight_kg} disabled={locked}
          onCommit={next => onPatch({ actual_weight_kg: next })} />
        {/* 顺序按「单价 × 数量 = 采购价」排：先填单价和数量，采购价自动出来，
            不用先算总价再除。 */}
        <NumberCell label="供货单价" unit="¥" step={0.01} value={item.unit_price_cny} disabled={locked}
          onCommit={next => onPatch({ unit_price_cny: next })} />
        <NumberCell label="采购数量" value={item.purchase_quantity} disabled={locked}
          onCommit={next => onPatch({ purchase_quantity: next })} />
        <NumberCell label="采购价" unit="¥" step={0.01}
          value={derivedCost ?? item.purchase_cost_cny}
          disabled={locked || derivedCost != null}
          hint={derivedCost != null ? '= 单价×数量' : undefined}
          onCommit={next => onPatch({ purchase_cost_cny: next })} />
        <NumberCell label="国内运费" unit="¥" step={0.01} value={item.purchase_shipping_cny} disabled={locked}
          onCommit={next => onPatch({ purchase_shipping_cny: next })} />
      </div>

      <div className="selection-row">
        <label className="selection-field is-wide">
          <span>物流方式</span>
          <select value={item.shipping_method || ''} disabled={locked}
            onChange={event => onPatch({ shipping_method: event.target.value || undefined })}>
            <option value="">未选择</option>
            {SHIPPING_METHODS.map(method => <option key={method} value={method}>{method}</option>)}
          </select>
        </label>
        <label className="selection-field is-wide">
          <span>类目</span>
          {/* 原来是自由输入框，得手打类目名，打错一个字就匹配不上费率。
              改成下拉，直接列全部 Fee 类目并带上成功率，选一个就行。 */}
          <select value={item.fee_category} disabled={locked}
            onChange={event => onPatch({ fee_category: event.target.value })}>
            <option value="">未匹配</option>
            {/* 库里存的值不在类目表中（改过名、已停用、或历史手输的）时补一条，
                否则下拉会显示成「未匹配」，看起来像这件货没设过类目。 */}
            {item.fee_category && !feeCategories.some(fc => fc.name === item.fee_category) && (
              <option value={item.fee_category}>{item.fee_category}（当前值）</option>
            )}
            {feeCategories.map(fc => (
              <option key={fc.id} value={fc.name}>
                {fc.name}（{(fc.success_fee_rate * 100).toFixed(0)}%）
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="selection-result">
        <MarginBadge item={item} />
        <dl>
          <div><dt>计费重</dt><dd>{item.chargeable_weight_kg != null ? `${item.chargeable_weight_kg} kg` : '-'}</dd></div>
          <div><dt>到仓成本</dt><dd>{formatPrice(item.cost_to_sa_warehouse_zar, 'ZAR')}</dd></div>
          <div><dt>总成本</dt><dd>{formatPrice(item.total_cost_zar, 'ZAR')}</dd></div>
          <div><dt>单件利润</dt><dd>{formatPrice(item.profit_zar, 'ZAR')}</dd></div>
        </dl>
      </div>

      <footer>
        <div className="selection-foot-note">
          {dimsMissing
            ? <span className="is-warn">缺重尺，算不出运费</span>
            : <span>重尺来源：{DIMENSION_SOURCE_LABEL[item.dimensions_source] || '—'}</span>}
          {item.enrich_message ? <span className="is-warn" title={item.enrich_message}>{item.enrich_message}</span> : null}
        </div>
        <div className="selection-foot-actions">
          {item.takealot_url
            ? <a href={item.takealot_url} target="_blank" rel="noreferrer">前台</a>
            : null}
          {item.purchase_url
            ? <a href={item.purchase_url} target="_blank" rel="noreferrer">货源</a>
            : null}
          {item.promoted_product_id
            ? <Link to={`/products/${item.promoted_product_id}`}>查看产品</Link>
            : null}
          {busy ? <span className="selection-busy">保存中…</span> : null}
        </div>
      </footer>
    </article>
  );
}

export default function Selection() {
  const [batches, setBatches] = useState<SelectionBatch[]>([]);
  const [batchId, setBatchId] = useState('');
  const [items, setItems] = useState<SelectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<SelectionSummary | null>(null);
  const [page, setPage] = useState(0);

  const [status, setStatus] = useState('');
  const [keyword, setKeyword] = useState('');
  const [keywordDraft, setKeywordDraft] = useState('');
  const [sort, setSort] = useState('tier');
  const [onlySelected, setOnlySelected] = useState(false);
  const [hideDuplicates, setHideDuplicates] = useState(false);

  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<{ kind: 'info' | 'warn' | 'ok'; text: string } | null>(null);
  const [importResult, setImportResult] = useState<SelectionImportResult | null>(null);
  const [enrichResult, setEnrichResult] = useState<SelectionEnrichResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [batchName, setBatchName] = useState('');
  const [bulkShipping, setBulkShipping] = useState('');
  const [probing, setProbing] = useState(false);
  const [probeReport, setProbeReport] = useState<CatalogueProbeReport | null>(null);
  const [publicProbing, setPublicProbing] = useState(false);
  const [publicProbe, setPublicProbe] = useState<PublicApiProbeReport | null>(null);
  // 类目下拉的候选：全量 Fee 类目，卡片里选一个就带出成功率。
  const feeCategoryState = useFeeCategories(FEE_CATEGORY_SOURCE);

  const loadList = useCallback(async () => {
    const result = await getSelectionItems({
      batch_id: batchId || undefined,
      status: status || undefined,
      keyword: keyword || undefined,
      sort,
      only_selected: onlySelected,
      hide_duplicates: hideDuplicates,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
    setItems(result.items);
    setTotal(result.total);
  }, [batchId, status, keyword, sort, onlySelected, hideDuplicates, page]);

  const loadSummary = useCallback(async () => {
    setSummary(await getSelectionSummary(batchId || undefined));
  }, [batchId]);

  const loadBatches = useCallback(async () => {
    setBatches(await getSelectionBatches());
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadList(), loadSummary()])
      .catch((error: unknown) => setNotice({ kind: 'warn', text: (error as Error).message }))
      .finally(() => setLoading(false));
  }, [loadList, loadSummary]);

  useEffect(() => { void loadBatches().catch(() => undefined); }, [loadBatches]);

  const refresh = async () => {
    await Promise.all([loadList(), loadSummary(), loadBatches()]);
  };

  const handleUpload = async (file: File) => {
    setImporting(true);
    setNotice({ kind: 'info', text: `正在读取 ${file.name}…` });
    try {
      const result = await importSelectionTable(file, batchName);
      setImportResult(result);
      setBatchId(result.batch_id);
      setPage(0);
      setNotice({
        kind: 'ok',
        text: `读入 ${result.source_rows} 行，合并成 ${result.created} 个商品`
          + (result.already_in_products ? `，其中 ${result.already_in_products} 个已在产品列表` : '')
          + '。下一步点「抓取主图与类目」。',
      });
      await refresh();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  /** 抓资料：后端一次最多 400 个，remaining 不为 0 就接着调。
   *
   * 后端主动节流并按估算量切块（见 fetch_budget）；收到拒绝/限流即停止后续轮次，
   * 所以一轮不一定能清空队列。remaining 还在就再调一轮；跑满上限仍有剩余时
   * 如实说「还剩多少」，不要报「完成」——用户看到的卡片才是可信的。
   */
  const handleEnrich = async (refreshAll = false) => {
    setEnriching(true);
    let done = 0;
    let succeeded = 0;
    let failed = 0;
    let last: SelectionEnrichResult | null = null;
    try {
      for (let round = 0; round < 20; round += 1) {
        const result = await enrichSelection({
          batch_id: batchId || undefined,
          refresh: refreshAll && round === 0,
        });
        last = result;
        done += result.processed;
        succeeded += result.ok;
        failed += result.failed;   // 每轮的失败要累加：只看最后一轮会漏报
        setEnrichResult(result);
        setNotice({
          kind: 'info',
          text: `已抓取 ${done} 个${result.remaining ? `，还剩 ${result.remaining} 个…` : '…'}`,
        });
        if (result.public_access_blocked || !result.processed || !result.remaining) break;
      }
      await refresh();
      if (last) {
        const bits = [`成功 ${succeeded} 个，失败 ${failed} 个`];
        if (last.from_product_list) bits.push(`${last.from_product_list} 个重尺直接取自产品列表`);
        if (last.with_dimensions) bits.push(`${last.with_dimensions} 个从卖家接口带出重尺`);
        if (last.skipped_network) bits.push(`${last.skipped_network} 个资料齐全、没再打网络`);
        if (last.public_access_blocked) {
          setNotice({kind:'warn',text:`读取已停止：${bits.join('，')}；公开接口访问受限，已停止后续批次${last.remaining ? `，还有 ${last.remaining} 个待处理` : ''}。`});
        } else if (last.remaining) {
          setNotice({
            kind: 'warn',
            text: `本轮${bits.join('，')}；还有 ${last.remaining} 个没抓完（本次达到处理上限），再点一次「重抓全部资料」接着抓，已抓到的不会重复抓。`,
          });
        } else {
          setNotice({ kind: failed ? 'warn' : 'ok', text: `抓取完成：${bits.join('，')}。` });
        }
      }
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setEnriching(false);
    }
  };

  const handlePatch = async (id: string, patch: SelectionItemPatch) => {
    setBusyIds(prev => new Set(prev).add(id));
    try {
      const updated = await patchSelectionItem(id, patch);
      setItems(prev => prev.map(item => (item.id === id ? updated : item)));
      void loadSummary().catch(() => undefined);
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setBusyIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleToggle = (item: SelectionItem) => {
    setItems(prev => prev.map(row => (row.id === item.id ? { ...row, selected: !row.selected } : row)));
    void handlePatch(item.id, { selected: !item.selected });
  };

  const handleSelectQualified = async () => {
    try {
      const result = await selectQualifiedSelection({ batch_id: batchId || undefined, min_margin: 0.15 });
      setNotice({
        kind: result.selected ? 'ok' : 'info',
        text: result.selected
          ? `已按 15% 利润率勾选 ${result.selected} 个（跳过已在产品列表的）`
          : '没有新的达标商品可勾选：要么还没算利润，要么利润率不足 15%',
      });
      await Promise.all([loadList(), loadSummary()]);
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    }
  };

  const handleClearSelection = async () => {
    const ids = items.filter(item => item.selected).map(item => item.id);
    if (!ids.length) return;
    await bulkPatchSelection(ids, { selected: false });
    await Promise.all([loadList(), loadSummary()]);
  };

  const handleBulkShipping = async () => {
    const ids = items.filter(item => item.selected).map(item => item.id);
    if (!ids.length || !bulkShipping) return;
    try {
      await bulkPatchSelection(ids, { shipping_method: bulkShipping });
      setNotice({ kind: 'ok', text: `已把本页选中的 ${ids.length} 个商品设为${bulkShipping}` });
      await loadList();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    }
  };

  const handlePromote = async () => {
    const ids = items.filter(item => item.selected && !item.promoted_product_id).map(item => item.id);
    if (!ids.length) {
      setNotice({ kind: 'info', text: '还没有勾选任何商品' });
      return;
    }
    setPromoting(true);
    try {
      const result = await promoteSelection(ids);
      setNotice({
        kind: result.created ? 'ok' : 'info',
        text: result.created
          ? `已新建 ${result.created} 个产品${result.skipped ? `，跳过 ${result.skipped} 个（${result.skip_details[0]?.reason || ''}）` : ''}`
          : `没有新建：${result.skip_details[0]?.reason || '未满足新建条件'}`,
      });
      await refresh();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setPromoting(false);
    }
  };

  /**
   * 检查已保存的目录连接。拿当前列表里第一个有 PLID 的商品当样本。
   * 未配置连接时不读密钥或发请求；已有连接只检查一次，不更换鉴权。
   */
  const handleProbeCatalogue = async () => {
    const sample = items.find(item => item.plid)?.plid;
    if (!sample) {
      setNotice({ kind: 'info', text: '先抓一次资料，或者列表里至少要有带 PLID 的商品才能检查' });
      return;
    }
    setProbing(true);
    setProbeReport(null);
    try {
      const report = await probeSelectionCatalogue(sample);
      setProbeReport(report);
      setNotice({
        kind: report.ok ? 'ok' : 'warn',
        text: report.ok
          ? `目录连接检查成功：当前样本返回完整重尺，后续只使用已保存连接。`
          : `目录连接未就绪（${report.reason}）。竞品重尺仍按人工填写处理。`,
      });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setProbing(false);
    }
  };

  /** 让服务器自己去读一次公开接口，确认「抓不到主图」到底卡在哪一层。 */
  const handleProbePublic = async () => {
    const sample = items.find(item => item.plid)?.plid;
    if (!sample) {
      setNotice({ kind: 'info', text: '列表里至少要有带 PLID 的商品才能测' });
      return;
    }
    setPublicProbing(true);
    setPublicProbe(null);
    try {
      const report = await probeSelectionPublicApi(sample);
      setPublicProbe(report);
      setNotice({
        kind: report.success && report.image_ok ? 'ok' : 'warn',
        text: `PLID ${report.plid}：${report.hint}`,
      });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setPublicProbing(false);
    }
  };

  const handleDeleteBatch = async () => {
    if (!batchId) return;
    const batch = batches.find(row => row.batch_id === batchId);
    if (!window.confirm(`删除批次「${batch?.batch_name || batchId}」里还没建产品的记录？已建产品的会保留。`)) return;
    try {
      const result = await deleteSelectionBatch(batchId);
      setNotice({ kind: 'ok', text: `已删除 ${result.deleted} 条选品记录` });
      setBatchId('');
      setPage(0);
      await refresh();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const selectedOnPage = useMemo(() => items.filter(item => item.selected).length, [items]);

  return (
    <div className="selection-page">
      <div className="page-heading">
        <div>
          <h2>选品池</h2>
          <p>
            上传带链接或 TSIN 的表，自动抓主图、类目和价格；重尺和采购价填完就出利润率。
            勾中达标的商品再「一键新建产品」进产品列表——核算和建档是同一次操作。
          </p>
        </div>
        <div className="page-heading-actions">
          <button type="button" className="btn" onClick={() => void handleProbePublic()} disabled={publicProbing}>
            {publicProbing ? '测试中…' : '测试主图接口'}
          </button>
          <button type="button" className="btn" onClick={() => void handleProbeCatalogue()} disabled={probing}>
            {probing ? '检查中…' : '检查目录接入'}
          </button>
          <button type="button" className="btn" onClick={() => void handleEnrich(true)} disabled={enriching || importing}>
            {enriching ? '抓取中…' : '重抓全部资料'}
          </button>
          <button type="button" className="btn-primary" onClick={() => void handlePromote()} disabled={promoting || !selectedOnPage}>
            {promoting ? '新建中…' : `一键新建产品${selectedOnPage ? `（${selectedOnPage}）` : ''}`}
          </button>
        </div>
      </div>

      {notice && (
        <div className={`operations-note is-dismissible selection-notice is-${notice.kind}`} role="status">
          {notice.text}
          <button type="button" className="note-dismiss" aria-label="关闭这条提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <section className="selection-import">
        <div className="selection-import-main">
          <label className="selection-file">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xlsm,.csv"
              disabled={importing}
              onChange={event => {
                const file = event.target.files?.[0];
                if (file) void handleUpload(file);
              }}
            />
            <span>{importing ? '正在读表…' : '选择 Excel / CSV'}</span>
          </label>
          <label className="selection-field is-wide">
            <span>批次名（可空，默认用文件名）</span>
            <input value={batchName} onChange={event => setBatchName(event.target.value)}
              placeholder="例如：竞品店铺 10-26" disabled={importing} />
          </label>
          <button type="button" className="btn" onClick={() => void handleEnrich(false)}
            disabled={enriching || !summary?.total}>
            {enriching ? '抓取中…' : '抓取主图与类目'}
          </button>
        </div>
        <p className="selection-import-hint">
          表头认这些列（中英文都行）：产品链接 / 产品名称 / TSIN / PLID / 产品价格(R) / 产品评分 / 评分数量 /
          Qty / Net Sales Amount。只给链接、只给 TSIN 都能跑；同一商品的多行会按 PLID 或 TSIN 合并。
        </p>
        {importResult && (
          <p className="selection-import-result">
            {importResult.sheet}：读入 {importResult.source_rows} 行 → 合并为 {importResult.created} 个商品
            {importResult.already_in_products ? `，${importResult.already_in_products} 个已在产品列表` : ''}
            {importResult.with_sales_data ? `，${importResult.with_sales_data} 个带销售数据（已分档）` : ''}
            {importResult.skipped ? `，${importResult.skipped} 行无法识别已跳过` : ''}
            {importResult.unknown_headers.length
              ? `。未识别的列：${importResult.unknown_headers.slice(0, 6).join('、')}${importResult.unknown_headers.length > 6 ? ' 等' : ''}`
              : ''}
          </p>
        )}
        {publicProbe && (
          <details className="selection-import-result" open={!publicProbe.success || !publicProbe.image_ok}>
            <summary>
              主图接口测试：PLID {publicProbe.plid} —— {publicProbe.hint}
            </summary>
            <ul>
              <li>接口结果：{publicProbe.success ? '通了' : '失败'}</li>
              {publicProbe.warnings.length > 0 && (
                <li>错误：{publicProbe.warnings.join('；')}</li>
              )}
              <li>商品名：{publicProbe.product_name || '（没拿到）'}</li>
              <li>
                主图：{publicProbe.image_ok
                  ? <code>{publicProbe.product_image_url}</code>
                  : '（没拿到）'}
              </li>
              <li>前台类目：{publicProbe.takealot_category_path || '（没拿到）'}</li>
              <li>售价：{formatPrice(publicProbe.actual_sale_price_zar, 'ZAR')}</li>
            </ul>
          </details>
        )}
        {probeReport && (
          <details className="selection-import-result" open={!probeReport.ok}>
            <summary>
              目录连接检查：{probeReport.ok ? '样本可读' : '未就绪'} —— {probeReport.reason || '可以用了'}
            </summary>
            <ul>
              {probeReport.tried.map((row: CatalogueProbeReport['tried'][number]) => (
                <li key={`${row.endpoint}-${row.auth}`}>
                  <code>{row.path}</code> · {row.auth} → HTTP {row.status}
                  {row.note ? `（${row.note}）` : ''}
                  {row.dimension_keys?.length ? ` · 重尺字段：${row.dimension_keys.join('、')}` : ''}
                  {row.dims?.length_mm ? ` · 解析出 ${row.dims.length_mm}×${row.dims.width_mm}×${row.dims.height_mm}mm` : ''}
                </li>
              ))}
            </ul>
          </details>
        )}
        {enrichResult && enrichResult.failures.length > 0 && (
          <details className="selection-import-result">
            <summary>{enrichResult.failed} 个没抓到，看原因</summary>
            <ul>
              {enrichResult.failures.map(row => (
                <li key={row.id}>{row.plid || row.tsin || row.id}：{row.message}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      {summary && summary.total > 0 && (
        <section className="selection-summary">
          <div><strong>{summary.total}</strong><span>候选商品</span></div>
          <div><strong>{summary.with_image}</strong><span>有主图</span></div>
          <div><strong>{summary.with_dimensions}</strong><span>重尺齐全</span></div>
          <div><strong>{summary.qualified}</strong><span>合格（≥15%）</span></div>
          <div><strong>{summary.selected}</strong><span>已勾选</span></div>
          <div><strong>{summary.promoted}</strong><span>已建产品</span></div>
          {summary.duplicates > 0 && <div className="is-warn"><strong>{summary.duplicates}</strong><span>与产品列表重复</span></div>}
          {summary.no_plid > 0 && <div className="is-warn"><strong>{summary.no_plid}</strong><span>没解析出 PLID</span></div>}
        </section>
      )}

      <section className="selection-toolbar">
        <label className="selection-field">
          <span>批次</span>
          <select value={batchId} onChange={event => { setBatchId(event.target.value); setPage(0); }}>
            <option value="">全部批次</option>
            {batches.map(batch => (
              <option key={batch.batch_id} value={batch.batch_id}>
                {batch.batch_name}（{batch.count}）
              </option>
            ))}
          </select>
        </label>
        <label className="selection-field">
          <span>状态</span>
          <select value={status} onChange={event => { setStatus(event.target.value); setPage(0); }}>
            <option value="">全部</option>
            {STATUS_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
        <label className="selection-field">
          <span>排序</span>
          <select value={sort} onChange={event => { setSort(event.target.value); setPage(0); }}>
            <option value="tier">档位（S→C）</option>
            <option value="sales">销量</option>
            <option value="profit">利润率</option>
            <option value="price">售价</option>
          </select>
        </label>
        <label className="selection-field is-wide">
          <span>搜索</span>
          <input value={keywordDraft} placeholder="商品名 / TSIN / PLID"
            onChange={event => setKeywordDraft(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { setKeyword(keywordDraft); setPage(0); } }}
            onBlur={() => { if (keywordDraft !== keyword) { setKeyword(keywordDraft); setPage(0); } }} />
        </label>
        <label className="selection-check">
          <input type="checkbox" checked={onlySelected} onChange={event => { setOnlySelected(event.target.checked); setPage(0); }} />
          只看已勾选
        </label>
        <label className="selection-check">
          <input type="checkbox" checked={hideDuplicates} onChange={event => { setHideDuplicates(event.target.checked); setPage(0); }} />
          隐藏已在产品列表的
        </label>
      </section>

      <section className="selection-actions">
        <button type="button" className="btn" onClick={() => void handleSelectQualified()}>
          按 15% 勾选达标商品
        </button>
        <button type="button" className="btn" onClick={() => void handleClearSelection()} disabled={!selectedOnPage}>
          取消本页勾选
        </button>
        <label className="selection-field">
          <span>批量设物流</span>
          <select value={bulkShipping} onChange={event => setBulkShipping(event.target.value)}>
            <option value="">选择…</option>
            {SHIPPING_METHODS.map(method => <option key={method} value={method}>{method}</option>)}
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void handleBulkShipping()}
          disabled={!bulkShipping || !selectedOnPage}>
          应用到本页选中
        </button>
        {batchId && (
          <button type="button" className="btn-link-danger" onClick={() => void handleDeleteBatch()}>
            删除本批次
          </button>
        )}
      </section>

      {loading ? (
        <div className="page-loading"><span />正在加载…</div>
      ) : items.length === 0 ? (
        <div className="selection-empty">
          <p>{summary?.total ? '当前筛选下没有商品。' : '还没有候选商品，先在上面传一份表。'}</p>
          <p className="selection-empty-hint">
            传一份只有「产品链接」的竞品表也行——PLID 会从链接里取，主图和类目走公开接口。
          </p>
        </div>
      ) : (
        <div className="selection-grid-cards">
          {items.map(item => (
            <SelectionCard
              key={item.id}
              item={item}
              busy={busyIds.has(item.id)}
              onPatch={patch => void handlePatch(item.id, patch)}
              onToggle={() => handleToggle(item)}
              feeCategories={feeCategoryState.categories}
            />
          ))}
        </div>
      )}

      {pageCount > 1 && (
        <div className="selection-pager">
          <button type="button" className="btn" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>上一页</button>
          <span>第 {page + 1} / {pageCount} 页 · 共 {total} 个</span>
          <button type="button" className="btn" disabled={page + 1 >= pageCount} onClick={() => setPage(value => value + 1)}>下一页</button>
        </div>
      )}
    </div>
  );
}
