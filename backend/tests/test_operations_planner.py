from datetime import date
from services.operations_planner import plan
from api.operations_routes import Profile
import pytest

TODAY = date(2026, 10, 13)

def profile(**kwargs):
    return dict(stock=10, stock_date='2026-10-13', observed_from='2026-10-11',
                sales=[{'date': '2026-10-12', 'quantity': 5}], air_days=15, sea_days=45,
                cycle_days=30, safety_days=7, allocations=[], **kwargs)

def test_new_product_uses_three_day_observation():
    r = plan(profile(), [], TODAY)
    assert r['velocity'] == 1.667
    assert r['air_quantity'] > r['sea_quantity'] > 0
    assert r['pre_air_shortage'] == 15

def test_purchased_air_sea_hide_only_covered_reminders():
    p = profile(); r = plan(p, [], TODAY)
    p['allocations'] = [{'procurement_id': 'a', 'mode': 'air', 'eta': '2026-10-28'},
                        {'procurement_id': 's', 'mode': 'sea', 'eta': '2026-11-27'}]
    purchases = [{'id': 'a', 'status': 'in_transit', 'quantity': r['air_quantity']},
                 {'id': 's', 'status': 'in_transit', 'quantity': r['sea_quantity']}]
    result = plan(p, purchases, TODAY)
    assert result['air_quantity'] == result['sea_quantity'] == 0
    purchases[0]['status'] = 'cancelled'
    assert plan(p, purchases, TODAY)['air_quantity'] > 0

def test_future_sea_cannot_cover_early_air_gap():
    p = profile(); p['allocations'] = [{'procurement_id': 's', 'mode': 'sea', 'eta': '2026-11-27'}]
    r = plan(p, [{'id': 's', 'status': 'in_transit', 'quantity': 1000}], TODAY)
    assert r['air_quantity'] > 0 and r['sea_quantity'] == 0

def test_overdue_not_assumed_in_stock():
    p = profile(); p['allocations'] = [{'procurement_id': 'a', 'mode': 'air', 'eta': '2026-10-12'}]
    r = plan(p, [{'id': 'a', 'status': 'in_transit', 'quantity': 1000}], TODAY)
    assert r['overdue'] == ['a'] and r['air_quantity'] > 0

def test_stock_snapshot_does_not_double_count_sales_or_receipts():
    p = profile(); p['allocations'] = [{'procurement_id': 'a', 'mode': 'air', 'eta': '2026-10-13', 'included_in_stock': True}]
    r = plan(p, [{'id': 'a', 'status': 'received', 'quantity': 1000}], TODAY)
    assert r['stock'] == 10 and r['pending']['air'] == 0

def test_zero_sales_no_purchase():
    p = profile(); p['sales'] = []
    r = plan(p, [], TODAY)
    assert r['air_quantity'] == r['sea_quantity'] == 0

def test_unmapped_purchase_not_silently_assigned():
    r = plan(profile(), [{'id': 'a', 'status': 'in_transit', 'quantity': 1000}], TODAY)
    assert r['unmapped'] == ['a'] and r['air_quantity'] > 0

def test_validation_rejects_reversed_lead_time_and_duplicate_allocation():
    with pytest.raises(ValueError): Profile(**{**profile(), 'air_days': 60})
    a = {'procurement_id': 'a', 'mode': 'air', 'eta': '2026-10-28'}
    with pytest.raises(ValueError): Profile(**{**profile(), 'allocations': [a, a]})
