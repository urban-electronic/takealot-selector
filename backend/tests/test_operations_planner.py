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
    assert r['sea_quantity'] > r['air_quantity'] > 0
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
    with pytest.raises(ValueError): Profile(**{**profile(), 'air_head_days': 60})
    a = {'procurement_id': 'a', 'mode': 'air', 'eta': '2026-10-28'}
    with pytest.raises(ValueError): Profile(**{**profile(), 'allocations': [a, a]})

def test_rolling_sea_schedule_dates_and_bridge_cover():
    p = {**profile(), 'air_days': 17, 'sea_days': 58}
    r = plan(p, [], TODAY)
    batches = r['schedule']; sea = [b for b in batches if b['mode'] == 'sea']
    assert batches[0]['arrival_date'] == '2026-10-30'
    assert sea[0]['arrival_date'] == '2026-12-10'
    assert sea[0]['coverage_until'] == '2027-02-14'
    assert sea[1]['purchase_date'] == '2026-11-12'
    assert sea[1]['arrival_date'] == '2027-01-09'
    assert sea[0]['quantity'] > r['air_quantity']
    assert all(b['mode'] == 'sea' for b in batches[1:])
    assert all(b['conditional'] for b in sea[1:])
    assert r['sea_share'] == round(sum(b['quantity'] for b in sea) / sum(b['quantity'] for b in batches), 3)
    assert r['sea_share'] > .75 and r['provisional']
    assert all(point['balance'] >= 0 for point in r['timeline'])

def test_procurement_time_moves_departure_and_arrival_not_purchase():
    r = plan({**profile(), 'procurement_days': 5}, [], TODAY)
    air = r['schedule'][0]
    assert air['purchase_date'] == '2026-10-13'
    assert air['departure_date'] == '2026-10-18'
    assert air['arrival_date'] == '2026-11-02'

def test_stock_and_confirmed_sea_cover_future_batches_without_duplicate_orders():
    p = {**profile(), 'stock': 1000}
    r = plan(p, [], TODAY)
    assert r['schedule'] == [] and r['air_quantity'] == r['sea_quantity'] == 0
    p['stock'] = 10
    p['allocations'] = [{'procurement_id':'s', 'mode':'sea', 'eta':'2026-11-27'}]
    r = plan(p, [{'id':'s', 'status':'in_transit', 'quantity':1000}], TODAY)
    assert not any(b['mode']=='sea' for b in r['schedule'])
    assert r['air_quantity'] > 0

def test_zero_sales_and_unprofitable_modes_have_no_future_schedule():
    p = profile(); p['sales'] = []
    assert plan(p, [], TODAY)['schedule'] == []
    e = {'available':True, 'minimum_margin':.15, 'air':{'profit_margin':0}, 'sea':{'profit_margin':0}}
    assert plan(profile(), [], TODAY, e)['schedule'] == []

def test_sea_coverage_must_cover_air_and_departure_cycle():
    with pytest.raises(ValueError):
        Profile(**{**profile(), 'sea_coverage_days':17, 'cycle_days':30})
