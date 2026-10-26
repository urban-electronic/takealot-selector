/**
 * 采集状态的人话翻译层。
 *
 * 界面必须回答三个问题，缺一个用户就会觉得「不知道它到底在干什么」：
 *   1. 现在有没有在采？—— 开关状态、本轮进度、以及**谁来执行**；
 *   2. 屏幕上这个价格是哪来的、还新不新？—— 报价新鲜度；
 *   3. 我该做什么？—— 唯一推荐动作，或明确「无需操作」。
 *
 * 术语全部来自后端（只读参考；本模块只做翻译，不改变任何判定语义）：
 *   status:                success | queued | running | error | paused
 *   error_code:            services/collection_diagnostics.classify、buybox_monitor、collector_routes
 *   price_ownership.state: matched | mismatch_pending | mismatch | checking | verification_failed
 *   collector_source:      cloud_browser | local_collector | browser_extension | pricepilot_relay
 *
 * 执行者是真实存在的分支，不是文案修辞：backend/services/buybox_monitor.py 的
 * start_worker() 在有采集器心跳时会直接 continue，后台线程不跑；也就是说
 * 「本机采集器在线」和「后台采集」互斥，界面必须说清当下是谁在采。
 */

export type Tone = 'success' | 'info' | 'warning' | 'danger' | 'neutral';
export type Freshness = 'fresh' | 'aging' | 'stale' | 'none' | 'invalid';

export const COLLECTOR_NAMES: Record<string, string> = {
  cloud_browser: '后台采集器',
  local_collector: '本机采集器',
  browser_extension: '浏览器扩展',
  pricepilot_relay: '原程序转发',
};

export function collectorName(source?: string): string {
  return COLLECTOR_NAMES[source || ''] || '采集器';
}

export interface CollectionContext {
  /** 毫秒时间戳。显式传入，便于测试与避免渲染期读时钟。 */
  now: number;
  collectorSource?: string;
  configured?: boolean;
}

/** 「3 分钟前」这类相对时间。无法解析时返回「时间未知」，绝不伪造。 */
export function humanAge(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '时间未知';
  if (ms < 60_000) return '刚刚';
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

/** 「约 3 分钟后」这类倒计时。调用方只在确实还有剩余时间时使用。 */
export function humanUntil(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '马上';
  if (ms < 60_000) return '不到 1 分钟内';
  const minutes = Math.ceil(ms / 60_000);
  if (minutes < 60) return `约 ${minutes} 分钟后`;
  return `约 ${Math.ceil(minutes / 60)} 小时后`;
}

export interface FreshnessView {
  fresh: Freshness;
  ageText: string;
  ageMs: number | null;
  tone: Tone;
}

/** 服务器与浏览器有几十秒时钟差异是正常的；超过这个量就不是差异，是数据有问题。 */
const FUTURE_TOLERANCE_MS = 5 * 60_000;

/**
 * 报价新鲜度。阈值按该行自己的采集间隔算，而不是写死 5 分钟：
 *   2 个间隔内 = 新鲜；6 个间隔内 = 偏旧；再久 = 过期。
 * 这是「界面够不够聪明」的关键——同一个 10 分钟前的价格，在 5 分钟一轮
 * 和 120 分钟一轮下含义完全不同。
 */
export function quoteFreshness(monitor: any, now: number): FreshnessView {
  const at = Date.parse(monitor?.last_success_at || '');
  if (!Number.isFinite(at)) return { fresh: 'none', ageText: '尚无成功记录', ageMs: null, tone: 'neutral' };
  // 未来时间戳不是"刚刚"：旧实现用 Math.max(0, now-at) 把负数压成 0，
  // 于是时钟错、时区错或脏数据都会显示成最新成功的报价，这是最危险的一种错。
  if (at > now + FUTURE_TOLERANCE_MS) {
    return { fresh: 'invalid', ageText: '记录时间在未来', ageMs: null, tone: 'danger' };
  }
  const ageMs = Math.max(0, now - at);
  const intervalMs = Math.max(1, Number(monitor?.interval_minutes) || 5) * 60_000;
  if (ageMs <= intervalMs * 2) return { fresh: 'fresh', ageText: humanAge(ageMs), ageMs, tone: 'success' };
  if (ageMs <= intervalMs * 6) return { fresh: 'aging', ageText: humanAge(ageMs), ageMs, tone: 'warning' };
  return { fresh: 'stale', ageText: humanAge(ageMs), ageMs, tone: 'danger' };
}

interface ErrorCopy {
  title: string;
  why: string;
  action: string;
}

/**
 * 这一行手上有没有一份「还能用」的报价。
 *
 * 有报价和没报价是两件完全不同的事，界面必须分开说：
 *   - 有报价、只是这一轮没刷新 → 数据偏旧，还能看，别报红；
 *   - 从没采到过报价 → 真的没有行情，得让人知道。
 * 原来两种情况都落进 `status === 'error'` 的红色分支，于是浏览器抖一下
 * （2026-10-18 SKU 9902391031247，库里还有 5 分钟前的 450）就被显示成
 * 故障，用户以为报价丢了。
 */
export function hasUsableQuote(monitor: any): boolean {
  const m = monitor || {};
  const price = m.buybox_price;
  if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) return false;
  return Number.isFinite(Date.parse(m.last_success_at || ''));
}

