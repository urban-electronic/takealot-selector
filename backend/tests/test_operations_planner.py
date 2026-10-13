from datetime import date, timedelta
from services.operations_planner import plan
from api.operations_routes import Profile
import pytest

TODAY = date(2026, 10, 13)
PROFITABLE = {'available': True, 'minimum_margin': .15,
              'air': {'profit_margin': .2, 'total_cost_zar': 100},
              'sea': {'profit_margin': .4, 'total_cost_zar': 80}}

def profile(**kwargs):
    return {**dict(stock=10, stock_date='2026-10-13', observed_from='2026-10-11',
                   sales=[{'date': '2026-10-12', 'quantity': 5}], air_days=15, sea_days=45,
                   cycle_days=30, safety_days=7, allocations=[]), **kwargs}

def test_new_product_uses_three_day_observation_and_defaults_to_sea_only():
    r = plan(profile(), [], TODAY, PROFITABLE)
    assert r['velocity'] == 1.667
    assert r['air_quantity'] == 0 and r['sea_quantity'] > 0
    assert r['pre_air_shortage'] == 15 and r['pre_sea_shortage'] == 65
    assert r['shortage_windows'] == [{'start_date':'2026-10-19', 'end_date':'2026-11-26', 'quantity':65}]
    assert all(b['mode'] == 'sea' for b in r['schedule'])

def test_purchased_air_sea_hide_only_covered_reminders():
    p = profile(allow_emergency_air=True); r = plan(p, [], TODAY, PROFITABLE)
    p['allocations'] = [{'procurement_id':'a','mode':'air','eta':'2026-10-28'},
                        {'procurement_id':'s','mode':'sea','eta':'2026-11-27'}]
    purchases = [{'id':'a','status':'in_transit','quantity':r['air_quantity']},
                 {'id':'s','status':'in_transit','quantity':r['sea_quantity']}]
    result = plan(p, purchases, TODAY, PROFITABLE)
    assert result['air_quantity'] == result['sea_quantity'] == 0
    assert result['pre_sea_shortage'] > 0  # limited air never pretends to fill the whole bridge
    purchases[0]['status'] = 'cancelled'
    assert plan(p, purchases, TODAY, PROFITABLE)['air_quantity'] > 0

def test_future_sea_cannot_cover_early_gap_or_turn_air_on():
    p = profile(allocations=[{'procurement_id':'s','mode':'sea','eta':'2026-11-27'}])
    r = plan(p, [{'id':'s','status':'in_transit','quantity':1000}], TODAY)
    assert r['air_quantity'] == r['sea_quantity'] == 0 and r['pre_sea_shortage'] == 65

def test_overdue_not_assumed_in_stock():
    p = profile(allocations=[{'procurement_id':'a','mode':'air','eta':'2026-10-12'}])
    r = plan(p, [{'id':'a','status':'in_transit','quantity':1000}], TODAY)
    assert r['overdue'] == ['a'] and r['pre_sea_shortage'] > 0 and r['sea_quantity'] > 0

def test_stock_snapshot_does_not_double_count_sales_or_receipts():
    p = profile(allocations=[{'procurement_id':'a','mode':'air','eta':'2026-10-13','included_in_stock':True}])
    r = plan(p, [{'id':'a','status':'received','quantity':1000}], TODAY)
    assert r['stock'] == 10 and r['pending']['air'] == 0

def test_zero_sales_no_purchase_or_shortage():
    r = plan(profile(sales=[]), [], TODAY)
    assert r['air_quantity'] == r['sea_quantity'] == 0 and r['shortage_windows'] == []

def test_unmapped_purchase_not_silently_assigned():
    r = plan(profile(), [{'id':'a','status':'in_transit','quantity':1000}], TODAY)
    assert r['unmapped'] == ['a'] and r['sea_quantity'] > 0

def test_validation_rejects_reversed_lead_time_and_duplicate_allocation():
    with pytest.raises(ValueError): Profile(**profile(air_head_days=60))
    a = {'procurement_id':'a','mode':'air','eta':'2026-10-28'}
    with pytest.raises(ValueError): Profile(**profile(allocations=[a,a]))
    with pytest.raises(ValueError): Profile(**profile(emergency_air_days=15))
    assert Profile(**profile()).allow_emergency_air is False

