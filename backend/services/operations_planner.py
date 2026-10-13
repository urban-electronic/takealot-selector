"""Deterministic air/sea pipeline planning. No AI, network or database mutation."""
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo


def plan(profile, purchases, today=None):
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
    air = profile['air_days']; sea = profile['sea_days']
    cycle = profile['cycle_days']; safety = profile['safety_days']
    horizon = sea + cycle + safety
    arrivals = {}
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
        arrivals[offset] = arrivals.get(offset, 0) + purchase['quantity']
    if velocity <= 0:
        return dict(air_quantity=0, sea_quantity=0, velocity=0, stock=stock, cover_days=None,
                    pending=pending, overdue=overdue, unmapped=unmapped, stale=elapsed > 3,
                    pre_air_shortage=0, next_review_days=cycle, timeline=[], reason='观测期零成交，不自动补货')
    def deficit(end, planned=None):
        balance = stock; worst = 0
        extra = planned or {}
        for day in range(end):
            balance += arrivals.get(day, 0) + extra.get(day, 0)
            balance -= velocity
            worst = max(worst, -balance)
        return max(0, math.ceil(worst - 1e-9))
    pre_air = deficit(air)
    # Cover daily deficits from air ETA to sea ETA + safety. Earlier stockout
    # cannot be repaired retroactively; air quantity covers the bridge itself.
    def required_after(start, end, extra=None):
        balance = stock; worst = 0
        for day in range(end):
            balance += arrivals.get(day, 0) + (extra or {}).get(day, 0)
            balance -= velocity
            if day >= start: worst = max(worst, -balance)
            elif balance < 0: balance = 0  # lost sales before delivery are not backorders
        return max(0, math.ceil(worst - 1e-9))
    air_qty = required_after(air, sea + safety)
    sea_qty = required_after(sea, horizon, {air: air_qty})
    timeline = []; balance = stock
    for day in range(horizon):
        received = arrivals.get(day, 0) + (air_qty if day == air else 0) + (sea_qty if day == sea else 0)
        balance += received - velocity
        if day in {0, air - 1, air, sea - 1, sea, horizon - 1}:
            timeline.append({'day': day + 1, 'balance': round(balance, 1), 'arrivals': received})
    return dict(air_quantity=air_qty, sea_quantity=sea_qty, velocity=round(velocity, 3), stock=stock,
                cover_days=round(stock / velocity, 1), pending=pending, overdue=overdue,
                unmapped=unmapped, stale=elapsed > 3, pre_air_shortage=pre_air,
                next_review_days=cycle, timeline=timeline, reason='空运补到海运接力；海运覆盖后续采购周期，按预计到货日扣除已购数量')
