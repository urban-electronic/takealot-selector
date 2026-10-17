"""Safe, structured diagnostics for public quote reads; never stores page content or tokens."""
from urllib.parse import urlsplit, urlunsplit
import re


def safe_url(url):
    try:
        p=urlsplit(url or '')
        if p.scheme not in ('https','http') or not p.hostname: return ''
        host=p.hostname
        if ':' in host: host='['+host+']'
        if p.port: host+=':'+str(p.port)
        return urlunsplit((p.scheme,host,p.path,'',''))
    except (ValueError,TypeError):
        return ''


def safe_diagnostics(raw):
    """Preserve named measurements, never arbitrary collector text/credentials."""
    if not isinstance(raw,dict): return {}
    out={}
    counters=('http_status','buybox_cards','variant_controls','price_nodes',
              'diag_version','buybox_card_text_len','buybox_card_amounts','buybox_card_struck')
    for key in counters:
        value=raw.get(key)
        if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=1000000:
            out[key]=value
    for key in ('requested_url','final_url'):
        if isinstance(raw.get(key),str): out[key]=safe_url(raw[key])
    if isinstance(raw.get('page_title'),str): out['page_title']=raw['page_title'][:160]
    if raw.get('block_reason') in ('verify you are human','checking your browser',
            'automated traffic','unusual traffic','complete the captcha','challenge_page_title'):
        out['block_reason']=raw['block_reason']
    return out


def classify(snapshot):
    d=snapshot.get('diagnostics') or {};error=str(snapshot.get('error') or '')
    status=d.get('http_status');reason=d.get('block_reason')
    title=(d.get('page_title') or '').strip().lower()
    if title in ('just a moment...', 'just a moment', 'attention required! | cloudflare'): reason=reason or 'challenge_page_title'
    if reason or error=='site_challenge': return 'site_challenge','后台访问商品页时被网站验证阻挡，本轮未取得报价。当前会话尚未验证可用，重复点击采集不能保证恢复'
    if status==429: return 'rate_limited','网站返回429限流，稍后重试'
    if status in (401,403): return 'http_forbidden',f'网站返回HTTP {status}，本轮未取得报价页面'
    if status and status>=500: return 'site_unavailable',f'网站返回HTTP {status}，报价服务暂不可用'
    if status==404 or error=='404': return 'product_not_found','商品公开页不存在（404）'
    if error=='invalid_url': return 'invalid_url','商品链接不是有效的Takealot PLID地址'
    if snapshot.get('variantSwitchFailed'):
        controls=(snapshot.get('diagnostics') or {}).get('variant_controls')
        suffix=f'（页面检测到 {controls} 个变体控件，需人工确认SKU对应变体）' if isinstance(controls,int) and controls>0 else ''
        return 'variant_unmatched',f'未确认SKU对应变体，不使用默认变体报价{suffix}'
    if 'CERT' in error.upper(): return 'tls_error','浏览器证书校验失败，未取得报价页面'
    if 'Timeout' in error or 'timeout' in error.lower(): return 'navigation_timeout','页面加载超时'
    if 'NameError' in error or 'TypeError' in error or 'ReferenceError' in error: return 'collector_error','采集程序运行异常，需修复解析器'
    if 'net::' in error: return 'network_error','浏览器连接商品页失败'
    requested=re.search(r'PLID\d+',d.get('requested_url',''),re.I)
    landed=re.search(r'PLID\d+',d.get('final_url',''),re.I)
    if requested and (not landed or requested.group().lower()!=landed.group().lower()): return 'product_redirect','页面跳转到其他地址，未确认目标商品'
    if error=='unstable_price': return 'unstable_price','多次读取的报价不一致，本轮不采信'
    # 下面两条靠「数元素」区分三种长得一样的失败。原来一律报 price_not_found
    # （页面打开了但没有 Buybox 价），用户无法判断该等一等、还是该叫维护，只能反复点采集。
    #   buybox_cards>0                → 有区块，价格没读出来（落到最后的 parse_error）
    #   buybox_cards==0 且 price_nodes>0 → 商品页在，价格也在，只有 Buybox 锚点没命中 = 改版
    #   buybox_cards==0 且 price_nodes==0 → 什么都没认出来 = 落地页不是商品页
    cards=d.get('buybox_cards');prices=d.get('price_nodes')
    if isinstance(cards,int) and cards==0 and isinstance(prices,int):
        if prices>0:
            return 'page_structure_changed',f'页面能打开，也有 {prices} 处价格，但 Buybox 区块一个都没匹配到（0 个）。这是解析锚点失效，不是商品下架'
        return 'page_not_recognized','页面里没认出任何商品元素（0 处价格、0 个 Buybox 区块），落地页不像商品页'
    if not snapshot.get('buyboxPrice'): return 'price_not_found','页面已读取，但未找到有效Buybox价格'
    if error: return 'parse_error','商品报价解析失败'
    return '', ''

def displayed_monitor(monitor):
    """Reclassify historical diagnostics consistently in BOTH collection/pricing views."""
    value=dict(monitor)
    if value.get('status')=='error' and value.get('diagnostics'):
        # 只在库里的错误文案为空、或是那句通用兜底时才用诊断重判。
        # 原来无条件覆盖，会把真实异常改写成「未找到 Buybox 价」：
        # 2026-10-17 浏览器 TargetClosedError 起不来，界面却一直显示
        # 「页面打开了但没有 Buybox 价」，排查被误导了整整几轮。
        current=(value.get('error') or '').strip()
        if current in ('','采集或接口暂不可用；未改价'):
            code,message=classify({'diagnostics':value['diagnostics'],'error':value['diagnostics'].get('raw_error')})
            if code: value.update(error_code=code,error=message)
    return value
