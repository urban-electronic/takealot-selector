# -*- coding: utf-8 -*-
"""兰特汇率：解析、缓存、降级。

这层的底线只有一条：**界面永远算得出兰特**。
汇率接口挂了、返回垃圾、或者从没成功拉过，都不能让参考价那一列变成空白——
空白看起来像「这款没有参考价」，而实际只是汇率没取到。
所以这里重点钉住降级链路：旧缓存 → 内置兜底，且降级必须带标记。

全部用例零真实出站：`fetch` 由注入的函数替代。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import SystemSettings
from services import fx_rates

UPSTREAM = {
    'result': 'success',
    'base_code': 'ZAR',
    'time_last_update_unix': 1792540952,  # 2026-10-21T00:02:32Z
    'rates': {'ZAR': 1, 'SGD': 0.077915, 'CNY': 0.408948, 'USD': 0.060986},
}


def _db(tmp_path, name='fx'):
    engine = create_engine(f'sqlite:///{tmp_path}/{name}.db', connect_args={'timeout': 30})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _cached(Session, **overrides):
    payload = {
        'base': 'ZAR',
        'rates': {'SGD': 12.0, 'CNY': 2.3},
        'at': '2026-10-20',
        'src': 'open.er-api.com',
        'fetched_at': (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat(),
    }
    payload.update(overrides)
    db = Session()
    db.add(SystemSettings(key=fx_rates.CACHE_KEY, value=json.dumps(payload)))
    db.commit()
    db.close()


def _boom():
    raise RuntimeError('上游挂了')


# ---- 解析 ---------------------------------------------------------------

def test_rates_are_inverted_from_upstream():
    """上游给「1 ZAR = 0.0779 SGD」，我们要的是「1 SGD = 12.83 ZAR」。"""
    rates = fx_rates.parse_rates(UPSTREAM)
    assert rates['SGD'] == pytest.approx(12.8345, abs=1e-4)
    assert rates['CNY'] == pytest.approx(2.4453, abs=1e-4)


def test_parses_source_date():
    """`at` 用上游的发布日（2026-10-21T00:02:32Z），不是我们拉取的时刻。"""
    assert fx_rates._date_of(UPSTREAM['time_last_update_unix']) == '2026-10-21'


def test_broken_source_timestamp_does_not_crash():
    assert fx_rates._date_of('abc')
    assert fx_rates._date_of(None)


@pytest.mark.parametrize('payload', [
    None, [], 'x', 123,
])
def test_rejects_non_object(payload):
    with pytest.raises(ValueError):
        fx_rates.parse_rates(payload)


def test_rejects_other_base_currency():
    with pytest.raises(ValueError):
        fx_rates.parse_rates({**UPSTREAM, 'base_code': 'USD'})


def test_rejects_failed_result():
    with pytest.raises(ValueError):
        fx_rates.parse_rates({**UPSTREAM, 'result': 'error', 'error-type': 'quota'})


def test_rejects_missing_currency():
    with pytest.raises(ValueError):
        fx_rates.parse_rates({**UPSTREAM, 'rates': {'ZAR': 1, 'SGD': 0.077915}})


@pytest.mark.parametrize('bad', [0, -1, 'abc', None, True, float('inf'), float('-inf'), float('nan')])
def test_rejects_bad_rate(bad):
    with pytest.raises(ValueError):
        fx_rates.parse_rates({**UPSTREAM, 'rates': {'ZAR': 1, 'SGD': bad, 'CNY': 0.408948}})


# ---- 缓存 ---------------------------------------------------------------

def test_fresh_cache_is_used_without_calling_upstream(tmp_path):
    Session = _db(tmp_path)
    _cached(Session)
    db = Session()
    calls = []
    result = fx_rates.get_rates(db, fetch=lambda: calls.append(1) or {'rates': {'SGD': 99.0, 'CNY': 99.0}})
    assert calls == []
    assert result['rates']['SGD'] == 12.0
    assert result['stale'] is False


def test_expired_cache_triggers_fetch_and_overwrites(tmp_path):
    Session = _db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(microsecond=0).isoformat()
    _cached(Session, fetched_at=old)
    db = Session()
    result = fx_rates.get_rates(db, fetch=lambda: {
        'base': 'ZAR', 'rates': {'SGD': 12.8345, 'CNY': 2.4453},
        'at': '2026-10-21', 'src': 'open.er-api.com',
        'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    })
    assert result['rates']['SGD'] == 12.8345
    assert result['stale'] is False
    # 新值必须落盘，否则每个请求都会重拉一次
    row = db.query(SystemSettings).filter(SystemSettings.key == fx_rates.CACHE_KEY).first()
    assert json.loads(row.value)['rates']['SGD'] == 12.8345


def test_force_ignores_fresh_cache(tmp_path):
    Session = _db(tmp_path)
    _cached(Session)
    db = Session()
    result = fx_rates.get_rates(db, force=True, fetch=lambda: {
        'base': 'ZAR', 'rates': {'SGD': 13.0, 'CNY': 2.5},
        'at': '2026-10-21', 'src': 'open.er-api.com',
        'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    })
    assert result['rates']['SGD'] == 13.0


def test_broken_timestamp_counts_as_expired():
    assert fx_rates.is_fresh({'fetched_at': '昨天'}) is False
    assert fx_rates.is_fresh({'fetched_at': ''}) is False
    assert fx_rates.is_fresh(None) is False


# ---- 降级 ---------------------------------------------------------------

def test_failed_fetch_falls_back_to_stale_cache(tmp_path):
    Session = _db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(microsecond=0).isoformat()
    _cached(Session, fetched_at=old)
    db = Session()
    result = fx_rates.get_rates(db, fetch=_boom)
    assert result['rates']['SGD'] == 12.0, '拉不到时要用旧汇率，不能清空'
    assert result['stale'] is True
    assert 'RuntimeError' in (result.get('error') or '')


def test_failed_fetch_without_cache_uses_builtin_fallback(tmp_path):
    Session = _db(tmp_path)
    db = Session()
    result = fx_rates.get_rates(db, fetch=_boom)
    assert result['fallback'] is True
    assert result['rates']['SGD'] == fx_rates.FALLBACK_RATES['SGD']
    assert result['rates']['CNY'] == fx_rates.FALLBACK_RATES['CNY']
    assert '兜底' in (result['src'] or '')


def test_fallback_is_not_written_to_cache(tmp_path):
    """兜底值是常量，不是「拉到的结果」；写进缓存会让下次误以为有 24 小时的有效数据。"""
    Session = _db(tmp_path)
    db = Session()
    fx_rates.get_rates(db, fetch=_boom)
    assert db.query(SystemSettings).filter(SystemSettings.key == fx_rates.CACHE_KEY).first() is None


def test_garbage_cache_is_ignored(tmp_path):
    Session = _db(tmp_path)
    db = Session()
    db.add(SystemSettings(key=fx_rates.CACHE_KEY, value='not-json'))
    db.commit()
    result = fx_rates.get_rates(db, fetch=lambda: {
        'base': 'ZAR', 'rates': {'SGD': 12.8345, 'CNY': 2.4453},
        'at': '2026-10-21', 'src': 'open.er-api.com',
        'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    })
    assert result['rates']['SGD'] == 12.8345
