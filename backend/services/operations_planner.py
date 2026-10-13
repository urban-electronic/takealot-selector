"""Deterministic air/sea pipeline planning. No AI, network or database mutation."""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def plan(profile, purchases, today=None, economics=None):
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    observed = date.fromisoformat(profile['observed_from'])
    snapshot = date.fromisoformat(profile['stock_date'])
    elapsed = (today - snapshot).days
    sales = profile.get('sales', [])
    days = min(30, max(1, (today - observed).days + 1))
    def units(n):
        start = today.toordinal() - n + 1
        return sum(s['quantity'] for s in sales if start <= date.fromisoformat(s['date']).toordinal() <= today.toordinal())
    baseline = units(days) / days
    recent = units(min(3, days)) / min(3, days)
    week = units(min(7, days)) / min(7, days)
    velocity = max(baseline, week, recent)
    if days >= 14 and baseline > 0:
        velocity = min(velocity, baseline * 3)
    # Snapshot represents end-of-day stock; subtract subsequent real sales only.
    consumed = sum(s['quantity'] for s in sales if snapshot < date.fromisoformat(s['date']) <= today)
    stock = max(0, profile['stock'] - consumed)
    prep = profile.get('procurement_days', 0)
    air = profile['air_days'] + prep; sea = profile['sea_days'] + prep
    cycle = profile['cycle_days']; safety = profile['safety_days']
    coverage = max(profile.get('sea_coverage_days', 60), cycle, profile['air_days'])
    planning_days = profile.get('planning_days', 120)
    emergency_air = profile.get('allow_emergency_air', False)
    emergency_days = profile.get('emergency_air_days', 7)
    horizon = sea + ((planning_days - 1) // cycle) * cycle + coverage + safety
    latest_sale = max((date.fromisoformat(s['date']) for s in sales if date.fromisoformat(s['date']) <= today), default=None)
    common = dict(units_window=units(days), units_recent=units(min(7, days)), observed_days=days, days_since_last_sale=(today-latest_sale).days if latest_sale else None, trend='rising' if recent > baseline * 1.5 else 'falling' if baseline and recent < baseline * .5 else 'flat')
    arrivals = {}; pending_air_before_sea = 0
    overdue = []; pending = {'air': 0, 'sea': 0}; unmapped = []
    mapping = {p['procurement_id']: p for p in profile.get('allocations', [])}
    for purchase in purchases:
        if purchase['status'] == 'cancelled':
            continue
        allocation = mapping.get(purchase['id'])
        if not allocation:
            if purchase['status'] == 'in_transit': unmapped.append(purchase['id'])
            continue
        if allocation.get('included_in_stock'):
            continue
        eta = date.fromisoformat(allocation['eta'])
        offset = (eta - today).days
        if offset < 0:
            overdue.append(purchase['id'])
            continue  # overdue is not usable stock until confirmed by a new snapshot
        pending[allocation['mode']] += purchase['quantity']
        if allocation['mode'] == 'air' and offset < sea: pending_air_before_sea += purchase['quantity']
        arrivals[offset] = arrivals.get(offset, 0) + purchase['quantity']
    if velocity <= 0:
        return dict(air_quantity=0, sea_quantity=0, velocity=0, stock=stock, cover_days=None,
                    pending=pending, overdue=overdue, unmapped=unmapped, stale=elapsed > 3,
                    pre_air_shortage=0, next_review_days=cycle, timeline=[], schedule=[], sea_share=0, air_gap_need=0, pre_sea_shortage=0, shortage_windows=[],
                    allow_emergency_air=emergency_air, emergency_air_days=emergency_days, reason='观测期零成交，不自动补货', level='slow_moving' if stock and days >= 30 else 'no_data', daily_loss_zar=0, **common)
    def shortage_before(end):
        balance = stock; lost = 0
        for day in range(end):
            available = balance + arrivals.get(day, 0)
            lost += max(0, velocity - available)
            balance = max(0, available - velocity)
        return math.ceil(lost - 1e-9)
    pre_air = shortage_before(air)
    # Suggested sea stock is for demand after arrival. Lost earlier sales are
    # recorded as shortages; they are not added to later purchase quantities.
    def required_after(start, end, extra=None):
        balance = stock; worst = 0
        for day in range(end):
            balance += arrivals.get(day, 0) + (extra or {}).get(day, 0)
            balance -= velocity
            if day >= start: worst = max(worst, -balance)
            elif balance < 0: balance = 0  # lost sales before delivery are not backorders
        return max(0, math.ceil(worst - 1e-9))
    air_gap_need = required_after(air, sea)
    air_limit = math.ceil(velocity * emergency_days - 1e-9)
    air_budget = max(0, air_limit - pending_air_before_sea)
    air_qty = min(air_gap_need, air_budget) if emergency_air else 0
    blocked = []
    if air_qty and (not economics or not economics.get('available')):
        blocked.append('空运成本资料不完整，暂停应急空运建议')
        air_qty = 0
    elif air_qty and economics['air']['profit_margin'] < economics['minimum_margin']:
        blocked.append('空运利润率低于补货门槛，暂停应急空运建议')
        air_qty = 0
    sea_allowed = not (economics and economics.get('available') and economics['sea']['profit_margin'] < economics['minimum_margin'])
    planned = {air: air_qty} if air_qty else {}
    schedule = []
    def entry(mode, quantity, purchase_day, eta):
        departure = purchase_day + prep
        cost = economics[mode].get('total_cost_zar') if economics and economics.get('available') else None
        return dict(mode=mode, quantity=quantity,
                    purchase_date=(today + timedelta(days=purchase_day)).isoformat(),
                    departure_date=(today + timedelta(days=departure)).isoformat(),
                    arrival_date=(today + timedelta(days=eta)).isoformat(),
                    coverage_until=(today + timedelta(days=eta + (coverage + safety if mode == 'sea' else math.ceil(quantity / velocity)) - 1)).isoformat(),
                    conditional=purchase_day > 0, estimated_cost_zar=round(cost * quantity, 2) if cost is not None else None)
    if air_qty: schedule.append(entry('air', air_qty, 0, air))
    sea_qty = 0
    for purchase_day in range(0, planning_days, cycle):
        eta = sea + purchase_day
        quantity = required_after(eta, eta + coverage + safety, planned)
        if purchase_day == 0 and quantity and not sea_allowed:
            blocked.append('海运利润率也低于补货门槛，暂停建议，先检查成本或售价')
        if not sea_allowed: quantity = 0
        if purchase_day == 0: sea_qty = quantity
        if quantity:
            planned[eta] = planned.get(eta, 0) + quantity
            schedule.append(entry('sea', quantity, purchase_day, eta))
    timeline = []; balance = stock; shortage_windows = []; pre_sea_lost = 0
    milestones = {0, air-1, air, sea-1, sea, sea+coverage+safety-1}
    milestones.update((date.fromisoformat(s['arrival_date'])-today).days for s in schedule)
    for day in range(horizon):
        received = arrivals.get(day, 0) + planned.get(day, 0)
        available = balance + received
        lost = max(0, velocity - available)
        balance = max(0, available - velocity)
        if day < sea: pre_sea_lost += lost
        if lost > 1e-9:
            current_date = (today+timedelta(days=day)).isoformat()
            if shortage_windows and shortage_windows[-1]['last_day'] == day - 1:
                shortage_windows[-1]['end_date'] = current_date
                shortage_windows[-1]['quantity'] += lost
                shortage_windows[-1]['last_day'] = day
            else:
                shortage_windows.append(dict(start_date=current_date, end_date=current_date, quantity=lost, last_day=day))
        if day in milestones:
            timeline.append({'day': day, 'date': (today+timedelta(days=day)).isoformat(), 'balance': round(balance, 1), 'arrivals': received, 'unmet_demand': round(lost, 1)})
    for window in shortage_windows:
        window['quantity'] = math.ceil(window['quantity'] - 1e-9)
        window.pop('last_day')
    total_sea = sum(s['quantity'] for s in schedule if s['mode'] == 'sea')
    total_air = air_qty
    savings = None
    if economics and economics.get('available') and all(economics[m].get('total_cost_zar') is not None for m in ('air','sea')):
        savings = round(total_sea * (economics['air']['total_cost_zar'] - economics['sea']['total_cost_zar']), 2)
    cover = stock / velocity
    level = 'out_of_stock' if stock == 0 else 'urgent' if cover <= air else 'watch' if cover <= sea else 'healthy'
    return dict(level=level, daily_loss_zar=round(velocity * (profile.get('current_price') or 0), 2) if stock == 0 else 0, **common, air_quantity=air_qty, sea_quantity=sea_qty, velocity=round(velocity, 3), stock=stock,
                cover_days=round(stock / velocity, 1), pending=pending, overdue=overdue,
                unmapped=unmapped, stale=elapsed > 3, pre_air_shortage=pre_air,
                next_review_days=cycle, timeline=timeline, blocked=blocked, schedule=schedule,
                air_gap_need=air_gap_need, pre_sea_shortage=math.ceil(pre_sea_lost - 1e-9),
                shortage_windows=shortage_windows, sea_start_date=(today+timedelta(days=sea)).isoformat(),
                emergency_air_limit=air_limit, emergency_air_budget_remaining=air_budget, allow_emergency_air=emergency_air, emergency_air_days=emergency_days,
                sea_coverage_days=coverage, planning_days=planning_days, total_sea_quantity=total_sea,
                sea_share=round(total_sea / (total_sea+total_air), 3) if total_sea+total_air else 0,
                estimated_transport_saving_zar=savings, provisional=days < 14 or elapsed > 3,
                reason='；'.join(blocked) if blocked else f'海运覆盖{coverage}天销量＋{safety}天安全库存，每{cycle}天滚动复核；默认仅安排多批海运持续衔接；空运需按商品开启且限量，首批前缺口单独提示，未来批次须按最新销量确认')
