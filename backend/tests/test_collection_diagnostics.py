from services.collection_diagnostics import classify,safe_url

def test_diagnostics_never_store_query_tokens():
    assert safe_url('https://www.takealot.com/x/PLID1?token=private#section')=='https://www.takealot.com/x/PLID1'

def test_bare_forbidden_is_not_called_captcha():
    assert classify({'diagnostics':{'http_status':403}})[0]=='http_forbidden'
    assert classify({'diagnostics':{'http_status':403,'block_reason':'verify you are human'}})[0]=='site_challenge'

def test_normal_product_slug_is_not_mistaken_for_homepage_redirect():
    d={'requested_url':'https://www.takealot.com/vacuum-bag/PLID123','final_url':'https://www.takealot.com/vacuum-bag/PLID123','http_status':200}
    assert classify({'error':'blocked_or_load_failed','diagnostics':d})[0]=='price_not_found'
    d['final_url']='https://www.takealot.com/'
    assert classify({'diagnostics':d})[0]=='product_redirect'

def test_variant_failure_and_navigation_exceptions_are_distinct():
    assert classify({'variantSwitchFailed':True,'buyboxPrice':100})[0]=='variant_unmatched'
    assert classify({'error':'Error: net::ERR_CERT_AUTHORITY_INVALID'})[0]=='tls_error'
    assert classify({'error':'NameError: parser missing'})[0]=='collector_error'
    assert classify({'error':'TimeoutError: Page.goto exceeded'})[0]=='navigation_timeout'

def test_variant_failure_reports_real_control_count_when_known():
    code,message=classify({'variantSwitchFailed':True,'diagnostics':{'variant_controls':3}})
    assert code=='variant_unmatched' and '3 个变体控件' in message
    code,message=classify({'variantSwitchFailed':True,'diagnostics':{'variant_controls':0}})
    assert code=='variant_unmatched' and message=='未确认SKU对应变体，不使用默认变体报价'

def test_success_http_status_with_just_a_moment_title_is_a_challenge_not_product_html():
    data={'http_status':200,'page_title':'Just a moment...','buybox_cards':0}
    assert classify({'diagnostics':data,'error':'blocked_or_load_failed'})[0]=='site_challenge'

def test_element_counts_separate_dead_anchor_from_absent_buybox():
    # 有价格、没 Buybox 区块：页面是商品页，是我们的锚点失效。
    d={'http_status':200,'page_title':'Product','buybox_cards':0,'price_nodes':4}
    code,message=classify({'diagnostics':d})
    assert code=='page_structure_changed' and '4 处价格' in message
    # 两者都 0：落地页根本不是商品页（验证页/跳转页）。
    d={'http_status':200,'page_title':'','buybox_cards':0,'price_nodes':0}
    assert classify({'diagnostics':d})[0]=='page_not_recognized'
    # 区块存在但价格没读出来：仍走原来的 price_not_found，不能误判成改版。
    d={'http_status':200,'buybox_cards':2,'price_nodes':4}
    assert classify({'diagnostics':d})[0]=='price_not_found'
    # 没统计过（旧记录）时保持原判，不凭缺失字段下结论。
    assert classify({'diagnostics':{'http_status':200}})[0]=='price_not_found'

def test_structural_failure_is_not_treated_as_site_throttling():
    from services.buybox_monitor import THROTTLED_CODES
    assert 'page_structure_changed' not in THROTTLED_CODES
    assert 'page_not_recognized' not in THROTTLED_CODES

def test_collector_stops_on_challenge_before_selectors_or_variant_attempts():
    import asyncio
    from types import SimpleNamespace
    from services.pricepilot_collector import scrape_product
    class Page:
        async def goto(self,*args,**kwargs):return SimpleNamespace(status=200)
        async def title(self):return 'Just a moment...'
        # Any selector/variant operation would fail: challenge is an early return.
    result=asyncio.run(scrape_product(Page(),{'url':'https://www.takealot.com/p/PLID1','name':'Product - Variant'}))
    assert result['error']=='site_challenge' and result['buyboxPrice'] is None
