import json
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services import automatic_pricing as pricing

RULE = dict(product_id='p', offer_id='1', sku='a', enabled=True, min_price=80, max_price=300,
            strategy='takealot_buybox', lead_amount=1, max_daily_changes=10, cooldown_minutes=10, revision=1)

def offer(price, kind='fastest', **extra):
    return {'price': price, 'offerType': kind, 'status': 'in_stock', 'isMine': False, **extra}

def test_pricepilot_fastest_fallback_and_never_raise_without_buybox():
    r = {**RULE, 'min_price': 113}
    assert pricing.decide(273, 5, r, {'buyboxPrice': 263, 'offers': [offer(274), offer(263, 'best_price')]})[0] == 262
    assert pricing.decide(273, 5, r, {'buyboxPrice': 300, 'offers': [offer(300)]})[0] is None
    assert pricing.decide(273, 5, {**r, 'min_price': 270}, {'buyboxPrice': 263, 'offers': [offer(263)]})[0] is None

def test_preorder_and_self_quotes_never_competed_with():
    assert pricing.decide(100, 5, RULE, {'offers': [offer(60, status='backorder')]})[0] is None
    assert pricing.decide(100, 5, RULE, {'offers': [offer(90, isMine=True)]})[0] is None
    assert pricing.decide(100, 5, RULE, {'offers': [offer(90)], 'error': 'variant_failed'})[0] is None
    assert pricing.decide(100, 0, RULE, {'offers': [offer(90)]})[0] is None

def test_holding_rebound_ceiling_and_cooldown():
    assert pricing.decide(100, 5, {**RULE, 'max_price': 105}, {'buyboxPrice': 100, 'holdingBuyboxVerified':True, 'offers': [offer(120)]})[0] == 105
    assert pricing.decide(100, 5, {**RULE, 'last_changed_epoch': pricing.time.time()}, {'buyboxPrice': 100, 'holdingBuyboxVerified':True, 'offers': [offer(120)]})[0] is None

def run_cycle(monkeypatch, pause_during_collect=False, verify_price=89, pause_before_patch=False, pause_all_before_patch=False, rule_extra=None):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); factory = sessionmaker(bind=engine)
    key = pricing.PREFIX + DEFAULT_STORE_ID + ':p:1'
    with factory() as db:
        db.add(Product(id='p', store_id=DEFAULT_STORE_ID, sku='a', takealot_url='https://www.takealot.com/x/PLID1'))
        db.add(SystemSettings(key=key, value=json.dumps({**RULE, **(rule_extra or {})}))); db.commit()
    import database
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(pricing, 'configured_key', lambda db: 'test-secret')
    sleeps=[]
    def delay(seconds):
        sleeps.append(seconds)
        if pause_before_patch and len(sleeps)==2:
            with factory() as db:
                row=db.query(SystemSettings).filter_by(key=key).first()
                value=json.loads(row.value);value.update(enabled=False,revision=2)
                row.value=json.dumps(value);db.commit()
        if pause_all_before_patch and len(sleeps)==2:
            from api import pricing_routes
            with factory() as db: pricing_routes.pause_all(db,DEFAULT_STORE_ID)
    monkeypatch.setattr(pricing.time, 'sleep', delay)
    client = MagicMock(); monkeypatch.setattr(pricing.httpx, 'Client', lambda **kwargs: client)
    client.__enter__.return_value = client
    response = lambda price: MagicMock(status_code=200, json=lambda: {'offer': {'offer_id': 1, 'sku': 'a', 'selling_price': price, 'stock_at_takealot_total': 5}})
    client.get.side_effect = [response(100), response(100), response(verify_price)]
    client.patch.return_value = MagicMock(status_code=200, json=lambda: {'offer': {'selling_price': 89}})
    def shared(db_session, product_id, offer_id, sku):
        if pause_during_collect:
            with factory() as db:
                row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
                value = json.loads(row.value); value.update(enabled=False, revision=2)
                row.value = json.dumps(value); db.commit()
        return {'buyboxPrice': 90, 'offers': [offer(90)]}
    monkeypatch.setattr(pricing, 'shared_snapshot', shared)
    monkeypatch.setattr(pricing, 'collect', lambda *args: (_ for _ in ()).throw(AssertionError('pricing must not launch collection')))
    pricing.process(key)
    with factory() as db: value = json.loads(db.query(SystemSettings).filter(SystemSettings.key == key).first().value)
    return client, value

def test_triggered_cycle_only_one_patch_and_readback_required(monkeypatch):
    client, rule = run_cycle(monkeypatch)
    assert client.patch.call_count == 1
    assert client.patch.call_args.kwargs['json'] == {'selling_price': 89}
    assert rule['enabled'] and not rule['write_pending'] and rule['current_price'] == 89
    assert rule['today_changes'] == 1 and rule['history'][0]['from'] == 100

def test_pause_during_collection_prevents_write(monkeypatch):
    client, rule = run_cycle(monkeypatch, pause_during_collect=True)
    assert client.patch.call_count == 0 and not rule['enabled'] and rule['revision'] == 2

