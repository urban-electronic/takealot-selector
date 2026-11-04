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
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ProductImage } from '../utils/imageFallback';
import {
  bulkPatchSelection,
  cancelSelectionCrawl,
  createSelectionCrawl,
  DEFAULT_AGENT_BASE,
  deleteSelectionBatch,
  getSelectionAgentBase,
  getSelectionAgentHealth,
  getSelectionCrawlTask,
  purgeSelection,
  setSelectionAgentBase,
  setSelectionAgentConfig,
  enrichSelection,
  getFeeCategories,
  getActiveStoreId,
  getImageUrl,
  getPortalTokenState,
  getSelectionBatches,
  getSelectionItems,
  getSelectionSummary,
  importSelectionTable,
  patchSelectionItem,
  probeSelectionCatalogue,
  probeSelectionPortal,
  probeSelectionPublicApi,
  promoteSelection,
  selectQualifiedSelection,
  setPortalToken,
  syncSelectionToProducts,
} from '../remoteApi';
import { useFeeCategories } from '../utils/useFeeCategories';
import { BATTERY_STATUS_LABEL, SHIPPING_METHODS, formatPercent, formatPrice } from '../types';
import type { CatalogueProbeReport, PortalProbeReport, PortalTokenState, PublicApiProbeReport, SelectionAgentHealth, SelectionCrawlTask } from '../remoteApi';
import type {
  FeeCategory,
  SelectionBatch,
  SelectionEnrichResult,
  SelectionImportResult,
  SelectionItem,
  SelectionItemPatch,
  SelectionSummary,
} from '../types';

import { selectedPromotionIds } from '../utils/selectionPromotion';
import { beijingDateTime } from '../utils/datetime';

/**
 * 令牌的 `expires_at` 是 epoch 秒（JWT 的 exp）。统一交给 `beijingDateTime`
 * 按业务时区格式化——直接 `toLocaleString()` 取的是运行机器的时区，换台机器
 * 显示的时间就对不上了。
 */
const formatTokenExpiry = (epochSeconds: number | null | undefined): string =>
  epochSeconds ? beijingDateTime(new Date(epochSeconds * 1000).toISOString()) : '—';

/**
 * 从卖家门户控制台取令牌的一行脚本。
 *
 * `copy()` 是 Chrome / Edge 开发者工具自带的函数（不是网页 API），所以这行只能
 * 粘在控制台里跑。手动路径（Application → Local Storage）也写在下面，给不想开
 * 控制台的人兜底。
 */
const PORTAL_TOKEN_SNIPPET =
  "copy(JSON.parse(localStorage.getItem('usr_st_auth')).api_key)";

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
  portal: '卖家门户实测',
  portal_empty: '门户也没有',
  public_api: '公开页预填',
  manual: '人工填写',
  import: '表格导入',
};

// value 是库里存的字符串（不能改，改了要迁数据），label 是界面上给用户看的话。
// 界面上统一叫「测算」而不是「核算」：这个页面的任务就是把重尺和成本填进去算利润，
// 用户嘴里的「测算过 / 没测算过」才是他们找东西时用的词。
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '待核算', label: '未测算' },
  { value: '合格', label: '合格（≥15%）' },
  { value: '不合格', label: '不合格' },
  { value: '已建产品', label: '已建产品' },
];

/**
 * 测算进度三态。
 *
 * 「已测算」的唯一判据是有利润率：`_calc` 里任一环缺失后面全是 None，
 * 所以 profit_margin 有值就等于「重尺、成本、售价、类目都齐了、算出来了」。
 * 不能拿「填了几个字段」当判据——填了长宽高但没填采购价，照样算不出来。
 */
function measureState(item: SelectionItem): 'promoted' | 'measured' | 'unmeasured' {
  if (item.promoted_product_id) return 'promoted';
  return item.profit_margin == null ? 'unmeasured' : 'measured';
}

/**
 * 未测算缺哪几项，按填表顺序排。
 *
 * 原来只写「待补数据」，等于告诉人「不完整」但不说缺什么，还得自己把卡片上
 * 十几个格子扫一遍。缺项直接列出来，一眼就能补。
 */
function missingInputs(item: SelectionItem): string[] {
  const missing: string[] = [];
  if (item.length_mm == null || item.width_mm == null || item.height_mm == null) missing.push('长宽高');
  if (item.actual_weight_kg == null) missing.push('重量');
  if (item.actual_sale_price_zar == null) missing.push('售价');
  if (item.purchase_cost_cny == null) missing.push('采购价');
  if (!item.shipping_method) missing.push('物流方式');
  if (!item.fee_category) missing.push('类目');
  return missing;
}

// useFeeCategories 的 effect 依赖 source 本身，传对象字面量会每渲染一次就重读一遍，
// 所以放在模块作用域固定住。
const FEE_CATEGORY_SOURCE = { getFeeCategories };

/**
 * 折叠状态和「跳过已测算」都存在本地。
 *
 * 这两个都是「这个人干活的习惯」，不是数据：把它当成每次进页面都要重设的东西，
 * 用户就得每回点一遍折叠。隐私模式下 localStorage 会抛，所以全部包在 try 里。
 */
const SECTIONS_KEY = 'selection.sections.collapsed.v1';
const SKIP_MEASURED_KEY = 'selection.skipMeasured.v1';

const PANELS_KEY = 'selection.panels.open.v1';

function readImportPanel(): boolean {
  try { return JSON.parse(localStorage.getItem(PANELS_KEY) || '{}')?.import === true; }
  catch { return false; }
}

type SectionKey = 'unmeasured' | 'measured';

function readSections(): Record<SectionKey, boolean> {
  // 默认：未测算展开（要干活的是这些），已测算折叠（它们只是备查）。
  const fallback: Record<SectionKey, boolean> = { unmeasured: false, measured: true };
  try {
    const raw = localStorage.getItem(SECTIONS_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<Record<SectionKey, boolean>>;
    return {
      unmeasured: typeof saved.unmeasured === 'boolean' ? saved.unmeasured : fallback.unmeasured,
      measured: typeof saved.measured === 'boolean' ? saved.measured : fallback.measured,
    };
  } catch {
    return fallback;
  }
}

function readSkipMeasured(): boolean {
  try {
    const raw = localStorage.getItem(SKIP_MEASURED_KEY);
    return raw == null ? true : raw === '1';
  } catch {
    return true;
  }
}

/**
 * 一个可折叠的卡片分区。
 *
 * 用真按钮而不是 <details>：标题里还有计数和一句说明，`<details>` 的 summary
 * 里塞这么多东西在部分读屏器上会被当成纯文本，点不动也读不出状态。
 */
function CardSection({
  tone, title, count, hint, note, collapsed, onToggle, children,
}: {
  tone: SectionKey;
  title: string;
  count: number;
  hint: string;
  /** 分区里的一次性提示（比如「N 个已经算完，还留在原位」）。不能塞进标题按钮里。 */
  note?: ReactNode;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`selection-section is-${tone}${collapsed ? ' is-collapsed' : ''}`}>
      <button type="button" className="selection-section-head" onClick={onToggle}
        aria-expanded={!collapsed}
        // 按钮的可访问名单独给：不然读屏器会把「未测算 1 重尺、采购价、售价填完
        // 就出利润率；抓取只会抓这些」整句念成按钮名，又长又抓不到重点。
        aria-label={`${title}，${count} 个，${collapsed ? '已折叠，点击展开' : '已展开，点击折叠'}`}>
        <span className="selection-section-caret" aria-hidden="true">▾</span>
        <span className="selection-section-title">{title}</span>
        <span className="selection-section-count">{count}</span>
        <span className="selection-section-hint">{hint}</span>
      </button>
      {collapsed ? null : note}
      {collapsed ? null : <div className="selection-grid-cards">{children}</div>}
    </section>
  );
}

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

/** Keep edits local until blur; only committed values go to the saved draft. */
function TextCell({ label, value, disabled, onCommit, placeholder }: {
  label: string; value: string; disabled: boolean;
  onCommit: (value: string) => void; placeholder?: string;
}) {
  const [draft, setDraft] = useState(value || '');
  useEffect(() => { setDraft(value || ''); }, [value]);
  return <label className="selection-field is-wide">
    <span>{label}</span>
    <input value={draft} disabled={disabled} placeholder={placeholder}
      onChange={event => setDraft(event.target.value)}
      onBlur={() => { if (draft !== (value || '')) onCommit(draft.trim()); }}
      onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} />
  </label>;
}

