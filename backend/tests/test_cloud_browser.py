import asyncio
from types import SimpleNamespace
import pytest
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from api import cloud_browser_routes as routes
from services.cloud_browser import CloudBrowser, allowed_request
from services.collection_diagnostics import displayed_monitor

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('API_KEY','test-site-key')
    app=FastAPI();app.include_router(routes.router)
    def store(request:Request):return request.headers.get('X-Store-ID','default-store')
    app.dependency_overrides[routes.get_store_id]=store
    return TestClient(app)

def test_browser_api_fails_closed_and_collector_key_cannot_control_it(client,monkeypatch):
    assert client.get('/api/buybox-browser/frame').status_code==401
    assert client.get('/api/buybox-browser/frame',headers={'X-Collector-Key':'test-site-key'}).status_code==401
    monkeypatch.delenv('API_KEY')
    assert client.get('/api/buybox-browser/frame',headers={'X-API-Key':'test-site-key'}).status_code==401

def test_other_store_and_invalid_input_do_not_touch_browser(client,monkeypatch):
    monkeypatch.setattr(routes,'browser',lambda:pytest.fail('must not create browser'))
    h={'X-API-Key':'test-site-key','X-Store-ID':'another-shop'}
    assert client.get('/api/buybox-browser/frame',headers=h).status_code==400
    h.pop('X-Store-ID')
    assert client.post('/api/buybox-browser/action',headers=h,json={'action':'click','x':1366}).status_code==422
    assert client.post('/api/buybox-browser/action',headers=h,json={'action':'key','key':'Control+L'}).status_code==422

def test_public_browser_cannot_navigate_to_private_hosts_or_purchase():
    for url in ['http://www.takealot.com/p/PLID1','https://127.0.0.1','https://evil.test','https://www.takealot.com/checkout','https://www.takealot.com/account']:
        assert not allowed_request(url,True)
    assert allowed_request('https://www.takealot.com/product/PLID123',True)
    assert allowed_request('https://challenges.cloudflare.com/frame',False)
    assert not allowed_request('https://api.takealot.com/cart/add',False)
    assert not allowed_request('https://127.0.0.1/api',False)
    assert not allowed_request('https://takealot-selector.railway.internal/api',False)

def test_historical_false_price_error_shown_as_challenge_without_mutating_record():
    old={'status':'error','error_code':'price_not_found','diagnostics':{'http_status':200,'page_title':'Just a moment...','raw_error':'blocked_or_load_failed'}}
    assert displayed_monitor(old)['error_code']=='site_challenge'
    assert old['error_code']=='price_not_found'

def test_real_error_message_is_not_overwritten_by_classification():
    # 浏览器起不来时，库里存的是「采集器不可用」；displayed_monitor 不能再把它
    # 改写成「未找到 Buybox 价」——2026-10-17 就是因为这个改写查错了方向。
    real={'status':'error','error':'采集器不可用（浏览器或接口未就绪）；未改价','error_code':'collector_unavailable',
          'diagnostics':{'stage':'public_page','failure':'TargetClosedError'}}
    shown=displayed_monitor(real)
    assert shown['error_code']=='collector_unavailable'
    assert shown['error'].startswith('采集器不可用')


class _FakeCtx:
    async def close(self): pass


class _FakePW:
    async def stop(self): pass


def test_launch_is_retried_once_when_profile_lock_is_stale(monkeypatch, tmp_path):
    import services.quote_browser as qb
    monkeypatch.setenv('BUYBOX_BROWSER_PROFILE', str(tmp_path / 'profile'))
    monkeypatch.setattr(qb, 'browser_display', lambda: None)
    attempts = []

    async def flaky(profile, display, args):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('Target page, context or browser has been closed')
        return _FakePW(), _FakeCtx()

    monkeypatch.setattr(qb, '_launch', flaky)

    async def check():
        async with qb.quote_context() as context:
            assert context is not None
    asyncio.run(check())
    assert len(attempts) == 2, '启动失败要重试一次，否则一次残留锁就让采集全线停摆'


def test_caller_errors_are_not_retried_by_quote_context(monkeypatch, tmp_path):
    # 关键回归：yield 之后的异常属于调用方（采集逻辑），不能触发重试，
    # 否则整轮采集会被悄悄重跑，可能重复抓页面甚至重复判定报价。
    import services.quote_browser as qb
    monkeypatch.setenv('BUYBOX_BROWSER_PROFILE', str(tmp_path / 'profile'))
    monkeypatch.setattr(qb, 'browser_display', lambda: None)
    calls = []

    async def ok(profile, display, args):
        calls.append('launch')
        return _FakePW(), _FakeCtx()

    monkeypatch.setattr(qb, '_launch', ok)

    async def check():
        with pytest.raises(ValueError):
            async with qb.quote_context():
                raise ValueError('collector bug')
    asyncio.run(check())
    assert calls == ['launch'], '调用方异常不得触发重试'

@pytest.mark.parametrize('token',[None,'wrong-session'])
def test_session_token_required_before_capture(token):
    async def check():
        obj=CloudBrowser.__new__(CloudBrowser);obj.guard=None;obj.token='real-session'
        import time
        obj.started=obj.touched=time.monotonic()
        with pytest.raises(Exception) as result:await obj.command('frame',token)
        assert result.value.status_code==410
    asyncio.run(check())

def test_one_profile_owner_and_release(monkeypatch):
    import services.quote_browser as qb
    events=[]
    class Page:
        main_frame=object()
        async def route(self,*args):pass
        async def goto(self,*args,**kwargs):events.append('opened')
        async def screenshot(self,**kwargs):return b'jpeg'
    class Manager:
        async def __aenter__(self):return SimpleNamespace(new_page=self.new_page,route=self.route)
        async def route(self,*args):pass
        async def new_page(self):return Page()
        async def __aexit__(self,*args):events.append('released')
    monkeypatch.setattr(qb,'quote_context',lambda wait:Manager())
    async def check():
        obj=CloudBrowser.__new__(CloudBrowser);obj.guard=None;obj.token=None;obj.context_manager=None;obj.page=None
        opened=await obj.command('open',url='https://www.takealot.com/p/PLID1')
        with pytest.raises(Exception) as result:await obj.command('open',url='https://www.takealot.com/p/PLID2')
        assert result.value.status_code==409
        assert await obj.command('frame',opened['session'])==b'jpeg'
        await obj.command('close',opened['session'])
        assert obj.token is None and events==['opened','released']
    asyncio.run(check())

def test_browser_frame_not_cached_and_token_only_in_header(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(routes,'browser',lambda:SimpleNamespace(run=lambda *a,**kw:calls.append((a,kw)) or b'jpeg'))
    result=client.get('/api/buybox-browser/frame',headers={'X-API-Key':'test-site-key','X-Browser-Session':'lease'})
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert calls[0][0]==('frame','lease')
