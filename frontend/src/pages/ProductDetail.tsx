import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { getProduct, updateProduct, deleteProduct, getFeeCategories } from '../api';
import type { Product, FeeCategory } from '../types';
import { formatPrice, formatPercent, SELECTION_STATUS_MAP, SHIPPING_METHODS, LINK_STATUS_OPTIONS, LINK_STATUS_MAP } from '../types';

export default function ProductDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [feeCategories, setFeeCategories] = useState<FeeCategory[]>([]);
  const [editMode, setEditMode] = useState(false);

  // edit form
  const [editData, setEditData] = useState<Partial<Product>>({});

  useEffect(() => {
    if (!id) return;
    getProduct(id)
      .then((p) => {
        setProduct(p);
        setEditData({
          note: p.note || '',
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
        });
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    getFeeCategories().then(setFeeCategories).catch(() => {});
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
      const updated = await updateProduct(id, {
        ...cleanedData,
        fee_category_confirmed: editData.fee_category ? true : product?.fee_category_confirmed,
      });
      setProduct(updated);
      setEditMode(false);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async () => {
    if (!id || !product) return;
    if (!window.confirm(`确定删除「${product.product_name}」？`)) return;
    try {
      await deleteProduct(id);
      navigate('/products');
    } catch (e: any) {
      alert(e.message);
    }
  };

  const updateField = (field: string, value: any) => {
    setEditData((prev) => ({ ...prev, [field]: value }));
  };

  if (loading) return <div className="loading">加载中...</div>;
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!product) return <div className="loading">产品不存在</div>;

  const status = SELECTION_STATUS_MAP[product.selection_status];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <a href="#" onClick={(e) => { e.preventDefault(); navigate(-1); }} style={{ fontSize: 13, color: 'var(--color-primary)', textDecoration: 'none' }}>&larr; 返回列表</a>
          <h2 style={{ marginTop: 4 }}>
            {product.product_name || '(无标题)'}
            <span className={`status-badge status-${product.selection_status === '合格选品' ? 'qualified' : product.selection_status === '不建议选品' ? 'not-recommended' : product.selection_status === '待确认品类' ? 'pending' : 'incomplete'}`} style={{ marginLeft: 12, verticalAlign: 'middle' }}>
              {status?.label || product.selection_status}
            </span>
          </h2>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
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
        <div className="two-col">
          <div>
            {product.product_image_url && (
              <img
                src={`/api/image-proxy?url=${encodeURIComponent(product.product_image_url)}`}
                alt=""
                referrerPolicy="no-referrer"
                className="image-preview"
              />
            )}
          </div>
          <div>
            <table>
              <tbody>
                <tr><td style={{ fontWeight: 600, width: 100 }}>售价</td><td style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-primary)' }}>{formatPrice(product.actual_sale_price_zar, 'ZAR')}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>TSIN</td><td>{product.tsin || '-'}</td></tr>
                <tr><td style={{ fontWeight: 600 }}>链接</td><td><a href={product.takealot_url || '#'} target="_blank" rel="noopener noreferrer">{product.takealot_url || '-'}</a></td></tr>
                <tr><td style={{ fontWeight: 600 }}>产品序号</td><td>{product.product_no}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Fee Category */}
      <div className="card">
        <div className="card-title">Fee 品类</div>
        {editMode ? (
          <div className="form-group">
            <select
              value={editData.fee_category || ''}
              onChange={(e) => updateField('fee_category', e.target.value || null)}
            >
              <option value="">-- 请选择 --</option>
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
              <input value={editData.sku || ''} onChange={(e) => updateField('sku', e.target.value)} />
            </div>
            <div className="form-group">
              <label>中文品名</label>
              <input
                type="text"
                maxLength={10}
                value={editData.chinese_product_name || ''}
                onChange={(e) => updateField('chinese_product_name', e.target.value)}
                placeholder="最多10个字"
              />
              {editData.chinese_product_name && editData.chinese_product_name.length >= 10 && (
                <small style={{ color: '#f44336' }}>已到上限</small>
              )}
            </div>
            <div className="form-group">
              <label>采购成本 (CNY)</label>
              <input type="number" step="0.01" value={editData.purchase_cost_cny ?? ''} onChange={(e) => updateField('purchase_cost_cny', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>采购运费 (CNY)</label>
              <input type="number" step="0.01" value={editData.purchase_shipping_cny ?? ''} onChange={(e) => updateField('purchase_shipping_cny', e.target.value ? parseFloat(e.target.value) : null)} />
            </div>
            <div className="form-group">
              <label>采购数量</label>
              <input type="number" min="1" value={editData.purchase_quantity ?? 4} onChange={(e) => updateField('purchase_quantity', parseInt(e.target.value) || 4)} />
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
                    <a href={product.purchase_url} target="_blank" rel="noopener noreferrer"
                       style={{ color: 'var(--color-primary)', textDecoration: 'underline' }}>
                      {product.purchase_url}
                    </a>
                  ) : (
                    <span>{product.purchase_url || '-'}</span>
                  )}
                </td>
              </tr>
              <tr><td style={{ fontWeight: 600 }}>SKU</td><td>{product.sku || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>中文品名</td><td>{product.chinese_product_name || '-'}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购成本</td><td>{formatPrice(product.purchase_cost_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购运费</td><td>{formatPrice(product.purchase_shipping_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>采购数量</td><td>{product.purchase_quantity}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>包装费/个</td><td>{formatPrice(product.packaging_cost_per_unit_cny, 'CNY')}</td></tr>
              <tr><td style={{ fontWeight: 600 }}>链接状态</td>
                <td><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 12, color: '#fff', background: LINK_STATUS_MAP[product.link_status || '未购买']?.color || '#999' }}>
                  {LINK_STATUS_MAP[product.link_status || '未购买']?.label || '未购买'}
                </span></td></tr>
            </tbody>
          </table>
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
              <div className="section-title">计费重量计算 <span style={{ fontWeight: 400, fontSize: 12, color: '#999' }}>体积重 = 长(cm) × 宽(cm) × 高(cm) ÷ 5000</span></div>
              <table>
                <tbody>
                  <tr><td style={{ fontWeight: 600, width: 180 }}>体积重 (kg)</td><td>{product.volumetric_weight_kg != null ? `${product.volumetric_weight_kg.toFixed(3)} kg` : '-'}</td></tr>
                  <tr style={{ background: product.volumetric_weight_kg != null && product.actual_weight_kg != null && product.volumetric_weight_kg > product.actual_weight_kg ? '#fffbe6' : undefined }}>
                    <td style={{ fontWeight: 700 }}>实际计费重量 (kg)</td>
                    <td style={{ fontWeight: 700 }}>
                      {product.chargeable_weight_kg != null ? `${product.chargeable_weight_kg.toFixed(3)} kg` : '-'}
                      {product.volumetric_weight_kg != null && product.actual_weight_kg != null && (
                        <span style={{ marginLeft: 8, fontSize: 12, color: '#666' }}>
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
                <tr><td style={{ fontWeight: 600, width: 180 }}>采购单价</td><td>{formatPrice(product.purchase_cost_cny, 'CNY')}</td></tr>
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
                <tr style={{ background: '#fafafa' }}><td style={{ fontWeight: 700 }}>综合单个成本 (AD)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.unit_product_cost_cny, 'CNY')}</td></tr>
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
                <tr style={{ background: '#fafafa' }}><td style={{ fontWeight: 700 }}>到南非仓总成本 (CNY)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.cost_to_sa_warehouse_cny, 'CNY')}</td></tr>
                <tr style={{ background: '#fafafa' }}><td style={{ fontWeight: 700 }}>到南非仓总成本 (ZAR)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.cost_to_sa_warehouse_zar, 'ZAR')}</td></tr>
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
                <tr style={{ background: '#fafafa' }}><td style={{ fontWeight: 700 }}>海外仓操作总成本 (CNY)</td>
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
                <tr style={{ background: '#fafafa' }}><td style={{ fontWeight: 700 }}>海外仓操作总成本 (ZAR)</td><td style={{ fontWeight: 700 }}>{formatPrice(product.overseas_warehouse_operation_cost_zar, 'ZAR')}</td></tr>
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
            <tr style={{ background: '#fafafa' }}>
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
              <td style={{ fontWeight: 700, fontSize: 22, color: (product.profit_margin ?? 0) >= 0.25 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                {formatPercent(product.profit_margin)}
              </td>
            </tr>
            <tr>
              <td style={{ fontWeight: 600 }}>20% 利润率最低售价</td>
              <td>{product.minimum_price_at_20_margin != null ? `R ${product.minimum_price_at_20_margin.toFixed(0)}` : '-'}</td>
            </tr>
            <tr>
              <td style={{ fontWeight: 600 }}>15% 利润率最低售价</td>
              <td>{product.minimum_price_at_15_margin != null ? `R ${product.minimum_price_at_15_margin.toFixed(0)}` : '-'}</td>
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
            <tr><td style={{ fontWeight: 600 }}>备注</td><td>{product.note || '-'}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