/**
 * 错误码 → 人话。标题要能让用户一眼看出「是谁的问题」：
 * 网站挡的、我们程序错的、配置缺的，处理方式完全不同。
 */
export const ERROR_COPY: Record<string, ErrorCopy> = {
  site_challenge: {
    title: '网站要求人工验证',
    why: '后台打开商品页时被网站的验证页挡住了，这一轮没拿到报价。已有的旧报价会继续保留，不会被清空。',
    action: '打开「采集维护（高级）」人工过一次验证；网站放行后会自动恢复。反复点「立即采集」没有用。',
  },
  rate_limited: {
    title: '被网站限流',
    why: '请求太频繁被网站临时拒绝（HTTP 429），系统已自动把这一行放慢到至少 30 分钟一轮。',
    action: '等它自己恢复，期间不要反复点「立即采集」。',
  },
  http_forbidden: {
    title: '网站拒绝访问',
    why: '网站返回 401/403，后台这次没拿到商品页。',
    action: '稍后会自动重试；若一直如此，改用本机采集器。',
  },
  site_unavailable: {
    title: '网站服务暂时不可用',
    why: '网站返回 5xx，是对方服务器的问题，不是本店配置问题。',
    action: '无需操作，等它恢复。',
  },
  product_not_found: {
    title: '商品链接已失效',
    why: '商品公开页返回 404，商品可能已下架或换链接。',
    action: '确认该 SKU 是否还在售；已下架就解除绑定。',
  },
  invalid_url: {
    title: '商品链接格式不对',
    why: '填的不是有效的 Takealot PLID 地址，程序无从打开。',
    action: '到商品详情里修正商品链接。',
  },
  variant_unmatched: {
    title: '确认不了是哪个变体',
    why: '页面检测到多个变体控件，程序不敢拿默认变体的价格当这个 SKU 的报价。',
    action: '人工打开页面，确认该 SKU 对应哪个变体。',
  },
  tls_error: {
    title: '证书校验失败',
    why: '浏览器无法校验证书，页面根本没打开。',
    action: '检查本机系统时间是否准确、是否有网络代理干扰。',
  },
  navigation_timeout: {
    title: '打开商品页超时',
    why: '页面加载太久被中断，这一轮没读到内容。',
    action: '等下一轮；持续超时请检查出网是否正常。',
  },
  collector_error: {
    title: '采集程序内部出错',
    why: '解析脚本抛了异常，属于程序缺陷，不是网络或配置问题。',
    action: '需要维护人员修复解析器，重复点击不会好。',
  },
  network_error: {
    title: '网络连接失败',
    why: '浏览器连不上商品页，这一轮没有读到任何内容。',
    action: '检查后台出网是否正常。',
  },
  product_redirect: {
    title: '页面跳到了别的商品',
    why: '打开后落地页不是目标 PLID，报价不能算在这个 SKU 上。',
    action: '核对商品链接是否被重定向。',
  },
  unstable_price: {
    title: '两次读到的价格不一致',
    why: '页面报价在读取过程中发生变化，这一轮不采信。',
    action: '等下一轮重新读取。',
  },
  price_not_found: {
    title: '页面打开了但没有 Buybox 价',
    why: '页面已加载，但没找到有效的 Buybox 报价区域。',
    action: '人工打开页面看看是不是已下架，或者页面结构变了。',
  },
  // 采集器自己没起来（浏览器启动失败、卖家接口超时）。以前这种会被显示成上面
  // 那条「没有 Buybox 价」，把基础设施故障说成页面问题，误导排查方向。
  collector_unavailable: {
    title: '采集器没起来',
    why: '这一轮采集没有真正执行：浏览器或卖家接口没就绪，所以根本没打开商品页。',
    action: '点「立即采集」重试一次；仍不行就是采集环境的问题，与商品页面无关。',
  },
  // 浏览器被上一个任务占着（它卡住了没释放）。这时候点重试只会继续排队，
  // 和「采集器没起来」要用户做的事完全不同，必须分开说。
  collector_busy: {
    title: '采集浏览器被占用',
    why: '上一个采集任务卡住了没释放浏览器，这一轮没排上，所以没打开商品页。',
    action: '等下一轮自动重试；连续出现说明浏览器卡死，需要重启采集服务。',
  },
  // 单个任务超过硬上限仍未返回：浏览器/页面挂住，任务已被放弃。
  collect_timeout: {
    title: '这一轮采集超时',
    why: '浏览器打开后超过 4 分钟没有返回结果，任务已被放弃，不是商品下架。',
    action: '等下一轮自动重试；连续出现说明浏览器卡死，需要重启采集服务。',
  },
  // 这两条靠"数元素"得来：有价格却没 Buybox 区块 = 我们的锚点失效；
  // 两者都是 0 = 落地页根本不是商品页。原来一律报成上面那条，用户没法判断该等还是该修。
  page_structure_changed: {
    title: '页面结构变了',
    why: '页面能正常打开、也有价格，但整页一个 Buybox 区块都没匹配到。这通常是网站改版让解析锚点失效，不是商品下架。',
    action: '重复点「立即采集」不会好，需要按新结构更新解析规则。',
  },
  page_not_recognized: {
    title: '落地页不像商品页',
    why: '打开后既没认出价格，也没认出 Buybox 区块，页面可能是验证页或跳转页。',
    action: '先确认商品链接还能正常打开；链接没问题就复制诊断发给维护。',
  },
  parse_error: {
    title: '报价解析失败',
    why: '页面内容读到了，但没解析出价格。',
    action: '需要维护人员更新解析规则。',
  },
  stale_source_quote: {
    title: '原程序给的报价已过期',
    why: '原程序转发过来的报价时间戳缺失或过期，没有当作新行情使用。',
    action: '让原程序重新采一轮，或改用本机采集器。',
  },
  local_collection_failed: {
    title: '本机采集没拿到有效报价',
    why: '采集器回报的数据里没有可用的 Buybox 价格。',
    action: '看采集器日志，确认是不是被网站挡住了。',
  },
  seller_read_failed: {
    title: '读卖家接口失败',
    why: '连卖家 API 就失败了，还没轮到读商品页。',
    action: '检查卖家接口配置是否仍然有效。',
  },
};

