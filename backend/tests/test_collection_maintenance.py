"""采集记录的自愈行为。

用例直接照抄 2026-10-18 线上那 3 条失败记录的字段形状，避免测试和现实脱节。
"""
import time
from datetime import datetime, timezone

import pytest

from services.collection_maintenance import (
    MAX_BACKOFF_SECONDS,
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
    record = challenge_record()
    record['enabled'] = True
    fixed, actions = repair_monitor(record, now=1791912000)
    assert fixed['error_code'] == 'site_challenge'
    assert '验证' in fixed['error'] or '网站' in fixed['error']
    assert any('price_not_found' in a and 'site_challenge' in a for a in actions)


def test_paused_row_never_schedules_collection_during_repair():
    now = 1791912000.0
    fixed, actions = repair_monitor(challenge_record(), now=now)
    assert fixed['request_once'] is False
    assert 'repair_attempted_at' not in fixed
    assert fixed['status'] == 'paused'
    assert not any('修复性重采' in a for a in actions)


def test_repair_preserves_explicit_one_shot_request():
    record = challenge_record()
    record['request_once'] = True
    fixed, _ = repair_monitor(record, now=1791912000)
    assert fixed['request_once'] is True
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


def test_repair_never_grants_a_one_shot_collection():
    """自愈只改记录，任何时间窗内都不许替用户开采集（COLLECT-BOUNDARY-01）。

    这条以前叫「修复性重采限速」，但重采授权已经在 COLLECT-BOUNDARY-01 里
    移除，冷却期也就没有意义了；断言保留下来盯住「不自作主张排采」这个不变量。
    """
    now = 1791912000.0
    record = challenge_record()
    record['repair_attempted_at'] = now - 6 * 3600 + 60     # 上一次修复尝试就在 6 小时内
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
    record['enabled'] = True
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
    record['enabled'] = True
    fixed, actions = repair_monitor(record, now=1791912000)
    assert fixed['error_code'] == 'price_not_found'
    assert not any('错误码' in a for a in actions)


# ---------------------------------------------------------------- 同轮重试

def test_transient_failure_is_retried_within_the_same_round(monkeypatch):
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def flaky(product, own, seller_name=None):
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

    async def slow_render(product, own, seller_name=None):
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

    async def unmatched(product, own, seller_name=None):
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

    async def challenged(product, own, seller_name=None):
        calls.append(1)
        return {'buyboxPrice': None, 'error': 'site_challenge',
                'diagnostics': {'http_status': 403, 'buybox_cards': 0, 'price_nodes': 0,
                                'page_title': 'Just a moment...'}}

    monkeypatch.setattr(monitor, 'collect', challenged)
    monitor._collect_with_retry(object(), {}, {})
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [
    {'error': 'site_challenge'},
    {'diagnostics': {'page_title': 'Just a moment...'}},
    {'diagnostics': {'http_status': 403}},
    {'diagnostics': {'http_status': 429}},
    {'variantSwitchFailed': True},
    {'error': 'variant_unmatched'},
])
def test_block_and_identity_signals_override_empty_page_retry(payload):
    from services.buybox_monitor import _should_retry_collect
    snapshot = {'error': 'price_not_found', **payload}
    snapshot['diagnostics'] = {'buybox_cards': 0, 'price_nodes': 0,
                               **payload.get('diagnostics', {})}
    assert _should_retry_collect(snapshot) is False


def test_exception_is_raised_after_the_last_attempt(monkeypatch):
    import services.buybox_monitor as monitor

    monkeypatch.setattr(monitor.time, 'sleep', lambda seconds: None)
    calls = []

    async def broken(product, own, seller_name=None):
        calls.append(1)
        raise RuntimeError('browser gone')

    monkeypatch.setattr(monitor, 'collect', broken)
    with pytest.raises(RuntimeError):
        monitor._collect_with_retry(object(), {}, {})
    assert len(calls) == monitor.COLLECT_ATTEMPTS
