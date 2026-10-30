"""卖家读取锁：只用隔离线程/缓存，不读取配置或发网络请求。"""
from threading import Event, Lock, Thread

import pytest

from services import takealot_seller_api as seller


def test_busy_seller_read_returns_without_releasing_owner_lock(monkeypatch):
    lock = Lock()
    lock.acquire()
    monkeypatch.setattr(seller, '_lock', lock)
    monkeypatch.setattr(seller, 'OFFERS_LOCK_WAIT_SECONDS', 0.02, raising=False)
    cache = {}
    monkeypatch.setattr(seller, '_cache', cache)
    monkeypatch.setattr(seller.httpx, 'Client', lambda **kw: pytest.fail('busy waiter must not create transport'))
    started, exited = Event(), Event()
    errors = []
    def read():
        started.set()
        try:
            seller._load_offers('isolated-fixture-key')
        except BaseException as error:
            errors.append(error)
        finally:
            exited.set()
    thread = Thread(target=read)
    thread.start()
    try:
        assert started.wait(1)
        assert exited.wait(0.3), 'seller waiter did not respect bounded lock wait'
        assert lock.locked(), 'waiter released the owner lock'
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
        assert '正忙' in str(errors[0])
        assert cache == {}
    finally:
        lock.release()
        thread.join(1)
    assert not thread.is_alive()


def test_owned_seller_read_releases_lock_on_error_and_cached_success(monkeypatch):
    import hashlib
    lock = Lock()
    monkeypatch.setattr(seller, '_lock', lock)
    monkeypatch.setattr(seller, '_cache', {})
    key = 'isolated-fixture-key'
    def fail(**kw):
        raise RuntimeError('fixture transport construction failed')
    monkeypatch.setattr(seller.httpx, 'Client', fail)
    with pytest.raises(RuntimeError, match='fixture transport'):
        seller._load_offers(key)
    assert not lock.locked()
    rows = [{'productline_id':123}]
    seller._cache[hashlib.sha256(key.encode()).hexdigest()] = {'rows':rows,'expires':float('inf'),'retry':0}
    assert seller._load_offers(key) == rows
    assert not lock.locked()