export function errorCopy(code?: string, raw?: string): ErrorCopy {
  const known = ERROR_COPY[code || ''];
  if (known) return known;
  return {
    title: '本轮没拿到报价',
    why: raw || '采集器没有给出具体原因，下面是原始信息。',
    action: '先看原始信息；无法判断时点「立即采集」再试一次。',
  };
}

export interface CollectionView extends FreshnessView {
  tone: Tone;
  label: string;
  headline: string;
  detail: string;
  action: string;
}

/**
 * 一行采集状态的完整翻译。
 * 优先级：配置缺失 > 正在跑 > 排队 > 失败 > 停采 > 成功（按新鲜度分档）> 从未采过。
 */
export function describeCollection(monitor: any, ctx: CollectionContext): CollectionView {
  const m = monitor || {};
  const now = ctx.now;
  const freshness = quoteFreshness(m, now);
  const interval = Math.max(1, Number(m.interval_minutes) || 5);
  const dueMs = Number(m.next_due) * 1000;
  const hasDue = Number.isFinite(dueMs) && dueMs > 0;
  const dueSoon = hasDue && dueMs > now;
  const source = ctx.collectorSource || 'cloud_browser';
  const isCloud = source === 'cloud_browser';
  const executor = collectorName(source);

  if (ctx.configured === false) {
    return {
      ...freshness,
      tone: 'danger',
      label: '无法采集',
      headline: '卖家接口还没配置',
      detail: '采集要先读到本店售价才能判断 Buybox 归属，接口没配置就一步都走不了。',
      action: '到「设置」完成卖家接口配置。',
    };
  }

  if (m.status === 'running') {
    return {
      ...freshness,
      tone: 'info',
      label: '采集中',
      headline: '正在采这一轮',
      detail: `通常 10–60 秒出结果，完成后本行自动更新，不用再点。${
        freshness.ageMs !== null ? `下面显示的是 ${freshness.ageText} 的报价。` : ''
      }`,
      action: '',
    };
  }

  if (m.status === 'queued') {
    if (dueSoon) {
      return {
        ...freshness,
        tone: 'neutral',
        label: '等待下轮',
        headline: `下一轮${humanUntil(dueMs - now)}`,
        detail: `已排进计划，${interval} 分钟一轮。到点自动开始，现在点「立即采集」只会被忽略。`,
        action: '',
      };
    }
    return {
      ...freshness,
      tone: 'warning',
      label: '排队中',
      headline: isCloud ? '已排队，后台马上执行' : `已排队，等${executor}来领`,
      detail: isCloud
        ? '后台每 10 秒取一次任务，正常 30 秒内开始。如果一直停在这里，说明后台采集线程没在跑。'
        : `${executor}每 10 秒取一次任务；如果超过 2 分钟没动静，说明它已经掉线，任务会卡在这里。`,
      action: isCloud ? '超过 5 分钟没动静，请查看后台服务状态' : `确认${executor}正在运行`,
    };
  }

  if (m.status === 'error') {
    const copy = errorCopy(m.error_code, m.error);
    // 手上还有一份有效报价时，这一轮失败只是「没刷新」，不是「没有行情」。
    // 说成故障会让用户以为价格丢了，而实际上旧价还能继续用。
    if (hasUsableQuote(m)) {
      return {
        ...freshness,
        tone: 'warning',
        label: '待刷新',
        // 原因照样要说清楚：不报红不等于瞒着。只是从"故障"改成"这一轮没刷新"，
        // 因为旧价还在，用户没有必须马上做的事。
        headline: `这一轮没刷新 · ${copy.title}`,
        detail: `${copy.why}${dueSoon ? `系统会在${humanUntil(dueMs - now)}自动重试。` : ''}下面仍用 ${freshness.ageText}的报价。`,
        action: '',
      };
    }
    return {
      ...freshness,
      tone: 'danger',
      label: '采集失败',
      headline: copy.title,
      detail: `${copy.why}${dueSoon ? `系统会在${humanUntil(dueMs - now)}自动重试。` : ''}${
        freshness.ageMs !== null ? `下面保留的是 ${freshness.ageText}的旧报价。` : ''
      }`,
      action: copy.action,
    };
  }

  if (m.status === 'paused') {
    return {
      ...freshness,
      tone: 'neutral',
      label: '已停采',
      headline: '这个 SKU 不会自动采',
      detail:
        freshness.fresh === 'invalid'
          ? '记录里的成功时间在未来，判断不了这份报价有多旧。'
          : freshness.ageMs !== null
            ? `最后一次成功是${freshness.ageText}，之后价格不会再更新。`
            : '还没有成功采过。',
      action: '需要更新时点「自动」重新开启。',
    };
  }

  // 时间戳不可信时不能落进下面的"成功"分支：宁可说不知道，也不能说这是最新价。
  if (freshness.fresh === 'invalid') {
    return {
      ...freshness,
      tone: 'danger',
      label: '时间异常',
      headline: '成功时间晚于当前时间',
      detail: '这条记录的成功时间在未来，通常来自时钟或时区错误，不能当作最新报价使用。',
      action: '点「立即采集」重新采一次；如果仍然如此，请检查服务器时间。',
    };
  }

  if (freshness.ageMs !== null) {
    const auto = !!m.enabled;
    const next = dueSoon ? `下一轮${humanUntil(dueMs - now)}。` : '';
    if (freshness.fresh === 'fresh') {
      return {
        ...freshness,
        tone: 'success',
        label: auto ? '自动采集' : '手动采集',
        headline: `${freshness.ageText}更新过报价`,
        detail: auto ? `每 ${interval} 分钟自动采一次。${next}` : `只采过这一次，不会自动更新。${next}`,
        action: auto ? '' : '要持续更新请点「自动」。',
      };
    }
    if (freshness.fresh === 'aging') {
      return {
        ...freshness,
        tone: 'warning',
        label: auto ? '数据偏旧' : '已过期',
        headline: `报价是${freshness.ageText}的`,
        detail: auto
          ? `已超过 ${Math.round(interval * 2)} 分钟没有新的成功记录，可能最近几轮都没采成。${next}`
          : '手动采集不会自动更新，这个价格可能已经不是当前价。',
        action: auto ? '看下面有没有失败原因；没有就等下一轮。' : '需要当前价请点「立即采集」。',
      };
    }
    return {
      ...freshness,
      tone: 'danger',
      label: '数据过期',
      headline: `报价停在${freshness.ageText}`,
      detail: `已经远超 ${interval} 分钟的采集间隔，这个价格不能当作当前行情使用。${next}`,
      action: auto ? '检查失败原因，必要时停采后重新开启。' : '点「立即采集」拿一次当前价。',
    };
  }

  return {
    ...freshness,
    tone: 'neutral',
    label: '未开始',
    headline: '还没采过这个 SKU',
    detail: '开启自动采集后才会定期抓 Buybox 报价；也可以先手动试一次。',
    action: '点「立即采集」试一次，或点「自动」开启。',
  };
}

