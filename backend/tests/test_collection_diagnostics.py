from services.collection_diagnostics import classify,safe_url

def test_display_boundary_hides_legacy_exception_text_without_losing_counts():
    import json
    from services.collection_diagnostics import displayed_monitor
    original={'status':'error','error':'采集器不可用（浏览器或接口未就绪）；未改价',
              'error_code':'collector_unavailable','diagnostics':{
                  'stage':'public_page','failure':'RuntimeError','price_nodes':4,
                  'failure_message':'/private/profile token=fixture-private'},
              'snapshot':{'diagnostics':{'failure_message':'fixture-private','buybox_cards':2}}}
    shown=displayed_monitor(original)
    assert 'fixture-private' not in json.dumps(shown)
    assert shown['diagnostics']['failure']=='RuntimeError'
    assert shown['diagnostics']['price_nodes']==4
    assert shown['snapshot']['diagnostics']['buybox_cards']==2
    assert original['diagnostics']['failure_message'].endswith('fixture-private')

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


def test_safe_url_removes_credentials_and_rejects_malformed():
    assert safe_url('https://user:fixture-private@www.takealot.com/p/PLID1?token=x#x')=='https://www.takealot.com/p/PLID1'
    assert safe_url('https://[broken')==''


def test_safe_diagnostics_keeps_new_measurements_not_free_text():
    from services.collection_diagnostics import safe_diagnostics
    value=safe_diagnostics({'price_nodes':4,'diag_version':3,'buybox_card_struck':1,
        'cookie':'fixture-private','failure_message':'fixture-private',
        'http_status':True,'buybox_cards':float('nan'),'debug':['fixture-private']})
    assert value=={'price_nodes':4,'diag_version':3,'buybox_card_struck':1}


def test_safe_diagnostics_keeps_which_variant_dimension_failed():
    """清洗不能把 classify() 要读的键洗掉。

    转发采集器（/api/collector/offers）和历史记录重判都先过 safe_diagnostics，
    再交给 classify()。清洗白名单里没有 variant_unmatched 时，那句
    「未能切到变体 Nude / L」永远出不来，只剩「未确认SKU对应变体」。
    """
    from services.collection_diagnostics import classify, safe_diagnostics
    value=safe_diagnostics({'variant_controls':3,'variant_unmatched':['Nude','L'],
        'failure_message':'fixture-private','cookie':'fixture-private'})
    assert value=={'variant_controls':3,'variant_unmatched':['Nude','L']}
    code,message=classify({'variantSwitchFailed':True,'diagnostics':value})
    assert code=='variant_unmatched' and 'Nude / L' in message


def test_safe_diagnostics_bounds_variant_labels_and_rejects_other_types():
    from services.collection_diagnostics import safe_diagnostics
    value=safe_diagnostics({'variant_unmatched':[1,None,'','x'*80]+['d']*8})
    labels=value['variant_unmatched']
    assert len(labels)==6 and all(isinstance(item,str) and len(item)<=40 for item in labels)
    # 不是列表就不是「维度名」，一律丢掉，不把任意文本当规格写进库。
    assert safe_diagnostics({'variant_unmatched':'Nude'})=={}
    assert safe_diagnostics({'variant_unmatched':['']})=={}