def test_uncertain_platform_result_pauses_instead_of_repeated_patch(monkeypatch):
    client, rule = run_cycle(monkeypatch, verify_price=100)
    assert client.patch.call_count == 1 and not rule['enabled'] and rule['write_pending']

def test_untriggered_rule_never_reads_or_writes_platform(monkeypatch):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); factory = sessionmaker(bind=engine)
    key = pricing.PREFIX + DEFAULT_STORE_ID + ':p:1'
    with factory() as db: db.add(SystemSettings(key=key, value=json.dumps({**RULE, 'enabled': False}))); db.commit()
    import database
    monkeypatch.setattr(database, 'SessionLocal', factory)
    client = MagicMock(); monkeypatch.setattr(pricing.httpx, 'Client', client)
    pricing.process(key)
    assert not client.called

def test_confirmed_scarcity_can_raise_but_empty_data_cannot():
    rule = {**RULE, 'smart_raise': True, 'reference_price': 100, 'max_price': None}
    snapshot = {'buyboxPrice': 100, 'holdingBuyboxVerified':True, 'offers': [offer(100, isMine=True), offer(60, 'best_price', status='backorder')]}
    assert pricing.decide(100, 5, rule, snapshot)[0] == 103
    assert pricing.decide(100, 5, rule, {'buyboxPrice': 100, 'offers': []})[0] is None
    assert pricing.decide(120, 5, rule, {**snapshot, 'buyboxPrice': 120})[0] is None

def test_scarcity_trial_observes_feedback_and_rolls_back_without_sales():
    now = pricing.time.time()
    rule = {**RULE, 'smart_raise': True, 'reference_price': 100, 'scarcity_trial_started': now - 49*3600,
            'scarcity_base_price': 100, 'sku_last_sale_epoch': now - 60*3600, 'sales_recent': True}
    snapshot = {'buyboxPrice': 110, 'holdingBuyboxVerified':True, 'offers': [offer(110, isMine=True), offer(60, 'best_price', status='backorder')]}
    assert pricing.decide(110, 5, rule, snapshot)[0] == 100
    assert pricing.decide(110, 5, {**rule, 'last_scarcity_raise': now, 'sku_last_sale_epoch': now - 3600}, snapshot)[0] is None


def test_price_match_does_not_prove_buybox_ownership():
    # holdingBuybox 是价格推断出来的旧字段，不能当作持位证据；
    # 只有 holdingBuyboxVerified（按卖家名核实）才算。
    target,reason=pricing.decide(100,5,{**RULE,'smart_raise':True},{'buyboxPrice':100,'holdingBuybox':True,'offers':[offer(120)]})
    assert target is None and '卖家名' in reason

def test_pause_after_persisting_intent_prevents_patch(monkeypatch):
    client,rule=run_cycle(monkeypatch,pause_before_patch=True)
    assert not client.patch.called and not rule['enabled'] and rule['revision']==2


def test_global_emergency_stop_invalidates_pending_patch(monkeypatch):
    client,rule=run_cycle(monkeypatch,pause_all_before_patch=True)
    assert not client.patch.called and not rule['enabled'] and rule['write_pending']
    assert rule['revision']==2 and '待核验' in rule['last_result']


def test_unaffordable_fastest_still_allows_a_distinct_best_price_target():
    """最快配送不可达时仍可评估最低价卡位；不能在同一卡位跳过领跑者。"""
    rule = {**RULE, 'min_price': 100, 'max_price': 500, 'reference_price': 300}
    snapshot = {'buyboxPrice': 80, 'offers': [offer(80), offer(200, 'best_price')]}
    assert pricing.decide(300, 5, rule, snapshot)[0] == 255
    assert pricing.decide(255, 5, rule, snapshot)[0] == 217
    assert pricing.decide(217, 5, rule, snapshot)[0] == 199
    plan = pricing.plan(300, 5, rule, snapshot)
    assert plan['risk'] == 'high' and plan['cheapest'] == 80
    assert plan['target_lane'] == 'best_price'


def test_hold_regime_raises_toward_next_competitor_not_past_ceiling():
    """守位时贴着下一个现货竞品的价，且不越过人工上限。"""
    rule = {**RULE, 'min_price': 80, 'max_price': 500, 'reference_price': 100}
    snapshot = {'buyboxPrice': 100, 'holdingBuyboxVerified': True, 'offers': [offer(100, isMine=True), offer(120)]}
    assert pricing.decide(100, 5, rule, snapshot)[0] == 119          # 顶到 119 仍是最低
    assert pricing.decide(100, 5, {**rule, 'max_price': 105}, snapshot)[0] == 105
    stepped = {**rule, 'smart_raise': True}
    assert pricing.decide(100, 5, stepped, snapshot)[0] == 103       # 开智能试涨就一步一步走


