from models import Product
from services.replenishment_economics import economics, logistics
from services.operations_planner import plan
from tests.test_operations_planner import profile, TODAY

def test_new_logistics_defaults_conservatively_include_tail_delivery():
    p = logistics(profile())
    assert p['air_days'] == 17 and p['sea_days'] == 58
    assert logistics({**profile(), 'tail_days': 2})['sea_days'] == 57

def test_existing_cost_engine_shows_sea_profit_advantage_without_mutation():
    p = Product(actual_sale_price_zar=200, purchase_cost_cny=40, purchase_shipping_cny=1,
                purchase_quantity=4, length_mm=100, width_mm=100, height_mm=100, actual_weight_kg=.2,
                shipping_method='空运普货', exchange_rate_used=.41, success_fee_rate=.15,
                packaging_cost_per_unit_cny=1, inbound_listing_fee_cny=.75,
                outbound_operation_fee_cny=.7, last_mile_delivery_fee_cny=2, other_fee_cny=2)
    e = economics(p, {})
    assert e['available'] and e['sea']['profit_zar'] > e['air']['profit_zar']
    assert p.shipping_method == '空运普货'

def test_air_below_margin_threshold_does_not_get_bulk_replenishment():
    e = {'available': True, 'minimum_margin': .15, 'air': {'profit_margin': .05}, 'sea': {'profit_margin': .30}}
    r = plan({**profile(), 'allow_emergency_air': True, 'dispatch_policy':'balanced'}, [], TODAY, economics=e)
    assert r['air_quantity'] == 0 and r['sea_quantity'] > 0 and r['blocked']

def test_both_modes_unprofitable_pause_purchase_suggestions():
    e = {'available': True, 'minimum_margin': .15, 'air': {'profit_margin': -.1}, 'sea': {'profit_margin': .05}}
    r = plan({**profile(), 'allow_emergency_air': True, 'dispatch_policy':'balanced'}, [], TODAY, economics=e)
    assert r['air_quantity'] == r['sea_quantity'] == 0
