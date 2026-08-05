import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import type { ScrapeResult, FeeCategory, Product } from '../types';
import { SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';

export default function ProductCreate() {
  const navigate = useNavigate();
  const api = useApi();
  const [url, setUrl] = useState('');
  const [scraping, setScraping] = useState(false);
  const [scrapeResult, setScrapeResult] = useState<ScrapeResult | null>(null);
  const [error, setError] = useState('');
  const [feeCategories, setFeeCategories] = useState<FeeCategory[]>([]);

  // Form fields
  const [feeCategory, setFeeCategory] = useState('');
  const [feeConfirmed, setFeeConfirmed] = useState(false);
  const [unitPrice, setUnitPrice] = useState('');
  const [purchaseShipping, setPurchaseShipping] = useState('');
  const [purchaseQty, setPurchaseQty] = useState('4');
  const [purchaseUrl, setPurchaseUrl] = useState('');
  const [sku, setSku] = useState('');
  const [chineseName, setChineseName] = useState('');
  const [length, setLength] = useState('');
  const [width, setWidth] = useState('');
  const [height, setHeight] = useState('');
  const [weight, setWeight] = useState('');
  const [packagingCost, setPackagingCost] = useState('');
  const [shippingMethod, setShippingMethod] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [linkStatus, setLinkStatus] = useState('未购买');

  useEffect(() => {
    api.getFeeCategories().then(setFeeCategories).catch(() => {});
  }, []);

  const handleScrape = async () => {
    if (!url.trim()) return;
    setScraping(true);
    setError('');
    try {
      const result: ScrapeResult = await api.scrapeTakealot(url.trim());
      setScrapeResult(result);
      if (result.recommended_fee_category) {
        setFeeCategory(result.recommended_fee_category);
      }
      // 自动翻译中文品名
      if (result.product_name && !chineseName) {
        try {
          const translated = await api.translateProductName(result.product_name);
          if (translated.chinese_name) {
            setChineseName(translated.chinese_name);
          }
        } catch {
          // 翻译失败静默处理，用户可手动填写
        }
      }
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '操作失败');
    } finally {
      setScraping(false);
    }
  };

  const handleConfirmFee = () => {
    setFeeConfirmed(true);
  };

  const handleSave = async () => {
    const selectedFee = feeCategories.find((f) => f.name === feeCategory);
    setSaving(true);
    try {
      const product: Product = await api.createProduct({
        takealot_url: scrapeResult?.normalized_url || url.trim(),
        tsin: scrapeResult?.tsin || '',
        product_name: scrapeResult?.product_name || '',
        product_image_url: scrapeResult?.product_image_url || '',
        actual_sale_price_zar: scrapeResult?.actual_sale_price_zar || null,
        fee_category: feeCategory || null,
        fee_category_confirmed: feeConfirmed,
        note: note,
        purchase_url: purchaseUrl,
        sku: sku,
        chinese_product_name: chineseName,
        purchase_cost_cny: unitPrice ? parseFloat(unitPrice) * (parseInt(purchaseQty) || 4) : null,
        unit_price_cny: unitPrice ? parseFloat(unitPrice) : null,
        purchase_shipping_cny: purchaseShipping ? parseFloat(purchaseShipping) : null,
        purchase_quantity: parseInt(purchaseQty) || 4,
        length_mm: length ? parseFloat(length) : null,
        width_mm: width ? parseFloat(width) : null,
        height_mm: height ? parseFloat(height) : null,
        actual_weight_kg: weight ? parseFloat(weight) : null,
        packaging_cost_per_unit_cny: packagingCost ? parseFloat(packagingCost) : null,
        shipping_method: shippingMethod || null,
        link_status: linkStatus,
      });
      navigate(`/products/${product.id}`);
    } catch (e: any) {
      setError(typeof e === 'string' ? e : e.message || '操作失败');
    } finally {
      setSaving(false);
    }
  };

  const selectedFee = feeCategories.find((f) => f.name === feeCategory);

  return (
    <div>
      <h2 style={{ marginBottom: 20 }}>新建产品</h2>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Takealot URL Input */}
      <div className="card">
        <div className="card-title">Step 1: 输入 Takealot 链接</div>
        <div style={{ display: 'flex', gap: 12 }}>
          <input
            type="url"
            placeholder="https://www.takealot.com/..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            style={{ flex: 1 }}
            onKeyDown={(e) => e.key === 'Enter' && handleScrape()}
          />
          <button className="btn btn-primary" onClick={handleScrape} disabled={scraping || !url.trim()}>
            {scraping ? '抓取中...' : '抓取产品信息'}
          </button>
        </div>
      </div>

      {/* Scrape Result */}
      {scrapeResult && (
        <div className="card">
          <div className="card-title">Step 2: 抓取结果</div>
          {scrapeResult.warnings.length > 0 && (
            <div className="alert alert-warning">
              {scrapeResult.warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}

          <div className="two-col">
            <div>
              {scrapeResult.product_image_url && (
                <img src={scrapeResult.product_image_url} alt="" className="image-preview" />
              )}
            </div>
            <div>
              <div className="form-group">
                <label>商品标题</label>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{scrapeResult.product_name || '(未获取到)'}</div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>售价 (ZAR)</label>
                  <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-primary)' }}>
                    {scrapeResult.actual_sale_price_zar ? `R ${scrapeResult.actual_sale_price_zar.toFixed(2)}` : '-'}
                  </div>
                </div>
                <div className="form-group">
                  <label>TSIN</label>
                  <div>{scrapeResult.tsin || '(未获取到)'}</div>
                </div>
              </div>
              <div className="form-group">
                <label>Takealot 分类路径</label>
                <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
                  {scrapeResult.takealot_category_path || '(未获取到)'}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Fee Category */}
      {scrapeResult && (
        <div className="card">
          <div className="card-title">Step 3: 确认 Fee 品类</div>
          <div className="alert alert-info">
            系统推荐: <strong>{scrapeResult.recommended_fee_category || '无推荐'}</strong>
            {scrapeResult.fee_match_reason && ` (${scrapeResult.fee_match_reason})`}
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Fee 品类</label>
              <select value={feeCategory} onChange={(e) => { setFeeCategory(e.target.value); setFeeConfirmed(false); }}>
                <option value="">-- 请选择 --</option>
                {feeCategories.map((fc) => (
                  <option key={fc.id} value={fc.name}>
                    {fc.name} ({fc.fee_rate_range}, 计算比例: {(fc.success_fee_rate * 100).toFixed(0)}%)
                  </option>
                ))}
              </select>
              {selectedFee && (
                <div style={{ marginTop: 4, fontSize: 13, color: 'var(--color-text-secondary)' }}>
                  费率: {selectedFee.fee_rate_range} | 计算比例: {(selectedFee.success_fee_rate * 100).toFixed(0)}%
                </div>
              )}
            </div>
            <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button
                className={`btn ${feeConfirmed ? 'btn-success' : 'btn-primary'}`}
                onClick={handleConfirmFee}
                disabled={!feeCategory}
              >
                {feeConfirmed ? '已确认' : '确认品类'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Purchase & Dimensions Form */}
      {scrapeResult && (
        <div className="card">
          <div className="card-title">Step 4: 填写采购、尺寸和物流信息</div>

          <div className="section">
            <div className="section-title">采购信息</div>
            <div className="form-row">
              <div className="form-group">
                <label>1688/采购链接</label>
                <input type="url" value={purchaseUrl} onChange={(e) => setPurchaseUrl(e.target.value)} placeholder="https://..." />
              </div>
              <div className="form-group">
                <label>SKU</label>
                <input value={sku} onChange={(e) => setSku(e.target.value)} />
              </div>
              <div className="form-group">
                <label>中文品名</label>
                <input value={chineseName} onChange={(e) => setChineseName(e.target.value)} />
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>产品单价 (CNY)</label>
                <input type="number" step="0.01" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
              </div>
              <div className="form-group">
                <label>采购运费 (CNY)</label>
                <input type="number" step="0.01" value={purchaseShipping} onChange={(e) => setPurchaseShipping(e.target.value)} />
              </div>
              <div className="form-group">
                <label>采购数量</label>
                <input type="number" min="1" value={purchaseQty} onChange={(e) => setPurchaseQty(e.target.value)} />
              </div>
              <div className="form-group">
                <label>包装费用/个 (CNY)</label>
                <input type="number" step="0.01" value={packagingCost} onChange={(e) => setPackagingCost(e.target.value)} />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">尺寸与重量</div>
            <div className="form-row">
              <div className="form-group">
                <label>长 (mm)</label>
                <input type="number" step="0.1" value={length} onChange={(e) => setLength(e.target.value)} />
              </div>
              <div className="form-group">
                <label>宽 (mm)</label>
                <input type="number" step="0.1" value={width} onChange={(e) => setWidth(e.target.value)} />
              </div>
              <div className="form-group">
                <label>高 (mm)</label>
                <input type="number" step="0.1" value={height} onChange={(e) => setHeight(e.target.value)} />
              </div>
              <div className="form-group">
                <label>实际重量 (kg)</label>
                <input type="number" step="0.01" value={weight} onChange={(e) => setWeight(e.target.value)} />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">物流</div>
            <div className="form-row">
              <div className="form-group">
                <label>运输方式</label>
                <select value={shippingMethod} onChange={(e) => setShippingMethod(e.target.value)}>
                  <option value="">-- 请选择 --</option>
                  {SHIPPING_METHODS.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>链接状态</label>
                <select value={linkStatus} onChange={(e) => setLinkStatus(e.target.value)}>
                  {LINK_STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>{LINK_STATUS_MAP[s].label}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="form-group">
            <label>备注</label>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </div>

          <button className="btn btn-success btn-lg" onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存产品'}
          </button>
        </div>
      )}
    </div>
  );
}