export interface OwnershipView {
  tone: Tone;
  label: string;
  explain: string;
}

const OWNERSHIP_COPY: Record<string, OwnershipView> = {
  matched: {
    tone: 'warning',
    label: '疑似在持位',
    explain:
      'Buybox 价和本店售价一样。价格相同不能证明是本店在占位——别的卖家也可能挂同价，所以这里只当参考，不会仅凭同价去涨价。',
  },
  mismatch_pending: {
    tone: 'warning',
    label: '疑似在持位 · 复核中',
    explain: '上一轮同价、这一轮不同价，但只丢了一轮。要连续两轮都不同价才算失去持位，所以暂时仍按在持位处理。',
  },
  mismatch: {
    tone: 'neutral',
    label: '不在持位',
    explain: 'Buybox 价和本店售价不同，并且已经连续两轮如此，判定本店不在持位。',
  },
  checking: {
    tone: 'info',
    label: '正在核对本店价',
    explain: '这一轮 Buybox 价和本店售价不同，系统正在用卖家接口核对本店实时售价，核对完会更新结论。',
  },
  verification_failed: {
    tone: 'warning',
    label: '本店价核对失败',
    explain: '这一轮没能确认本店实时售价（卖家接口或报价缺失），判断不了是否在持位，本轮不做涨价。',
  },
};

