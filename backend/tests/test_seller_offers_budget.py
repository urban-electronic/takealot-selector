"""分页等待只用MockTransport和Event；不连真实接口或配置。"""
from threading import Event, Thread

import httpx
import pytest

from services import takealot_seller_api as seller


@pytest.mark.parametrize('status', [200,401,403,429])
def test_slow_offer_read_returns_on_budget_and_reuses_inflight(monkeypatch, status):
    release, entered, exited = Event(), Event(), Event()
    monkeypatch.setattr(seller, 'OFFERS_READ_WAIT_SECONDS', 0.02, raising=False)
    monkeypatch.setattr(seller, '_cache', {})
    monkeypatch.setattr(seller, '_active_offer_read', None, raising=False)
    calls, errors, results = [], [], []
    original = httpx.Client
    def respond(request):
        calls.append(request.method)
        entered.set()
        assert release.wait(2)
        return httpx.Response(status, json={'items':[{'productline_id':123}]})
    monkeypatch.setattr(seller.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    def read():
        try: results.append(seller._load_offers('fixture-key'))
        except BaseException as error: errors.append(error)
        finally: exited.set()
    thread = Thread(target=read)
    thread.start()
    try:
        assert entered.wait(1)
        assert exited.wait(0.3), 'seller caller still waits for slow transport after budget'
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
        assert '到时' in str(errors[0]) and not results
        with pytest.raises(ValueError, match='到时'):
            seller._load_offers('fixture-key')
        assert calls == ['GET']  # 旧读取未退出，不能排第二次网络读取。
        with pytest.raises(ValueError, match='正忙'):
            seller._load_offers('different-fixture-key')
        assert calls == ['GET']
    finally:
        release.set()
        thread.join(1)
        active = getattr(seller, '_active_offer_read', None)
        if active:
            try: active[1].result(timeout=1)
            except ValueError: pass
    assert not thread.is_alive()
    if status == 200:
        assert seller._load_offers('fixture-key') == [{'productline_id':123}]
    else:
        with pytest.raises(ValueError, match=f'HTTP {status}'):
            seller._load_offers('fixture-key')
    assert calls == ['GET']  # 迟到成功只作真实缓存；迟到拒绝继续遵守冷却。


def test_pagination_stops_at_shared_budget_without_caching_partial_rows(monkeypatch):
    import hashlib
    now, sleeps, calls = [100.0], [], []
    monkeypatch.setattr(seller.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(seller, 'OFFERS_READ_WAIT_SECONDS', 25.0)
    monkeypatch.setattr(seller, '_active_offer_read', None)
    monkeypatch.setattr(seller, '_cache', {})
    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
    monkeypatch.setattr(seller.time, 'sleep', sleep)
    original = httpx.Client
    def respond(request):
        calls.append(request.extensions['timeout']['read'])
        now[0] += 8
        return httpx.Response(200, json={'items':[{'productline_id':123}], 'continuation_token':'fixture-next'})
    monkeypatch.setattr(seller.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(ValueError, match='到时'):
        seller._load_offers('fixture-pages')
    assert calls == [20.0,7.0]
    assert sleeps == [10.0]
    state = seller._cache[hashlib.sha256(b'fixture-pages').hexdigest()]
    assert state['rows'] == [] and state['expires'] == 0
    assert state['retry'] > now[0]
    assert not seller._lock.locked()


@pytest.mark.parametrize('status', [401,403,429])
def test_late_refusal_renews_cooldown_from_response_time(monkeypatch, status):
    import hashlib
    now, calls = [100.0], []
    monkeypatch.setattr(seller.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(seller, '_active_offer_read', None)
    monkeypatch.setattr(seller, '_cache', {})
    original = httpx.Client
    def respond(request):
        calls.append(request.method)
        now[0] = 500.0  # 响应晚于最初300秒冷却；不真实等待。
        return httpx.Response(status)
    monkeypatch.setattr(seller.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    for _ in range(2):
        with pytest.raises(ValueError, match=f'HTTP {status}'):
            seller._load_offers('fixture-late-refusal')
    assert calls == ['GET']
    state = seller._cache[hashlib.sha256(b'fixture-late-refusal').hexdigest()]
    assert state['retry'] == 500.0 + (1800 if status == 429 else 300)