function MarginBadge({ item }: { item: SelectionItem }) {
  // 已建产品也要看利润率：改完重尺/成本重算的数字，和产品列表里那个是对得上的，
  // 核账时得能一眼看到。
  if (item.profit_margin == null) {
    return (
      <span className={`selection-margin ${item.promoted_product_id ? 'is-done' : 'is-unmeasured'}`}>
        {item.promoted_product_id ? '已建产品' : '未测算'}
      </span>
    );
  }
  // 徽章只说结论（合格/不合格），具体数字交给下面网格里的「利润率」一行。
  // 两边都写「利润率 31.00%」等于同一句话在卡片上印两遍，还占一行高度。
  const ok = item.profit_margin >= 0.15;
  return (
    <span className={`selection-margin ${ok ? 'is-ok' : 'is-bad'}`}>
      {ok ? '合格' : '不合格'}
    </span>
  );
}

/**
 * 点「带电 / 待确认」标记时一起把运输方式改对。
 *
 * 只改普货↔带电那一半，海/空运不动：用户选空运是因为赶时效，不是因为电池。
 */
function toggleBattery(item: SelectionItem): SelectionItemPatch {
  const next = item.battery_status === 'yes' ? 'no' : 'yes';
  const current = item.shipping_method || '海运普货';
  const prefix = current.startsWith('空运') ? '空运' : '海运';
  return { battery_status: next, shipping_method: `${prefix}${next === 'yes' ? '带电' : '普货'}` };
}

/**
 * 售价口径的中文说法。
 *
 * 同一个商品有三个价：前台展示价（配送最快的那家）、有现货卖家的最低价、
 * 全部报价的最低价。界面上必须写清用的是哪个，否则「售价 R 178」和前台
 * 显示的 R 309 对不上时，人只会以为系统抓错了。
 */
const PRICE_SOURCE_LABEL: Record<string, string> = {
  in_stock: '现货最低价',
  offer: '最低报价（含备货期）',
  listed: '前台展示价',
  import: '导入表售价',
  manual: '人工填写',
  portal: '目录价',
};

/**
 * 售价口径说明，用在 title 里。
 *
 * 「前台展示价」是 buybox 里选中的那条（配送最快），往往比有货的最低价贵；
 * 这就是用户看到的「抓到的价比前台低」的原因。
 */
function priceNote(item: SelectionItem): string {
  const parts: string[] = [];
  const source = PRICE_SOURCE_LABEL[item.price_source] || '售价';
  parts.push(`当前售价：${source}`);
  if (item.listed_price_zar != null) parts.push(`前台展示价 R ${item.listed_price_zar}`);
  if (item.lowest_in_stock_price_zar != null) parts.push(`现货最低 R ${item.lowest_in_stock_price_zar}`);
  if (item.lowest_offer_price_zar != null) parts.push(`最低报价 R ${item.lowest_offer_price_zar}`);
  if (item.seller_offer_count != null) parts.push(`${item.seller_offer_count} 个卖家报价`);
  return parts.join(' · ');
}

