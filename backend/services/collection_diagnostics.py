"""Safe, structured diagnostics for public quote reads; never stores page content or tokens."""
from urllib.parse import urlsplit, urlunsplit
import re


def safe_url(url):
    p=urlsplit(url or '')
    return urlunsplit((p.scheme,p.netloc,p.path,'',''))


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
    if snapshot.get('variantSwitchFailed'): return 'variant_unmatched','未确认SKU对应变体，不使用默认变体报价'
    if 'CERT' in error.upper(): return 'tls_error','浏览器证书校验失败，未取得报价页面'
    if 'Timeout' in error or 'timeout' in error.lower(): return 'navigation_timeout','页面加载超时'
    if 'NameError' in error or 'TypeError' in error or 'ReferenceError' in error: return 'collector_error','采集程序运行异常，需修复解析器'
    if 'net::' in error: return 'network_error','浏览器连接商品页失败'
    requested=re.search(r'PLID\d+',d.get('requested_url',''),re.I)
    landed=re.search(r'PLID\d+',d.get('final_url',''),re.I)
    if requested and (not landed or requested.group().lower()!=landed.group().lower()): return 'product_redirect','页面跳转到其他地址，未确认目标商品'
    if error=='unstable_price': return 'unstable_price','多次读取的报价不一致，本轮不采信'
    if not snapshot.get('buyboxPrice'): return 'price_not_found','页面已读取，但未找到有效Buybox价格'
    if error: return 'parse_error','商品报价解析失败'
    return '', ''

def displayed_monitor(monitor):
    """Reclassify historical diagnostics consistently in BOTH collection/pricing views."""
    value=dict(monitor)
    if value.get('status')=='error' and value.get('diagnostics'):
        code,message=classify({'diagnostics':value['diagnostics'],'error':value['diagnostics'].get('raw_error')})
        if code: value.update(error_code=code,error=message)
    return value
