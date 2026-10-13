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
                   cycle_days=30, safety_days=7, allocations=[], emergency_air_days=7, adaptive_dispatch=False,
                   dispatch_policy='balanced' if kwargs.get('allow_emergency_air') else 'sea_only'), **kwargs}

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
    with pytest.raises(ValueError): Profile(**profile(emergency_air_days=31))
    assert Profile(**profile()).dispatch_policy == 'sea_only'

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

def test_balanced_hundred_units_can_dispatch_twenty_sixty_twenty():
    p=profile(dispatch_policy='balanced', allow_emergency_air=True, emergency_air_days=14,
              batch_quantity=100, stock=0, air_days=17, sea_days=58,
              sales=[{'date':'2026-10-12','quantity':6}])
    r=plan(p,[],TODAY,PROFITABLE)
    first=[b for b in r['schedule'] if b['batch_id']=='2026-10-13-0']
    assert [(b['mode'],b['quantity'])for b in first]==[('air',20),('sea',60),('sea',20)]
    assert first[1]['departure_date']=='2026-10-13'
    assert first[2]['purchase_date']=='2026-10-13'  # same purchase, retained before departure
    assert first[2]['departure_date']=='2026-10-28'
    assert first[2]['arrival_date']=='2026-12-25'
    assert r['initial_batch_quantity']==100 and r['initial_air_share']==.2
    assert r['pre_sea_shortage']>0  # twenty units cannot cover forty-one days at two/day

def test_balanced_reduces_air_when_stock_already_covers_early_demand():
    p=profile(dispatch_policy='balanced',allow_emergency_air=True,emergency_air_days=14,stock=100)
    r=plan(p,[],TODAY,PROFITABLE)
    assert r['air_quantity']==0
    assert r['sea_quantity']>0

def test_sea_split_is_advanced_if_deferral_would_cause_stockout():
    p=profile(dispatch_policy='balanced',allow_emergency_air=False,batch_quantity=25,
              stock=0,sea_split_days=15,sales=[{'date':'2026-10-12','quantity':6}])
    r=plan(p,[],TODAY,PROFITABLE)
    first=[b for b in r['schedule']if b['batch_id']=='2026-10-13-0']
    assert [(b['mode'],b['quantity'])for b in first]==[('sea',25)]
    assert r['shortage_windows']  # insufficient budget is shown, never assumed covered

def test_balanced_stock_and_air_can_cover_lead_time_while_sea_batches_take_over():
    p=profile(dispatch_policy='balanced',allow_emergency_air=True,emergency_air_days=14,
              batch_quantity=100,stock=96,air_days=17,sea_days=58,
              sales=[{'date':'2026-10-12','quantity':6}])
    r=plan(p,[],TODAY,PROFITABLE)
    assert r['air_quantity']==20 and r['pre_sea_shortage']==0 and r['shortage_windows']==[]
    by_day={}
    for b in r['schedule']:
        day=(date.fromisoformat(b['arrival_date'])-TODAY).days
        by_day[day]=by_day.get(day,0)+b['quantity']
    balance=96
    for day in range(58+90+67):
        balance+=by_day.get(day,0)
        assert balance >= 2
        balance-=2

def test_adaptive_air_ratio_responds_to_profit_instead_of_fixing_twenty_percent():
    p=profile(dispatch_policy='balanced',adaptive_dispatch=True,allow_emergency_air=True,
              emergency_air_days=30,batch_quantity=100,stock=0,air_days=17,sea_days=58,
              sales=[{'date':'2026-10-12','quantity':6}])
    low={**PROFITABLE,'air':{'profit_margin':.16,'total_cost_zar':100}}
    high={**PROFITABLE,'air':{'profit_margin':.36,'total_cost_zar':100}}
    a=plan(p,[],TODAY,low);b=plan(p,[],TODAY,high)
    assert 20<a['air_quantity']<b['air_quantity']<=40
    assert a['effective_air_share']<b['effective_air_share']
    assert sum(x['quantity']for x in b['schedule']if x['batch_id']=='2026-10-13-0')==100

def test_adaptive_small_sea_batch_moves_second_departure_earlier():
    p=profile(dispatch_policy='balanced',adaptive_dispatch=True,allow_emergency_air=False,
              batch_quantity=40,stock=0,sea_split_days=15,sales=[{'date':'2026-10-12','quantity':6}])
    r=plan(p,[],TODAY,PROFITABLE)
    parts=[b for b in r['schedule']if b['batch_id']=='2026-10-13-0']
    assert sum(b['quantity']for b in parts)==40
    later=next(b for b in parts if b['deferred_dispatch'])
    assert 0<(date.fromisoformat(later['departure_date'])-TODAY).days<15

def test_single_purchase_splits_are_counted_once_by_actual_arrival_date():
    p=profile(allocations=[{'procurement_id':'p','mode':'air','quantity':20,'eta':'2026-10-28'},
                           {'procurement_id':'p','mode':'sea','quantity':60,'eta':'2026-11-27'},
                           {'procurement_id':'p','mode':'sea','quantity':20,'eta':'2026-12-12'}])
    r=plan(p,[{'id':'p','status':'in_transit','quantity':100}],TODAY)
    assert r['pending']=={'air':20,'sea':80} and not r['unmapped']
    assert r['pre_sea_shortage']==45
    p['allocations'][0]['included_in_stock']=True
    assert plan(p,[{'id':'p','status':'in_transit','quantity':100}],TODAY)['pending']=={'air':0,'sea':80}

def test_stale_split_exceeding_purchase_quantity_does_not_inflate_stock():
    p=profile(allocations=[{'procurement_id':'p','mode':'air','quantity':60,'eta':'2026-10-28'},
                           {'procurement_id':'p','mode':'sea','quantity':60,'eta':'2026-11-27'}])
    r=plan(p,[{'id':'p','status':'in_transit','quantity':100}],TODAY)
    assert r['pending']=={'air':0,'sea':0} and r['unmapped']==['p']
