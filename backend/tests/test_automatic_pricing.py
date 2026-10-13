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

def run_cycle(monkeypatch, pause_during_collect=False, verify_price=89, pause_before_patch=False):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); factory = sessionmaker(bind=engine)
    key = pricing.PREFIX + DEFAULT_STORE_ID + ':p:1'
    with factory() as db:
        db.add(Product(id='p', store_id=DEFAULT_STORE_ID, sku='a', takealot_url='https://www.takealot.com/x/PLID1'))
        db.add(SystemSettings(key=key, value=json.dumps(RULE))); db.commit()
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
    target,reason=pricing.decide(100,5,{**RULE,'smart_raise':True},{'buyboxPrice':100,'holdingBuybox':True,'offers':[offer(120)]})
    assert target is None and '身份未核实' in reason

def test_pause_after_persisting_intent_prevents_patch(monkeypatch):
    client,rule=run_cycle(monkeypatch,pause_before_patch=True)
    assert not client.patch.called and not rule['enabled'] and rule['revision']==2