def test_multiple_sea_batches_continuously_cover_demand_after_first_arrival():
    p = profile(air_days=17, sea_days=58)
    r = plan(p, [], TODAY, PROFITABLE)
    sea = r['schedule']
    assert len(sea) == 4 and all(b['mode']=='sea' for b in sea)
    assert sea[0]['arrival_date'] == '2026-12-10'
    assert sea[0]['coverage_until'] == '2027-02-14'
    assert sea[1]['purchase_date'] == '2026-11-12'
    assert sea[1]['arrival_date'] == '2027-01-09'
    assert all(b['conditional'] for b in sea[1:])
    assert all(sea[i+1]['arrival_date'] <= sea[i]['coverage_until'] for i in range(len(sea)-1))
    assert r['sea_share'] == 1 and r['provisional']
    assert all(w['end_date'] < sea[0]['arrival_date'] for w in r['shortage_windows'])
    # Independently simulate every day through the last target coverage date.
    arrivals={(date.fromisoformat(b['arrival_date'])-TODAY).days:b['quantity'] for b in sea}
    balance=p['stock']; v=5/3
    for day in range((date.fromisoformat(sea[-1]['coverage_until'])-TODAY).days+1):
        available=balance+arrivals.get(day,0)
        if day >= 58: assert available+1e-8 >= v
        balance=max(0,available-v)

def test_procurement_time_moves_departure_and_arrival_not_purchase():
    r = plan(profile(procurement_days=5), [], TODAY)
    sea = r['schedule'][0]
    assert sea['purchase_date']=='2026-10-13' and sea['departure_date']=='2026-10-18'
    assert sea['arrival_date']=='2026-12-02'

def test_stock_and_confirmed_sea_cover_future_batches_without_duplicate_orders():
    assert plan(profile(stock=1000), [], TODAY)['schedule'] == []
    p=profile(allocations=[{'procurement_id':'s','mode':'sea','eta':'2026-11-27'}])
    r=plan(p,[{'id':'s','status':'in_transit','quantity':1000}],TODAY)
    assert r['schedule']==[] and r['pre_sea_shortage']>0

def test_unprofitable_modes_have_no_future_schedule():
    e={'available':True,'minimum_margin':.15,'air':{'profit_margin':0},'sea':{'profit_margin':0}}
    assert plan(profile(allow_emergency_air=True),[],TODAY,e)['schedule']==[]

def test_sea_coverage_must_cover_air_and_departure_cycle():
    with pytest.raises(ValueError): Profile(**profile(sea_coverage_days=17,cycle_days=30))

def test_opt_in_air_is_limited_and_unknown_profit_does_not_generate_air():
    p=profile(allow_emergency_air=True)
    r=plan(p,[],TODAY,PROFITABLE)
    assert r['air_quantity']==12 < r['air_gap_need']
    assert r['pre_sea_shortage']==53
    assert all(b['mode']=='sea' for b in r['schedule'][1:])
    unknown=plan(p,[],TODAY,{'available':False})
    assert unknown['air_quantity']==0 and unknown['blocked']

def test_partial_confirmed_air_is_deducted_from_emergency_budget():
    p=profile(allow_emergency_air=True,allocations=[{'procurement_id':'a','mode':'air','eta':'2026-10-28'}])
    r=plan(p,[{'id':'a','status':'in_transit','quantity':8}],TODAY,PROFITABLE)
    assert r['air_quantity']==4 and r['emergency_air_budget_remaining']==4

def test_existing_air_still_counts_when_new_air_is_disabled():
    p=profile(allocations=[{'procurement_id':'a','mode':'air','eta':'2026-10-28'}])
    r=plan(p,[{'id':'a','status':'in_transit','quantity':50}],TODAY)
    assert r['air_quantity']==0 and r['pending']['air']==50
    assert r['pre_sea_shortage']==15
