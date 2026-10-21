import { useEffect, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import { openUrl } from '../api';
import type { Product } from '../types';
import { formatPrice, formatPercent, SELECTION_STATUS_MAP, SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';
import { ProductImage } from '../utils/imageFallback';
import { useFeeCategories } from '../utils/useFeeCategories';
import { MARKET_META, MARKET_ORDER, formatMarketPrice, marketInsights, parseMarketPrices, serializeMarketPrices, today } from '../utils/marketPrice';
import type { MarketCode, MarketPrices } from '../utils/marketPrice';
import { toNumber } from '../utils/number';
import { copyText } from '../utils/clipboard';
import { notifyCatalogChanged } from '../utils/catalogEvents';

export default function ProductDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const api = useApi();
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const feeCategoryState = useFeeCategories(api);
  const feeCategories = feeCategoryState.categories;
  const [editMode, setEditMode] = useState(searchParams.get('edit') === '1');

  // edit form
  const [editData, setEditData] = useState<Partial<Product>>({});
  // 多市场参考价：草稿态便于逐格编辑，保存时序列化回 editData.market_prices
  const [mpDraft, setMpDraft] = useState<MarketPrices>({});
  const [copiedSku, setCopiedSku] = useState(false);
  const copySku = async () => {
    if (!product?.sku) return;
    const ok = await copyText(product.sku);
    if (!ok) return;
    setCopiedSku(true);
    setTimeout(() => setCopiedSku(false), 2000);
  };

  useEffect(() => {
    if (!id) return;
    if (id === 'new') {
      navigate('/create', { replace: true });
      return;
    }
    api.getProduct(id)
      .then((p: Product) => {
        setProduct(p);
        setMpDraft(parseMarketPrices(p.market_prices));
        setEditData({
          product_name: p.product_name || '',
          takealot_url: p.takealot_url || '',
          product_image_url: p.product_image_url || '',
          note: p.note || '',
          actual_sale_price_zar: p.actual_sale_price_zar,
          unit_price_cny: p.unit_price_cny,
          purchase_cost_cny: p.purchase_cost_cny,
          purchase_shipping_cny: p.purchase_shipping_cny,
          purchase_quantity: p.purchase_quantity,
          length_mm: p.length_mm,
          width_mm: p.width_mm,
          height_mm: p.height_mm,
          actual_weight_kg: p.actual_weight_kg,
          packaging_cost_per_unit_cny: p.packaging_cost_per_unit_cny,
          shipping_method: p.shipping_method,
          fee_category: p.fee_category,
          purchase_url: p.purchase_url || '',
          sku: p.sku || '',
          chinese_product_name: p.chinese_product_name || '',
          link_status: p.link_status || '未购买',
          inbound_listing_fee_cny: p.inbound_listing_fee_cny,
          outbound_operation_fee_cny: p.outbound_operation_fee_cny,
          last_mile_delivery_fee_cny: p.last_mile_delivery_fee_cny,
          other_fee_cny: p.other_fee_cny,
          fulfillment_fee_zar: p.fulfillment_fee_zar,
          manual_domestic_forwarding_cny: p.manual_domestic_forwarding_cny ?? null,
          manual_international_shipping_cny: p.manual_international_shipping_cny ?? null,
          manual_overseas_op_cost_cny: p.manual_overseas_op_cost_cny ?? null,
          manual_success_fee_zar: p.manual_success_fee_zar ?? null,
          manual_fulfillment_fee_zar: p.manual_fulfillment_fee_zar ?? null,
          manual_total_cost_zar: p.manual_total_cost_zar ?? null,
          market_prices: p.market_prices || '',
        });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  const handleSave = async () => {
    if (!id) return;
    try {
      // 手动覆盖字段：空字符串转为 null
      const manualFields = [
        'manual_domestic_forwarding_cny', 'manual_international_shipping_cny',
        'manual_overseas_op_cost_cny', 'manual_success_fee_zar',
        'manual_fulfillment_fee_zar', 'manual_total_cost_zar',
      ];
      const cleanedData: any = { ...editData };
      manualFields.forEach((f) => {
        if (cleanedData[f] === '' || cleanedData[f] === undefined) {
          cleanedData[f] = null;
        }
      });
      // 显式计算采购总成本 = 产品单价 × 采购数量
      const upVal = cleanedData.unit_price_cny ?? product?.unit_price_cny ?? 0;
      const qtyVal = cleanedData.purchase_quantity ?? product?.purchase_quantity ?? 4;
      cleanedData.purchase_cost_cny = upVal * qtyVal;

      const updated: Product = await api.updateProduct(id, {
        ...cleanedData,
        fee_category_confirmed: editData.fee_category ? true : product?.fee_category_confirmed,
      });
      setProduct(updated);
      setEditMode(false);
      // 名称/SKU 变了要让装箱单重新同步，不然那边还是旧名字。
      notifyCatalogChanged(['sku', 'product_name', 'chinese_product_name']);
      if (searchParams.get('from')) navigate(-1);
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '保存失败');
    }
  };

  const handleDelete = async () => {
    if (!id || !product) return;
    if (!window.confirm(`确定删除「${product.product_name}」？`)) return;
    try {
      await api.deleteProduct(id);
      navigate('/products');
    } catch (e: any) {
      alert(e.message);
    }
  };

  const updateField = (field: string, value: any) => {
    setEditData((prev) => {
      const next = { ...prev, [field]: value };
      // Auto-compute purchase_cost_cny when unit_price_cny or purchase_quantity changes
      if (field === 'unit_price_cny' || field === 'purchase_quantity') {
        const up = field === 'unit_price_cny' ? value : prev.unit_price_cny;
        const qty = field === 'purchase_quantity' ? value : prev.purchase_quantity;
        if (up != null && up > 0) {
          next.purchase_cost_cny = up * (qty != null ? qty : 4);
        }
      }
      return next;
    });
  };

  /** 改一个市场的参考价：清空即删除该市场；写入时自动补币种、来源和日期 */
  const setMarketPrice = (code: MarketCode, raw: string) => {
    const next: MarketPrices = { ...mpDraft };
    const value = raw.trim() === '' ? NaN : Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      delete next[code];
    } else {
      next[code] = {
        ...(next[code] || {}),
        price: value,
        cur: MARKET_META[code].cur,
        src: next[code]?.src || '手工',
        at: today(),
      };
    }
    setMpDraft(next);
    updateField('market_prices', serializeMarketPrices(next));
  };

  /** 淘宝均价的样本条数 */
  const setMarketSample = (code: MarketCode, raw: string) => {
    const entry = mpDraft[code];
    if (!entry) return;
    const n = raw.trim() === '' ? NaN : Number(raw);
    const next: MarketPrices = {
      ...mpDraft,
      [code]: { ...entry, n: Number.isFinite(n) && n > 0 ? Math.round(n) : undefined },
    };
    setMpDraft(next);
    updateField('market_prices', serializeMarketPrices(next));
  };

  if (loading) return <div className="loading">加载中...</div>;
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!product) return <div className="loading">产品不存在</div>;

  const status = SELECTION_STATUS_MAP[product.selection_status];
  const skuCount = (editData.sku || product.sku || '').split(/[\s,，;；]+/).filter(Boolean).length;

  return (
    <div className="product-detail-page">
      <div className="page-heading">
        <div>
          <a href="#" onClick={(e) => { e.preventDefault(); navigate(-1); }} style={{ fontSize: 13, color: 'var(--color-primary)', textDecoration: 'none' }}>&larr; 返回列表</a>
          <h2 style={{ marginTop: 4 }}>
            {product.product_name || '(无标题)'}
            <span className={`status-badge status-${product.selection_status === '合格选品' ? 'qualified' : product.selection_status === '不建议选品' ? 'not-recommended' : product.selection_status === '待确认品类' ? 'pending' : 'incomplete'}`} style={{ marginLeft: 12, verticalAlign: 'middle' }}>
              {status?.label || product.selection_status}
            </span>
          </h2>
        </div>
        <div className="page-actions">
          {!editMode ? (
            <button className="btn btn-primary" onClick={() => setEditMode(true)}>编辑</button>
          ) : (
            <>
              <button className="btn btn-success" onClick={handleSave}>保存</button>
              <button className="btn btn-outline" onClick={() => setEditMode(false)}>取消</button>
            </>
          )}
          <button className="btn btn-danger" onClick={handleDelete}>删除</button>
        </div>
      </div>

      {/* Takealot Info */}
      <div className="card">
        <div className="card-title">Takealot 信息</div>
        {editMode && <div className="form-row" style={{ marginBottom: 16 }}>
          <div className="form-group"><label>英文产品标题</label><input value={editData.product_name || ''} onChange={e => updateField('product_name', e.target.value)} /></div>
          <div className="form-group"><label>Takealot 链接</label><input type="url" value={editData.takealot_url || ''} onChange={e => updateField('takealot_url', e.target.value)} /></div>
          <div className="form-group"><label>产品图片链接</label><input type="url" value={editData.product_image_url || ''} onChange={e => updateField('product_image_url', e.target.value)} /></div>
        </div>}
        <div className="two-col">
          <div>
            {product.product_image_url && (
              <ProductImage
              src={api.getImageUrl(product.product_image_url)}
                referrerPolicy="no-referrer"
                className="image-preview"
              />
            )}
          </div>
          <div>
            <table>
              <tbody>
                <tr><td style={{ fontWeight: 600, width: 100 }}>售价</td><td style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-primary)' }}>
                  {editMode ? (
                    <input type="number" step="0.01" value={editData.actual_sale_price_zar ?? ''} onChange={(e) => updateField('actual_sale_price_zar', e.target.value ? parseFloat(e.target.value) : null)} style={{ width: 180, fontSize: 16 }} />
                  ) : (
                    formatPrice(product.actual_sale_price_zar, 'ZAR')
                  )}
                </td></tr>
                <tr><td style={{ fontWeight: 600 }}>TSIN</td><td>{product.tsin || '-'}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>链接</td><td><span onClick={() => openUrl(product.takealot_url!)} style={{ color: 'var(--color-primary)', cursor: 'pointer', textDecoration: 'underline' }}>{product.takealot_url || '-'}</span></td></tr>
                <tr><td style={{ fontWeight: 600 }}>产品序号</td><td>{product.product_no}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Fee Category */}
      <div className="card">
        <div className="card-title">Fee 品类</div>
        {/* 提示放在编辑态之外：只读时「费率范围/计算比例」也是从品类表算的，
            读失败同样会显示成「-」，两种情况都要能看出是没读到而不是没有。 */}
        {feeCategoryState.error && (
          <div className="alert alert-error" role="alert" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{feeCategoryState.error}</span>
            <button type="button" className="btn btn-outline btn-sm" onClick={feeCategoryState.reload}>
              重新读取
            </button>
          </div>
        )}
        {editMode ? (
          <div className="form-group">
            <select
              value={editData.fee_category || ''}
              onChange={(e) => updateField('fee_category', e.target.value || null)}
            >
              <option value="">-- 请选择 --</option>
              {/* 当前值不在下拉里时补一条：品类表读失败、或品类后来被改名/停用时，
                  原来下拉会直接显示「-- 请选择 --」，看起来像这件货没设过品类。
                  值不变，只是让屏幕上显示的和库里存的一致。 */}
              {editData.fee_category && !feeCategories.some((fc) => fc.name === editData.fee_category) && (
                <option value={editData.fee_category}>{editData.fee_category}（当前值）</option>
              )}
              {feeCategories.map((fc) => (
                <option key={fc.id} value={fc.name}>
                  {fc.name} ({(fc.success_fee_rate * 100).toFixed(0)}%)
                </option>
              ))}
            </select>
          </div>
        ) : (
          <table>
            <tbody>
              <tr><td style={{ fontWeight: 600, width: 140 }}>品类</td><td>{product.fee_category || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>费率范围</td><td>{product.fee_rate_range || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>计算比例</td><td>{product.success_fee_rate ? `${(product.success_fee_rate * 100).toFixed(0)}%` : '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>已确认</td><td>{product.fee_category_confirmed ? '是' : '否'}</td></tr>
            </tbody>
          </table>
        )}
      </div>

      {/* Purchase */}
      <div className="card">
        <div className="card-title">采购信息</div>
        {editMode ? (
          <div className="form-row">
            <div className="form-group">
              <label>采购链接</label>
              <input type="url" value={editData.purchase_url || ''} onChange={(e) => updateField('purchase_url', e.target.value)} />
              <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2, display: 'block' }}>保存后可点击跳转</span>
            </div>
            <div className="form-group">
              <label>SKU</label>
              {/* SKU 是平台侧唯一标识，改了它采集/调价/装箱单就对不上号，所以只读 + 复制。 */}
              <div className="sku-cell">
                <span className="sku-value" translate="no">{product.sku || '-'}</span>
                <button
                  type="button" className={`sku-copy${copiedSku ? ' is-done' : ''}`}
                  aria-label={`复制SKU：${product.sku || ''}`}
                  disabled={!product.sku}
                  onClick={() => { void copySku(); }}
                >{copiedSku ? '已复制' : '复制'}</button>
              </div>
              <small style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>暂不支持修改</small>
            </div>
            <div className="form-group">
              <label>中文品名</label>
              <input
                type="text"
                maxLength={skuCount > 1 ? 200 : 30}
                value={editData.chinese_product_name || ''}
                onChange={(e) => updateField('chinese_product_name', e.target.value)}
                placeholder={skuCount > 1 ? '多个 SKU 的中文规格名请按 SKU 顺序用空格分隔' : '输入简洁中文品名'}
              />
              {skuCount > 1 && <small style={{ color: 'var(--color-text-secondary)' }}>当前识别到 {skuCount} 个 SKU，请填写相同数量的中文名称，例如：黑色 白色 蓝色。</small>}
            </div>
            <div className="form-group">
              <label>产品单价 (CNY)</label>
              <input type="number" step="0.01" value={editData.unit_price_cny ?? ''} onChange={(e) => updateField('unit_price_cny', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>采购成本 (CNY, 自动计算)</label>
                <input type="number" step="0.01" value={editData.purchase_cost_cny ?? ''} readOnly style={{ background: 'var(--surface-muted)', cursor: 'not-allowed' }} />
              </div>
              <div className="form-group">
                <label>采购数量</label>
                <input type="text" inputMode="numeric" pattern="[0-9]*" value={editData.purchase_quantity ?? 4} onChange={(e) => { const v = e.target.value.replace(/\D/g, ''); updateField('purchase_quantity', v ? parseInt(v) : 1); }} />
              </div>
            </div>
            <div className="form-group">
              <label>采购运费 (CNY)</label>
              <input type="number" step="0.01" value={editData.purchase_shipping_cny ?? ''} onChange={(e) => updateField('purchase_shipping_cny', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>包装费/个 (CNY)</label>
              <input type="number" step="0.01" value={editData.packaging_cost_per_unit_cny ?? ''} onChange={(e) => updateField('packaging_cost_per_unit_cny', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>链接状态</label>
              <select value={editData.link_status || '未购买'} onChange={(e) => updateField('link_status', e.target.value)}>
                {LINK_STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{LINK_STATUS_MAP[s].label}</option>
                ))}
              </select>
            </div>
          </div>
        ) : (
          <table>
            <tbody>
              <tr><td style={{ fontWeight: 600, width: 160 }}>采购链接</td>
                <td>
                  {product.purchase_url && product.purchase_url.startsWith('http') ? (
                    <span onClick={() => openUrl(product.purchase_url!)}
                       style={{ color: 'var(--color-primary)', textDecoration: 'underline', cursor: 'pointer' }}>
                      {product.purchase_url}
                    </span>
                  ) : (
                    <span>{product.purchase_url || '-'}</span>
                  )}
                </td>
              </tr>
              <tr><td style={{ fontWeight: 600 }}>SKU</td><td>{product.sku || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>中文品名</td><td>{product.chinese_product_name || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>产品单价</td><td>{formatPrice(product.unit_price_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购总成本</td><td>{formatPrice(product.purchase_cost_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购运费</td><td>{formatPrice(product.purchase_shipping_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购数量</td><td>{product.purchase_quantity}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>包装费/个</td><td>{formatPrice(product.packaging_cost_per_unit_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>链接状态</td>
                <td><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600, color: LINK_STATUS_MAP[product.link_status || '未购买']?.text || 'var(--text-neutral-strong)', background: LINK_STATUS_MAP[product.link_status || '未购买']?.color || 'var(--surface-muted)' }}>
                  {LINK_STATUS_MAP[product.link_status || '未购买']?.label || '未购买'}
                </span></td></tr>
            </tbody>
          </table>
        )}
      </div>

      {/* 多市场参考价 */}
      <div className="card">
        <div className="card-title">多市场参考价</div>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--color-text-secondary)' }}>
          只作定价参考，不参与利润计算。新加坡官网价由服务端每天自动重读一次（SGD，不与人民币直接比价）；
          中国官网与淘宝需要手工录入——中国官网（charleskeith.cn）已停摆，淘宝/天猫要登录态，都不做自动抓取。
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>
              <th style={{ textAlign: 'left', padding: '4px 0', fontWeight: 400 }}>市场</th>
              <th style={{ textAlign: 'left', padding: '4px 0', fontWeight: 400 }}>价格</th>
              <th style={{ textAlign: 'left', padding: '4px 0', fontWeight: 400 }}>样本 / 日期</th>
            </tr>
          </thead>
          <tbody>
            {MARKET_ORDER.map((code) => {
              const entry = mpDraft[code];
              return (
                <tr key={code}>
                  <td style={{ padding: '6px 0', borderTop: '0.5px solid var(--color-border)' }}>
                    {MARKET_META[code].label}
                    {entry?.src === '官网自动' ? <span style={{ color: 'var(--color-text-secondary)', fontSize: 11.5 }}> · 自动</span> : null}
                  </td>
                  <td style={{ padding: '6px 0', borderTop: '0.5px solid var(--color-border)' }}>
                    {editMode ? (
                      <input
                        type="number" step="0.01" min="0"
                        aria-label={`${MARKET_META[code].label}价格`}
                        value={entry?.price ?? ''}
                        onChange={(e) => setMarketPrice(code, e.target.value)}
                        style={{ width: 110 }}
                        placeholder={MARKET_META[code].cur}
                      />
                    ) : entry ? (
                      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatMarketPrice(code, entry)}</span>
                    ) : (
                      <span style={{ color: 'var(--color-text-secondary)' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '6px 0', borderTop: '0.5px solid var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 12 }}>
                    {code === 'TB' && editMode ? (
                      entry ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                          <input
                            type="number" step="1" min="0"
                            aria-label="淘宝均价样本条数"
                            value={entry?.n ?? ''}
                            onChange={(e) => setMarketSample(code, e.target.value)}
                            style={{ width: 70 }}
                          />
                          <span>条样本</span>
                        </span>
                      ) : (
                        // 没有均价就没有「均了几个样本」：setMarketSample 在没有该市场时会直接返回，
                        // 输入框留着只会让敲进去的数字被静默丢掉。
                        <span>先填价格</span>
                      )
                    ) : (
                      <>
                        {entry?.n ? `${entry.n} 条样本` : ''}
                        {entry?.at ? `${entry.n ? ' · ' : ''}${entry.at}` : ''}
                        {entry?.stale ? ' · 已过期（最近一次没读到）' : ''}
                        {!entry?.n && !entry?.at ? '—' : ''}
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {marketInsights(mpDraft).length > 0 && (
          <div style={{ marginTop: 10, paddingTop: 8, borderTop: '0.5px solid var(--color-border)', fontSize: 12.5, color: 'var(--color-text-secondary)' }}>
            {marketInsights(mpDraft).map((it) => (
              <span key={it.label} style={{ marginRight: 16 }}>
                {it.label} <span style={{ color: 'var(--color-text)', fontWeight: 500 }}>{it.value}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Dimensions & Weight */}
      <div className="card">
        <div className="card-title">尺寸、重量与计费</div>
        {editMode ? (
          <div className="form-row">
            <div className="form-group">
              <label>长 (mm)</label>
              <input type="number" step="0.1" value={editData.length_mm ?? ''} onChange={(e) => updateField('length_mm', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>宽 (mm)</label>
              <input type="number" step="0.1" value={editData.width_mm ?? ''} onChange={(e) => updateField('width_mm', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>高 (mm)</label>
              <input type="number" step="0.1" value={editData.height_mm ?? ''} onChange={(e) => updateField('height_mm', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>实际重量 (kg)</label>
              <input type="number" step="0.01" value={editData.actual_weight_kg ?? ''} onChange={(e) => updateField('actual_weight_kg', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>运输方式</label>
              <select value={editData.shipping_method || ''} onChange={(e) => updateField('shipping_method', e.target.value || null)}>
                <option value="">-- 请选择 --</option>
                {SHIPPING_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <div className="section-title">产品尺寸</div>
              <table>
                <tbody>
                  <tr><td style={{ fontWeight: 600, width: 180 }}>尺寸 (mm)</td><td>{product.length_mm ?? '-'} x {product.width_mm ?? '-'} x {product.height_mm ?? '-'}</td></tr>
                  <tr><td style={{ fontWeight: 600 }}>体积 (CBM)</td><td>{product.volume_cbm != null ? product.volume_cbm.toFixed(6) : '-'}</td></tr>
                  <tr><td style={{ fontWeight: 600 }}>实际重量 (kg)</td><td>{product.actual_weight_kg != null ? `${product.actual_weight_kg} kg` : '-'}</td></tr>
                </tbody>
              </table>
            </div>
            <div>
              <div className="section-title">计费重量计算 <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text-subtle)' }}>体积重 = 长(cm) × 宽(cm) × 高(cm) ÷ 6000</span></div>
              <table>
                <tbody>
                  <tr><td style={{ fontWeight: 600, width: 180 }}>体积重 (kg)</td><td>{product.volumetric_weight_kg != null ? `${product.volumetric_weight_kg.toFixed(3)} kg` : '-'}</td></tr>
                  <tr style={{ background: product.volumetric_weight_kg != null && product.actual_weight_kg != null && product.volumetric_weight_kg > product.actual_weight_kg ? 'var(--surface-warning)' : undefined }}>
                    <td style={{ fontWeight: 700 }}>实际计费重量 (kg)</td>
                    <td style={{ fontWeight: 700 }}>
                      {product.chargeable_weight_kg != null ? `${product.chargeable_weight_kg.toFixed(3)} kg` : '-'}
                      {product.volumetric_weight_kg != null && product.actual_weight_kg != null && (
                        <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--text-muted)' }}>
                          (取{product.volumetric_weight_kg > product.actual_weight_kg ? '体积重' : '实际重'}较大者)
                        </span>
                      )}
                    </td>
                  </tr>
                  <tr><td style={{ fontWeight: 600 }}>运输方式</td><td>{product.shipping_method || '-'}</td></tr>
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Calculated Results */}
      <div className="card">
        <div className="card-title">成本明细</div>
        <div className="two-col">
          <div>
            <div className="section-title">国内段 (CNY)</div>
            <table>
              <tbody>
                <tr><td style={{ fontWeight: 600, width: 180 }}>产品单价</td><td>{formatPrice(product.unit_price_cny, 'CNY')}</td></tr>
                <tr><td style={{ fontWeight: 600, width: 180 }}>采购总成本</td><td>{formatPrice(product.purchase_cost_cny, 'CNY')}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>采购运费</td><td>{formatPrice(product.purchase_shipping_cny, 'CNY')}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>采购数量</td><td>{product.purchase_quantity}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>包装费/个</td><td>{formatPrice(product.packaging_cost_per_unit_cny, 'CNY')}</td></tr>
                <tr>
                  <td style={{ fontWeight: 600 }}>前置仓快递/个</td>
                  <td>
                    {formatPrice(product.domestic_forwarding_cost_per_unit_cny, 'CNY')}
                    {editMode && (
                      <input
                        type="number"
                        step="0.01"
                        placeholder={`${product.domestic_forwarding_cost_per_unit_cny ?? ''}`}
                        value={editData.manual_domestic_forwarding_cny ?? ''}
                        onChange={(e) => updateField('manual_domestic_forwarding_cny', e.target.value ? parseFloat(e.target.value) : null)}
                        style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                      />
                    )}
                    {!editMode && product.manual_domestic_forwarding_cny != null && (
                      <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                        (手动: {formatPrice(product.manual_domestic_forwarding_cny, 'CNY')})
                      </span>
                    )}
                  </td>
                </tr>
                <tr style={{ background: 'var(--surface-muted)' }}><td style={{ fontWeight: 700 }}>综合单个成本 (AD)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.unit_product_cost_cny, 'CNY')}</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <div className="section-title">国际头程 &amp; 海外仓</div>
            <table>
              <tbody>
                <tr><td style={{ fontWeight: 600, width: 200 }}>运输方式</td><td>{product.shipping_method || '-'}</td></tr>
                <tr>
                  <td style={{ fontWeight: 600 }}>国际头程运费/个 (CNY)</td>
                  <td>
                    {formatPrice(product.international_shipping_per_unit_cny, 'CNY')}
                    {editMode && (
                      <input
                        type="number"
                        step="0.01"
                        placeholder={`${product.international_shipping_per_unit_cny ?? ''}`}
                        value={editData.manual_international_shipping_cny ?? ''}
                        onChange={(e) => updateField('manual_international_shipping_cny', e.target.value ? parseFloat(e.target.value) : null)}
                        style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                      />
                    )}
                    {!editMode && product.manual_international_shipping_cny != null && (
                      <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                        (手动: {formatPrice(product.manual_international_shipping_cny, 'CNY')})
                      </span>
                    )}
                  </td>
                </tr>
                <tr style={{ background: 'var(--surface-muted)' }}><td style={{ fontWeight: 700 }}>到南非仓总成本 (CNY)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.cost_to_sa_warehouse_cny, 'CNY')}</td></tr>
                <tr style={{ background: 'var(--surface-muted)' }}><td style={{ fontWeight: 700 }}>到南非仓总成本 (ZAR)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.cost_to_sa_warehouse_zar, 'ZAR')}</td></tr>
                <tr><td colSpan={2} style={{ paddingTop: 8 }}></td></tr>
                <tr><td style={{ fontWeight: 600 }}>入库上架费 (CNY)</td>
                  <td>
                    {editMode ? (
                      <input type="number" step="0.01" value={editData.inbound_listing_fee_cny ?? ''} onChange={(e) => updateField('inbound_listing_fee_cny', e.target.value ? parseFloat(e.target.value) : null)} style={{ width: 120 }} />
                    ) : formatPrice(product.inbound_listing_fee_cny, 'CNY')}
                  </td>
                </tr>
                <tr><td style={{ fontWeight: 600 }}>出库操作费 (CNY)</td>
                  <td>
                    {editMode ? (
                      <input type="number" step="0.01" value={editData.outbound_operation_fee_cny ?? ''} onChange={(e) => updateField('outbound_operation_fee_cny', e.target.value ? parseFloat(e.target.value) : null)} style={{ width: 120 }} />
                    ) : formatPrice(product.outbound_operation_fee_cny, 'CNY')}
                  </td>
                </tr>
                <tr><td style={{ fontWeight: 600 }}>尾程派送费 (CNY)</td>
                  <td>
                    {editMode ? (
                      <input type="number" step="0.01" value={editData.last_mile_delivery_fee_cny ?? ''} onChange={(e) => updateField('last_mile_delivery_fee_cny', e.target.value ? parseFloat(e.target.value) : null)} style={{ width: 120 }} />
                    ) : formatPrice(product.last_mile_delivery_fee_cny, 'CNY')}
                  </td>
                </tr>
                <tr><td style={{ fontWeight: 600 }}>其他费用 (CNY)</td>
                  <td>
                    {editMode ? (
                      <input type="number" step="0.01" value={editData.other_fee_cny ?? ''} onChange={(e) => updateField('other_fee_cny', e.target.value ? parseFloat(e.target.value) : null)} style={{ width: 120 }} />
                    ) : formatPrice(product.other_fee_cny, 'CNY')}
                  </td>
                </tr>
                <tr style={{ background: 'var(--surface-muted)' }}><td style={{ fontWeight: 700 }}>海外仓操作总成本 (CNY)</td>
                  <td style={{ fontWeight: 700 }}>
                    {formatPrice(product.overseas_warehouse_operation_cost_cny, 'CNY')}
                    {editMode && (
                      <input
                        type="number"
                        step="0.01"
                        placeholder={`${product.overseas_warehouse_operation_cost_cny ?? ''}`}
                        value={editData.manual_overseas_op_cost_cny ?? ''}
                        onChange={(e) => updateField('manual_overseas_op_cost_cny', e.target.value ? parseFloat(e.target.value) : null)}
                        style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                      />
                    )}
                    {!editMode && product.manual_overseas_op_cost_cny != null && (
                      <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                        (手动: {formatPrice(product.manual_overseas_op_cost_cny, 'CNY')})
                      </span>
                    )}
                  </td>
                </tr>
                <tr style={{ background: 'var(--surface-muted)' }}><td style={{ fontWeight: 700 }}>海外仓操作总成本 (ZAR)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.overseas_warehouse_operation_cost_zar, 'ZAR')}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Platform Fees & Profit */}
      <div className="card">
        <div className="card-title">Takealot 费用与利润</div>
        <table>
          <tbody>
            <tr>
              <td style={{ fontWeight: 600, width: 220 }}>Success Fee (ZAR)</td>
              <td>
                {formatPrice(product.success_fee_zar, 'ZAR')}
                {editMode && (
                  <input
                    type="number"
                    step="0.01"
                    placeholder={`${product.success_fee_zar ?? ''}`}
                    value={editData.manual_success_fee_zar ?? ''}
                    onChange={(e) => updateField('manual_success_fee_zar', e.target.value ? parseFloat(e.target.value) : null)}
                    style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                  />
                )}
                {!editMode && product.manual_success_fee_zar != null && (
                  <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                    (手动: {formatPrice(product.manual_success_fee_zar, 'ZAR')})
                  </span>
                )}
              </td>
            </tr>
            <tr><td style={{ fontWeight: 600 }}>Fulfillment Fee (ZAR)</td>
              <td>
                {formatPrice(product.fulfillment_fee_zar, 'ZAR')}
                {editMode && (
                  <input
                    type="number"
                    step="0.01"
                    placeholder={`${product.fulfillment_fee_zar}`}
                    value={editData.manual_fulfillment_fee_zar ?? ''}
                    onChange={(e) => updateField('manual_fulfillment_fee_zar', e.target.value ? parseFloat(e.target.value) : null)}
                    style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                  />
                )}
                {!editMode && product.manual_fulfillment_fee_zar != null && (
                  <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                    (手动: {formatPrice(product.manual_fulfillment_fee_zar, 'ZAR')})
                  </span>
                )}
              </td>
            </tr>
            <tr><td style={{ fontWeight: 600 }}>官方总成本 (ZAR)</td><td>{formatPrice(product.official_total_cost_zar, 'ZAR')}</td></tr>
            <tr style={{ background: 'var(--surface-muted)' }}>
              <td style={{ fontWeight: 700 }}>总成本 (ZAR)</td>
              <td style={{ fontWeight: 700 }}>
                {formatPrice(product.total_cost_zar, 'ZAR')}
                {editMode && (
                  <input
                    type="number"
                    step="0.01"
                    placeholder={`${product.total_cost_zar ?? ''}`}
                    value={editData.manual_total_cost_zar ?? ''}
                    onChange={(e) => updateField('manual_total_cost_zar', e.target.value ? parseFloat(e.target.value) : null)}
                    style={{ marginLeft: 8, width: 120, fontSize: 13 }}
                  />
                )}
                {!editMode && product.manual_total_cost_zar != null && (
                  <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-primary)' }}>
                    (手动: {formatPrice(product.manual_total_cost_zar, 'ZAR')})
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>利润 (ZAR)</td>
              <td style={{ fontWeight: 700, fontSize: 18, color: (product.profit_zar ?? 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                {formatPrice(product.profit_zar, 'ZAR')}
              </td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>利润率</td>
              {/* 缺数据不能当成 0%：?? 0 会把"没算出来"渲染成红色低毛利 */}
              <td style={{ fontWeight: 700, fontSize: 22, color: product.profit_margin == null ? 'var(--color-text-secondary)' : (toNumber(product.profit_margin) ?? 0) >= 0.25 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                {formatPercent(product.profit_margin)}
              </td>
            </tr>
            <tr>
              <td style={{ fontWeight: 600 }}>20% 利润率最低售价</td>
              <td>{formatPrice(product.minimum_price_at_20_margin, 'ZAR')}</td>
            </tr>
            <tr>
              <td style={{ fontWeight: 600 }}>15% 利润率最低售价</td>
              <td>{formatPrice(product.minimum_price_at_15_margin, 'ZAR')}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Audit Info */}
      <div className="card">
        <div className="card-title">审计信息</div>
        <table>
          <tbody>
            <tr><td style={{ fontWeight: 600, width: 140 }}>使用汇率</td><td>{product.exchange_rate_used}</td></tr>
            <tr><td style={{ fontWeight: 600 }}>创建时间</td><td>{product.created_at ? new Date(product.created_at).toLocaleString('zh-CN') : '-'}</td></tr>
            <tr><td style={{ fontWeight: 600 }}>更新时间</td><td>{product.updated_at ? new Date(product.updated_at).toLocaleString('zh-CN') : '-'}</td></tr>
            <tr><td style={{ fontWeight: 600 }}>备注</td><td>
              {editMode ? (
                <textarea value={editData.note || ''} onChange={(e) => updateField('note', e.target.value)} rows={3} style={{ width: '100%', maxWidth: 400 }} />
              ) : (
                product.note || '-'
              )}
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
