import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO
from urllib.error import HTTPError
import pytest

spec=importlib.util.spec_from_file_location('native_runner',Path(__file__).resolve().parents[2]/'tools/native-collector/collector.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def test_native_runner_reuses_retained_pricepilot_core():
    core=runner.parser_module()
    assert Path(core.__file__).name=='pricepilot_collector.py'
    assert core._norm_label(' ✓ Blue ')=='blue'
    assert not runner.valid_product_url('https://example.com/PLID123')
    with pytest.raises(ValueError): runner.Api('https://example.com','test')

def test_initial_document_gets_one_passive_wait_without_retry():
    core=runner.parser_module()
    class Page:
        waits=0
        resolved=False
        async def title(self):return 'A product | takealot.com' if self.resolved else 'Just a moment...'
        async def wait_for_function(self,*args,**kwargs):
            self.waits+=1;self.resolved=True
    page=Page()
    assert asyncio.run(core.wait_initial_document(page)) is True and page.waits==1
    class Blocked(Page):
        async def wait_for_function(self,*args,**kwargs):
            self.waits+=1;raise TimeoutError()
    page=Blocked()
    assert asyncio.run(core.wait_initial_document(page)) is None and page.waits==1
    page=Page();page.resolved=True
    assert asyncio.run(core.wait_initial_document(page)) is False and page.waits==0

def test_native_report_is_per_item_and_no_write_endpoints(monkeypatch):
    calls=[]
    item={'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/x/PLID1'}
    class Api:
        def call(self,path,body=None):
            calls.append((path,body))
            if path.endswith('/products'): return {'items':[item]}
            return {'processed':1}
    async def scrape(*args): return {**item,'buyboxPrice':100,'collectedAt':'2026-10-14T00:00:00+00:00'}
    monkeypatch.setattr(runner,'scrape',scrape)
    assert asyncio.run(runner.round_once(None,Api(),None))==(1,0,0)
    assert [p for p,b in calls]==['/api/collector/products','/api/collector/offers']
    assert len(calls[1][1]['items'])==1

def test_failed_report_releases_unfinished_lease(monkeypatch):
    calls=[]
    class Api:
        def call(self,path,body=None):
            calls.append((path,body))
            if path.endswith('/products'): return {'items':[{'productId':'lease','sku':'SKU1'}]}
            if path.endswith('/offers'): raise RuntimeError('network unavailable')
            return {'released':1}
    async def scrape(*args): return {'productId':'lease','sku':'SKU1','error':'price_not_found'}
    monkeypatch.setattr(runner,'scrape',scrape)
    assert asyncio.run(runner.round_once(None,Api(),None))==(1,0,0)
    assert calls[-1]==('/api/collector/release',{'product_ids':['lease']})

def test_native_wrapper_preserves_quotes_but_never_verifies_identity():
    closed=[]
    class Page:
        async def close(self): closed.append(True)
    class Context:
        async def new_page(self): return Page()
    async def parse(page,item): return {'productId':'lease','sku':'SKU1','buyboxPrice':100,
        'holdingBuybox':True,'holdingBuyboxVerified':True,'offers':[{'price':100,'isMine':True}]}
    result=asyncio.run(runner.scrape(Context(),{'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/x/PLID1'},SimpleNamespace(scrape_product=parse)))
    assert result['buyboxPrice']==100 and result['collectedAt']
    assert not result['holdingBuybox'] and 'holdingBuyboxVerified' not in result
    assert result['offers'][0]['isMine'] and closed==[True]

def test_native_challenge_diagnostics_are_reported_without_session_data():
    class Page:
        url='https://www.takealot.com/p/PLID1?token=private'
        def on(self,name,callback):
            callback(SimpleNamespace(status=403,request=SimpleNamespace(resource_type='document')))
        async def title(self):return 'Just a moment...'
        def locator(self,selector):return self
        async def count(self):return 0
        async def close(self):pass
    class Context:
        async def new_page(self):return Page()
    async def parse(*args):return {'error':'site_challenge','buyboxPrice':None}
    result=asyncio.run(runner.scrape(Context(),{'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/p/PLID1'},SimpleNamespace(scrape_product=parse)))
    assert result['diagnostics']['http_status']==403
    assert result['diagnostics']['page_title']=='Just a moment...'
    assert 'private' not in str(result['diagnostics'])

def test_native_diagnostics_include_variant_control_count():
    class Page:
        url='https://www.takealot.com/p/PLID1'
        def on(self,name,callback): pass
        async def title(self):return 'Product | takealot.com'
        def locator(self,selector):return self
        async def count(self):return 1
        async def close(self):pass
    class Context:
        async def new_page(self):return Page()
    async def parse(page,item): return {'productId':'lease','sku':'SKU1','buyboxPrice':100,'variantControls':2,
        'collectedAt':'2026-10-14T00:00:00+00:00'}
    result=asyncio.run(runner.scrape(Context(),{'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/p/PLID1'},SimpleNamespace(scrape_product=parse)))
    assert result['diagnostics']['variant_controls']==2
    async def parse_without_controls(page,item): return {'productId':'lease','sku':'SKU1','buyboxPrice':100,
        'collectedAt':'2026-10-14T00:00:00+00:00'}
    result=asyncio.run(runner.scrape(Context(),{'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/p/PLID1'},SimpleNamespace(scrape_product=parse_without_controls)))
    assert 'variant_controls' not in result['diagnostics']


@pytest.mark.parametrize('body,expected',[
    (b'{"detail":"store-missing"}','请求被拒绝'),
    ('{"detail":"店铺不存在或已停用"}'.encode(),'店铺不存在或已停用'),
    (b'<html>reflected-test-secret</html>','请求被拒绝')])
def test_http400_reports_safe_details_only(body,expected):
    api=runner.Api(runner.BASE,'test')
    class Opener:
        def open(self,*args,**kwargs): raise HTTPError('https://example.invalid',400,'Bad Request',{},BytesIO(body))
    api.opener=Opener()
    with pytest.raises(RuntimeError) as exc: api.call('/api/collector/status')
    assert expected in str(exc.value) and 'reflected-test-secret' not in str(exc.value)


def test_failed_preflight_never_launches_browser(monkeypatch,tmp_path):
    import playwright.async_api
    monkeypatch.setenv('PP_COLLECTOR_KEY','test')
    def fail(*args,**kwargs): raise RuntimeError('服务接口返回 HTTP 400')
    monkeypatch.setattr(runner.Api,'call',fail)
    monkeypatch.setattr(playwright.async_api,'async_playwright',lambda:(_ for _ in ()).throw(AssertionError('must not launch browser')))
    with pytest.raises(RuntimeError): asyncio.run(runner.run(SimpleNamespace(profile=str(tmp_path/'profile'),login=False)))
    assert not (tmp_path/'profile').exists()


def test_desktop_pause_cancels_capture_and_releases_lease(monkeypatch):
    calls=[]
    class Api:
        def call(self,path,body=None):
            calls.append((path,body))
            if path.endswith('/products'):return {'items':[{'productId':'lease','sku':'SKU1'}]}
            return {'released':1}
    async def scenario():
        started=asyncio.Event()
        async def blocked(*args):
            started.set();await asyncio.Future()
        monkeypatch.setattr(runner,'scrape',blocked)
        task=asyncio.create_task(runner.round_once(None,Api(),None))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(scenario())
    assert calls[-1]==('/api/collector/release',{'product_ids':['lease']})
    assert not any(path.endswith('/offers') for path,body in calls)

