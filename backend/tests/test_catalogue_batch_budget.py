"""目录批读等待：隔离Event，不访问真实配置或上游。"""
from threading import Event, Thread

import pytest

from services import takealot_catalogue_api as api

WINNER = {'endpoint':'catalogue_by_id','auth':'header_x_api_key'}


@pytest.mark.parametrize('status', [200,401,403,429])
def test_catalogue_batch_returns_on_budget_and_retains_late_result(monkeypatch, status):
    release, entered, exited = Event(), Event(), Event()
    calls, errors, results = [], [], []
    monkeypatch.setattr(api, 'CATALOGUE_BATCH_WAIT_SECONDS', 0.02, raising=False)
    monkeypatch.setattr(api, '_active_catalogue_read', None, raising=False)
    def fetch(plid, key, winner, **kwargs):
        calls.append(plid)
        entered.set()
        assert release.wait(2)
        if status != 200: raise api.CatalogueAccessDenied(status)
        return {'length_mm':100,'width_mm':20,'height_mm':10,'weight_kg':0.3}
    monkeypatch.setattr(api, 'fetch_dims', fetch)
    def read():
        try: results.append(api.fetch_dims_many(['123','123'], 'fixture-key', WINNER))
        except BaseException as error: errors.append(error)
        finally: exited.set()
    thread = Thread(target=read)
    thread.start()
    try:
        assert entered.wait(1)
        assert exited.wait(0.3), 'catalogue caller waited past budget'
        assert len(errors) == 1 and isinstance(errors[0], api.CatalogueReadPending)
        with pytest.raises(api.CatalogueReadPending):
            api.fetch_dims_many(['123'], 'fixture-key', WINNER)
        with pytest.raises(api.CatalogueReadPending):
            api.fetch_dims_many(['456'], 'fixture-key', WINNER)
        assert calls == ['123']
    finally:
        release.set()
        thread.join(1)
        active = getattr(api, '_active_catalogue_read', None)
        if active:
            try: active['future'].result(timeout=1)
            except api.CatalogueAccessDenied: pass
    if status == 200:
        result = api.fetch_dims_many(['123'], 'fixture-key', WINNER)
        assert result['123']['length_mm'] == 100
    else:
        # 不同批也须先报告尚未交付的迟到拒绝，不得发456。
        with pytest.raises(api.CatalogueAccessDenied) as error:
            api.fetch_dims_many(['456'], 'fixture-key', WINNER)
        assert error.value.status == status
    assert calls == ['123']


def test_invalid_catalogue_batch_does_not_dispatch(monkeypatch):
    monkeypatch.setattr(api, 'fetch_dims', lambda *a, **k: pytest.fail('invalid connection dispatched'))
    assert api.fetch_dims_many(['123'], 'fixture', {'endpoint':'evil','auth':'bearer'}) == {}
    assert api.fetch_dims_many(['not-a-plid'], 'fixture', WINNER) == {}



def test_catalogue_batch_stops_next_send_at_budget_and_keeps_completed_results(monkeypatch):
    now, calls = [100.0], []
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(api, '_active_catalogue_read', None)
    monkeypatch.setattr(api, 'CATALOGUE_BATCH_WAIT_SECONDS', 15.0)
    def fetch(plid, key, winner, **kwargs):
        calls.append((plid, kwargs['timeout']))
        now[0] += 8
        return {'length_mm':100}
    monkeypatch.setattr(api, 'fetch_dims', fetch)
    results = api.fetch_dims_many(['123','456','789'], 'fixture', WINNER)
    assert calls == [('123',15.0),('456',7.0)]
    assert set(results) == {'123','456'}
    assert '789' not in results
