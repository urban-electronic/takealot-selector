import { useEffect, useRef, useState } from 'react';
import { useApi, useDataSource, isTauri } from '../DataSourceContext';
import type { FeeCategory, FeeMappingRule } from '../types';
import { getSellerApiStatus, getSellerIdentity, setSellerIdentity } from '../remoteApi';
import { buildExportPayload } from '../utils/exportPayload';

export default function Settings() {
  const api = useApi();
  const { dataSource, setDataSource } = useDataSource();
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [exchangeRate, setExchangeRate] = useState('');
  const [apiBaseUrl, setApiBaseUrl] = useState(() => {
    try { return localStorage.getItem('api_base_url') || ''; } catch { return ''; }
  });
  const [apiKey, setApiKey] = useState(() => {
    try { return localStorage.getItem('api_key') || ''; } catch { return ''; }
  });
  // ref 兜底：部分浏览器/自动化填写不会触发 React onChange，保存时从 DOM 读真实值
  const apiBaseUrlRef = useRef<HTMLInputElement>(null);
  const apiKeyRef = useRef<HTMLInputElement>(null);
  const [exporting, setExporting] = useState(false);
  const [feeCategories, setFeeCategories] = useState<FeeCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [sellerApiStatus, setSellerApiStatus] = useState<'checking' | 'configured' | 'missing' | 'unavailable' | 'local'>('checking');
  // 本店卖家名：调价靠它区分「Buybox 是本店在持位」还是「对手同价压着」。
  // 后端会从已采集的页面自动投票学出来，这里只是给人一个手动纠正的入口。
  const [identity, setIdentity] = useState<{ name: string; source: string; candidates: { name: string; skus: number }[] }>({ name: '', source: '', candidates: [] });
  const [identityDraft, setIdentityDraft] = useState('');
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityMsg, setIdentityMsg] = useState('');

  // Fee mapping rule form
  const [rulePattern, setRulePattern] = useState('');
  const [ruleCategory, setRuleCategory] = useState('');
  const [rulePriority, setRulePriority] = useState('0');

  // 成功提示与错误提示是两块独立的状态，同时挂出来就是两句互相打脸的话：
  // 「汇率已更新」还在屏幕上，紧接着「品类费率保存失败」的红条又冒出来，
  // 用户不知道这次到底成没成。每个动作只允许留下自己那一条
  // （采购页 PROC-ERR-STALE-01 是同一个判据）。
  const notice = (text: string) => { setMessage(text); setError(''); };
  const fail = (e: unknown) => {
    setError(e instanceof Error ? e.message : String(e));
    setMessage('');
  };

  useEffect(() => {
    Promise.all([api.getSettings(), api.getFeeCategories()])
      .then(([s, f]: [Record<string, string>, FeeCategory[]]) => {
        setSettings(s);
        setExchangeRate(s.cny_per_zar || '0.41');
        setFeeCategories(f);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshSellerApiStatus = async () => {
    setSellerApiStatus('checking');
    try {
      const status = await getSellerApiStatus();
      setSellerApiStatus(status.configured ? 'configured' : 'missing');
    } catch {
      setSellerApiStatus('unavailable');
    }
  };

  useEffect(() => {
    if (dataSource !== 'remote') {
      setSellerApiStatus('local');
      return;
    }
    void refreshSellerApiStatus();
  }, [dataSource]);

  const loadIdentity = async () => {
    try {
      const value = await getSellerIdentity();
      setIdentity(value);
      setIdentityDraft(value.name || '');
    } catch {
      // 身份读不到不影响其它设置；调价那边会显示「待核实卖家名」。
    }
  };

  useEffect(() => {
    if (dataSource === 'remote') void loadIdentity();
  }, [dataSource]);

  // 确认本店身份应该是一步操作。候选已经在库里了，让人照着抄一遍名字
  // 是把「严谨」变成了「麻烦」——麻烦的结果就是不填，调价就一直停着。
  const saveIdentity = async (name: string) => {
    setIdentityBusy(true); setIdentityMsg('');
    try {
      const value = await setSellerIdentity(name);
      setIdentity((prev) => ({ ...prev, name: value.name, source: value.source }));
      setIdentityDraft(value.name);
      setIdentityMsg(value.name ? `已确认「${value.name}」，按此核实持位。` : '已清除，持位身份待确认。');
      void loadIdentity();
    } catch (e) {
      setIdentityMsg(`保存失败：${(e as Error).message}`);
    } finally {
      setIdentityBusy(false);
    }
  };
  const handleSaveIdentity = () => saveIdentity(identityDraft.trim());

  const handleSaveExchange = async () => {
    try {
      await api.updateSettings({ cny_per_zar: exchangeRate });
      notice('汇率已更新');
    } catch (e) {
      fail(e);
    }
  };

  const handleToggleCategory = async (fc: FeeCategory) => {
    try {
      await api.updateFeeCategory(fc.id, { active: !fc.active });
      setFeeCategories((prev) =>
        prev.map((f) => (f.id === fc.id ? { ...f, active: !f.active } : f))
      );
      notice(`品类「${fc.name}」已${fc.active ? '停用' : '启用'}`);
    } catch (e) {
      fail(e);
    }
  };

  const handleUpdateFeeRate = async (fc: FeeCategory, newRate: number) => {
    try {
      await api.updateFeeCategory(fc.id, { success_fee_rate: newRate });
      setFeeCategories((prev) =>
        prev.map((f) => (f.id === fc.id ? { ...f, success_fee_rate: newRate } : f))
      );
      notice(`品类「${fc.name}」费率已更新为 ${(newRate * 100).toFixed(0)}%`);
    } catch (e) {
      fail(e);
    }
  };

  const handleAddMappingRule = async () => {
    if (!ruleCategory) return;
    try {
      await api.createFeeMappingRule({
        takealot_category_pattern: rulePattern,
        fee_category: ruleCategory,
        priority: parseInt(rulePriority) || 0,
        created_by_user: true,
      });
      notice('映射规则已添加');
      setRulePattern('');
      setRuleCategory('');
      setRulePriority('0');
    } catch (e) {
      fail(e);
    }
  };

  const handleSaveApiUrl = () => {
    // 优先读 DOM 真实值（兼容 onChange 未触发的场景），再与 state 同步
    const baseUrl = (apiBaseUrlRef.current?.value ?? apiBaseUrl).trim();
    const key = (apiKeyRef.current?.value ?? apiKey).trim();
    setApiBaseUrl(baseUrl);
    setApiKey(key);
    try {
      localStorage.setItem('api_base_url', baseUrl);
      localStorage.setItem('api_key', key);
    } catch {
      fail('API 配置保存失败：浏览器存储不可用');
      return;
    }
    // 保存后立即读回验证，避免"点了保存但没落库"
    let verified = false;
    try {
      verified =
        localStorage.getItem('api_base_url') === baseUrl &&
        localStorage.getItem('api_key') === key;
    } catch {
      verified = false;
    }
    if (!verified) {
      fail('API 配置未生效，请重试（必要时清理浏览器缓存后刷新）');
      return;
    }
    notice('API 配置已保存并生效');
  };

  const handleExportToCloud = async () => {
    const baseUrl = apiBaseUrl || localStorage.getItem('api_base_url') || 'https://takealot-selector-production.up.railway.app';
    if (!baseUrl) { fail('请先设置远程库 API 地址'); return; }
    setExporting(true);
    setError('');
    setMessage('');
    try {
      const products = await api.getProducts() as any[];
      const categories = await api.getFeeCategories();
      // 读不到费率映射规则不能当成「本地没有规则」：后端 /api/migrate 会先清空
      // 本次 payload 涉及的每一张表，发一张空表过去等于把云端规则全删掉，
      // 而界面还写「导出成功」。失败时传 null，让 buildExportPayload 整表省略。
      const rules: FeeMappingRule[] | null = await api.getFeeMappingRules().catch(() => null);
      const settingsData = await api.getSettings(); // 跟随当前数据源
      const { payload, skipped } = buildExportPayload({
        products, feeCategories: categories, feeRules: rules, settings: settingsData,
      });
      const migrateKey = (apiKeyRef.current?.value ?? apiKey ?? localStorage.getItem('api_key') ?? '').trim();
      const res = await fetch(`${baseUrl}/api/migrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': migrateKey },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const imported = data.imported || {};
      // 被省略的表不能写成「0 条」——那读起来像「同步过去 0 条规则」，
      // 而实际是根本没动云端那张表。如实说是「未同步 + 原因」。
      const rulesPart = skipped.includes('fee_mapping_rules')
        ? 'fee_mapping_rules: 未同步（本地读取失败，云端原值保留）'
        : `fee_mapping_rules: ${imported.fee_mapping_rules || 0}`;
      notice(`导出成功！products: ${imported.products || 0}, fee_categories: ${imported.fee_categories || 0}, ${rulesPart}, system_settings: ${imported.system_settings || 0}`);
    } catch (e) {
      fail(`导出失败: ${(e as Error).message}`);
    } finally {
      setExporting(false);
    }
  };

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div className="settings-page">
      <div className="page-heading"><div><h1>系统设置</h1><p>管理数据连接、费率与基础参数。</p></div></div>

      {message && <div className="alert alert-success">{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {/* Data Source */}
      <div className="card">
        <div className="card-title">数据源</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          {isTauri() ? (
            <>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <input type="radio" name="dataSource" value="local" checked={dataSource === 'local'} onChange={() => setDataSource('local')} />
                本地数据库
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <input type="radio" name="dataSource" value="remote" checked={dataSource === 'remote'} onChange={() => setDataSource('remote')} />
                远程共享库
              </label>
            </>
          ) : (
            <span style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>
              网页版仅支持远程共享库（数据保存在云端）
            </span>
          )}
        </div>
        {dataSource === 'remote' && (
          <>
          <div className="settings-connection-row">
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
          <div className="settings-connection-row">
            <label style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>API Key:</label>
            <input
              ref={apiKeyRef}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="填写后端 API Key（未设置鉴权可留空）"
              style={{ flex: 1, maxWidth: 400 }}
              type="password"
            />
          </div>
          </>
        )}
      </div>

      <section className="card integration-status-card" aria-live="polite">
        <div className="integration-status-copy">
          <div className="card-title">Takealot 本店资料 API</div>
          <p>用于读取默认主店的商品资料和报价，帮助核对、补齐选品信息；密钥已配置不代表接口实时可用。</p>
        </div>
        <span className={`integration-status-badge ${sellerApiStatus}`}>
          <i aria-hidden="true" />
          {{checking: '检查中', configured: '密钥已配置', missing: '未配置', unavailable: '暂时无法检查', local: '本地模式'}[sellerApiStatus]}
        </span>
        <button className="btn btn-outline btn-sm" disabled={dataSource !== 'remote' || sellerApiStatus === 'checking'} onClick={refreshSellerApiStatus}>重新检查</button>
      </section>

      <section className="card integration-status-card" aria-live="polite">
        <div className="integration-status-copy">
          <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            本店卖家名
            {identity.source === 'manual' && <span className="integration-status-badge configured"><i aria-hidden="true" />手动指定</span>}
            {identity.source === 'candidate' && <span className="integration-status-badge"><i aria-hidden="true" />候选待确认</span>}
          </div>
          <p>
            调价要靠它分辨 Buybox 是<b>本店在持位</b>还是对手同价压着——只看价格会把两者搞混，
            方向一反就是赔钱。
          </p>
          <p>下方是采集统计到的卖家名，点一下即确认，确认后才用于持位判断。</p>
          {identity.candidates.length > 0 && (
            <div className="identity-candidates">
              <span>点一下即确认：</span>
              {identity.candidates.map((c) => (
                <button
                  key={c.name}
                  type="button"
                  className="btn btn-outline btn-sm"
                  disabled={identityBusy || dataSource !== 'remote' || identity.name === c.name}
                  title={`把「${c.name}」确认为本店卖家名`}
                  onClick={() => saveIdentity(c.name)}
                >
                  {c.name} · {c.skus} 个 SKU
                </button>
              ))}
            </div>
          )}
          {identityMsg && <p role="status" style={{ fontSize: 12, color: 'var(--text-body)' }}>{identityMsg}</p>}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <input
            aria-label="本店卖家名"
            value={identityDraft}
            onChange={(e) => setIdentityDraft(e.target.value)}
            placeholder={identity.name || '例如 Urban Electronics'}
            style={{ flex: 1, minWidth: 200, maxWidth: 320 }}
          />
          <button className="btn btn-primary btn-sm" disabled={identityBusy || dataSource !== 'remote'} onClick={handleSaveIdentity}>
            {identityBusy ? '保存中…' : '保存'}
          </button>
        </div>
      </section>

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