/**
 * 「本店是不是这个 Buybox 的持有者」的翻译。
 *
 * 证据分两级，界面必须分开说，否则两个方向都会骗人：
 *   1. **卖家名核实**（`price_ownership.identity_verified === true`）——采集器按
 *      本店卖家名比对了页面 "Sold by X"，`services/price_ownership.estimate()` 在
 *      `verified` 为 True/False 时都会写 `identity_verified=True`。这是硬证据，
 *      同价时说"已核实是本店"，同价但卖家是别家时说"已核实不在持位"。
 *   2. **同价推断**（`identity_verified !== true`）——只有价格相同这一个信号，
 *      别的卖家也可能挂同价，所以只能说"疑似"，不能据此声称本店在持位。
 * 旧注释写的是「identity_verified 恒为 False / holding_buybox 从不写 True」，
 * 那是卖家名核实上线前的口径；现在 `buybox_monitor.py` 会把三态如实落库
 * （`holding_buybox` = True/False/None），照着旧注释做会漏掉整个第 1 级。
 * 历史记录里残留的 `holding_buybox=true` 没有 `identity_verified` 佐证，
 * 仍然只按第 2 级显示，不给成功态。
 */
export function describeOwnership(monitor: any): OwnershipView {
  const m = monitor || {};
  const price = m.buybox_price;
  if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) {
    return { tone: 'neutral', label: '未采集', explain: '还没有采到 Buybox 报价，所以无法判断本店是否在持位。' };
  }
  const state = m.price_ownership?.state;
  // 第 1 级：卖家名已核实。`matched` 和 `mismatch` 两个态都可能是核实过的，
  // 差别只在结论方向，所以先按 identity_verified 分流，再落到下面的价格推断。
  if (m.price_ownership?.identity_verified === true) {
    if (state === 'matched') {
      return {
        tone: 'success',
        label: '已核实持位',
        explain: '页面 "Sold by" 的卖家就是本店（按卖家名比对），不是靠同价推断，所以这里是已确认在持位。',
      };
    }
    if (state === 'mismatch') {
      return {
        tone: 'neutral',
        label: '不在持位 · 已核实',
        explain: '页面 "Sold by" 是别家卖家，所以哪怕价格和本店一样，也判定本店不在持位。',
      };
    }
  }
  if (state && OWNERSHIP_COPY[state]) return OWNERSHIP_COPY[state];
  if (m.holding_buybox === true || m.price_matches_buybox) {
    return {
      tone: 'warning',
      label: '同价 · 卖家未核实',
      explain: '本店售价与 Buybox 价相同，但这是价格推断，没有卖家身份证据，不能据此认定本店在持位。',
    };
  }
  if (m.holding_buybox === false) {
    return { tone: 'neutral', label: '不在持位', explain: 'Buybox 价和本店售价不同，本店不在持位。' };
  }
  return { tone: 'neutral', label: '待核对卖家', explain: '已有报价，但还没能判断本店是否在持位。' };
}