def test_sole_seller_climbs_but_missing_quotes_hold():
    """只有本店在售 = 最大定价权，可以试涨；一条报价都没采到 = 证据缺失，不许动。"""
    rule = {**RULE, 'smart_raise': True, 'reference_price': 100, 'max_price': None}
    only_mine = {'buyboxPrice': 100, 'holdingBuyboxVerified': True, 'offers': [offer(100, isMine=True)]}
    plan = pricing.plan(100, 5, rule, only_mine)
    assert plan['sole_seller'] is True and plan['target'] == 103 and plan['regime'] == 'scarcity'
    empty = pricing.plan(100, 5, rule, {'buyboxPrice': 100, 'holdingBuyboxVerified': True, 'offers': []})
    assert empty['target'] is None and empty['sole_seller'] is False


def test_plan_carries_the_numbers_the_ui_shows():
    """界面上的决策卡片读 plan，不再从 last_result 的一句话反推。"""
    rule = {**RULE, 'min_price': 113, 'max_price': 400, 'reference_price': 273}
    plan = pricing.plan(273, 5, rule, {'buyboxPrice': 263, 'offers': [offer(274), offer(263, 'best_price')]})
    assert plan['regime'] == 'compete' and plan['target'] == 262
    assert plan['floor'] == 113 and plan['ceiling'] == 400
    assert plan['cheapest'] == 263 and plan['confidence'] in ('medium', 'high')
    assert 'R 262' in plan['reason']


def test_blind_regime_when_ownership_unproven():
    # 同价但还不知道本店卖家名：两个方向都不能动，但要给出可操作的说法
    # （去设置里填一次卖家名，之后就能自动核实）。
    plan = pricing.plan(100, 5, {**RULE, 'smart_raise': True}, {'buyboxPrice': 100, 'offers': [offer(120)]})
    assert plan['regime'] == 'blind' and plan['target'] is None and '卖家名' in plan['reason']


# ── 价格战熔断 ──────────────────────────────────────────────────────────────

def test_price_war_breaker_stops_following_down():
    """连续跟降还没拿到 Buybox，说明对手在陪着降。继续跟只会两边一起掉到地板上。"""
    snap = {'offers': [offer(190)]}
    ok = pricing.plan(200, 5, {**RULE, 'drop_streak': 3, 'drop_streak_from': 200}, snap)
    assert ok['regime'] == 'compete' and ok['target'] == 189 and ok['war'] is None
    war = pricing.plan(200, 5, {**RULE, 'drop_streak': 4, 'drop_streak_from': 200}, snap)
    assert war['regime'] == 'hold' and war['war'] == 'open' and war['target'] is None
    assert '价格战' in war['reason'] and war['war_info']['streak'] == 4


def test_price_war_breaker_also_trips_on_total_discount():
    """次数少但每刀都深：连降 2 次已经让了 10%，同样要停手。"""
    p = pricing.plan(180, 5, {**RULE, 'drop_streak': 2, 'drop_streak_from': 200}, {'offers': [offer(178)]})
    assert p['war'] == 'open' and p['war_info']['drop_pct'] == 10.0


def test_war_pause_blocks_only_the_drop_not_the_raise():
    """熔断只停「跟降」。守位涨价和缺货试涨不是在打价格战，照常走。"""
    future = pricing.time.time() + 3600
    rule = {**RULE, 'war_until': future, 'drop_streak': 6, 'reference_price': 100, 'smart_raise': True}
    hold = {'buyboxPrice': 100, 'holdingBuyboxVerified': True, 'offers': [offer(120)]}
    assert pricing.plan(100, 5, rule, hold)['target'] == 103
    drop = pricing.plan(200, 5, rule, {'offers': [offer(190)]})
    assert drop['regime'] == 'hold' and drop['war'] == 'paused' and drop['target'] is None


def test_drop_streak_is_counted_from_the_first_drop(monkeypatch):
    """连降计数与起点价落库，下一轮才判得出来「这段让了多少」。"""
    client, rule = run_cycle(monkeypatch)
    assert client.patch.call_count == 1
    assert rule['drop_streak'] == 1 and rule['drop_streak_from'] == 100


def test_price_war_breaker_pauses_the_rule_end_to_end(monkeypatch):
    client, rule = run_cycle(monkeypatch, rule_extra={'drop_streak': 4, 'drop_streak_from': 100})
    assert client.patch.call_count == 0
    assert rule['war_until'] > pricing.time.time()
    assert rule['plan']['war'] == 'open'


def test_expired_breaker_rearms_instead_of_freezing_the_rule(monkeypatch):
    """熔断期满必须清零计数，否则下一轮立刻再次熔断，这条 SKU 就永远动不了。"""
    past = pricing.time.time() - 60
    client, rule = run_cycle(monkeypatch, rule_extra={'drop_streak': 6, 'drop_streak_from': 100, 'war_until': past})
    assert client.patch.call_count == 1
    assert rule['war_until'] == 0 and rule['war_count'] == 1
    assert rule['drop_streak'] == 1 and rule['drop_streak_from'] == 100

