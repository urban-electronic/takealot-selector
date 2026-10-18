import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
const CloudBrowser = lazy(() => import('../operations/CloudBrowser'));
import { getAutomaticPricing, configureAutomaticPricing, pauseAutomaticPricing, reconcileAutomaticPricing, bulkAutomaticPricing, pauseAllAutomaticPricing, configureBuyboxMonitor, collectBuyboxNow, bulkBuyboxMonitor, getImageUrl } from '../remoteApi';
import { useVisibleInterval } from '../utils/useVisibleInterval';
import { describeCollection, describeOwnership, describeFleet, errorCopy, collectorName, hasUsableQuote } from '../utils/buyboxStatus';

/**
 * 44px 商品缩略图。加载失败就留一个方块占位：
 * ProductImage 的兜底文案是「图暂不可用」，五个字塞进 44px 会把方块撑破，
 * 这里要的只是「有没有图」这个信息，不是一句解释。
 */
function Thumb({ src }: { src: string }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => { setBroken(false); }, [src]);
  if (!src || broken) {
    return <span className="product-thumb is-empty" title={src ? '图片加载失败' : '暂无图片'} aria-hidden="true">无图</span>;
  }
  return <span className="product-thumb"><img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} /></span>;
}

export default function AutoPricing() {
  const [rows, setRows] = useState<any[]>([]); const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [maintenance, setMaintenance] = useState(false);
  // 原名 interval 会遮蔽 window.setInterval，排查定时器时极易误读，改名与后端字段一致。
  const [intervalMinutes, setIntervalMinutes] = useState(5);
  const [loaded, setLoaded] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [collectorSource, setCollectorSource] = useState('cloud_browser');
  // 采集循环自己的心跳（worker_alive / 轮次 / 下一轮倒计时）。没有它，
  // 界面分不清「循环停了」和「这一条刚好没到期」，用户只能反复点立即采集。
  const [loop, setLoop] = useState<any>(null);
  const [refreshing, setRefreshing] = useState(false);
  const refreshState = useRef<{ active: boolean; sequence: number; pending: Promise<void> | null }>({ active: false, sequence: 0, pending: null });
  const [segment, setSegment] = useState('all'); const [segmentName, setSegmentName] = useState('');
  const [selected, setSelected] = useState<string[]>([]); const [group, setGroup] = useState('all'); const [groupName, setGroupName] = useState('');
  const [copied, setCopied] = useState(false);
  // 展开哪一个商品页的变体列表。按 product_id 记，不按行记：
  // 同一商品页下的多条分支共用一个展开区，否则展开 24 个变体就是 24 个面板。
  const [openVariantOf, setOpenVariantOf] = useState('');
  // 被网站验证挡住时，唯一真正有效的动作是让真人在同一个持久化浏览器里过掉那次验证。
  // 按钮直接落到维护面板，而不是再点一次「立即采集」——那只会多一次失败记录。
  const maintenanceRef = useRef<HTMLDetailsElement>(null);
  const openMaintenance = () => {
    const node = maintenanceRef.current;
    if (!node) return;
    node.open = true; setMaintenance(true);
    node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  useEffect(() => { setPage(1); }, [search, group, segment]);
  const key = (r: any) => `${r.product_id}:${r.offer_id}`;
  const refresh = useCallback((afterMutation = false): Promise<void> => {
    const state = refreshState.current;
    if (!state.active) return Promise.resolve();
    // Ordinary reads share one request; a completed write needs a newer snapshot.
    if (state.pending && !afterMutation) return state.pending;
    const sequence = ++state.sequence;
    const isCurrent = () => state.active && sequence === state.sequence;
    setRefreshing(true);
    const pending = getAutomaticPricing().then(d => {
      if (!isCurrent()) return;
      // 响应形状不保证。缺 rows 时保留上一次的有效快照并明说：整表清空比显示
      // 旧数据更危险——用户会以为 SKU 都没了，还会顺手重新开启采集。
      const list = Array.isArray(d?.rows) ? d.rows : null;
      if (list) {
        setRows(list.filter((r: any) => typeof r.stock === 'number' && r.stock > 0));
        setConfigured(!!d?.configured); setCollectorSource(d?.collector_source || 'cloud_browser');
        setLoop(d?.loop && typeof d.loop === 'object' ? d.loop : null);
        setError('');
      } else {
        setError('接口返回格式异常：缺少 rows 数组。已保留上一次成功加载的列表，请稍后重试。');
      }
      setLoaded(true);
    }).catch(e => {
      if (isCurrent()) setError(e.message);
    }).finally(() => {
      if (isCurrent()) { state.pending = null; setRefreshing(false); }
    });
    state.pending = pending;
    return pending;
  }, []);
  useEffect(() => {
    const state = refreshState.current;
    state.active = true;
    void refresh();
    return () => { state.active = false; state.sequence++; state.pending = null; };
  }, [refresh]);
  // 隐藏标签页时不轮询，回到前台若已过期立即补一次
  useVisibleInterval(() => { void refresh(); }, 30000);
  const act = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'reconcile') await reconcileAutomaticPricing(r.product_id, r.offer_id);
      else if (action === 'pause') await pauseAutomaticPricing(r.product_id, r.offer_id);
      else {
        const draft = { ...r.rule, ...drafts[key(r)] };
        const useSystem = drafts[key(r)]?.use_system_min ?? r.rule.use_system_min ?? true;
        const min = useSystem ? r.suggested_min : Number(draft.min_price);
        if (!min || !Number.isInteger(min)) throw new Error('系统底价缺失，请补全成本或手工输入该SKU最低价。');
        if (draft.max_price && !Number.isInteger(Number(draft.max_price))) throw new Error('最高价必须为整数，也可留空使用智能上限。');
        await configureAutomaticPricing(r.product_id, r.offer_id, { min_price: useSystem ? null : min, use_system_min: useSystem, smart_raise: (draft.strategy || 'takealot_buybox') === 'takealot_buybox', max_price: draft.max_price ? Number(draft.max_price) : null, strategy: draft.strategy || 'takealot_buybox', lead_amount: 1, cooldown_minutes: 10, max_daily_changes: 10, enabled: action === 'start' ? true : !!r.rule.enabled });
        setDrafts(prev => { const next = { ...prev }; delete next[key(r)]; return next; });
      }
      await refresh(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const emergencyStop = async () => { setBusy('all'); try { await pauseAllAutomaticPricing(); await refresh(true); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } };
  const bulk = async (action: string) => {
    if (!selected.length) { setError('请先勾选需要操作的SKU。'); return; }
    setBusy('bulk'); setError('');
    try {
      const chosen = rows.filter(r => selected.includes(key(r)));
      await bulkAutomaticPricing({ action, group: groupName, items: chosen.map(r => ({ product_id: r.product_id, offer_id: r.offer_id })) });
      await refresh(true); setSelected([]);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitor = async (r: any, action: string) => {
    setBusy(key(r)); setError('');
    try {
      if (action === 'once') await collectBuyboxNow(r.product_id,r.offer_id);
      else await configureBuyboxMonitor(r.product_id,r.offer_id,{enabled:action==='start',interval_minutes:intervalMinutes});
      await refresh(true);
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  const monitorBulk = async (enabled: boolean, currentPage = false) => {
    setBusy('monitor'); setError('');
    try {
      await bulkBuyboxMonitor({enabled,interval_minutes:intervalMinutes,segment:enabled?(segmentName.trim()||`第${activePage}段`):'',items:(currentPage?pageRows:rows.filter(r=>selected.includes(key(r)))).map(r=>({product_id:r.product_id,offer_id:r.offer_id}))});
      await refresh(true);
    } catch(e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  // 一键处理失败行。两类失败要分开：
  //   自身故障（解析器、网络、配置）→ 清 next_due 立刻重试，这一步有意义；
  //   被网站验证/限流挡住 → 只改间隔，不清 next_due。界面正文已经在劝用户
  //   「反复点立即采集没有用」，按钮自己就不能偷偷再点一次。
  // backend/api/pricing_routes.py 的 keep_backoff 就是为这件事加的。
  const BLOCKED = ['site_challenge', 'rate_limited', 'http_forbidden'];
  const retryFailed = async (minutes?: number) => {
    const failed = rows.filter(r => r.monitor?.status === 'error' && r.monitor?.enabled);
    if (!failed.length) return;
    const next = minutes ?? intervalMinutes;
    const blocked = failed.filter(r => BLOCKED.includes(r.monitor?.error_code));
    const retryable = failed.filter(r => !BLOCKED.includes(r.monitor?.error_code));
    setBusy('retry'); setError('');
    try {
      if (minutes) setIntervalMinutes(minutes);
      const items = (list: any[]) => list.map(r => ({ product_id: r.product_id, offer_id: r.offer_id }));
      if (retryable.length) await bulkBuyboxMonitor({ enabled: true, interval_minutes: next, segment: '', items: items(retryable) });
      if (blocked.length) await bulkBuyboxMonitor({ enabled: true, keep_backoff: true, interval_minutes: next, segment: '', items: items(blocked) });
      await refresh(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  // 失败时把诊断一次复制走。采集失败的原因有一半在页面里（HTTP 状态、落地页标题、
  // 匹配到几个 Buybox 区块），这些数字后端已经采到了，但只躺在 JSON 里没人看见，
  // 于是用户只能反复点「立即采集」。这里做成一个按钮，一次给全。
  // 只含 URL 路径、元素计数和错误码：safe_url 已剥掉查询串，不含任何令牌或页面正文。
  const copyDiagnostics = async () => {
    const failed = rows.filter(r => r.monitor?.status === 'error');
    if (!failed.length) { setError('当前没有采集失败的行可导出。'); return; }
    const payload = {
      exported_at: new Date().toISOString(),
      collector_source: collectorSource,
      seller_api_configured: configured,
      total_rows: rows.length,
      failing: failed.length,
      rows: failed.slice(0, 60).map(r => ({
        sku: r.sku, name: r.name, url: r.url,
        error_code: r.monitor?.error_code || '', error: r.monitor?.error || '',
        error_stage: r.monitor?.error_stage || '',
        last_attempt_at: r.monitor?.last_attempt_at || null,
        diagnostics: r.monitor?.diagnostics || null,
      })),
    };
    const text = JSON.stringify(payload, null, 2);
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch { ok = false; }
    if (!ok) {
      // Tauri 的 webview 不保证给 clipboard 权限，退回 textarea + execCommand。
      try {
        const box = document.createElement('textarea');
        box.value = text; box.setAttribute('readonly', '');
        box.style.position = 'fixed'; box.style.opacity = '0';
        document.body.appendChild(box); box.select();
        ok = document.execCommand('copy');
        document.body.removeChild(box);
      } catch { ok = false; }
    }
    if (ok) { setCopied(true); setTimeout(() => setCopied(false), 2500); }
    else setError('复制失败：环境不允许写剪贴板。请手动展开任一行的「为什么」查看诊断。');
  };
  const visible = rows.filter(r => typeof r.stock === 'number' && r.stock > 0 && (group === 'all' || r.group === group) && (segment==='all' || (r.monitor?.segment||'未分段')===segment) && `${r.chinese_name || ''} ${r.name} ${r.sku}`.toLowerCase().includes(search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(visible.length / 10)); const activePage = Math.min(page, pages);
  const pageRows = visible.slice((activePage-1)*10, activePage*10);
  const input = (r: any, field: string, label: string) => <input type="number" min="1" step="1" aria-label={`${r.sku}${label}`} title={field === 'min_price' ? '最低价：留空=自动使用系统利润底价，填了就按手动底价' : '最高价：可留空，走智能上限'} placeholder={field === 'min_price' ? `自动 ${r.suggested_min ?? '待补'}` : '智能上限'} style={{ width: 86 }} value={drafts[key(r)]?.[field] ?? (field === 'min_price' && r.rule.use_system_min !== false ? r.suggested_min ?? '' : r.rule[field] ?? '')} onChange={e => setDrafts({ ...drafts, [key(r)]: { ...drafts[key(r)], [field]: e.target.value, ...(field === 'min_price' ? { use_system_min: e.target.value === '' } : {}) } })} />;
  const relative = (v: string) => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
  // 调价决策由后端 plan() 落库，界面只负责翻译。
  // 这些词直接对应两种真实动作：抢位是让利，守位是要价，判错方向就是钱。
  const REGIME: Record<string,string> = { compete:'抢位让利', climb:'守位提价', scarcity:'独家提价', hold:'保持现价', blind:'证据不足', off:'无库存' };
  const CONFIDENCE: Record<string,string> = { high:'高', medium:'中', low:'低' };
  const RISK: Record<string,string> = { low:'低', medium:'中', high:'高', urgent:'紧急' };
  const planOf = (r:any) => r.rule?.plan && typeof r.rule.plan === 'object' ? r.rule.plan : null;
  // 状态翻译集中在 utils/buyboxStatus，渲染层只负责摆放，不再各自拼文案。
  // 这里显式取一次时钟，保证同一屏内所有相对时间用的是同一个基准。
  const ctx = { now: Date.now(), collectorSource, configured };
  const fleet = describeFleet(rows, ctx);
  // 被网站挡住的行没有"立刻重试"这个选项，只有"把间隔调长"；只有自身故障才给重试。
  const canAct = fleet.retryable > 0 || (fleet.blocked > 0 && !!fleet.suggestedInterval);
  const fleetAction = fleet.suggestedInterval
    ? `改为 ${fleet.suggestedInterval} 分钟${fleet.retryable ? `，重试 ${fleet.retryable} 个` : ''}`
    : `重试 ${fleet.retryable} 个`;
  // 取更近的一份行情：采集成功晚于上次调价评估时用采集结果，否则用调价评估时留下的。
  const market = (r: any) => r.monitor?.last_success_at && (!r.rule.last_evaluated_at || r.monitor.last_success_at > r.rule.last_evaluated_at) ? r.monitor : r.rule;
  const collection = (r:any) => describeCollection(r.monitor, ctx);
  // 循环轨道：一个格子 = 一个 SKU。比堆一排大数字更贴合这件事——
  // 运营真正想知道的不是"总共多少条"，而是"哪几个没在采"。
  const cellState = (r:any) => {
    const m = r.monitor || {};
    // 只有明确关掉才算「未开启」；字段缺失时按状态判断，别把在采的 SKU 画成灰的。
    if (m.enabled === false && !m.request_once) return 'off';
    if (m.status === 'success') return 'ok';
    // 失败要分成两件事：手上还有有效报价 = 只是这一轮没刷新（黄），
    // 从没采到过报价 = 真的没有行情（红）。原来一律画红，用户看到
    // 一片红以为采集全废了，其实多数行旧价还能用。
    if (m.status === 'error') return hasUsableQuote(m) ? 'warn' : 'bad';
    if (m.status === 'running' || m.status === 'queued') return 'live';
    return 'idle';
  };
  const cellLabel = (r:any) => {
    const m = r.monitor || {};
    if (m.enabled === false && !m.request_once) return '未开启采集';
    if (m.status === 'success') return '本轮已采到';
    if (m.status === 'error') {
      if (hasUsableQuote(m)) return `这一轮没刷新，仍用上次报价${m.error_code ? `：${errorCopy(m.error_code, m.error).title}` : ''}`;
      return m.error_code ? `采集失败：${errorCopy(m.error_code, m.error).title}` : '采集失败';
    }
    if (m.status === 'running' || m.status === 'queued') return '正在采这一轮';
    return '等待下轮';
  };
  const loopInterval = loop?.interval_minutes ?? intervalMinutes;
  const loopTone = !loop ? 'neutral' : !loop.worker_alive ? 'danger' : loop.paused_by === 'local_collector' ? 'warning' : 'success';
  const loopTitle = !loop ? '采集循环状态未知'
    : !loop.worker_alive ? '采集循环已停止'
    : loop.paused_by === 'local_collector' ? '本机采集器接管中'
    : '采集循环运行中';
  const loopNote = !loop ? '还没拿到后台心跳'
    : !loop.worker_alive ? '后台线程没在跑，SKU 不会自动重采，请检查服务状态'
    : loop.paused_by === 'local_collector' ? '云端循环让位给本机采集器，属正常'
    : `每 ${loopInterval} 分钟一轮${loop.cycles ? ` · 已跑 ${loop.cycles} 轮` : ''}`;
  const nextIn = loop?.next_due_in;
  // 一轮实测耗时 vs 设定间隔。这两个数一比就能回答「为什么一直在延迟」：
  // 28 个 SKU 串行采、每个十几秒，实际一轮可能把 5 分钟的间隔占满——那延迟
  // 不是故障，是排期本身就紧。不说清楚，用户只会以为程序卡住了。
  const roundMinutes = loop?.round_seconds ? Math.round(loop.round_seconds / 6) / 10 : null;
  // 上一轮可能只轮到 4 个 SKU，拿这 4 个的耗时当整轮会得出「排得下」的假结论。
  // 按每个 SKU 的均摊时间乘上在采数量外推，才是整轮的真实长度。
  const perSku = loop?.seconds_per_sku
    ?? (loop?.round_seconds && loop?.round_jobs ? Math.round(loop.round_seconds / loop.round_jobs * 10) / 10 : null);
  const fullMinutes = perSku && loop?.enabled ? Math.round(perSku * loop.enabled / 6) / 10 : null;
  const roundEstimate = Math.max(roundMinutes ?? 0, fullMinutes ?? 0) || null;
  const overCapacity = roundEstimate != null && loop?.interval_minutes != null && roundEstimate > loop.interval_minutes;
  const tight = !overCapacity && roundEstimate != null && loop?.interval_minutes != null
    && roundEstimate > loop.interval_minutes * 0.8;
  const measuredText = roundMinutes == null
    ? (fullMinutes != null ? `按 ${loop?.enabled} 个在采算，整轮约 ${fullMinutes} 分钟` : '')
    // 上一轮只轮到 4 个 SKU 时，说「一轮 0.5 分钟」是假话：那是 4 个的耗时，不是整轮。
    : loop?.round_jobs && loop?.enabled && fullMinutes != null && loop.round_jobs < loop.enabled
      ? `上一轮只采了 ${loop.round_jobs}/${loop.enabled} 个，花了 ${roundMinutes} 分钟（每个 ${perSku} 秒），整轮约 ${fullMinutes} 分钟`
      : `一轮实测 ${roundMinutes} 分钟（${loop?.round_jobs ?? '?'} 个 SKU${perSku ? `，每个 ${perSku} 秒` : ''}）`;
  const roundText = measuredText;
  const capacityNote = overCapacity
    ? `${roundText}，比设定间隔 ${loop.interval_minutes} 分钟长，循环排得比跑得完的更快`
    : tight
      ? `${roundText}，几乎占满 ${loop.interval_minutes} 分钟间隔——任何一个 SKU 变慢，下一轮立刻逾期`
      : roundText ? `${roundText}，间隔 ${loop?.interval_minutes ?? '?'} 分钟，排得下` : '';
  // 逾期要说逾期，还要说清逾期到哪一步了：是「还没轮到」还是「已经在补跑」。
  const nextText = nextIn == null ? '—'
    : nextIn > 60 ? `约 ${Math.round(nextIn / 60)} 分钟后`
    : nextIn >= 0 ? `${nextIn} 秒后`
    : nextIn > -120 ? '马上开始'
    // 超过一小时就不报精确分钟了：那说明排期本身有问题，精确到分钟是假精确。
    : nextIn > -3600 ? `已到期 ${Math.round(-nextIn / 60)} 分钟` + (loop?.due_now > 0 ? ` · ${loop.due_now} 个在排队` : '')
    : '排期已过期 1 小时以上';
  const nextHint = nextIn != null && nextIn < -120
    ? `最早该开始的那一轮比计划晚了 ${Math.abs(Math.round(nextIn / 60))} 分钟。` + capacityNote
    : capacityNote;
  // 红的是哪个：循环轨道只给颜色，这里把失败的 SKU 直接点名，点一下就筛出那一行。
  // 只算「在采的」那些：关掉的 SKU 身上留着的旧错误不该被算成当前要处理的事，
  // 否则上面的数字和下面点名的列表会对不上。
  // 「需处理」只算真的没有报价的行。这一轮没刷新但旧价还在的不算——
  // 它不需要用户动手，列进"需处理"只会让人白忙。
  const failing = rows.filter(r => r.monitor?.status === 'error' && (r.monitor?.enabled || r.monitor?.request_once) && !hasUsableQuote(r.monitor));
  const focusSku = (sku: string) => {
    setSearch(sku); setPage(1);
    document.querySelector('.operations-table-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  return <div className="operations-workspace pricing-workspace">
    <div className="dashboard-heading"><div><h1>Buybox与自动调价</h1><p>采集和调价分别调度、分别开关</p></div><button className="btn btn-outline" disabled={refreshing} aria-busy={refreshing} onClick={()=>{void refresh();}}>刷新列表</button></div>

    {/* 页头只留一行：什么情况、为什么、该怎么办。原来这里是六行列表，没人看。 */}
    {loaded&&<section className={`loop-strip tone-${loopTone}`} aria-label="采集循环状态">
      <div className="loop-lead">
        <span className="loop-dot" aria-hidden="true" />
        <div className="loop-lead-text">
          <strong>{loopTitle}</strong>
          <small>{loopNote}</small>
        </div>
      </div>
      <ol className="loop-track" aria-hidden="true">
        {rows.map(r=>{
          const state=cellState(r); const label=`${r.sku} · ${cellLabel(r)}`;
          // 只有失败/未开启/待刷新的格子可点：正常的不需要点，28 个 tab 停靠点反而碍事。
          return (state==='bad'||state==='off'||state==='warn')
            ? <li key={key(r)} className={`loop-cell is-${state}`}><button type="button" className="loop-cell-hit" aria-label={label} title={label} onClick={()=>focusSku(r.sku)} /></li>
            : <li key={key(r)} className={`loop-cell is-${state}`} title={label} />;
        })}
      </ol>
      <div className="loop-facts">
        <span className="is-ok"><b>{loop?.success ?? 0}</b> 已采到</span>
        <span className={failing.length ? 'is-bad' : ''}><b>{failing.length}</b> 需处理</span>
        <span className="is-live"><b>{loop?.running ?? 0}</b> 进行中</span>
        <span className="loop-next" title={nextHint}>下一轮 {nextText}</span>
      </div>
      {(failing.length>0||overCapacity||tight)&&<div className="loop-notes">
        {failing.length>0&&<div className="loop-failing">
          <span>需处理 {failing.length} 个：</span>
          {failing.map(r=><button type="button" key={key(r)} onClick={()=>focusSku(r.sku)} title={errorCopy(r.monitor?.error_code, r.monitor?.error).title}>{r.sku}</button>)}
        </div>}
        {(overCapacity||tight)&&<p className={`loop-capacity${overCapacity?'':' is-tight'}`}>{capacityNote}{overCapacity?`。把间隔调到 ${Math.ceil((roundEstimate??0)/5)*5} 分钟以上，循环才追得上。`:''}</p>}
      </div>}
    </section>}

    {loaded&&<div className={`collection-bar tone-${fleet.tone}`}>
      <span className={`status-pill tone-${fleet.tone}`}>{fleet.summary}</span>
      {fleet.action&&<small>{fleet.action}</small>}
      <div className="collection-bar-actions">
        {canAct&&<button className="btn btn-outline btn-sm" disabled={!!busy} onClick={()=>retryFailed(fleet.suggestedInterval??undefined)}>{fleetAction}</button>}
        {fleet.blocked>0&&<button className="btn btn-outline btn-sm" disabled={!!busy} onClick={openMaintenance}>人工过验证</button>}
        {rows.some(r=>r.monitor?.status==='error')&&<button className="btn btn-outline btn-sm" disabled={!!busy} onClick={()=>{void copyDiagnostics();}}>{copied?'已复制':'复制诊断'}</button>}
      </div>
    </div>}

    {error&&<div role="alert" className="alert alert-error">{error}</div>}

    <div className="card operations-table-card">
      <div className="pricing-toolbar"><input aria-label="搜索调价商品" placeholder="商品名称 / SKU" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="SKU分类筛选" value={group} onChange={e=>{setGroup(e.target.value);setSelected([]);}}><option value="all">全部分类</option>{[...new Set(rows.map(r=>r.group))].map(g=><option key={g} value={g}>{g}</option>)}</select><select aria-label="采集分段筛选" value={segment} onChange={e=>{setSegment(e.target.value);setSelected([]);}}><option value="all">全部分段</option>{[...new Set(rows.map(r=>r.monitor?.segment||'未分段'))].map(sg=><option key={sg} value={sg}>{sg}</option>)}</select><input placeholder="本段名称（可选）" value={segmentName} onChange={e=>setSegmentName(e.target.value)}/><span className="selection-count">已选 {selected.length} SKU</span></div>
      <div className="pricing-actions"><div><span>Buybox采集</span><select aria-label="自动采集间隔" value={intervalMinutes} onChange={e=>setIntervalMinutes(Number(e.target.value))}>{[5,10,15,30,60,120].map(n=><option key={n} value={n}>{n}分钟</option>)}</select><button className="btn btn-info" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(true)}>开启选中采集</button><button className="btn btn-info" disabled={!!busy||!pageRows.length||!configured} onClick={()=>monitorBulk(true,true)}>开启本页 {pageRows.length} 个</button><button className="btn btn-outline" disabled={!!busy||!selected.length||!configured} onClick={()=>monitorBulk(false)}>停采</button></div><div><span>实际调价</span><button className="btn btn-success" disabled={!!busy||!selected.length||!configured} onClick={()=>bulk('start')}>启动选中调价</button><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('pause')}>暂停</button><input placeholder="分类名称" value={groupName} onChange={e=>setGroupName(e.target.value)} /><button className="btn btn-outline" disabled={!!busy||!selected.length} onClick={()=>bulk('group')}>归类</button><button className="btn btn-danger" disabled={!!busy||!rows.some(r=>r.rule.enabled)} onClick={emergencyStop}>停止全部调价</button></div></div>

      <div className="operations-table-scroll"><table className="data-table pricing-table"><thead><tr><th><input type="checkbox" aria-label="勾选当前筛选的全部SKU" checked={visible.length>0&&visible.every(r=>selected.includes(key(r)))} onChange={e=>setSelected(e.target.checked?visible.map(key):[])} /></th><th>商品</th><th>本店价 / Buybox</th><th>采集</th><th>调价区间 / 策略</th></tr></thead><tbody>{pageRows.map(r=>{
        const m=market(r); const view=collection(r); const own=describeOwnership(r.monitor); const hasQuote=m.buybox_price!=null; const diag=r.monitor?.diagnostics;
        const plan=planOf(r);
        // 一个数字只说一次。原来本店价在商品行、Buybox 列、判断依据里各印一遍，
        // 同一行出现三次 R 269——这不是信息，是噪音。
        const bb=m.buybox_price; const same=bb!=null&&r.price!=null&&Math.abs(bb-r.price)<0.01;
        const variantCount=r.variant?.count||1;
        // 无定语形态：变体行只显示商品本体，定语（黑白/XLMS）收进展开区。
        const title=r.variant?.base||r.chinese_name||'中文名待核对';
        const openVariant=openVariantOf===r.product_id;
        return <tr key={key(r)} className={openVariant?'is-expanded':''}><td><input type="checkbox" aria-label={`选择SKU ${r.sku}`} checked={selected.includes(key(r))} onChange={e=>setSelected(e.target.checked?[...selected,key(r)]:selected.filter(k=>k!==key(r)))} /></td>
          <td className="operations-product"><div className="product-cell"><Thumb src={r.image ? getImageUrl(r.image) : ''} /><div className="product-body"><a className="product-link" title={r.name} href={r.url} target="_blank" rel="noreferrer">{title}</a>{!r.variant?.base&&!r.chinese_name&&<small className="text-warning">{r.name_status==='invalid'?'中文字段为非中文，请修正':r.name_status==='ambiguous'?'SKU与中文名对应不明确':'中文名待补充'}</small>}
            {/* 中文名对上号用，英文名才是平台上的标题：宽屏有富余就摆出来，窄屏藏掉。 */}
            {r.name&&r.name!==title&&<small className="product-en" title={r.name}>{r.name}</small>}
            <div className="product-meta"><span>{r.sku}</span><span>库存 {r.stock??'—'}</span>
              {variantCount>1&&<button type="button" className={`variant-toggle${openVariant?' is-open':''}`} aria-expanded={openVariant} onClick={()=>setOpenVariantOf(openVariant?'':r.product_id)}>变体 {variantCount}</button>}
            </div></div></div>
            {openVariant&&<ul className="variant-list" aria-label={`${title} 的全部变体`}>{r.variant.siblings.map((v:any)=><li key={v.sku} className={v.sku===r.sku?'is-current':''}><b>{v.label||'未命名'}</b><span>R {v.price??'—'}</span><span>库存 {v.stock??'—'}</span></li>)}</ul>}
          </td>
          <td className="quote-cell"><div className="quote-line"><strong className="own-price">R {r.price??'—'}</strong><span title={own.explain} className={`status-pill tone-${own.tone}`}>{own.label}</span></div>
            <small className="quote-sub">{bb==null?'未采到 Buybox 价':same?`Buybox 同价`:`Buybox R ${bb}`}{m.competitor_price!=null&&!same?` · 竞品 R ${m.competitor_price}`:''}</small>
          </td>
          <td className="collection-status-cell"><div className="collection-head"><span className={`status-pill tone-${view.tone}`}>{view.label}</span><small className="collection-headline" title={view.headline}>{view.headline}</small></div><div className="row-buttons"><button className="btn btn-outline btn-sm" disabled={!!busy||!configured||['queued','running'].includes(r.monitor?.status)} title={['queued','running'].includes(r.monitor?.status)?'本轮已在队列或正在采集，重复提交会被忽略':''} onClick={()=>monitor(r,'once')}>立即采集</button><button className="btn btn-outline btn-sm" disabled={!!busy||!configured} title={configured?'':'卖家接口未配置，无法采集'} onClick={()=>monitor(r,r.monitor?.enabled?'stop':'start')}>{r.monitor?.enabled?'停采':'自动'}</button></div></td>
          {/* 依据、诊断、决策卡片全部收进这一个 details。原来每行两个 details
              各写一段散文，28 行就是 56 段；现在一屏能看完整页。 */}
          <td><div className="price-range">{input(r,'min_price','最低')}{input(r,'max_price','最高')}{plan&&<span className={`decision-regime is-${plan.regime||'hold'}`} title={plan.reason}>{REGIME[plan.regime||'']||plan.regime}</span>}<select aria-label={`${r.sku}调价策略`} value={drafts[key(r)]?.strategy??r.rule.strategy??'takealot_buybox'} onChange={e=>setDrafts({...drafts,[key(r)]:{...drafts[key(r)],strategy:e.target.value}})}><option value="takealot_buybox">Buybox / 智能试涨</option><option value="follow_lowest">跟随现货最低价</option></select></div><div className="row-buttons"><button className="btn btn-outline btn-sm" disabled={!!busy} onClick={()=>act(r,'save')}>保存区间</button><button className={`btn btn-sm ${r.rule.enabled?'btn-outline':'btn-success'}`} disabled={!!busy||!configured} title={r.rule.last_result||''} onClick={()=>act(r,r.rule.write_pending?'reconcile':r.rule.enabled?'pause':'start')}>{busy===key(r)?'处理中…':r.rule.write_pending?'核验现价':r.rule.enabled?'暂停调价':'启动调价'}</button><small className={`price-state ${r.rule.write_pending?'text-warning':r.rule.enabled?'text-success':''}`}>调价：{r.rule.write_pending?'待核验':r.rule.enabled?'运行中':'关闭'}</small>
              <details className="row-details"><summary>详情</summary>
              {plan&&<div className="decision-card">
                <span className={`decision-regime is-${plan.regime||'hold'}`}>{REGIME[plan.regime||'']||plan.regime||'—'}</span>
                <span>把握 <b>{CONFIDENCE[plan.confidence||'']||'—'}</b></span>
                <span>风险 <b>{RISK[plan.risk||'']||'—'}</b></span>
                <span>底价 <b>R {plan.floor??'—'}</b></span>
                <span>上限 <b>{plan.ceiling==null?'不限':`R ${plan.ceiling}`}</b></span>
                <span>对手最低 <b>{plan.cheapest==null?'无现货':`R ${plan.cheapest}`}</b></span>
                <p>{plan.reason}</p>
              </div>}
              <small role="status">{own.explain}</small>
              {m.price_ownership?.own_price!=null&&<small>对照本店价：R {m.price_ownership.own_price} · {m.price_ownership.own_price_source==='official_live'?'实时API':m.price_ownership.own_price_source==='official_snapshot'?'官方同步快照':'任务售价快照'}</small>}
              <small role="status">{view.detail}</small>
              {view.action&&<small className="collection-advice">建议：{view.action}</small>}
              {diag&&<small className="diag-line" title={JSON.stringify(diag)}>页面 HTTP {diag.http_status??'—'} · Buybox 区块 {diag.buybox_cards??'未统计'} 个 · 价格节点 {diag.price_nodes??'未统计'} 个{diag.page_title?` · 标题「${diag.page_title}」`:''}</small>}
              <small>页面卖家：{m.snapshot?.buyboxSeller||'未读取'}</small>
              {hasQuote&&<small>报价来源：{m.source?collectorName(m.source):'未记录'}</small>}
              {hasQuote&&['queued','running','error','paused'].includes(r.monitor?.status)&&<small>上一次成功采到的报价，不是本轮结果</small>}
              <small>下次采集 {(r.monitor?.enabled||r.monitor?.request_once)&&r.monitor?.next_due?relative(new Date(r.monitor.next_due*1000).toISOString()):'—'}</small>
              <small>最近成功 {relative(r.monitor?.last_success_at)}</small>
              {r.monitor?.error&&<small>原始报错：{r.monitor.error}</small>}
              </details>
            </div></td></tr>;
      })}</tbody></table></div>
      <div className="operations-pagination"><span>{visible.length} 个SKU · 每页10条</span><button className="btn btn-outline btn-sm" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>上一页</button><span>{activePage} / {pages}</span><button className="btn btn-outline btn-sm" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>下一页</button></div>
      {loaded&&!rows.length&&!error&&<p className="operations-empty">暂无本店有库存的SKU，请先同步库存。</p>}
    </div>
    <details className="operations-explanation" ref={maintenanceRef} onToggle={e=>setMaintenance(e.currentTarget.open)}><summary>采集维护（高级）</summary><p>日常采集由当前在线的采集器执行。这里只用于人工排查网站验证或配送设置；关闭会释放操作会话。</p><p className="maintenance-note">被网站验证挡住时：在这个浏览器里正常打开商品页、把验证过掉，然后回到这里点「立即采集」。采集器用的是同一个持久化配置目录，验证过掉之后后台这一轮就能读到页面。<b>系统不会去解验证码、不会伪装浏览器指纹、不会换 IP</b>——这三件事会让店铺账号本身被平台盯上，代价远大于一次采集失败。</p>{maintenance&&<Suspense fallback={<p>正在加载维护工具…</p>}><CloudBrowser rows={rows} onCollected={()=>{void refresh(true);}} /></Suspense>}</details>
    <details className="operations-explanation"><summary>调价与试涨规则（点开看具体判定）</summary><ul><li><b>最低价</b>默认用系统 20% 利润底价；可以手动填一个固定底价。</li><li><b>Buybox 价和本店售价相同</b>只说明价格一样，不能证明占位的是本店，所以此时保持现价，不涨价。</li><li><b>确认本店在持位</b>才评估试涨；<b>不在持位</b>时只降不升。</li><li><b>确认竞品非现货</b>时约 3% 试涨，12 小时观察一次，累计不超过启用时参考价的 20%。</li><li><b>48 小时没有新成交</b>则回退到之前的价位。</li><li><b>采集失败</b>保持现价；<b>改价结果未确认</b>时自动暂停调价，等你核验。</li></ul></details>
  </div>;
}