export interface FleetView {
  tone: Tone;
  /** 一句话讲清现在什么情况，页头直接显示。 */
  summary: string;
  /** 建议动作，空串表示不需要用户动手。 */
  action: string;
  /** 可以立刻重试的行数：自身故障类，清掉退避是合理的。 */
  retryable: number;
  /**
   * 被网站验证/限流挡住的行数。这些行只能改间隔，不能立刻重试——
   * 否则界面一边劝"别反复点采集"，按钮自己就在反复点。
   */
  blocked: number;
  /** 被限流/被验证阻挡时建议调大的间隔，null 表示不建议改。 */
  suggestedInterval: number | null;
  /** 这一轮没刷新、但手上报价仍可用的行数。是提示，不是故障。 */
  refreshing: number;
}

/** 被这些原因挡住时，加长间隔是唯一能在前端做的减损动作。 */
const THROTTLED = new Set(['site_challenge', 'rate_limited', 'http_forbidden']);

/**
 * 被挡住时建议把间隔拉到的档位（分钟）。
 *
 * 与调价页 `AutoPricing` 的间隔下拉（5/10/15/30/60/120）最慢一档一致——
 * 建议一个下拉里选不到的值，用户照着做都做不到。
 */
const THROTTLE_INTERVAL_MINUTES = 120;

