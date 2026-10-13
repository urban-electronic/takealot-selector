"""Use Takealot's existing cost engine for transport-specific business profit."""
from services.product_calculator import calculate_all

def logistics(profile):
    p = dict(profile)
    p.setdefault('air_head_days', 14); p.setdefault('sea_head_days', 55)
    p.setdefault('tail_days', 3); p.setdefault('minimum_margin', .15)
    p.setdefault('dispatch_policy', 'balanced'); p.setdefault('air_share_cap', .2); p.setdefault('first_sea_share', .75)
    p.setdefault('sea_split_days', 15); p.setdefault('batch_quantity', None)
    p.setdefault('allow_emergency_air', True); p.setdefault('emergency_air_days', 14)
    p.setdefault('sea_coverage_days', 60); p.setdefault('planning_days', 120); p.setdefault('procurement_days', 0)
    p['air_days'] = p['air_head_days'] + p['tail_days']
    p['sea_days'] = p['sea_head_days'] + p['tail_days']
    return p

def economics(product, profile):
    data = {c.name: getattr(product, c.name) for c in product.__table__.columns}
    price = profile.get('current_price') or product.actual_sale_price_zar
    data['actual_sale_price_zar'] = price
    # A single manual transport/total quote cannot honestly stand for both modes.
    if data.get('manual_total_cost_zar') is not None or data.get('manual_international_shipping_cny') is not None:
        return {'available': False, 'reason': '存在手动运费或总成本，需补充空海分别报价后再比较利润'}
    battery = '带电' in (product.shipping_method or '')
    results = {}
    for mode, name in [('air', '空运'), ('sea', '海运')]:
        estimate = calculate_all({**data, 'shipping_method': name + ('带电' if battery else '普货')}, product.exchange_rate_used or .41)
        if estimate.get('profit_margin') is None:
            return {'available': False, 'reason': '采购、尺寸重量或费率资料不完整，暂不能评估空海利润'}
        results[mode] = {k: estimate.get(k) for k in ['profit_zar', 'profit_margin', 'total_cost_zar', 'international_shipping_per_unit_cny']}
    return {'available': True, 'price': price, 'minimum_margin': profile.get('minimum_margin', .15), **results}

def profitable_plan(profile, purchases, product):
    from services.operations_planner import plan
    p = logistics(profile); e = economics(product, p)
    result = plan(p, purchases, economics=e)
    result['economics'] = e
    result['air_days'] = p['air_days']; result['sea_days'] = p['sea_days']
    return result


def system_floor(product):
    import math
    if product.minimum_price_at_20_margin and product.minimum_price_at_20_margin > 0:
        return math.ceil(product.minimum_price_at_20_margin)
    data = {c.name: getattr(product, c.name) for c in product.__table__.columns}
    def margin(price):
        return calculate_all({**data, 'actual_sale_price_zar': price}, product.exchange_rate_used or .41).get('profit_margin')
    if margin(1000000) is None or margin(1000000) < .20: return None
    low, high = 1, 1000000
    while low < high:
        mid = (low + high) // 2
        if margin(mid) >= .20: high = mid
        else: low = mid + 1
    return low