function SelectionCard({
  item, busy, saving, acting, onPatch, onToggle, feeCategories, onSync, onPromote,
}: {
  item: SelectionItem;
  /** 整页在忙（批量建档/抓取中）：卡片锁住，别让人改到一半被打断。 */
  busy: boolean;
  /** 正在写**这一条**（保存字段），只有它显示「保存中…」。 */
  saving?: boolean;
  /** 这一条正在跑的动作，只影响它自己的按钮文案。 */
  acting?: 'promote' | 'sync' | null;
  onPatch: (patch: SelectionItemPatch) => void;
  onToggle: () => void;
  feeCategories: FeeCategory[];
  onSync?: () => void;
  onPromote?: () => void;
}) {
  const tier = TIER_STYLE[item.tier];
  const battery = BATTERY_STATUS_LABEL[item.battery_status || ''];
  // 已建产品不再锁死：选品池是核算台，产品主档是结果，中间往往要改好几次
  // （换了货源、重量改过、售价定了）。锁死的后果是这些改动只能去产品详情页改，
  // 而那边看不到选品池的缺项提示，核算和建档就对不上了。
  const locked = busy;
  const promoted = Boolean(item.promoted_product_id);
  const state = measureState(item);
  const missing = state === 'unmeasured' ? missingInputs(item) : [];
  // 低于 15% 的已测算卡片左轨标红：一屏扫下来绿的是能做的，红的是已经算过但不够的，
  // 两种都不用再点开看。颜色只是加强，真正区分用的是虚线框和「未测算」标签。
  const below = state === 'measured' && (item.profit_margin ?? 0) < 0.15;
  const cardClass = [
    'selection-card',
    `is-${state}`,
    below ? 'is-below' : '',
    item.selected ? 'is-selected' : '',
    locked ? 'is-locked' : '',
  ].filter(Boolean).join(' ');
  const dimsMissing = item.length_mm == null || item.width_mm == null
    || item.height_mm == null || item.actual_weight_kg == null;
  // 供货单价填了就以它为准：采购价 = 单价 × 数量。后端 _calc 里是同一个口径，
  // 这里算一份只为把值立刻显示出来，不用等一次往返。
  const derivedCost = item.unit_price_cny == null
    ? null
    : Math.round(item.unit_price_cny * (item.purchase_quantity || 4) * 100) / 100;

  return (
    <article className={cardClass}>
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
        {state === 'unmeasured'
          ? <span className="selection-flag is-unmeasured" title={missing.length ? `还缺：${missing.join('、')}` : '可以测算了'}>未测算</span>
          : null}
        {item.already_in_products ? <span className="selection-flag is-dup">已在产品列表</span> : null}
        {promoted ? <span className="selection-flag is-done">已建产品</span> : null}
        {promoted && item.pool_dirty
          ? <span className="selection-flag is-sync" title="选品池里改过了，产品列表还是旧的">待更新</span>
          : null}
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

      <div className="selection-price" title={priceNote(item)}>
        <strong>{formatPrice(item.actual_sale_price_zar, 'ZAR')}</strong>
        <span className="selection-price-source">{PRICE_SOURCE_LABEL[item.price_source] || '售价'}</span>
        {item.sales_qty ? <span>{item.sales_qty} 件 · 净销售 {formatPrice(item.net_sales_zar, 'ZAR')}</span> : null}
      </div>

      <div className="selection-category">
        <span className={`selection-chip confidence-${item.fee_category_confidence || 'low'}`}
          title={item.fee_category_reason || item.takealot_category_path}>
          {item.fee_category || '类目待定'}
        </span>
        {/* 带电标记：判成 yes 的运输方式已经自动切成「海运带电」，
            maybe 的仍是普货、需要人看一眼货源页确认。
            已建产品（locked）时只展示不可点。 */}
        {battery && battery.tone !== 'muted' ? (
          <span className={`selection-chip is-battery ${battery.tone}${locked ? ' is-static' : ''}`}
            title={`判定依据：${item.battery_reason || '—'}${locked ? '' : '（点一下可手动确认）'}`}
            role={locked ? undefined : 'button'}
            tabIndex={locked ? undefined : 0}
            onClick={locked ? undefined : () => onPatch(toggleBattery(item))}
            onKeyDown={locked ? undefined : event => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onPatch(toggleBattery(item));
              }
            }}>
            {battery.label}
          </span>
        ) : null}
        {item.takealot_category_path
          ? <small title={item.takealot_category_path}>{item.takealot_category_path.split(' > ').slice(-2).join(' > ')}</small>
          : null}
      </div>

      <div className="selection-row">
        <TextCell label="中文品名" value={item.chinese_product_name} disabled={locked || busy}
          onCommit={value => onPatch({ chinese_product_name: value })} />
        <TextCell label="1688 / 货源链接" value={item.purchase_url} disabled={locked || busy}
          placeholder="粘贴已核对的货源链接"
          onCommit={value => onPatch({ purchase_url: value })} />
      </div>
      <TextCell label="选品备注" value={item.note} disabled={locked || busy}
        placeholder="规格、颜色、供应商与核算依据"
        onCommit={value => onPatch({ note: value })} />

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
        {/* 未测算时把「缺什么」直接列出来。只说「待补数据」等于让人把十几个格子
            自己扫一遍；列出缺项，补哪个一目了然。超过 3 项就收成「等 N 项」，
            免得一行字挤成两行把卡片撑高。 */}
        {missing.length > 0 ? (
          <p className="selection-missing">
            还缺 {missing.slice(0, 3).join('、')}
            {missing.length > 3 ? ` 等 ${missing.length} 项` : ''}
          </p>
        ) : null}
        <dl>
          <div><dt>计费重</dt><dd>{item.chargeable_weight_kg != null ? `${item.chargeable_weight_kg} kg` : '-'}</dd></div>
          <div><dt>到仓成本</dt><dd>{formatPrice(item.cost_to_sa_warehouse_zar, 'ZAR')}</dd></div>
          <div><dt>总成本</dt><dd>{formatPrice(item.total_cost_zar, 'ZAR')}</dd></div>
          <div><dt>单件利润</dt><dd>{formatPrice(item.profit_zar, 'ZAR')}</dd></div>
          {/* 利润率是「能不能做」的判据（≥15%），单件利润只是绝对值。
              以前这里只给单件利润，同一批里全是 R 20 / R 180 时没法横向比。 */}
          <div>
            <dt>利润率</dt>
            <dd className={item.profit_margin == null
              ? undefined
              : item.profit_margin >= 0.15 ? 'is-good' : 'is-bad'}>
              {formatPercent(item.profit_margin)}
            </dd>
          </div>
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
          {/* 回推：把选品池里改过的值写回产品主档并重算利润。产品列表里被人工
              改过的字段不覆盖，会在结果里列出来。 */}
          {promoted && onSync ? (
            <button type="button" className="btn-link" onClick={onSync} disabled={busy}>
              {acting === 'sync' ? '更新中…' : item.pool_dirty ? '更新到产品列表' : '重新推送'}
            </button>
          ) : null}
          {/* 单条建档：不用先勾选、再点工具栏那个批量按钮。看到一条合适的就直接
              加，比「勾一下 → 滚回顶部 → 点批量」少两步。 */}
          {!promoted && onPromote ? (
            <button type="button" className="btn-link" onClick={onPromote} disabled={busy}
              title="把这一条加入产品列表（和批量按钮是同一个动作）">
              {acting === 'promote' ? '加入中…' : '加入产品列表'}
            </button>
          ) : null}
          {saving ? <span className="selection-busy">保存中…</span> : null}
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
  // 每条卡片这一屏归属哪个分区。列表每次重新加载都会重算；编辑不会。
  const [sectionOf, setSectionOf] = useState<Record<string, SectionKey>>({});

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
  // 哪一条正在跑哪个动作。只用来改它自己的按钮文案——用全局 promoting 的话，
  // 点一张卡的按钮，整页所有卡片都会显示「加入中…」。
  const [acting, setActing] = useState<{ id: string; kind: 'promote' | 'sync' } | null>(null);
  const promoteLock = useRef(false);
  const promotionMounted = useRef(true);
  useEffect(() => {
    promotionMounted.current = true;
    return () => { promotionMounted.current = false; };
  }, []);
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
  // 卖家门户令牌：主图和重尺的唯一可靠来源。状态单独存，因为它会过期。
  const [portalState, setPortalState] = useState<PortalTokenState | null>(null);
  const [portalInput, setPortalInput] = useState('');
  const [portalSaving, setPortalSaving] = useState(false);
  const [portalProbing, setPortalProbing] = useState(false);
  const [portalProbe, setPortalProbe] = useState<PortalProbeReport | null>(null);
  // 类目下拉的候选：全量 Fee 类目，卡片里选一个就带出成功率。
  const feeCategoryState = useFeeCategories(FEE_CATEGORY_SOURCE);

  // 卡片分区的折叠状态，以及「抓取时跳过已测算」。两个都从本地读上次的选择。
  const [collapsed, setCollapsed] = useState<Record<SectionKey, boolean>>(readSections);
  const [skipMeasured, setSkipMeasured] = useState(readSkipMeasured);

  const [importExpanded, setImportExpanded] = useState(readImportPanel);
  const toggleImportPanel = () => {
    const next = !importExpanded;
    setImportExpanded(next);
    try { localStorage.setItem(PANELS_KEY, JSON.stringify({ import: next })); } catch { /* storage unavailable */ }
  };

  // 「从 Takealot 抓取」面板：独立于导入面板的展开状态。
  const CRAWL_PANEL_KEY = 'selection.panels.crawl.open.v1';
  const [crawlExpanded, setCrawlExpanded] = useState<boolean>(() => {
    try { return JSON.parse(localStorage.getItem(CRAWL_PANEL_KEY) || 'false') === true; }
    catch { return false; }
  });
  const toggleCrawlPanel = () => {
    const next = !crawlExpanded;
    setCrawlExpanded(next);
    try { localStorage.setItem(CRAWL_PANEL_KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  };

  /* ---- 从 Takealot 抓取：任务交给本机采集服务跑，抓完直投进这个池子 ----
   *
   * 抓取确实在用户自己的浏览器里发生（Takealot 有 Cloudflare 风控，云端跑必拦），
   * 网页这边只负责下指令、看进度。服务地址按店铺存，换店铺不必重填。
   * 后端 X-API-Key 不在这里经手——它存进本机采集服务的本地配置，由服务自己带去后端。
   */
  const [agentBase, setAgentBase] = useState(() => getSelectionAgentBase());
  const [agentBaseDraft, setAgentBaseDraft] = useState(() => getSelectionAgentBase());
  const [agentHealth, setAgentHealth] = useState<SelectionAgentHealth | null>(null);
  const [agentReach, setAgentReach] = useState<'checking' | 'ok' | 'down'>('checking');
  const [crawlMode, setCrawlMode] = useState<'keyword' | 'link'>('keyword');
  const [crawlTargets, setCrawlTargets] = useState('');
  const [crawlPages, setCrawlPages] = useState('');
  const [crawlMinReviews, setCrawlMinReviews] = useState('');
  const [crawlSort, setCrawlSort] = useState('价格从高到低');
  // 评论体检是抓取器独有的增量信号（本年度评论数 / 畅销变体），后端拿不到，
  // 但每个商品都要多开一次详情页，所以默认关，按需勾。
  const [crawlAudit, setCrawlAudit] = useState(false);
  const [crawlBatchName, setCrawlBatchName] = useState('');
  const [crawlTask, setCrawlTask] = useState<SelectionCrawlTask | null>(null);
  const [crawlSubmitting, setCrawlSubmitting] = useState(false);
  // 本机采集服务的密钥（API Key）只在本机回环里存一次，不进组件状态、不进公网请求。
  const [agentKeyDraft, setAgentKeyDraft] = useState('');
  const [savingAgentConfig, setSavingAgentConfig] = useState(false);
  const [agentConfigMsg, setAgentConfigMsg] = useState('');

  const checkAgent = useCallback(async (base: string) => {
    setAgentReach('checking');
    try {
      const health = await getSelectionAgentHealth(base);
      setAgentHealth(health);
      setAgentReach('ok');
      return health;
    } catch {
      setAgentHealth(null);
      setAgentReach('down');
      return null;
    }
  }, []);

  // 进页面就探一次：连着就顺手取服务端的排序项和体检默认条数，避免前后端两套候选。
  useEffect(() => { void checkAgent(agentBase); }, [agentBase, checkAgent]);

  const saveAgentBase = () => {
    setSelectionAgentBase(agentBaseDraft);
    const next = getSelectionAgentBase();
    setAgentBaseDraft(next);
    setAgentBase(next);
  };

  // 把 API Key 写进本机采集服务的本地配置（~/TakealotTool/selection_agent.json）。
  // 只走 127.0.0.1 回环，之后抓取就由服务自己带密钥，网页不再经手。
  const saveAgentConfig = async () => {
    if (!agentKeyDraft.trim()) {
      setAgentConfigMsg('先填 API Key');
      return;
    }
    setSavingAgentConfig(true);
    setAgentConfigMsg('');
    try {
      await setSelectionAgentConfig(agentBase, { api_key: agentKeyDraft.trim() });
      setAgentConfigMsg('已存到本机采集服务（只此一次，之后抓取不再经网页传密钥）');
      setAgentKeyDraft('');
      await checkAgent(agentBase);
    } catch (error) {
      setAgentConfigMsg((error as Error).message);
    } finally {
      setSavingAgentConfig(false);
    }
  };

  const crawlRunning = crawlTask?.status === 'pending' || crawlTask?.status === 'running';

  const handleStartCrawl = async () => {
    const targets = crawlTargets.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (!targets.length) {
      setNotice({ kind: 'warn', text: crawlMode === 'keyword' ? '先填关键词，一行一个' : '先填 Takealot 链接，一行一个' });
      return;
    }
    if (agentReach !== 'ok') {
      setNotice({ kind: 'warn', text: `本机采集服务没连上（${agentBase}），先在 Mac 上双击 启动采集服务.command` });
      return;
    }
    if (!agentHealth?.has_api_key) {
      setNotice({ kind: 'warn', text: '本机采集服务还没存 API Key：展开下方「本机采集服务凭证」存一次，之后抓取才能投递到后端' });
      return;
    }
    setCrawlSubmitting(true);
    try {
      const task = await createSelectionCrawl(agentBase, {
        mode: crawlMode,
        targets,
        pages: crawlPages.trim() ? Number(crawlPages) : undefined,
        min_reviews: crawlMinReviews.trim() ? Number(crawlMinReviews) : undefined,
        sort: crawlSort,
        audit: crawlAudit,
        batch_name: crawlBatchName.trim(),
      });
      setCrawlTask(task);
      setNotice({ kind: 'info', text: `已交给本机采集服务（${task.id}）。抓取时会弹出一个浏览器窗口，别关它。` });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setCrawlSubmitting(false);
    }
  };

  const handleCancelCrawl = async () => {
    if (!crawlTask) return;
    try {
      setCrawlTask(await cancelSelectionCrawl(agentBase, crawlTask.id));
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    }
  };

  const toggleSection = useCallback((key: SectionKey) => {
    setCollapsed(prev => {
      const next = { ...prev, [key]: !prev[key] };
      try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(next)); } catch { /* 隐私模式存不住 */ }
      return next;
    });
  }, []);

  const toggleSkipMeasured = useCallback(() => {
    setSkipMeasured(prev => {
      const next = !prev;
      try { localStorage.setItem(SKIP_MEASURED_KEY, next ? '1' : '0'); } catch { /* 同上 */ }
      return next;
    });
  }, []);

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
    // 分区归属在这一刻定下来。之后用户改字段、利润率从无到有，卡片也不会换区——
    // 想让它归位，翻页/切筛选/抓取/建档都会重新加载，或者点分区里的「归到已测算」。
    setSectionOf(Object.fromEntries(result.items.map(
      item => [item.id, measureState(item) === 'unmeasured' ? 'unmeasured' : 'measured'],
    )));
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

  // 门户令牌会过期，进页面就读一次状态，好在过期时提前提示而不是等抓完才发现。
  useEffect(() => {
    void getPortalTokenState()
      .then(setPortalState)
      .catch(() => undefined);
  }, []);

  const refresh = async () => {
    await Promise.all([loadList(), loadSummary(), loadBatches()]);
  };

  // 轮询要看最新列表，但 refresh 每次渲染都是新函数；存 ref 才不会把轮询打断重来。
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; });

  // 抓取任务轮询：任务在跑就每 2.5s 拉一次进度，结束（完成/取消/失败）后刷新选品池。
  useEffect(() => {
    if (!crawlTask || (crawlTask.status !== 'pending' && crawlTask.status !== 'running')) return;
    let stopped = false;
    let timer = 0;

    const poll = async () => {
      try {
        const detail = await getSelectionCrawlTask(agentBase, crawlTask.id);
        if (stopped) return;
        setCrawlTask(detail);
        if (detail.status === 'done' || detail.status === 'cancelled' || detail.status === 'failed') {
          setNotice({
            kind: detail.status === 'done' ? 'ok' : 'warn',
            text: `${detail.stage}：抓到 ${detail.products_total} 个，新建 ${detail.created} 个`
              + (detail.duplicates ? `，池内重复 ${detail.duplicates} 个` : '')
              + (detail.errors && detail.errors.length ? `，${detail.errors.length} 处出错（见抓取日志）` : ''),
          });
          await refreshRef.current();
          return;
        }
      } catch { /* 单次轮询失败不算失败，下一轮继续 */ }
      if (!stopped) timer = window.setTimeout(poll, 2500);
    };

    timer = window.setTimeout(poll, 1500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [agentBase, crawlTask?.id, crawlTask?.status]);

  const handleUpload = async (file: File) => {
    if (promoteLock.current) return;
    setImporting(true);
    setNotice({ kind: 'info', text: `正在读取 ${file.name}…` });
    try {
      const result = await importSelectionTable(file, batchName);
      setImportResult(result);
      setBatchId(result.batch_id);
      setPage(0);
      setNotice({
        kind: 'ok',
        text: `读入 ${result.source_rows} 行，新建 ${result.created} 个商品`
          + (result.branded ? `，跳过 ${result.branded} 个有品牌` : '')
          + (result.already_in_products ? `，跳过 ${result.already_in_products} 个已在产品列表` : '')
          + (result.pool_duplicates ? `，跳过 ${result.pool_duplicates} 个池子里已有` : '')
          + '。下一步点「抓取本批次没抓过的」。',
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
    if (promoteLock.current) return;
    setEnriching(true);
    let done = 0;
    let succeeded = 0;
    let failed = 0;
    let fixed = 0;
    // 已建档产品的售价也是每轮各推各的，只看最后一轮会漏报前几轮推过去的。
    let syncedPromoted = 0;
    let stalled = false;
    let last: SelectionEnrichResult | null = null;
    try {
      for (let round = 0; round < 40; round += 1) {
        const result = await enrichSelection({
          batch_id: batchId || undefined,
          // 重抓要跑多轮才能覆盖全部行（一次 400 个），所以不能只在第一轮传 true，
          // 否则 1700 多行里永远只有最早那 400 行被重新取价。
          refresh: refreshAll,
          // 测算过的行默认跳过：价格、重尺、成本都是核对过的，再抓一遍既费配额，
          // 也可能把刚调好的售价换回去。
          skip_measured: skipMeasured,
        });
        last = result;
        done += result.processed;
        succeeded += result.ok;
        failed += result.failed;   // 每轮的失败要累加：只看最后一轮会漏报
        fixed += result.price_fixed || 0;
        syncedPromoted += result.promoted_price_synced || 0;
        setEnrichResult(result);
        setNotice({
          kind: 'info',
          text: `已抓取 ${done} 个${result.remaining ? `，还剩 ${result.remaining} 个…` : '…'}`,
        });
        // 门户配额用完时这一轮可能一条都没处理完（`processed === 0`），但那不是
        // 「没得抓了」——下一轮配额就回来了。只有 pending 也是 0 才真的收手。
        if (result.public_access_blocked) break;
        if (!result.remaining) break;
        if (!result.processed && !result.pending) break;
        // 重抓时 `remaining` 里还包含「价格没换成现货最低价」的行。这类行可能怎么抓
        // 都换不掉（PLID 没有卖家报价、公开接口限流），再跑也是原样——只要这一轮
        // 一条价格都没修好、也没有待重试的，就收手，别空转到 40 轮上限。
        if (refreshAll && round > 0 && !result.price_fixed && !result.pending) {
          stalled = true;
          break;
        }
        // 门户是 200 次/分钟的硬配额。配额见底时后端会把「还要等多久」告诉我们，
        // 这里照着实等——不这样的话会连续空转几十次，每次都被整批推迟。
        if (result.portal_limited && result.portal_retry_after) {
          const wait = Math.min(result.portal_retry_after, 65);
          setNotice({
            kind: 'info',
            text: `门户配额已用完（200 次/分钟），等 ${Math.ceil(wait)} 秒继续，还剩 ${result.remaining} 个…`,
          });
          await new Promise(resolve => setTimeout(resolve, wait * 1000));
        }
      }
      await refresh();
      if (last && done === 0) {
        // 一条都没处理。最常见的原因是「跳过已测算」把整个范围都筛掉了——这时候
        // 报「抓取完成：成功 0 个」等于让人以为系统坏了，得直接说清楚为什么。
        setNotice({
          kind: 'info',
          text: skipMeasured
            ? '没有要抓的：当前范围内都测算过了。要连已测算的一起重抓，取消勾选「跳过已测算」。'
            : '没有要抓的：当前范围内没有待抓的商品（资料都齐了）。',
        });
      } else if (last) {
        const bits = [`成功 ${succeeded} 个，失败 ${failed} 个`];
        // 价格口径是这次重抓的主要目的，单独报一句：用户得知道有多少条真的从
        // 「前台展示价」换成了「现货最低价」，而不是只看到「成功 N 个」。
        if (refreshAll) bits.push(`${fixed} 个售价换成了现货最低价`);
        // 已建档的行抓到新价后会推给产品主档。不报的话，用户只能去产品列表
        // 挨个点开才知道到底更新没更新。
        if (syncedPromoted) {
          bits.push(`顺带更新了 ${syncedPromoted} 个已建档产品的售价`);
        }
        // 被限流的不是「抓不到」，只是还没轮到——分开说，别让人以为这些商品废了。
        if (last.pending) bits.push(`${last.pending} 个被限流、稍后再抓`);
        if (last.portal_limited) bits.push('门户配额这一轮用完了（200 次/分钟），再点一次接着抓');
        if (last.from_product_list) bits.push(`${last.from_product_list} 个重尺直接取自产品列表`);
        if (last.with_dimensions) bits.push(`${last.with_dimensions} 个从卖家接口带出重尺`);
        if (last.skipped_network) bits.push(`${last.skipped_network} 个资料齐全、没再打网络`);
        // 品名是本地生成的，不占配额，但用户得知道「这些名字是机器起的」，
        // 抽查几条再往下走。
        if (last.names_filled) bits.push(`${last.names_filled} 个补了中文品名（按标题自动生成，建议抽查）`);
        // 带电判定同理：只有判出明确结论的才计入，判不准的仍是「待确认」。
        if (last.battery_judged) bits.push(`${last.battery_judged} 个判了普货/带电（判不准的标「待确认」）`);
        if (last.public_access_blocked) {
          setNotice({kind:'warn',text:`读取已停止：${bits.join('，')}；公开接口访问受限，已停止后续批次${last.remaining ? `，还有 ${last.remaining} 个待处理` : ''}。`});
        } else if (last.remaining) {
          setNotice({
            kind: 'warn',
            text: stalled
              // 剩下的这些是「怎么抓都换不到现货最低价」的行：别家没有现货报价、
              // PLID 已下架、或者公开接口在限流。让用户再点一次只会原样跑一遍，
              // 所以直说保留原价，而不是催他再点。
              ? `本轮${bits.join('，')}；还有 ${last.remaining} 个换不到现货最低价（这些商品没有别家的现货报价，或公开接口在限流），已保留原来的售价。`
              : `本轮${bits.join('，')}；还有 ${last.remaining} 个没抓完（本次达到处理上限），再点一次抓取按钮接着抓，已抓到的不会重复抓。`,
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
    if (promoteLock.current) return;
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
    if (promoteLock.current) return;
    setItems(prev => prev.map(row => (row.id === item.id ? { ...row, selected: !row.selected } : row)));
    void handlePatch(item.id, { selected: !item.selected });
  };

  const handleSelectQualified = async () => {
    if (promoteLock.current) return;
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
    if (promoteLock.current) return;
    const ids = items.filter(item => item.selected).map(item => item.id);
    if (!ids.length) return;
    await bulkPatchSelection(ids, { selected: false });
    await Promise.all([loadList(), loadSummary()]);
  };

  const handleBulkShipping = async () => {
    if (promoteLock.current) return;
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
    if (promoteLock.current) return;
    promoteLock.current = true;
    setPromoting(true);
    const initiatingStore = getActiveStoreId();
    const ensureCurrent = () => {
      if (!promotionMounted.current || getActiveStoreId() !== initiatingStore) throw Error('页面或店铺已变化，批量建档已停止');
    };
    let created = 0;
    let skipped = 0;
    try {
      // Read all pages first. Filters on the visible page must not hide selected drafts.
      const ids = await selectedPromotionIds(async params => {
        ensureCurrent();
        const result = await getSelectionItems(params);
        ensureCurrent();
        return result;
      }, batchId || undefined);
      ensureCurrent();
      if (!ids.length) {
        setNotice({ kind: 'info', text: '当前批次没有尚未建档的勾选商品' });
        return;
      }
      if (!window.confirm(`将${batchId ? '本批次' : '全部批次'}已勾选的 ${ids.length} 个商品加入产品列表？包含其他页勾选的商品。`)) return;
      for (let offset = 0; offset < ids.length; offset += 500) {
        ensureCurrent();
        const result = await promoteSelection(ids.slice(offset, offset + 500));
        ensureCurrent();
        created += result.created;
        skipped += result.skipped;
      }
      ensureCurrent();
      await refresh();
      ensureCurrent();
      setNotice({ kind: created ? 'ok' : 'info', text: `已新建 ${created} 个产品，跳过 ${skipped} 个（已建档或身份重复等）。` });
    } catch (error) {
      if (!promotionMounted.current) return;
      setNotice({ kind: 'warn', text: `本次已确认新建 ${created} 个、跳过 ${skipped} 个；后续处理停止：${(error as Error).message}。请刷新核对后再继续。` });
    } finally {
      promoteLock.current = false;
      if (promotionMounted.current) setPromoting(false);
    }
  };

  /**
   * 单条建档：卡片上直接「加入产品列表」。
   *
   * 和工具栏那个批量按钮是同一个后端动作，只是 ids 只放这一条。好处是不用
   * 先勾选、再滚回顶部——一条条看完顺手就加了。已建档的行不显示这个按钮，
   * 它们走的是「重新推送」。
   */
  const handlePromoteOne = async (item: SelectionItem) => {
    if (promoteLock.current || item.promoted_product_id) return;
    promoteLock.current = true;
    setPromoting(true);
    setActing({ id: item.id, kind: 'promote' });
    // 先存好名字：refresh() 之后 item 就是旧对象了。
    const label = item.product_name || item.chinese_product_name || item.plid || '这条商品';
    try {
      const result = await promoteSelection([item.id]);
      if (!promotionMounted.current) return;
      await refresh();
      if (!promotionMounted.current) return;
      if (result.created) {
        const productNo = result.products?.[0]?.product_no;
        setNotice({
          kind: 'ok',
          text: `已把「${label}」加入产品列表${productNo ? `（产品编号 ${productNo}）` : ''}。`,
        });
      } else {
        setNotice({
          kind: 'info',
          text: `「${label}」没加进去：${result.skip_details?.[0]?.reason || '已建档或身份重复'}`,
        });
      }
    } catch (error) {
      if (!promotionMounted.current) return;
      setNotice({ kind: 'warn', text: `加入产品列表失败：${(error as Error).message}` });
    } finally {
      promoteLock.current = false;
      setActing(null);
      if (promotionMounted.current) setPromoting(false);
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

  /**
   * 保存卖家门户令牌。
   *
   * 令牌从卖家门户的 localStorage 里取：打开 sellers.takealot.com → F12 →
   * Application → Local Storage → `usr_st_auth` → 复制里面的 `api_key`。
   * 有效期 24 小时，过期后主图会重新开始吃 429、重尺也补不上。
   */
  const handleSavePortalToken = async () => {
    const value = portalInput.trim();
    if (!value) return;
    setPortalSaving(true);
    try {
      const state = await setPortalToken(value);
      setPortalState(state);
      setPortalInput('');
      setNotice({
        kind: 'ok',
        text: state.expires_at
          ? `门户令牌已保存，${formatTokenExpiry(state.expires_at)} 前有效。`
          : '门户令牌已保存。',
      });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setPortalSaving(false);
    }
  };

  const handleClearPortalToken = async () => {
    setPortalSaving(true);
    try {
      setPortalState(await setPortalToken(''));
      setNotice({ kind: 'info', text: '已清除门户令牌，抓取将退回公开接口。' });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setPortalSaving(false);
    }
  };

  /** 用服务器这一侧的令牌实测一个样本，把主图和重尺的结果摊开看。 */
  const handleProbePortal = async () => {
    const sample = items.find(item => item.plid)?.plid;
    if (!sample) {
      setNotice({ kind: 'info', text: '列表里至少要有带 PLID 的商品才能测' });
      return;
    }
    setPortalProbing(true);
    setPortalProbe(null);
    try {
      const report = await probeSelectionPortal(sample);
      setPortalProbe(report);
      setPortalState(report.token);
      setNotice({
        kind: report.ok ? 'ok' : 'warn',
        text: `PLID ${report.plid}：${report.reason}`,
      });
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setPortalProbing(false);
    }
  };

  /** 让服务器自己去读一次公开接口，确认「抓不到主图」到底卡在哪一层。 */
  const handleProbePublic = async () => {    const sample = items.find(item => item.plid)?.plid;
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

  /** 清理：有品牌 / 已在产品列表 / 池内重复。
   *
   * 先 dry-run 拿到清单给人看一眼，确认了才真删——这三类删掉是不可逆的，
   * 尤其「有品牌」判错的代价是丢候选。
   */
  const handlePurge = async () => {
    if (promoting) return;
    try {
      const preview = await purgeSelection(true);
      if (!preview.total) {
        setNotice({ kind: 'ok', text: '没有需要清理的记录：没有有品牌的、没有已在产品列表的、池子里也没有重复的。' });
        return;
      }
      const bits = [
        preview.counts.branded ? `有品牌 ${preview.counts.branded} 条` : '',
        preview.counts.in_products ? `已在产品列表 ${preview.counts.in_products} 条` : '',
        preview.counts.duplicates ? `重复 ${preview.counts.duplicates} 条` : '',
      ].filter(Boolean).join('、');
      const sample = (preview.samples.branded || []).slice(0, 5)
        .map(row => `· ${row.brand || '—'} ${row.product_name || row.chinese_product_name}`)
        .join('\n');
      const ok = window.confirm(
        `将删除 ${preview.total} 条：${bits}。\n`
        + (sample ? `\n其中部分有品牌商品：\n${sample}\n\n` : '\n')
        + '已建过产品的记录会保留。删除后不可恢复，确定继续？',
      );
      if (!ok) return;
      const result = await purgeSelection(false);
      setNotice({ kind: 'ok', text: `已清理 ${result.deleted ?? 0} 条记录` });
      setPage(0);
      await refresh();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    }
  };

  /**
   * 把选品池里的修改回推到已建的产品主档。
   *
   * 「已建产品」以前是把这条记录锁死的，改只能去产品详情页改——但那边看不到
   * 选品池的缺项提示，重尺改完也没法在这里重算。现在这里能改，改完点这个
   * 把值写回主档并重算利润。
   *
   * 产品列表里被人工改过的字段不会被覆盖，结果里会列出来。
   */
  const handleSync = async (ids: string[]) => {
    if (!ids.length || promoting) return;
    setPromoting(true);
    if (ids.length === 1) setActing({ id: ids[0], kind: 'sync' });
    try {
      const result = await syncSelectionToProducts(ids);
      const kept = result.products.flatMap(row => row.kept_fields || []);
      const keptNote = kept.length
        ? `（产品列表里人工改过的 ${kept.length} 个字段没覆盖：${[...new Set(kept)].join('、')}）`
        : '';
      setNotice({
        kind: 'ok',
        text: `已把 ${result.updated} 条更新到产品列表${keptNote}`
          + (result.skipped ? `；${result.skipped} 条没推：${result.skip_details.map(d => d.reason).join('；')}` : ''),
      });
      await refresh();
    } catch (error) {
      setNotice({ kind: 'warn', text: (error as Error).message });
    } finally {
      setActing(null);
      setPromoting(false);
    }
  };

  const handleDeleteBatch = async () => {
    if (promoteLock.current) return;
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

  // 已测算数：优先用后端直接给的 measured（它和卡片分区、跳过已测算是同一个判据），
  // 老接口没有这个字段时才退回 total - pending。都缺时别算出 NaN 显示在进度条上。
  const measuredCount = summary
    ? (summary.measured ?? Math.max(0, (summary.total || 0) - (summary.pending || 0)))
    : 0;
  const measuredPercent = summary && summary.total
    ? Math.round((measuredCount / summary.total) * 100)
    : 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const selectedOnPage = useMemo(() => items.filter(item => item.selected).length, [items]);
  // 卡片按「有没有算出利润率」分成两块：没算出来的要干活，算出来的折叠起来备查。
  // 已建产品归到「已测算」——它们不是待测算对象，混在未测算里只会干扰。
  //
  // 分区归属用**列表加载时**定下来的 `sectionOf`，不用实时的 profit_margin：
  // 否则填完最后一个字段 → 利润率一出现 → 卡片当场搬进默认折叠着的「已测算」，
  // 输入框跟着卸载（React 换了父节点就是重挂），光标和没提交完的草稿一起没了。
  // 用户的原话是「我还没填完它就跑了，还得切合格/不合格再找回来」。
  // 现在算完就留在原位出结果，要不要建档在这一张卡上决定；下次加载列表才归位。
  const sectionOfItem = useCallback(
    (item: SelectionItem): SectionKey => sectionOf[item.id]
      ?? (measureState(item) === 'unmeasured' ? 'unmeasured' : 'measured'),
    [sectionOf],
  );
  const unmeasuredItems = useMemo(
    () => items.filter(item => sectionOfItem(item) === 'unmeasured'),
    [items, sectionOfItem],
  );
  const measuredItems = useMemo(
    () => items.filter(item => sectionOfItem(item) !== 'unmeasured'),
    [items, sectionOfItem],
  );
  // 留在「未测算」里但已经算出结果的——它们是这次编辑的产物，等用户看完再归位。
  const settledInPlace = useMemo(
    () => unmeasuredItems.filter(item => measureState(item) !== 'unmeasured'),
    [unmeasuredItems],
  );
  /** 把上面这批归到「已测算」。只在用户主动点的时候发生——自动搬正是要修的问题。 */
  const settleMeasured = () => {
    if (!settledInPlace.length) return;
    setSectionOf(prev => {
      const next = { ...prev };
      settledInPlace.forEach(item => { delete next[item.id]; });
      return next;
    });
  };
  // 抓取按钮的文案要跟着范围走：选了批次就只抓这个批次（导入新表后正好是这一批）。
  const fetchNewLabel = batchId ? '抓取本批次没抓过的' : '抓取没抓过的';

  // 两个分区共用同一套卡片属性，抽出来免得改一处漏一处。
  const renderCard = (item: SelectionItem) => (
    <SelectionCard
      key={item.id}
      item={item}
      busy={promoting || busyIds.has(item.id)}
      saving={busyIds.has(item.id)}
      acting={acting?.id === item.id ? acting.kind : null}
      onPatch={patch => void handlePatch(item.id, patch)}
      onToggle={() => handleToggle(item)}
      feeCategories={feeCategoryState.categories}
      onSync={item.promoted_product_id ? () => void handleSync([item.id]) : undefined}
      onPromote={item.promoted_product_id ? undefined : () => void handlePromoteOne(item)}
    />
  );
  // 勾选中且已建产品的行——这批是「要回推到产品列表」的，不是「要新建」的。
  const promotedSelectedIds = useMemo(
    () => items.filter(item => item.selected && item.promoted_product_id).map(item => item.id),
    [items],
  );
  const promotedSelected = promotedSelectedIds.length;

  const portalBroken = portalState !== null && (!portalState.configured || portalState.expired);
  const importOpen = importExpanded || importing || enriching || promoting || portalBroken;
  const importHint = batchName.trim() ? `当前批次：${batchName.trim()}` : '选一份 Excel / CSV 起新批次';

  // 抓取面板：抓取中或刚提交时强制展开，否则看不到进度和取消按钮。
  const crawlTargetCount = crawlTargets.split('\n').filter(line => line.trim()).length;
  const crawlOpen = crawlExpanded || crawlRunning || crawlSubmitting;
  const crawlHint = crawlRunning
    ? '抓取中，点开看进度'
    : crawlTask
      ? `上次：${crawlTask.stage}${crawlTask.targets_total ? ` ${crawlTask.targets_done}/${crawlTask.targets_total}` : ''}`
      : crawlTargetCount
        ? `${crawlMode === 'keyword' ? '关键词' : '链接'} ${crawlTargetCount} 个 · ${crawlSort}${crawlAudit ? ' · 评论体检' : ''}`
        : crawlMode === 'keyword' ? '按关键词搜索 · 还没填关键词' : '按链接抓 · 还没填链接';

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
          <button type="button" className="btn" onClick={() => void handleEnrich(true)} disabled={promoting || enriching || importing}>
            {enriching ? '抓取中…' : skipMeasured ? '重抓未测算的资料' : '重抓全部资料'}
          </button>
          <button type="button" className="btn-primary" onClick={() => void handlePromote()} disabled={promoting || enriching || importing || busyIds.size > 0 || !summary?.selected}>
            {promoting ? '新建中…' : `批量加入产品列表${summary?.selected ? `（${summary.selected}）` : ''}`}
          </button>
          {/* 已建产品的行也能勾选了：勾上之后这个按钮把修改回推到产品列表。 */}
          {promotedSelected > 0 ? (
            <button type="button" className="btn" onClick={() => void handleSync(promotedSelectedIds)} disabled={promoting || enriching || importing}>
              {promoting ? '更新中…' : `更新到产品列表（${promotedSelected}）`}
            </button>
          ) : null}
        </div>
      </div>

      {notice && (
        <div className={`operations-note is-dismissible selection-notice is-${notice.kind}`} role="status">
          {notice.text}
          <button type="button" className="note-dismiss" aria-label="关闭这条提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      {/*
        从 Takealot 抓取。抓取本身必须在本机浏览器里发生，所以这里是「下指令 +
        看进度」的入口：抓到的身份和发现信号由本机采集服务直投进这个池子，
        主图/类目/重尺/三档价格仍由上面那个抓取按钮走后端补，不重复造。
        后端 X-API-Key 不在这里经手——它存进本机采集服务的本地配置，由服务自己带去后端。
      */}
      <section className={`selection-import selection-crawl${crawlOpen ? '' : ' is-collapsed'}`}>
        <button type="button" className="selection-import-head" onClick={toggleCrawlPanel}
          aria-expanded={crawlOpen}
          aria-label={`从 Takealot 抓取，${crawlOpen ? '已展开，点击收起' : '已收起，点击展开'}`}>
          <span className="selection-section-caret" aria-hidden="true">▾</span>
          <strong className="selection-section-title">从 Takealot 抓取</strong>
          <span className={`selection-crawl-state is-${agentReach}`}>
            {agentReach === 'checking'
              ? '检测本机采集服务…'
              : agentReach === 'ok'
                ? `在线${agentHealth?.busy ? '（有任务在跑）' : ''}${agentHealth?.has_api_key ? '' : '（未存密钥）'}`
                : '未启动'}
          </span>
          <span className="selection-import-head-hint">{crawlHint}</span>
        </button>

        <div className="selection-import-body">
          <div className="selection-import-main">
          <label className="selection-field is-wide">
            <span>采集服务地址</span>
            <input
              value={agentBaseDraft}
              onChange={event => setAgentBaseDraft(event.target.value)}
              onBlur={saveAgentBase}
              spellCheck={false}
              placeholder={DEFAULT_AGENT_BASE}
            />
          </label>
          <button type="button" className="btn" onClick={() => { saveAgentBase(); void checkAgent(agentBaseDraft.trim() || agentBase); }}>
            重新检测
          </button>
        </div>

        <p className="selection-import-hint">
          抓取在你自己已登录的浏览器里跑（Takealot 有 Cloudflare 风控，放云端必被拦）。先在 Mac 上运行
          <code> TakealotTool/启动采集服务.command </code>，再从这里下指令。抓回来的是身份 + 发现信号
          （链接 / PLID / 名称 / 前台价 / 评分 / 评论数），主图、类目、重尺和三档价格仍由上面的抓取按钮走后端补。
        </p>

        {/*
          密钥只在「本机采集服务」这一侧存一次（127.0.0.1 回环），网页不再把
          后端 X-API-Key 转发到公网页面能触达的任何地方——这是 PR #222 砍掉抓取入口的原因，
          这里用「本地存密钥」而不是「网页透传」把功能安全地还回来。
        */}
        <details className="selection-agent-credential">
          <summary>本机采集服务凭证（仅首次）</summary>
          <p className="selection-import-hint">
            API Key 存到本机采集服务（~/TakealotTool/selection_agent.json），之后抓取由服务自己带密钥去后端，
            网页这层再也碰不到密钥。只走 127.0.0.1 回环，不外泄。
          </p>
          <div className="selection-import-main">
            <label className="selection-field is-wide">
              <span>API Key</span>
              <input
                type="password"
                value={agentKeyDraft}
                onChange={event => setAgentKeyDraft(event.target.value)}
                placeholder="后端 X-API-Key"
                spellCheck={false}
              />
            </label>
            <button type="button" className="btn" onClick={() => void saveAgentConfig()} disabled={savingAgentConfig}>
              {savingAgentConfig ? '保存中…' : '保存到本机采集服务'}
            </button>
          </div>
          {agentConfigMsg ? <p className="selection-import-head-hint">{agentConfigMsg}</p> : null}
        </details>

        <div className="selection-import-main">
          <label className="selection-field">
            <span>抓取方式</span>
            <select value={crawlMode} onChange={event => setCrawlMode(event.target.value as 'keyword' | 'link')} disabled={crawlRunning}>
              <option value="keyword">按关键词搜索</option>
              <option value="link">按链接抓</option>
            </select>
          </label>
          <label className="selection-field">
            <span>每词批次<i>可空</i></span>
            <input value={crawlPages} onChange={event => setCrawlPages(event.target.value)}
              inputMode="numeric" placeholder="跑完为止" disabled={crawlRunning} />
          </label>
          <label className="selection-field">
            <span>最少评论<i>可空</i></span>
            <input value={crawlMinReviews} onChange={event => setCrawlMinReviews(event.target.value)}
              inputMode="numeric" placeholder="不限" disabled={crawlRunning} />
          </label>
          <label className="selection-field">
            <span>排序</span>
            <select value={crawlSort} onChange={event => setCrawlSort(event.target.value)} disabled={crawlRunning}>
              {(agentHealth?.sorts?.length
                ? agentHealth.sorts
                : ['价格从高到低', '价格从低到高', '新品优先', '最受欢迎', '评分最高']
              ).map((option: string) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
          <label className="selection-field is-wide">
            <span>批次名<i>可空</i></span>
            <input value={crawlBatchName} onChange={event => setCrawlBatchName(event.target.value)}
              placeholder="例如：吸尘器袋 11-04" disabled={crawlRunning} />
          </label>
          <label className="selection-crawl-audit"
            title="勾上后每个商品多开一次详情页，换「本年度评论数」和「畅销变体」——这两列后端拿不到">
            <input type="checkbox" checked={crawlAudit} onChange={() => setCrawlAudit(prev => !prev)} disabled={crawlRunning} />
            <span>评论体检</span>
          </label>
        </div>

        <label className="selection-field is-wide selection-crawl-input">
          <span>{crawlMode === 'keyword' ? '关键词（一行一个）' : 'Takealot 链接（一行一个）'}</span>
          <textarea
            rows={4}
            value={crawlTargets}
            onChange={event => setCrawlTargets(event.target.value)}
            spellCheck={false}
            placeholder={crawlMode === 'keyword'
              ? 'vacuum cleaner bags\ncat litter box'
              : 'https://www.takealot.com/xxx/PLID12345678'}
          />
        </label>

        <div className="selection-import-main">
          <button type="button" className="btn-primary" onClick={() => void handleStartCrawl()} disabled={crawlSubmitting || crawlRunning}>
            {crawlSubmitting ? '下指令中…' : crawlRunning ? '抓取中…' : '开始抓取'}
          </button>
          {crawlRunning && (
            <button type="button" className="btn" onClick={() => void handleCancelCrawl()}>取消</button>
          )}
          {crawlTask && (
            <span className="selection-crawl-progress">
              {crawlTask.stage}｜商品 {crawlTask.products_total}｜新建 {crawlTask.created}
              {crawlTask.duplicates ? `｜池内重复 ${crawlTask.duplicates}` : ''}
              {crawlTask.targets_total ? `｜进度 ${crawlTask.targets_done}/${crawlTask.targets_total}` : ''}
            </span>
          )}
        </div>

        {crawlTask?.logs?.length ? (
          <pre className="selection-crawl-log">{crawlTask.logs.slice(-40).join('\n')}</pre>
        ) : null}
        {crawlTask?.errors?.length ? (
          <p className="selection-import-hint">
            出错 {crawlTask.errors.length} 处：{crawlTask.errors.slice(0, 3).join('；')}
            {crawlTask.errors.length > 3 ? ' …' : ''}
          </p>
        ) : null}
        </div>
      </section>

      <section className={`selection-import${importOpen ? '' : ' is-collapsed'}`}>
        <button type="button" className="selection-import-head" onClick={toggleImportPanel}
          aria-expanded={importOpen}
          aria-label={`Excel 与 CSV 导入，${importOpen ? '已展开，点击收起' : '已收起，点击展开'}`}>
          <span className="selection-section-caret" aria-hidden="true">▾</span>
          <strong className="selection-section-title">Excel / CSV 导入</strong>
          {portalBroken && <span className="selection-notice warn">{portalState?.configured ? '令牌已过期' : '未配令牌'}</span>}
          <span className="selection-import-head-hint">
            {portalBroken ? '令牌失效时一键上架会整批失败，请重新连接卖家门户' : importHint}
          </span>
        </button>

        <div className="selection-import-body">
        <div className="selection-import-main">
          <label className="selection-file">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xlsm,.csv"
              disabled={promoting || importing}
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
              placeholder="例如：竞品店铺 10-26" disabled={promoting || importing} />
          </label>
          <button type="button" className="btn" onClick={() => void handleEnrich(false)}
            disabled={promoting || enriching || !summary?.total}>
            {enriching ? '抓取中…' : fetchNewLabel}
          </button>
          {/*
            「跳过已测算」是这里最常按的一个开关：测算过的商品价格、重尺、成本
            都是核对过的，重抓既费门户配额（200 次/分钟），也可能把刚调好的售价
            换回去。默认勾着，想刷已测算的再取消。
          */}
          <label className="selection-skip-measured"
            title="勾上后，两个抓取按钮都会跳过已经算出利润率的商品（它们在你说的「已测算」分区里）">
            <input type="checkbox" checked={skipMeasured} onChange={toggleSkipMeasured}
              disabled={promoting || enriching} />
            <span>跳过已测算</span>
          </label>
        </div>
        <p className="selection-import-hint">
          表头认这些列（中英文都行）：产品链接 / 产品名称 / TSIN / PLID / 产品价格(R) / 产品评分 / 评分数量 /
          Qty / Net Sales Amount。只给链接、只给 TSIN 都能跑；同一商品的多行会按 PLID 或 TSIN 合并。
        </p>

        {/*
          卖家门户令牌。放在这里而不是设置页，是因为它和「抓主图/重尺」是同一件
          事：没有它，1804 个候选里有一多半拿不到主图，长宽高更是完全没有。
        */}
        <details className="selection-portal-token" open={!portalState?.configured || portalState?.expired}>
          <summary>
            卖家门户令牌：
            {portalState === null
              ? '读取中…'
              : !portalState.configured
                ? '未配置 —— 重尺抓不到、主图容易限流'
                : portalState.expired
                  ? '已过期 —— 打开一次卖家门户就会自动续上'
                  : `有效${portalState.days_left != null ? `（还剩 ${portalState.days_left} 天）` : ''}`}
          </summary>
          <p className="selection-import-hint">
            默认<strong>自动同步</strong>：本机每 30 分钟从 Chrome 的 <code>sellers.takealot.com</code>{' '}
            登录态里读一次最新令牌推上来，不用再手动取。前提是这台 Mac 上装过{' '}
            <code>scripts/install-portal-token-sync.sh</code>；令牌本身只有 24 小时寿命，续期由浏览器在
            你打开卖家门户时完成，脚本只负责把新令牌搬过来。
          </p>
          <p className="selection-import-hint">
            自动同步没跑起来时才需要手动兜底。浏览器打开 <code>sellers.takealot.com</code> 并<strong>先登录</strong>，
            按 F12 打开控制台，粘这一行——令牌会直接进剪贴板，回来按 Ctrl/⌘+V 就行：
          </p>
          <pre className="selection-portal-snippet">
            <code>{PORTAL_TOKEN_SNIPPET}</code>
          </pre>
          <p className="selection-import-hint">
            不想用控制台就手动取：Application → Local Storage → <code>sellers.takealot.com</code> →
            <code>usr_st_auth</code> → 复制 <code>api_key</code> 那一长串 <code>eyJ</code> 开头的内容。
            有效期 24 小时，过期后重新取一次即可。有了它，主图和长宽高走卖家门户自己的目录接口，
            不再受公开接口限流影响。
          </p>
          <div className="selection-import-main">
            <label className="selection-field is-wide">
              <span>粘贴令牌</span>
              <input
                type="password"
                value={portalInput}
                onChange={event => setPortalInput(event.target.value)}
                placeholder="eyJhbGciOiJIUzI1NiIs..."
                disabled={portalSaving}
              />
            </label>
            <button type="button" className="btn" onClick={() => void handleSavePortalToken()}
              disabled={portalSaving || !portalInput.trim()}>
              {portalSaving ? '保存中…' : '保存令牌'}
            </button>
            <button type="button" className="btn" onClick={() => void handleProbePortal()}
              disabled={portalProbing || !portalState?.configured}>
              {portalProbing ? '测试中…' : '测一次主图与重尺'}
            </button>
            {portalState?.configured && (
              <button type="button" className="btn" onClick={() => void handleClearPortalToken()}
                disabled={portalSaving}>
                清除
              </button>
            )}
          </div>
          {portalState?.expires_at && (
            <p className="selection-portal-token">
              到期时间：{formatTokenExpiry(portalState.expires_at)}
            </p>
          )}
          {portalProbe && (
            <details className="selection-portal-token" open={!portalProbe.ok}>
              <summary>
                门户接口测试：PLID {portalProbe.plid} —— {portalProbe.reason}
              </summary>
              <ul>
                <li>HTTP：{portalProbe.http_status ?? '—'}</li>
                <li>商品名：{portalProbe.product_name || '（没拿到）'}</li>
                <li>
                  主图：{portalProbe.product_image_url
                    ? <code>{portalProbe.product_image_url}</code>
                    : '（没拿到）'}
                  {portalProbe.image_count > 1 ? `，共 ${portalProbe.image_count} 张` : ''}
                </li>
                <li>前台类目：{portalProbe.takealot_category_path || '（没拿到）'}</li>
                <li>
                  长宽高：{portalProbe.dimensions.length_mm
                    ? `${portalProbe.dimensions.length_mm} × ${portalProbe.dimensions.width_mm} × ${portalProbe.dimensions.height_mm} mm`
                    : '（Takealot 目录里没有实测尺寸）'}
                  {portalProbe.dimensions.weight_kg
                    ? `，重 ${portalProbe.dimensions.weight_kg} kg`
                    : ''}
                  {portalProbe.dimensions.source ? `（来源：${portalProbe.dimensions.source}）` : ''}
                </li>
              </ul>
            </details>
          )}
        </details>
        {importResult && (
          <p className="selection-import-result">
            {importResult.sheet}：读入 {importResult.source_rows} 行 → 新建 {importResult.created} 个商品
            {importResult.branded ? `，跳过 ${importResult.branded} 个有品牌` : ''}
            {importResult.already_in_products ? `，跳过 ${importResult.already_in_products} 个已在产品列表` : ''}
            {importResult.pool_duplicates ? `，跳过 ${importResult.pool_duplicates} 个池子里已有` : ''}
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
        {/* failures 做兜底：接口少给这一个数组时，整页不该跟着白屏——那是最难
            解释的一种「抓取失败」。 */}
        {enrichResult && (enrichResult.failures?.length ?? 0) > 0 && (
          <details className="selection-import-result">
            <summary>{enrichResult.failed} 个没抓到，看原因</summary>
            <ul>
              {enrichResult.failures.map(row => (
                <li key={row.id}>{row.plid || row.tsin || row.id}：{row.message}</li>
              ))}
            </ul>
          </details>
        )}
        </div>
      </section>

      {summary && summary.total > 0 && (
        <section className="selection-summary">
          {/* 进度条本身就是筛选器：看到「还有 800 个没测算」的下一个动作一定是
              「把它们调出来」，所以点它直接切到未测算。省掉一个下拉框里的来回。 */}
          <button
            type="button"
            className={`selection-progress ${status === '待核算' ? 'is-active' : ''}`}
            onClick={() => { setStatus(status === '待核算' ? '' : '待核算'); setPage(0); }}
            title={status === '待核算' ? '当前只看未测算，点击看全部' : '点击只看未测算'}
          >
            <span className="selection-progress-head">
              <strong>{measuredCount}</strong>
              <span>/ {summary.total} 已测算</span>
              <em>{measuredPercent}%</em>
            </span>
            <span className="selection-progress-bar" aria-hidden="true">
              <i style={{ width: `${measuredPercent}%` }} />
            </span>
            <span className="selection-progress-hint">
              {status === '待核算' ? '点击看全部' : `未测算 ${summary.pending} 个 · 点击只看这些`}
            </span>
          </button>
          <div><strong>{summary.total}</strong><span>候选商品</span></div>
          <div><strong>{summary.with_image}</strong><span>有主图</span></div>
          <div><strong>{summary.with_dimensions}</strong><span>重尺齐全</span></div>
          <div><strong>{summary.qualified}</strong><span>合格（≥15%）</span></div>
          <div><strong>{summary.selected}</strong><span>已勾选</span></div>
          <div><strong>{summary.promoted}</strong><span>已建产品</span></div>
          {summary.pending_sync > 0 && <div className="is-warn"><strong>{summary.pending_sync}</strong><span>待更新到产品列表</span></div>}
          {summary.price_in_stock > 0 && <div><strong>{summary.price_in_stock}</strong><span>现货最低价</span></div>}
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
            {STATUS_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
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
        <button type="button" className="btn" onClick={() => void handleSelectQualified()} disabled={promoting}>
          按 15% 勾选达标商品
        </button>
        <button type="button" className="btn" onClick={() => void handleClearSelection()} disabled={promoting || !selectedOnPage}>
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
          disabled={promoting || !bulkShipping || !selectedOnPage}>
          应用到本页选中
        </button>
        {batchId && (
          <button type="button" className="btn-link-danger" onClick={() => void handleDeleteBatch()} disabled={promoting}>
            删除本批次
          </button>
        )}
        <button type="button" className="btn-link-danger" onClick={() => void handlePurge()} disabled={promoting}>
          清理有品牌 / 重复
        </button>
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
        <>
          {/*
            两块分开：没测算的是要干活的，测算过的是备查的。已测算那块默认折叠，
            于是「这一页要处理哪些」一眼就能看完，不用在几十张算好的卡片里翻。
            分区的计数是**本页**的（列表是分页的），全局进度看上面的进度条。
          */}
          {unmeasuredItems.length > 0 && (
            <CardSection
              tone="unmeasured"
              title="未测算"
              count={unmeasuredItems.length}
              hint="填完就出利润率，卡片留在原位；抓取只会抓这些"
              note={settledInPlace.length > 0 ? (
                <div className="selection-section-note">
                  <span>{settledInPlace.length} 个已经算出结果，留在原位方便你看完再决定</span>
                  <button type="button" className="btn-link" onClick={settleMeasured}>
                    归到「已测算」
                  </button>
                </div>
              ) : undefined}
              collapsed={collapsed.unmeasured}
              onToggle={() => toggleSection('unmeasured')}
            >
              {unmeasuredItems.map(renderCard)}
            </CardSection>
          )}
          {measuredItems.length > 0 && (
            <CardSection
              tone="measured"
              title="已测算"
              count={measuredItems.length}
              // 未测算一条都没有时（整页都算完了），已测算这块必须展开，否则整页
              // 只剩一个折叠标题，看着像加载失败。
              collapsed={collapsed.measured && unmeasuredItems.length > 0}
              hint={skipMeasured ? '已折叠备查；抓取时跳过（可在上面取消勾选）' : '已折叠备查；抓取时会被重抓'}
              onToggle={() => toggleSection('measured')}
            >
              {measuredItems.map(renderCard)}
            </CardSection>
          )}
        </>
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
