import { useEffect, useState } from 'react';
import { useApi, useDataSource } from '../DataSourceContext';
import { getProducts, getFeeCategories, updateFeeCategory, getFeeMappingRules, createFeeMappingRule, updateFeeMappingRule, getSettings, updateSettings } from '../api';
import type { FeeCategory, FeeMappingRule } from '../types';

export default function Settings() {
  const api = useApi();
  const { dataSource, setDataSource } = useDataSource();
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [exchangeRate, setExchangeRate] = useState('');
  const [apiBaseUrl, setApiBaseUrl] = useState(() => {
    try { return localStorage.getItem('api_base_url') || ''; } catch { return ''; }
  });
  const [exporting, setExporting] = useState(false);
  const [feeCategories, setFeeCategories] = useState<FeeCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Fee mapping rule form
  const [rulePattern, setRulePattern] = useState('');
  const [ruleCategory, setRuleCategory] = useState('');
  const [rulePriority, setRulePriority] = useState('0');

  useEffect(() => {
    Promise.all([getSettings(), getFeeCategories()])
      .then(([s, f]: [Record<string, string>, FeeCategory[]]) => {
        setSettings(s);
        setExchangeRate(s.cny_per_zar || '0.41');
        setFeeCategories(f);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleSaveExchange = async () => {
    try {
      await updateSettings({ cny_per_zar: exchangeRate });
      setMessage('汇率已更新');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleToggleCategory = async (fc: FeeCategory) => {
    try {
      await updateFeeCategory(fc.id, { active: !fc.active });
      setFeeCategories((prev) =>
        prev.map((f) => (f.id === fc.id ? { ...f, active: !f.active } : f))
      );
      setMessage(`品类「${fc.name}」已${fc.active ? '停用' : '启用'}`);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdateFeeRate = async (fc: FeeCategory, newRate: number) => {
    try {
      await updateFeeCategory(fc.id, { success_fee_rate: newRate });
      setFeeCategories((prev) =>
        prev.map((f) => (f.id === fc.id ? { ...f, success_fee_rate: newRate } : f))
      );
      setMessage(`品类「${fc.name}」费率已更新为 ${(newRate * 100).toFixed(0)}%`);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAddMappingRule = async () => {
    if (!ruleCategory) return;
    try {
      await createFeeMappingRule({
        takealot_category_pattern: rulePattern,
        fee_category: ruleCategory,
        priority: parseInt(rulePriority) || 0,
        created_by_user: true,
      });
      setMessage('映射规则已添加');
      setRulePattern('');
      setRuleCategory('');
      setRulePriority('0');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSaveApiUrl = () => {
    try { localStorage.setItem('api_base_url', apiBaseUrl); } catch {}
    setMessage('API 地址已保存');
  };

  const handleExportToCloud = async () => {
    const baseUrl = apiBaseUrl || localStorage.getItem('api_base_url') || 'https://takealot-selector-production.up.railway.app';
    if (!baseUrl) { setError('请先设置远程库 API 地址'); return; }
    setExporting(true);
    setError('');
    setMessage('');
    try {
      const products = await getProducts() as any[];
      const categories = await getFeeCategories();
      const rules: FeeMappingRule[] = [];
      try { const r = await getFeeMappingRules(); rules.push(...r); } catch {}
      const settingsData = await getSettings(); // 始终读本地，不受远程模式影响
      const settingsRows = Object.entries(settingsData).map(([key, value]) => ({ key, value }));
      const payload = [
        { table: 'products', rows: products.map(({ product_no, ...rest }: any) => rest) },
        { table: 'fee_categories', rows: categories },
        { table: 'fee_mapping_rules', rows: rules },
        { table: 'system_settings', rows: settingsRows },
      ];
      const res = await fetch(`${baseUrl}/api/migrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMessage(`导出成功！products: ${data.imported?.products || 0}, fee_categories: ${data.imported?.fee_categories || 0}, fee_mapping_rules: ${data.imported?.fee_mapping_rules || 0}, system_settings: ${data.imported?.system_settings || 0}`);
    } catch (e: any) {
      setError(`导出失败: ${e.message}`);
    } finally {
      setExporting(false);
    }
  };

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div>
      <h2 style={{ marginBottom: 20 }}>系统设置</h2>

      {message && <div className="alert alert-success">{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {/* Data Source */}
      <div className="card">
        <div className="card-title">数据源</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="radio" name="dataSource" value="local" checked={dataSource === 'local'} onChange={() => setDataSource('local')} />
            本地数据库
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="radio" name="dataSource" value="remote" checked={dataSource === 'remote'} onChange={() => setDataSource('remote')} />
            远程共享库
          </label>
        </div>
        {dataSource === 'remote' && (
          <div style={{ marginTop: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
            <label style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>API 地址:</label>
            <input
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              placeholder="https://xxx.railway.app"
              style={{ flex: 1, maxWidth: 400 }}
            />
            <button className="btn btn-primary" onClick={handleSaveApiUrl}>保存</button>
            <button className="btn btn-outline" onClick={handleExportToCloud} disabled={exporting}>
              {exporting ? '导出中...' : '一键导出到云端'}
            </button>
          </div>
        )}
      </div>

      {/* Exchange Rate */}
      <div className="card">
        <div className="card-title">汇率设置</div>
        <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 12 }}>
          当前: 1 ZAR = {settings.cny_per_zar || '0.41'} CNY。修改后仅影响新计算，历史产品保留原汇率。
        </p>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <label style={{ fontWeight: 600 }}>CNY per ZAR:</label>
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={exchangeRate}
            onChange={(e) => setExchangeRate(e.target.value)}
            style={{ width: 120 }}
          />
          <button className="btn btn-primary" onClick={handleSaveExchange}>保存</button>
        </div>
      </div>

      {/* Fee Categories */}
      <div className="card">
        <div className="card-title">Fee 品类费率管理</div>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>品类</th>
                <th>费率范围</th>
                <th>计算比例</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {feeCategories.map((fc) => (
                <tr key={fc.id} style={!fc.active ? { opacity: 0.5 } : undefined}>
                  <td>{fc.name}</td>
                  <td>{fc.fee_rate_range}</td>
                  <td>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      max="1"
                      value={fc.success_fee_rate}
                      onChange={(e) => handleUpdateFeeRate(fc, parseFloat(e.target.value) || 0)}
                      style={{ width: 80 }}
                    />
                  </td>
                  <td>
                    <span className={`status-badge ${fc.active ? 'status-qualified' : 'status-not-recommended'}`}>
                      {fc.active ? '启用' : '停用'}
                    </span>
                  </td>
                  <td>
                    <button className="btn btn-outline btn-sm" onClick={() => handleToggleCategory(fc)}>
                      {fc.active ? '停用' : '启用'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Fee Mapping Rules */}
      <div className="card">
        <div className="card-title">添加 Fee 品类映射规则</div>
        <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 12 }}>
          当 Takealot 分类路径包含指定关键词时，自动匹配到对应 Fee 品类。
        </p>
        <div className="form-row">
          <div className="form-group">
            <label>Takealot 分类关键词</label>
            <input
              value={rulePattern}
              onChange={(e) => setRulePattern(e.target.value)}
              placeholder="如 Small Appliances"
            />
          </div>
          <div className="form-group">
            <label>目标 Fee 品类</label>
            <select value={ruleCategory} onChange={(e) => setRuleCategory(e.target.value)}>
              <option value="">-- 请选择 --</option>
              {feeCategories.map((fc) => (
                <option key={fc.id} value={fc.name}>{fc.name}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>优先级</label>
            <input type="number" value={rulePriority} onChange={(e) => setRulePriority(e.target.value)} />
          </div>
          <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button className="btn btn-primary" onClick={handleAddMappingRule}>添加规则</button>
          </div>
        </div>
      </div>
    </div>
  );
}
