"""Target-lane reachability and symmetric observation windows; no platform calls."""
from unittest.mock import patch
from services import automatic_pricing as pricing

RULE = dict(min_price=100, max_price=500, strategy='takealot_buybox',
            lead_amount=1, max_daily_changes=10, cooldown_minutes=10,
            reference_price=120)


def offer(price, lane='fastest', **extra):
    return dict(price=price, offerType=lane, status='in_stock', isMine=False, **extra)


def test_unaffordable_lane_leader_cannot_be_discarded_to_justify_a_discount():
    for strategy in ('takealot_buybox', 'follow_lowest'):
        result = pricing.plan(120, 50, {**RULE, 'strategy': strategy},
                              {'offers': [offer(90), offer(115)]})
        assert result['target'] is None, result
        assert result['cheapest'] == 90


def test_unreachable_fastest_does_not_block_a_distinct_best_price_lane():
    result = pricing.plan(120, 50, RULE,
                          {'offers': [offer(90), offer(115, 'best_price')]})
    assert result['target'] == 114
    assert result['target_lane'] == 'best_price'
    assert '待新行情确认' in result['reason']


def test_missing_lane_types_do_not_invent_a_second_opportunity():
    result = pricing.plan(120, 50, RULE,
                          {'offers': [offer(90, ''), offer(115, '')]})
    assert result['target'] is None


def test_each_lane_uses_its_actual_leader_and_respects_the_floor():
    result = pricing.plan(120, 50, RULE,
                          {'offers': [offer(90), offer(115),
                                      offer(95, 'best_price'), offer(118, 'best_price')]})
    assert result['target'] is None
    # A target exactly at the floor is feasible; a lead below it is not.
    assert pricing.plan(120, 50, RULE, {'offers': [offer(101)]})['target'] == 102
    assert pricing.plan(120, 50, RULE, {'offers': [offer(100)]})['target'] is None


def test_cooldown_applies_after_a_discount_even_when_still_losing():
    with patch.object(pricing.time, 'time', return_value=10000):
        result = pricing.plan(120, 50, {**RULE, 'last_changed_epoch': 9990},
                              {'offers': [offer(110)]})
        assert result['target'] is None
        assert '冷却' in result['reason']
        # At the exact observation boundary a new decision can be considered.
        result = pricing.plan(120, 50, {**RULE, 'last_changed_epoch': 9400},
                              {'offers': [offer(110)]})
        assert result['target'] == 109


def test_hold_and_bounded_progress_are_not_reported_as_a_won_position():
    result = pricing.plan(300, 50, {**RULE, 'reference_price': 300},
                          {'offers': [offer(200)]})
    assert result['target'] == 255
    assert '分步' in result['reason'] and '待新行情确认' in result['reason']
    assert result['target_lane'] == 'fastest'
    assert pricing.plan(120, 0, RULE, {'offers': [offer(110)]})['target'] is None
    assert pricing.plan(120, 50, RULE, {'error': 'variant_failed'})['target'] is None