/**
 * 这个错误码是不是「被网站挡住」（只能等 / 加长间隔，不能立刻重试）。
 *
 * 后端 `services/buybox_monitor.THROTTLED_CODES` 是同一组码，前端只在这一处判。
 * 之前调价页自己又抄了一份字面量给「重试」用，两处一旦不一致就会出现
 * 「状态条劝你把间隔调长、重试按钮却在清 next_due 立刻再打一次」——
 * 而这恰恰是页面正文在劝用户别做的事。
 */
export function isThrottled(code?: string): boolean {
  return THROTTLED.has(code || '');
}

/**
 * 采集失败的行：启用中的，或用户点过「立即采集」的一次性请求。
 *
 * 状态条按这份集合数「几个失败」，重试按钮也必须按同一份集合去重试。
 * 之前重试只看 `enabled`，而「立即采集」失败的行 `enabled` 是 false
 * （`request_once` 才是它的开关），于是状态条写着「重试 1 个」，
 * 点下去一条请求都不发——按钮看起来像坏了，而且没有任何提示。
 */
export function failingRows(rows: any[]): any[] {
  return (Array.isArray(rows) ? rows : []).filter(
    r => r?.monitor?.status === 'error' && (r?.monitor?.enabled || r?.monitor?.request_once)
  );
}

export interface RetryGroup {
  items: any[];
  enabled: boolean;
  /** 一次性请求的行重试后仍然只是一次，不能顺手打开连续采集。 */
  request_once: boolean;
  /** 被网站挡住的行保留退避，只加长间隔。 */
  keep_backoff: boolean;
}

/**
 * 把失败行分成可以直接提交的批次。
 *
 * 两条轴都不能省：被挡住的行只加长间隔、保留退避，不能立刻再打一次；
 * 一次性请求的行重试后仍然只是一次，不能把用户没要求开启的连续采集打开。
 */
export function retryGroups(rows: any[]): RetryGroup[] {
  const groups = new Map<string, RetryGroup>();
  for (const r of failingRows(rows)) {
    const enabled = !!r?.monitor?.enabled;
    const keep_backoff = isThrottled(r?.monitor?.error_code);
    const key = `${keep_backoff ? 'blocked' : 'retry'}|${enabled ? 'auto' : 'once'}`;
    const group = groups.get(key) || { items: [], enabled, request_once: !enabled, keep_backoff };
    group.items.push(r);
    groups.set(key, group);
  }
  return [...groups.values()];
}

/**
 * 页头一句话状态。
 *
 * 之前这里是六行列表，信息全但没人看——用户的原话是「不需要那么多东西显示出来」。
 * 所以只保留「什么坏了 / 为什么 / 该怎么办」三件事，其余交给表格行。
 */
