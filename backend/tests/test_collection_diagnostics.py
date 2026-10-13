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
