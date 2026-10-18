"""Deterministic air/sea pipeline planning. No AI, network or database mutation."""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def _known_stock(profile):
    """Stock snapshot when it is a real number; legacy rows may omit it."""
    value = profile.get('stock')
    return value if not isinstance(value, bool) and isinstance(value, (int, float)) else None


def _observation_ready(profile):
    """A profile can only forecast when it has an observation window and stock."""
    for key in ('observed_from', 'stock_date'):
        try:
            date.fromisoformat(profile[key])
        except (KeyError, TypeError, ValueError):
            return False
    return _known_stock(profile) is not None


def velocity_of(profile, today=None):
    """日销速度（件/天）：基线、近 7 天、近 3 天取最大，再按基线 3 倍封顶。

    补货和调价必须用同一套口径。两边各算一份，就会出现「补货说还有 40 天、
    调价说只剩 5 天」这种自相矛盾的判断——而这两个数字都在决定钱。
    所以这里单独抽出来，plan() 和调价都调它，不允许再写第二份。
    """
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    sales = profile.get('sales') or []
    try:
        observed = date.fromisoformat(profile['observed_from'])
    except (KeyError, TypeError, ValueError):
        return 0.0
    days = min(30, max(1, (today - observed).days + 1))

    def units(n):
        start = today.toordinal() - n + 1
        total = 0
        for sale in sales:
            try:
                day = date.fromisoformat(sale['date']).toordinal()
                quantity = sale['quantity']
            except (KeyError, TypeError, ValueError):
                continue
            if start <= day <= today.toordinal() and isinstance(quantity, (int, float)):
                total += quantity
        return total

    baseline = units(days) / days
    recent = units(min(3, days)) / min(3, days)
    week = units(min(7, days)) / min(7, days)
    velocity = max(baseline, week, recent)
    if days >= 14 and baseline > 0:
        velocity = min(velocity, baseline * 3)
    return velocity