export function describeFleet(rows: any[], ctx: CollectionContext): FleetView {
  const list = Array.isArray(rows) ? rows : [];
  const total = list.length;
  const source = ctx.collectorSource || 'cloud_browser';
  const isCloud = source === 'cloud_browser';
  const monitors = list.map(r => r?.monitor || {});
  const auto = monitors.filter(m => m.enabled).length;
  const running = monitors.filter(m => m.status === 'running').length;
  const queued = monitors.filter(m => m.status === 'queued').length;
  // 失败只算「启用中、或点过立即采集、而且手上没有可用报价」的行。
  // 停采的行不该因为一条旧错误把整页刷红（用户已经把它关了）；
  // 有报价只是这一轮没刷新的行也不算失败（旧价还能用）。
  const failedRows = failingRows(list);
  const errored = failedRows.filter(r => !hasUsableQuote(r?.monitor)).map(r => r?.monitor || {});
  const failing = errored.length;
  const refreshing = failedRows.length - failing;
  const blocked = failedRows.filter(
    r => !hasUsableQuote(r?.monitor) && isThrottled(r?.monitor?.error_code)
  ).length;
  const retryable = failing - blocked;
  const newest = monitors.reduce((max, m) => {
    const at = Date.parse(m.last_success_at || '');
    return Number.isFinite(at) && at > max ? at : max;
  }, 0);

  const base = { retryable, blocked, suggestedInterval: null as number | null, refreshing };
  const none = (tone: Tone, summary: string, action = ''): FleetView => ({ ...base, tone, summary, action });

  if (ctx.configured === false) return none('danger', '卖家接口没配置，采集无法开始', '到「设置」完成卖家接口配置');
  if (!total) return none('neutral', '没有可采集的 SKU', '先在库存页同步一次');

  // 失败原因取众数：一屏 30 行里通常是同一个原因，说一次就够。
  const counts: Record<string, number> = {};
  for (const m of errored) {
    const code = m.error_code || 'unknown';
    counts[code] = (counts[code] || 0) + 1;
  }
  const dominant = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
  const reason = dominant && dominant !== 'unknown' ? errorCopy(dominant).title : '采集未取得报价';
  const throttled = dominant ? isThrottled(dominant) : false;
  const interval = Math.max(1, Number(monitors.find(m => m.interval_minutes)?.interval_minutes) || 5);
  // 「把间隔调到 120 分钟」只在**还能调**的时候说。间隔已经是 120 之后，状态条
  // 原来还在重复同一句，而按钮靠 suggestedInterval 才出现（此时是 null）——
  // 用户看到一条永远做不完的指令，点不到任何东西，只会以为按钮坏了。
  const canWiden = throttled && interval < THROTTLE_INTERVAL_MINUTES;
  const suggestedInterval = canWiden ? THROTTLE_INTERVAL_MINUTES : null;

  const advice = !throttled
    ? ''
    : canWiden
      ? isCloud
        ? `启动本机采集器，或把间隔调到 ${THROTTLE_INTERVAL_MINUTES} 分钟`
        : `把间隔调到 ${THROTTLE_INTERVAL_MINUTES} 分钟`
      : isCloud
        // 已经是最慢档：能做的只有等，或者换一台机器/在维护工具里人工过验证。
        ? `间隔已是 ${interval} 分钟（最慢档），等网站验证解除；也可以启动本机采集器`
        : `间隔已是 ${interval} 分钟（最慢档），等网站验证解除`;

  if (running) return none('info', `正在采集 ${running} 个 SKU`, '');
  if (failing && failing === total) {
    return { ...base, suggestedInterval, tone: 'danger', summary: `全部 ${total} 个 SKU 采集失败 · ${reason}`, action: advice || '看下方失败详情' };
  }
  if (failing && !auto) {
    return { ...base, suggestedInterval, tone: 'danger', summary: `${failing} 个 SKU 采集失败 · ${reason}`, action: advice || '处理原因后点「自动」' };
  }
  if (queued && !isCloud) {
    return none('warning', `${queued} 个 SKU 等${collectorName(source)}领取`, `确认${collectorName(source)}正在运行`);
  }
  if (failing) {
    return { ...base, suggestedInterval, tone: 'warning', summary: `${failing} 个 SKU 采集失败 · ${reason}`, action: advice };
  }
  if (queued) return none('info', `${queued} 个 SKU 排队中，马上执行`, '');
  if (auto) {
    const when = newest ? ` · 最近 ${humanAge(ctx.now - newest)}更新` : '';
    // 有行这一轮没刷新、但报价还在：说清"报价还能用，会自动重试"，
    // 不要用失败的红字——用户没有需要动手的事。
    if (refreshing) {
      return none('warning', `${auto} 个 SKU 自动采集中${when} · ${refreshing} 个待刷新`, '报价仍可继续使用，下一轮会自动重试');
    }
    return none('success', `${auto} 个 SKU 自动采集中${when}`, '');
  }
  return none('neutral', '自动采集未开启', '选中 SKU 后点「开启采集」');
}
