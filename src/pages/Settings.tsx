import { useEffect, useRef, useState } from 'react';
import { useApi, useDataSource, isTauri } from '../DataSourceContext';
import type { FeeCategory, FeeMappingRule } from '../types';
import { getSellerApiStatus, getSellerIdentity, setSellerIdentity, setupSellerApi, replaceSellerApi, getStores, getActiveStoreId, ACTIVE_STORE_CHANGED } from '../remoteApi';
import { buildExportPayload } from '../utils/exportPayload';
import { createLatestGate } from '../utils/latestGate';

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
  // 卖家 API 密钥按店铺保存：切到哪个店铺，这里写进的就是哪个店铺。
  const [sellerKeyDraft, setSellerKeyDraft] = useState('');
  const [sellerKeyBusy, setSellerKeyBusy] = useState(false);
  const [sellerKeyMsg, setSellerKeyMsg] = useState('');
  // 密钥本身看不出归属：把自营店铺的钥匙存到代运营 CK 名下，服务端照样收下，
  // 界面上还显示「已配置」，且可能读到错误店铺的数据。所以这里把当前店铺名
  // 显式摆出来，保存前再确认一次。
  const [activeStoreId, setActiveStoreIdState] = useState(() => getActiveStoreId());
  const [activeStoreLabel, setActiveStoreLabel] = useState('');
  // 切店铺时上一家店的请求可能还在飞。晚到的旧响应会把新店铺的状态刷成旧店铺的
  // —— 不报错、不闪烁，只是显示错的结论。所以按发起先后丢弃过期响应。
  const statusGate = useRef(createLatestGate()).current;
  const saveGate = useRef(createLatestGate()).current;
  const identityGate = useRef(createLatestGate()).current;
  const identitySaveGate = useRef(createLatestGate()).current;
  const identitySaving = useRef(false);
  const [identityStore, setIdentityStore] = useState<string | null>(null);
  const identityReady = dataSource === 'remote' && identityStore === activeStoreId;


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
    const token = statusGate.begin();
    setSellerApiStatus('checking');
    try {
      const status = await getSellerApiStatus();
      if (!statusGate.isLatest(token)) return; // 切店前发出的，丢弃
      setSellerApiStatus(status.configured ? 'configured' : 'missing');
    } catch {
      if (!statusGate.isLatest(token)) return;
      setSellerApiStatus('unavailable');
    }
  };

  // 跟着左上角的店铺切换器走。切店时组件不卸载，不监听就一直是旧店铺的状态。
  useEffect(() => {
    const onSwitch = () => {
      statusGate.begin(); saveGate.begin(); identityGate.begin(); identitySaveGate.begin();
      identitySaving.current = false;
      setIdentity({ name: '', source: '', candidates: [] });
      setIdentityDraft(''); setIdentityMsg(''); setIdentityStore(null); setIdentityBusy(false);
      setSellerKeyDraft(''); setSellerKeyMsg(''); setSellerKeyBusy(false);
      setActiveStoreLabel(getActiveStoreId());
      setActiveStoreIdState(getActiveStoreId());
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === 'active_store_id' || event.key === null) onSwitch();
    };
    window.addEventListener(ACTIVE_STORE_CHANGED, onSwitch);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(ACTIVE_STORE_CHANGED, onSwitch);
      window.removeEventListener('storage', onStorage);
      statusGate.begin(); saveGate.begin(); identityGate.begin(); identitySaveGate.begin();
    };
  }, []);

  // 店铺 id → 店铺名，只为给人看。取不到就用 id 兜底，不显示空。
  useEffect(() => {
    let alive = true;
    if (dataSource !== 'remote') {
      setActiveStoreLabel('');
      return () => { alive = false; };
    }
    getStores()
      .then((stores) => {
        if (!alive) return;
        const hit = stores.find((s) => s.id === activeStoreId);
        setActiveStoreLabel(hit?.name || activeStoreId);
      })
      .catch(() => { if (alive) setActiveStoreLabel(activeStoreId); });
    return () => { alive = false; };
  }, [dataSource, activeStoreId]);

  useEffect(() => {
    if (dataSource !== 'remote') {
      setSellerApiStatus('local');
      return;
    }
    // 换店铺时清掉上一次的输入和提示，否则会把 A 店的密钥显示在 B 店下面
    setSellerKeyDraft('');
    setSellerKeyMsg('');
    // 让换店前发起的保存作废：它写的是上一家店，结果不该落到这张卡片上
    saveGate.begin();
    void refreshSellerApiStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSource, activeStoreId]);

  // 已配置的店铺走 PUT（替换），未配置走 POST（首次）。
  // 分开是后端定的：POST 配过就 409，不允许覆盖，避免误操作把好钥匙冲掉。
  const handleSaveSellerKey = async () => {
    if (activeStoreId !== getActiveStoreId()) return;
    const value = sellerKeyDraft.trim();
    if (!value) return;
    // 本地先拦一道：Takealot 卖家密钥固定 128 位十六进制。
    // 位数不对就说明粘错了东西（门户令牌、SKU、半截复制），不必等服务端 400。
    if (!/^[a-fA-F0-9]{128}$/.test(value)) {
      setSellerKeyMsg(
        `格式不对：卖家 API 密钥是 128 位十六进制，当前 ${value.length} 位。请确认复制的是「卖家 API 密钥」，不是门户令牌或别的串。`
      );
      return;
    }
    const label = activeStoreLabel || activeStoreId;
    const ok = window.confirm(
      `即将把密钥保存到店铺「${label}」。\n\n` +
      `密钥看不出归属：存错店铺不会报任何错，可能读取错误店铺的数据。\n` +
      `请先确认左上角当前切换的就是「${label}」。\n\n继续保存？`
    );
    if (!ok || activeStoreId !== getActiveStoreId()) return;
    const token = saveGate.begin();
    setSellerKeyBusy(true); setSellerKeyMsg('');
    try {
      if (sellerApiStatus === 'configured') await replaceSellerApi(value);
      else await setupSellerApi(value);
      // 保存期间切了店铺：这次写入落在上一家店，别把提示打到新店铺的卡片上
      if (!saveGate.isLatest(token)) return;
      setSellerKeyDraft('');
      setSellerKeyMsg(`已保存到「${label}」。`);
      await refreshSellerApiStatus();
    } catch (e) {
      if (!saveGate.isLatest(token)) return;
      setSellerKeyMsg(`保存失败：${(e as Error).message}`);
    } finally {
      if (saveGate.isLatest(token)) setSellerKeyBusy(false);
    }
  };

  const loadIdentity = async () => {
    const store = getActiveStoreId();
    const token = identityGate.begin();
    try {
      const value = await getSellerIdentity();
      if (!identityGate.isLatest(token) || store !== getActiveStoreId()) return;
      setIdentity(value);
      setIdentityDraft(value.name || '');
      setIdentityStore(store);
    } catch {
      if (!identityGate.isLatest(token) || store !== getActiveStoreId()) return;
      setIdentityStore(null);
      setIdentityMsg('卖家身份读取失败，请重新读取后再保存。');
    }
  };

  useEffect(() => {
    identityGate.begin(); identitySaveGate.begin();
    identitySaving.current = false;
    setIdentity({ name: '', source: '', candidates: [] });
    setIdentityDraft(''); setIdentityMsg(''); setIdentityStore(null); setIdentityBusy(false);
    if (dataSource === 'remote') void loadIdentity();
    return () => { identityGate.begin(); identitySaveGate.begin(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSource, activeStoreId]);

  const saveIdentity = async (name: string) => {
    const store = getActiveStoreId();
    if (!identityReady || identityStore !== store || identitySaving.current) return;
    identitySaving.current = true;
    const token = identitySaveGate.begin();
    identityGate.begin(); // Reads begun before this mutation may not overwrite it.
    const current = () => identitySaveGate.isLatest(token) && store === getActiveStoreId();
    setIdentityBusy(true); setIdentityMsg('');
    try {
      const value = await setSellerIdentity(name);
      if (!current()) return;
      setIdentity((prev) => ({ ...prev, name: value.name, source: value.source }));
      setIdentityDraft(value.name);
      setIdentityMsg(value.name ? `已确认「${value.name}」，按此核实持位。` : '已清除，持位身份待确认。');
      void loadIdentity();
    } catch (e) {
      if (current()) setIdentityMsg(`保存失败：${(e as Error).message}`);
    } finally {
      if (current()) { identitySaving.current = false; setIdentityBusy(false); }
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
              aria-label="后端 API Key"
              type="password"
            />
          </div>
          </>
        )}
      </div>

      <section className="card integration-status-card" aria-live="polite">
        <div className="integration-status-copy">
          <div className="card-title">Takealot 本店资料 API</div>
          <p>用于读取<strong>当前店铺</strong>的商品资料和报价，帮助核对、补齐选品信息；密钥已配置不代表接口实时可用。</p>
        </div>
        <span className={`integration-status-badge ${sellerApiStatus}`}>
          <i aria-hidden="true" />
          {{checking: '检查中', configured: '密钥已配置', missing: '未配置', unavailable: '暂时无法检查', local: '本地模式'}[sellerApiStatus]}
        </span>
        <button className="btn btn-outline btn-sm" disabled={dataSource !== 'remote' || sellerApiStatus === 'checking'} onClick={refreshSellerApiStatus}>重新检查</button>
      </section>

      {dataSource === 'remote' && (
        <section className="card">
          <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            当前店铺的卖家 API 密钥
            <span className="integration-status-badge" title={`store_id: ${activeStoreId}`}>
              <i aria-hidden="true" />
              将保存到：{activeStoreLabel || activeStoreId}
            </span>
          </div>
          <p>密钥跟着左上角切换的店铺走：切到哪个店铺，写进去的就是哪个店铺，互不影响。</p>
          <div className="settings-connection-row">
            <input
              value={sellerKeyDraft}
              onChange={(e) => setSellerKeyDraft(e.target.value)}
              placeholder="粘贴该店铺的 128 位卖家 API 密钥"
              style={{ flex: 1, maxWidth: 400 }}
              aria-label="当前店铺卖家 API 密钥"
              type="password"
              autoComplete="off"
            />
            <button
              className="btn btn-primary"
              onClick={handleSaveSellerKey}
              disabled={sellerKeyBusy || !sellerKeyDraft.trim()}
            >
              {sellerKeyBusy ? '保存中...' : sellerApiStatus === 'configured' ? '替换密钥' : '保存密钥'}
            </button>
          </div>
          {sellerKeyMsg && <p>{sellerKeyMsg}</p>}
          <p>
            密钥只在服务端加密保存，界面上不会回显。已配置的店铺可用同一个入口替换。
            <br />
            注意：密钥本身不携带店铺信息，存错店铺时系统<b>不会报错</b>，可能读取错误店铺的数据 —— 保存前请核对上面这个店铺名。
          </p>
        </section>
      )}

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
                  disabled={identityBusy || !identityReady || identity.name === c.name}
                  title={`把「${c.name}」确认为本店卖家名`}
                  onClick={() => saveIdentity(c.name)}
                >
                  {c.name} · {c.skus} 个 SKU
                </button>
              ))}
            </div>
          )}
          <button className="btn btn-outline btn-sm" disabled={identityBusy || dataSource !== 'remote'} onClick={loadIdentity}>重新读取卖家名</button>
          {identityMsg && <p role="status" style={{ fontSize: 12, color: 'var(--text-body)' }}>{identityMsg}</p>}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <input
            aria-label="本店卖家名"
            disabled={identityBusy || !identityReady}
            value={identityDraft}
            onChange={(e) => setIdentityDraft(e.target.value)}
            placeholder={identity.name || '例如 Urban Electronics'}
            style={{ flex: 1, minWidth: 200, maxWidth: 320 }}
          />
          <button className="btn btn-primary btn-sm" disabled={identityBusy || !identityReady} onClick={handleSaveIdentity}>
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
