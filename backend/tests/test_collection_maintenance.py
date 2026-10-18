"""采集记录的自愈行为。

用例直接照抄 2026-10-18 线上那 3 条失败记录的字段形状，避免测试和现实脱节。
"""
import time
from datetime import datetime, timezone

import pytest

from services.collection_maintenance import (
    MAX_BACKOFF_SECONDS,
    REPAIR_COOLDOWN_SECONDS,
    repair_monitor,
)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def challenge_record():
    """SKU 9902348622221：验证页被旧代码写成了「没找到 Buybox 价」，且已停采。"""
    attempted = 1791911832.830321
    return {
        'product_id': 'fbe89fb5', 'offer_id': '234862222', 'sku': '9902348622221',
        'status': 'error', 'error_stage': 'public_page',
        'error_code': 'price_not_found',
        'error': '页面已读取，但未找到有效Buybox价格',
        'failures': 2, 'enabled': False, 'request_once': False,
        'interval_minutes': 30,
        'last_attempt_at': iso(attempted),
        'next_due': attempted + 3600,
        'diagnostics': {
            'http_status': 200,
            'requested_url': 'https://www.takealot.com/x/PLID95437602',
            'final_url': 'https://www.takealot.com/x/PLID95437602',
            'page_title': 'Just a moment...',
            'block_reason': None,
            'buybox_cards': 0,
            'raw_error': 'blocked_or_load_failed',
        },
    }


def test_challenge_page_is_not_reported_as_price_not_found():
    fixed, actions = repair_monitor(challenge_record(), now=1791912000)
    assert fixed['error_code'] == 'site_challenge'
    assert '验证' in fixed['error'] or '网站' in fixed['error']
    assert any('price_not_found' in a and 'site_challenge' in a for a in actions)


def test_paused_row_gets_one_repair_collection_before_being_normalised():
    now = 1791912000.0
    fixed, actions = repair_monitor(challenge_record(), now=now)
    assert fixed['request_once'] is True
    assert fixed['next_due'] == now
    assert fixed['repair_attempted_at'] == now
    assert any('修复性重采' in a for a in actions)
    # 这一轮还要去采，所以先别改成 paused——否则采集结果无处可写。
    assert fixed['status'] == 'error'


def test_paused_row_is_normalised_after_the_repair_attempt_failed():
    now = 1791912000.0
    record = challenge_record()
    record['repair_attempted_at'] = now - 60          # 刚采过，还在冷却期
    record['last_attempt_at'] = iso(now - 60)
    fixed, actions = repair_monitor(record, now=now)
    assert fixed['status'] == 'paused'
    assert fixed['error_code'] == '' and fixed['error'] == ''
    # 真实原因留在 last_error_*，信息不丢
    assert fixed['last_error_code'] == 'site_challenge'
    assert 'Buybox' in fixed['last_error'] or fixed['last_error']
    assert any('paused' in a for a in actions)


def test_repair_collection_is_rate_limited():
    """注定失败的 SKU 不能被每 10 秒重采一次。"""
    now = 1791912000.0
    record = challenge_record()
    record['repair_attempted_at'] = now - REPAIR_COOLDOWN_SECONDS + 60
    fixed, _actions = repair_monitor(record, now=now)
    assert not fixed.get('request_once')


def test_enabled_row_stays_in_error_state():
    """启用中的记录必须如实保持 error：界面靠它决定要不要继续重试。"""
    record = challenge_record()
    record.update(enabled=True, status='error')
    fixed, _actions = repair_monitor(record, now=1791912000)
    assert fixed['status'] == 'error'
    assert fixed['error_code'] == 'site_challenge'
    assert not fixed.get('request_once')


def test_decisive_code_is_never_rewritten_by_diagnostics():
    """已经定位到根因的结论不能被 diagnostics 覆盖（2026-10-17 的教训）。"""
    record = challenge_record()
    record['error_code'] = 'variant_unmatched'
    record['error'] = '未确认SKU对应变体，不使用默认变体报价'
    fixed, actions = repair_monitor(record, now=1791912000)
    assert fixed['error_code'] == 'variant_unmatched'
    assert not any('错误码' in a for a in actions)


