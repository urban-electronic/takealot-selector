import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../DataSourceContext';
import type { ScrapeResult, Product } from '../types';
import { SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';
import { importSavedTakealotPage, makeTakealotBookmarklet, parseTakealotPageImport } from '../takealotPageImport';
import { ProductImage } from '../utils/imageFallback';
import { useFeeCategories } from '../utils/useFeeCategories';

export default function ProductCreate() {
  const navigate = useNavigate();
  const api = useApi();
  const [url, setUrl] = useState('');
  const [scraping, setScraping] = useState(false);
  const [scrapeResult, setScrapeResult] = useState<ScrapeResult | null>(null);
  const [error, setError] = useState('');
  const feeCategoryState = useFeeCategories(api);
  const feeCategories = feeCategoryState.categories;
  const [productName, setProductName] = useState('');
  const [productImage, setProductImage] = useState('');
  const [salePrice, setSalePrice] = useState('');
  const importerLink = useRef<HTMLAnchorElement>(null);
  const identityVersion = useRef(0);
  useEffect(() => () => { identityVersion.current += 1; }, []);
  const [showImporter, setShowImporter] = useState(false);
  const [importingFile, setImportingFile] = useState(false);

  const importPageFile = async (file: File) => {
    setError(''); setImportingFile(true);
    try {
      if (!/\.html?$/i.test(file.name)) throw new Error('请选择浏览器保存的 .html 或 .htm 网页文件。');
      if (file.size > 15 * 1024 * 1024) throw new Error('网页文件过大，请保存为“网页，仅 HTML”。');
      // DOMParser reads an inert document; never mount its HTML or execute its scripts.
      const result = importSavedTakealotPage(new DOMParser().parseFromString(await file.text(), 'text/html'), url);
      applyImportedResult(result);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : '网页文件导入失败'); }
    finally { setImportingFile(false); }
  };

  const startManual = () => {
    try {
      const parsed = new URL(url.trim());
      if (!['http:', 'https:'].includes(parsed.protocol) || !['takealot.com', 'www.takealot.com'].includes(parsed.hostname) || parsed.username || parsed.password) throw new Error();
      identityVersion.current += 1;
      setScrapeResult({ normalized_url: `${parsed.origin}${parsed.pathname}`, tsin: parsed.pathname.match(/PLID\d+/i)?.[0].toUpperCase() || null,
        product_name: null, product_image_url: null, actual_sale_price_zar: null, in_stock_price: null,
        takealot_category_path: null, warnings: ['手动录入：请填写标题和售价，选择 Fee 品类。'], success: false, data_source: 'manual' });
      setError('');
    } catch {
      setError('请提供有效的 takealot.com 链接');
    }
  };

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
  const [lastCostProduct, setLastCostProduct] = useState<Product | null>(null);
  const [costCopyNotice, setCostCopyNotice] = useState('');
  // 两处「辅助能力」的读失败以前都是 `.catch(() => {})`：按钮直接不出现、
  // 「系统推荐」直接写「无推荐」，用户分不清「本来就没有」和「这次没读到」。
  const [costSourceError, setCostSourceError] = useState('');
  const [feeMatchError, setFeeMatchError] = useState('');

  useEffect(() => {
    let active = true;
    api.getProducts({ sort_by: 'created_at', sort_order: 'desc' })
      .then((products: Product[]) => {
        const source = products.find((product) =>
          !product.is_archived && (
            product.unit_price_cny != null || product.purchase_cost_cny != null ||
            product.purchase_shipping_cny != null || product.packaging_cost_per_unit_cny != null ||
            product.length_mm != null || product.width_mm != null || product.height_mm != null ||
            product.actual_weight_kg != null || product.shipping_method
          ),
        );
        if (active) setLastCostProduct(source || null);
      })
      // 「沿用上一件成本」靠这条列表找源商品。读失败时按钮会整块消失，
      // 用户只看到少了那个按钮，不知道是自己没建过商品还是这次没读到。
      .catch((e: unknown) => { if (active) setCostSourceError(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (importerLink.current) importerLink.current.href = makeTakealotBookmarklet('https://urban-electronic.github.io/takealot-selector/create');
  }, [showImporter]);

  const applyImportedResult = (result: ScrapeResult) => {
      const version = ++identityVersion.current;
      setScraping(false);
      setUrl(result.normalized_url); setScrapeResult(result);
      setError(''); setSku(''); setFeeCategory(''); setFeeConfirmed(false); setChineseName(''); setFeeMatchError('');
      setProductName(result.product_name || ''); setProductImage(result.product_image_url || '');
      setSalePrice(result.actual_sale_price_zar !== null ? String(result.actual_sale_price_zar) : '');
      api.getFeeMappingRules().then(rules => {
        if (version !== identityVersion.current) return;
        const match = rules.filter(rule => rule.active && rule.takealot_category_pattern && result.takealot_category_path?.toLowerCase().includes(rule.takealot_category_pattern.toLowerCase())).sort((a, b) => b.priority - a.priority)[0];
        if (match) { setFeeCategory(match.fee_category); setScrapeResult(prev => prev ? { ...prev, recommended_fee_category: match.fee_category, fee_category_confidence: 'high', fee_match_reason: `官网页面分类匹配：${match.takealot_category_pattern}` } : prev); }
      })
        // 映射规则读不到时「系统推荐」会写「无推荐」，和「规则里确实没有匹配项」长得一样。
        // 页面导入本来就是这个流程的卖点，读失败必须说明，否则用户会以为分类路径没匹配上。
        .catch((e: unknown) => { if (version === identityVersion.current) setFeeMatchError(e instanceof Error ? e.message : String(e)); });
      if (result.product_name) api.translateProductName(result.product_name).then(res => { if (version === identityVersion.current && res.chinese_name) setChineseName(prev => prev || res.chinese_name); }).catch(() => {});
  };

  useEffect(() => {
    if (!window.location.hash.startsWith('#takealot-import=')) return;
    try {
      applyImportedResult(parseTakealotPageImport(decodeURIComponent(window.location.hash.slice('#takealot-import='.length))));
    } catch (e: unknown) { setError(e instanceof Error ? e.message : '页面导入失败'); }
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }, [api]);

  const handleScrape = async () => {
    if (!url.trim()) return;
    const version = ++identityVersion.current;
    setScraping(true);
    setError('');
    setScrapeResult(null);
    setProductName('');
    setProductImage('');
    setSalePrice('');
    setSku('');
    setFeeCategory('');
    setFeeConfirmed(false);
    try {
      const result: ScrapeResult = await api.scrapeTakealot(url.trim());
      if (version !== identityVersion.current) return;
      if (!result.success) setShowImporter(true);
      setScrapeResult(result);
      setProductName(result.product_name || '');
      setProductImage(result.product_image_url || '');
      setSalePrice(result.actual_sale_price_zar != null ? String(result.actual_sale_price_zar) : '');
      if (result.variants?.length) setSku(result.variants.map(item => item.sku).join('\n'));
      if (result.recommended_fee_category) {
        setFeeCategory(result.recommended_fee_category);
      }
      // 自动翻译中文品名
      if (result.product_name && !chineseName) {
        try {
          const translated = await api.translateProductName(result.product_name);
          if (version === identityVersion.current && translated.chinese_name) {
            setChineseName(prev => prev || translated.chinese_name);
          }
        } catch {
          // 翻译失败静默处理，用户可手动填写
        }
      }
    } catch (e: any) {
      if (version === identityVersion.current) setError(typeof e === 'string' ? e : e.message || '操作失败');
    } finally {
      if (version === identityVersion.current) setScraping(false);
    }
  };

  const handleConfirmFee = () => {
    setFeeConfirmed(true);
  };

  const applyLastProductCosts = () => {
    if (!lastCostProduct) return;
    const source = lastCostProduct;
    const sourceUnitPrice = source.unit_price_cny ?? (
      source.purchase_cost_cny != null && source.purchase_quantity > 0
        ? source.purchase_cost_cny / source.purchase_quantity
        : null
    );
    let copied = 0;
    const copyIfEmpty = (current: string, value: number | null | undefined, setter: (next: string) => void) => {
      if (!current.trim() && value != null) {
        setter(String(value));
        copied += 1;
      }
    };

    copyIfEmpty(unitPrice, sourceUnitPrice, setUnitPrice);
    copyIfEmpty(purchaseShipping, source.purchase_shipping_cny, setPurchaseShipping);
    copyIfEmpty(packagingCost, source.packaging_cost_per_unit_cny, setPackagingCost);
    copyIfEmpty(length, source.length_mm, setLength);
    copyIfEmpty(width, source.width_mm, setWidth);
    copyIfEmpty(height, source.height_mm, setHeight);
    copyIfEmpty(weight, source.actual_weight_kg, setWeight);
    if (!shippingMethod && source.shipping_method) {
      setShippingMethod(source.shipping_method);
      copied += 1;
    }

    const sourceName = source.chinese_product_name || source.product_name || `商品 #${source.product_no ?? ''}`;
    setCostCopyNotice(copied
      ? `已从“${sourceName}”填入 ${copied} 项空白成本；已有内容未覆盖。请核对采购价、尺寸和重量。`
      : '本页成本项已有内容，未覆盖任何字段。');
  };

  const handleSave = async () => {
    if (!productName.trim()) { setError('请填写商品标题'); return; }
    if (salePrice && (!Number.isFinite(Number(salePrice)) || Number(salePrice) <= 0)) { setError('售价必须大于 0'); return; }
    setSaving(true);
    try {
      const product: Product = await api.createProduct({
        takealot_url: scrapeResult?.normalized_url || url.trim(),
        tsin: scrapeResult?.tsin || '',
        product_name: productName.trim(),
        product_image_url: productImage.trim(),
        actual_sale_price_zar: salePrice ? Number(salePrice) : null,
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
        packaging_cost_per_unit_cny: packagingCost ? parseFloat(packagingCost) : 1.0,
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
    <div className="product-create-page">
      <div className="create-page-heading"><div><span className="create-eyebrow">选品工作台</span><h2>新建产品</h2><p>粘贴 Takealot 链接，先识别商品，再核对采购与物流成本。</p></div><span className="create-flow-chip">识别商品 <b>›</b> 核对成本 <b>›</b> 计算利润</span></div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Takealot URL Input */}
      <div className="card">
        <div className="card-title create-step-title"><span>01</span><div><strong>识别 Takealot 商品</strong><small>粘贴商品链接，自动读取可用资料</small></div></div>
        <div style={{ display: 'flex', gap: 12 }}>
          <input
            type="url"
            disabled={scraping}
            placeholder="https://www.takealot.com/..."
            value={url}
            onChange={(e) => { identityVersion.current += 1; setUrl(e.target.value); setScrapeResult(null); setProductName(''); setProductImage(''); setSalePrice(''); setSku(''); setChineseName(''); setFeeMatchError(''); setFeeCategory(''); setFeeConfirmed(false); }}
            style={{ flex: 1 }}
            onKeyDown={(e) => e.key === 'Enter' && handleScrape()}
          />
          <button className="btn btn-primary" onClick={handleScrape} disabled={scraping || !url.trim()}>
            {scraping ? '抓取中...' : '抓取产品信息'}
          </button>
        </div>
        <button className="btn" style={{ marginTop: 12 }} onClick={startManual} disabled={scraping || !url.trim()}>直接手动填写</button>
        <button className="btn btn-outline" style={{ marginTop: 12, marginLeft: 12 }} onClick={() => setShowImporter(prev => !prev)}>从已打开的官网页面导入</button>
        {showImporter && <div className="alert alert-info" style={{ marginTop: 12 }}>
          <p><strong>推荐：导入保存的网页，无需书签</strong></p>
          <p>在官网等商品标题、价格显示后，按 Ctrl+S，保存类型选“网页，仅 HTML”或“网页，全部”。回到这里选择保存的 .html 文件，不需要上传配套文件夹。</p>
          <label>选择官网商品网页 <input type="file" accept=".html,.htm,text/html" disabled={importingFile} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void importPageFile(file); }} /></label>
          {importingFile && <p>正在读取网页…</p>}
          <p>程序自动填写能核实的商品信息，未读取到的字段会提示补充。请核对后保存。</p>
          <details><summary>其他方式：书签导入</summary>
          <p>把下面的“导入 Takealot 商品”拖到浏览器书签栏。</p>
          <a ref={importerLink} className="btn btn-primary" draggable onClick={e => { e.preventDefault(); setError('请先将此按钮拖到书签栏，再到官网商品详情页点击该书签。'); }}>导入 Takealot 商品</a>
          <p>打开官网商品页，等标题、图片和价格显示后，点击该书签。程序会自动打开并填入商品信息，核对后再保存。</p>
          <small>仅读取当前页的商品标题、图片、售价和分类；不读取账号或登录信息。</small>
          </details>
        </div>}
      </div>

      {/* Scrape Result */}
      {scrapeResult && (
        <div className="card">
          <div className="card-title create-step-title"><span>02</span><div><strong>核对商品资料</strong><small>{scrapeResult.data_source === 'seller_api' ? '来源：本店 Seller API' : scrapeResult.data_source === 'offer_catalog' ? '来源：卖家资料表' : scrapeResult.data_source === 'browser_page' ? '来源：已打开的官网页面' : '请核对识别结果'}</small></div></div>
          {scrapeResult.warnings.length > 0 && (
            <div className="alert alert-warning">
              {scrapeResult.warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}

          <div className="two-col">
            <div>
              {productImage && (
                <ProductImage src={api.getImageUrl(productImage)} className="image-preview" />
              )}
              <div className="form-group"><label>商品图片链接</label><input type="url" value={productImage} onChange={(e) => setProductImage(e.target.value)} placeholder="未获取时可粘贴图片链接" /></div>
            </div>
            <div>
              <div className="form-group">
                <label>商品标题</label>
                <input value={productName} onChange={(e) => setProductName(e.target.value)} placeholder="请填写或核对商品标题" />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>售价 (ZAR)</label>
                  <input type="number" min="0.01" step="0.01" value={salePrice} onChange={(e) => setSalePrice(e.target.value)} placeholder="当前售价未获取，请补填" />
                </div>
                <div className="form-group">
                  <label>TSIN / PLID</label>
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
          <div className="card-title create-step-title"><span>03</span><div><strong>确认平台费率</strong><small>确认 Fee 品类后计算利润</small></div></div>
          <div className="alert alert-info">
            系统推荐: <strong>{scrapeResult.recommended_fee_category || '无推荐'}</strong>
            {scrapeResult.fee_match_reason && ` (${scrapeResult.fee_match_reason})`}
          </div>
          {feeMatchError && (
            <div className="alert alert-warning" role="status">
              Fee 映射规则读取失败（{feeMatchError}），这次没有自动匹配品类——「无推荐」不代表分类路径不匹配，请手动选择。
            </div>
          )}
          {feeCategoryState.error && (
            <div className="alert alert-error" role="alert" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <span>{feeCategoryState.error}</span>
              <button type="button" className="btn btn-outline btn-sm" onClick={feeCategoryState.reload}>
                重新读取
              </button>
            </div>
          )}
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
          <div className="card-title create-step-title"><span>04</span><div><strong>填写采购与物流</strong><small>补充成本、尺寸和运输方式</small></div>
            {lastCostProduct && <button type="button" className="btn btn-outline btn-sm cost-copy-button" onClick={applyLastProductCosts}>沿用上一件成本</button>}
          </div>
          {costSourceError && (
            <p className="cost-copy-notice" role="status">
              已有商品列表读取失败（{costSourceError}），这次无法「沿用上一件成本」，请手动填写成本与尺寸。
            </p>
          )}
          {costCopyNotice && <p className="cost-copy-notice" role="status">{costCopyNotice}</p>}
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