def plan(profile, purchases, today=None, economics=None):
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if not _observation_ready(profile):
        # Legacy or hand-edited rows predate the observation-window schema.
        # Without a window and stock snapshot there is no honest velocity:
        # report "no data" instead of failing the whole page for every SKU.
        return dict(air_quantity=0, sea_quantity=0, velocity=0, stock=_known_stock(profile),
                    cover_days=None, pending={'air': 0, 'sea': 0}, overdue=[], unmapped=[], stale=True,
                    pre_air_shortage=0, next_review_days=profile.get('cycle_days', 30),
                    timeline=[], schedule=[], sea_share=0, air_gap_need=0, pre_sea_shortage=0,
                    shortage_windows=[], allow_emergency_air=profile.get('allow_emergency_air', True),
                    emergency_air_days=profile.get('emergency_air_days', 21),
                    reason='尚未同步库存与销量观测期，暂不生成补货计划', level='no_data',
                    daily_loss_zar=0, units_window=0, units_recent=0, observed_days=0,
                    days_since_last_sale=None, trend='flat', provisional=True)
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
    # 日销口径统一走 velocity_of()：调价判断「还有几天可卖」读的就是这个数，
    # 这里再算一份迟早会漂。baseline/recent 留给下面的趋势判断用。
    velocity = velocity_of(profile, today)
    # Snapshot represents end-of-day stock; subtract subsequent real sales only.
    consumed = sum(s['quantity'] for s in sales if snapshot < date.fromisoformat(s['date']) <= today)
    stock = max(0, profile['stock'] - consumed)
    prep = profile.get('procurement_days', 0)
    air = profile['air_days'] + prep; sea = profile['sea_days'] + prep
    cycle = profile['cycle_days']; safety = profile['safety_days']
    coverage = max(profile.get('sea_coverage_days', 60), cycle, profile['air_days'])
    planning_days = profile.get('planning_days', 120)
    policy = profile.get('dispatch_policy', 'balanced')
    adaptive = profile.get('adaptive_dispatch', True)
    effective_air_share = profile.get('air_share_cap', .2)
    effective_first_share = profile.get('first_sea_share', .75)
    if adaptive and policy == 'balanced':
        if recent > baseline * 1.5: effective_first_share = min(.9, effective_first_share + .1)
        elif baseline and recent < baseline * .5: effective_first_share = max(.5, effective_first_share - .1)
    emergency_air = profile.get('allow_emergency_air', policy == 'balanced') and policy != 'sea_only'
    emergency_days = profile.get('emergency_air_days', 21)
    split_days = profile.get('sea_split_days', 15) if policy == 'balanced' else 0
    manual_batch = profile.get('batch_quantity')
    horizon = sea + ((planning_days - 1) // cycle) * cycle + coverage + safety
    latest_sale = max((date.fromisoformat(s['date']) for s in sales if date.fromisoformat(s['date']) <= today), default=None)
    common = dict(units_window=units(days), units_recent=units(min(7, days)), observed_days=days, days_since_last_sale=(today-latest_sale).days if latest_sale else None, trend='rising' if recent > baseline * 1.5 else 'falling' if baseline and recent < baseline * .5 else 'flat')
    arrivals = {}; pending_air_before_sea = 0
    overdue = []; pending = {'air': 0, 'sea': 0}; unmapped = []
    mapping = {}
    for a in profile.get('allocations', []): mapping.setdefault(a['procurement_id'], []).append(a)
    for purchase in purchases:
        if purchase['status'] == 'cancelled': continue
        batches = mapping.get(purchase['id'], [])
        total = sum(a.get('quantity') if a.get('quantity') is not None else purchase['quantity'] for a in batches)
        # A later purchase edit must never make stale splits inflate inventory.
        if total > purchase['quantity']:
            unmapped.append(purchase['id']); continue
        if not batches or total < purchase['quantity']:
            if purchase['status'] == 'in_transit': unmapped.append(purchase['id'])
        for allocation in batches:
            quantity = allocation.get('quantity') if allocation.get('quantity') is not None else purchase['quantity']
            if allocation.get('included_in_stock'): continue
            eta = date.fromisoformat(allocation['eta']); offset = (eta - today).days
            if offset < 0:
                if purchase['id'] not in overdue: overdue.append(purchase['id'])
                continue
            pending[allocation['mode']] += quantity
            if allocation['mode'] == 'air' and offset < sea: pending_air_before_sea += quantity
            arrivals[offset] = arrivals.get(offset, 0) + quantity
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
    initial_sea_need = required_after(sea, sea + coverage + safety)
    air_limit = math.ceil(velocity * emergency_days - 1e-9)
    if policy == 'balanced':
        share = effective_air_share
        if adaptive and economics and economics.get('available'):
            urgency = min(1, air_gap_need / max(1, velocity * (sea - air)))
            profitability = max(0, min(1, economics['air']['profit_margin'] / max(.01, economics['sea']['profit_margin'])))
            effective_air_share = min(.4, max(0, share + .15 * urgency * profitability)) if share else 0
            share = effective_air_share
        sea_basis = initial_sea_need + min(pending['sea'], math.ceil(velocity * coverage))
        ratio_limit = math.floor(manual_batch * share) if manual_batch else math.floor(sea_basis * share / (1 - share))
        air_limit = min(air_limit, ratio_limit)
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
    if policy == 'balanced' and not sea_allowed and air_qty:
        blocked.append('海运利润未达标，暂停本轮混合补货')
        air_qty = 0
    planned = {air: air_qty} if air_qty else {}
    schedule = []
    def entry(mode, quantity, purchase_day, eta, delay=0):
        departure = purchase_day + prep + delay
        cost = economics[mode].get('total_cost_zar') if economics and economics.get('available') else None
        return dict(mode=mode, quantity=quantity,
                    purchase_date=(today + timedelta(days=purchase_day)).isoformat(),
                    departure_date=(today + timedelta(days=departure)).isoformat(),
                    arrival_date=(today + timedelta(days=eta)).isoformat(),
                    coverage_until=(today + timedelta(days=eta + (coverage + safety if mode == 'sea' else math.ceil(quantity / velocity)) - 1)).isoformat(),
                    conditional=purchase_day > 0 or delay > 0, batch_id=f'{today.isoformat()}-{purchase_day}', deferred_dispatch=delay > 0, estimated_cost_zar=round(cost * quantity, 2) if cost is not None else None)
    if air_qty: schedule.append(entry('air', air_qty, 0, air))
    sea_qty = 0; initial_total = 0
    for purchase_day in range(0, planning_days, cycle):
        eta = sea + purchase_day
        quantity = required_after(eta, eta + coverage + safety, planned)
        if purchase_day == 0 and manual_batch is not None:
            quantity = min(quantity, max(0, manual_batch - air_qty))
        if purchase_day == 0 and quantity and not sea_allowed:
            blocked.append('海运利润率低于补货门槛，暂停建议，先检查成本或售价')
        if not sea_allowed: quantity = 0
        if purchase_day == 0:
            sea_qty = quantity; initial_total = air_qty + quantity
            delay = split_days
            if quantity and delay and adaptive:
                seed = math.ceil(quantity * effective_first_share)
                delay = min(delay, max(0, math.floor(seed / velocity) - safety))
            if quantity and delay:
                # Never defer units needed before the second sea arrival.
                first_needed = required_after(eta, eta + delay, planned)
                first = min(quantity, max(first_needed, math.ceil(quantity * effective_first_share)))
                second = quantity - first
                planned[eta] = planned.get(eta, 0) + first
                schedule.append(entry('sea', first, purchase_day, eta))
                if second:
                    planned[eta+delay] = planned.get(eta+delay, 0) + second
                    schedule.append(entry('sea', second, purchase_day, eta+delay, delay=delay))
                continue
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
                dispatch_policy=policy, adaptive_dispatch=adaptive, effective_air_share=round(effective_air_share,3), effective_first_sea_share=effective_first_share, initial_batch_quantity=initial_total, initial_air_share=round(air_qty/initial_total,3) if initial_total else 0,
                air_gap_need=air_gap_need, pre_sea_shortage=math.ceil(pre_sea_lost - 1e-9),
                shortage_windows=shortage_windows, sea_start_date=(today+timedelta(days=sea)).isoformat(),
                emergency_air_limit=air_limit, emergency_air_budget_remaining=air_budget, allow_emergency_air=emergency_air, emergency_air_days=emergency_days,
                sea_coverage_days=coverage, planning_days=planning_days, total_sea_quantity=total_sea,
                sea_share=round(total_sea / (total_sea+total_air), 3) if total_sea+total_air else 0,
                estimated_transport_saving_zar=savings, provisional=days < 14 or elapsed > 3,
                reason='；'.join(blocked) if blocked else f'海运覆盖{coverage}天销量＋{safety}天安全库存，每{cycle}天滚动复核；少量空运启动，首批海运为主、第二批错峰发运；缺口和利润单独核验，后续海运持续衔接')