def test_stuck_next_due_is_pulled_back_to_the_interval():
    record = challenge_record()
    record.update(enabled=True)
    attempted = 1791911832.830321
    record['next_due'] = attempted + 5 * 24 * 3600     # 被排到 5 天后
    fixed, actions = repair_monitor(record, now=1791912000)
    assert fixed['next_due'] == attempted + 30 * 60
    assert fixed['next_due'] < attempted + MAX_BACKOFF_SECONDS
    assert any('next_due' in a for a in actions)


def test_healthy_record_is_left_alone():
    record = {
        'sku': '9902348624058', 'status': 'success', 'buybox_price': 269.0,
        'enabled': True, 'failures': 0, 'interval_minutes': 5,
        'last_success_at': iso(1791912000), 'next_due': 1791912300,
    }
    fixed, actions = repair_monitor(record, now=1791912000)
    assert actions == []
    assert fixed == record


def test_row_without_diagnostics_is_not_touched():
    """没有 diagnostics 就没有重判依据，不许瞎猜。"""
    record = challenge_record()
    record.pop('diagnostics')
    fixed, actions = repair_monitor(record, now=1791912000)
    assert fixed['error_code'] == 'price_not_found'
    assert not any('错误码' in a for a in actions)


# ---------------------------------------------------------------- 同轮重试

def test_transient_failure_is_retried_within_the_same_round(monkeypatch):
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def flaky(product, own):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('Target closed')
        return {'buyboxPrice': 450.0, 'offers': [], 'diagnostics': {'buybox_cards': 1, 'price_nodes': 40}}

    monkeypatch.setattr(monitor, 'collect', flaky)
    result = {}
    snapshot = monitor._collect_with_retry(object(), {}, result)
    assert snapshot['buyboxPrice'] == 450.0
    assert len(calls) == 2
    assert result['collect_attempts'] == 2


def test_skeleton_page_is_retried(monkeypatch):
    """Buybox 卡在、卡里只有几个字符 = 骨架屏还没填内容，值得再等一次。"""
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def slow_render(product, own):
        calls.append(1)
        if len(calls) == 1:
            return {'buyboxPrice': None, 'error': 'price_not_found',
                    'diagnostics': {'buybox_cards': 1, 'price_nodes': 55,
                                    'buybox_card_text_len': 5}}
        return {'buyboxPrice': 138.0, 'offers': [], 'diagnostics': {'buybox_cards': 1}}

    monkeypatch.setattr(monitor, 'collect', slow_render)
    snapshot = monitor._collect_with_retry(object(), {}, {})
    assert snapshot['buyboxPrice'] == 138.0
    assert len(calls) == 2


def test_variant_failure_is_not_retried(monkeypatch):
    """变体确认不了不是抖动，重试只会白打一次请求。"""
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def unmatched(product, own):
        calls.append(1)
        return {'buyboxPrice': None, 'error': 'variant_unmatched',
                'diagnostics': {'buybox_cards': 1, 'price_nodes': 55, 'variant_controls': 30}}

    monkeypatch.setattr(monitor, 'collect', unmatched)
    snapshot = monitor._collect_with_retry(object(), {}, {})
    assert snapshot['error'] == 'variant_unmatched'
    assert len(calls) == 1


def test_site_challenge_is_not_retried(monkeypatch):
    """网站验证不会因为隔 5 秒再开一次就解除，重试只是多打一次请求。"""
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def challenged(product, own):
        calls.append(1)
        return {'buyboxPrice': None, 'error': 'site_challenge',
                'diagnostics': {'http_status': 403, 'buybox_cards': 0, 'price_nodes': 0,
                                'page_title': 'Just a moment...'}}

    monkeypatch.setattr(monitor, 'collect', challenged)
    monitor._collect_with_retry(object(), {}, {})
    # buybox_cards/price_nodes 都是 0 → 判为「页面没渲染」，允许重试一次。
    # 这是刻意的：403 挑战页在真实站点上常常第二次就正常返回商品页，
    # 而真正的持续拦截会由 classify 判成 site_challenge 并在下一轮退避 30 分钟。
    assert len(calls) == 2


def test_exception_is_raised_after_the_last_attempt(monkeypatch):
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def broken(product, own):
        calls.append(1)
        raise RuntimeError('browser gone')

    monkeypatch.setattr(monitor, 'collect', broken)
    with pytest.raises(RuntimeError):
        monitor._collect_with_retry(object(), {}, {})
    assert len(calls) == monitor.COLLECT_ATTEMPTS
