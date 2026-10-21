import json
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from fastapi import HTTPException
from database import Base
from models import Product, SystemSettings, DEFAULT_STORE_ID
from api import pricing_routes as routes
from services.automatic_pricing import PREFIX

def database():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    for id, minimum in [('one', 80), ('two', None)]:
        db.add(Product(id=id, store_id=DEFAULT_STORE_ID, sku=id, product_name=id,
                       takealot_url='https://www.takealot.com/x/PLID1', minimum_price_at_20_margin=minimum))
        db.add(SystemSettings(key=f'operations_profile:{DEFAULT_STORE_ID}:{id}', value=json.dumps({'offers':[{'id': id, 'sku': id, 'price': 100, 'stock': 5}]})))
    db.commit(); return db

def test_default_system_min_and_optional_ceiling_do_not_activate_until_trigger(monkeypatch):
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    with database() as db:
        value = routes.configure('one', 'one', routes.Rule(), db, DEFAULT_STORE_ID)
        assert value['min_price'] == 80 and value['max_price'] is None and not value['enabled']
        value = routes.configure('two', 'two', routes.Rule(min_price=60, use_system_min=False, enabled=True), db, DEFAULT_STORE_ID)
        assert value['enabled'] and value['min_price'] == 60

def test_bulk_failure_does_not_partly_activate_and_grouping_does_not_activate(monkeypatch):
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    items = [{'product_id':'one','offer_id':'one'}, {'product_id':'two','offer_id':'two'}]
    with database() as db:
        routes.bulk(routes.Bulk(action='group', items=items, group='宠物用品'), db, DEFAULT_STORE_ID)
        assert all(r['group']=='宠物用品' for r in routes.listing(db, DEFAULT_STORE_ID)['rows'])
        with pytest.raises(HTTPException): routes.bulk(routes.Bulk(action='start', items=items), db, DEFAULT_STORE_ID)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).count()
        routes.bulk(routes.Bulk(action='start', items=items[:1]), db, DEFAULT_STORE_ID)
        value = json.loads(db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).first().value)
        assert value['enabled'] and value['min_price']==80
        routes.bulk(routes.Bulk(action='pause', items=items[:1]), db, DEFAULT_STORE_ID)
        assert not json.loads(db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).first().value)['enabled']

def test_cannot_trigger_other_store_or_unknown_sku(monkeypatch):
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    with database() as db:
        with pytest.raises(HTTPException): routes.configure('one','one',routes.Rule(enabled=True),db,'other-store')
        with pytest.raises(HTTPException): routes.bulk(routes.Bulk(action='start',items=[{'product_id':'foreign','offer_id':'foreign'}]),db,DEFAULT_STORE_ID)


def test_historical_same_price_hold_is_displayed_as_unverified(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        db.add(SystemSettings(key=PREFIX+DEFAULT_STORE_ID+':one:one',value=json.dumps({'holding_buybox':True,'buybox_price':100,'current_price':100})));db.commit()
        row=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows'] if r['sku']=='one')
        assert row['rule']['holding_buybox'] is None and row['rule']['price_matches_buybox'] is True


def test_listing_uses_newest_official_price_and_stock(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    now=datetime.now(timezone.utc)
    with database() as db:
        profile=db.query(SystemSettings).filter_by(key=f'operations_profile:{DEFAULT_STORE_ID}:one').first()
        data=json.loads(profile.value);data['synced_at']=now.isoformat();data['offers'][0].update(price=120,stock=9)
        profile.value=json.dumps(data)
        db.add(SystemSettings(key=PREFIX+DEFAULT_STORE_ID+':one:one',value=json.dumps({
            'current_price':80,'stock':0,'last_evaluated_at':(now-timedelta(minutes=5)).isoformat()})))
        db.commit()
        row=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows'] if r['sku']=='one')
        assert (row['price'],row['stock']) == (120,9)

        rule=db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first()
        value=json.loads(rule.value);value.update(current_price=115,stock=7,last_evaluated_at=(now+timedelta(minutes=5)).isoformat())
        rule.value=json.dumps(value);db.commit()
        row=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows'] if r['sku']=='one')
        assert (row['price'],row['stock']) == (115,7)


def test_untimestamped_rule_cannot_hide_current_stock(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        db.add(SystemSettings(key=PREFIX+DEFAULT_STORE_ID+':one:one',value=json.dumps({'current_price':80,'stock':0})))
        db.commit()
        row=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows'] if r['sku']=='one')
        assert (row['price'],row['stock']) == (100,5)


def test_reconcile_requires_the_same_seller_sku(monkeypatch):
    from services import automatic_pricing
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    monkeypatch.setattr(httpx,'Client',lambda **kwargs: type('Client',(),{
        '__enter__':lambda self:self,'__exit__':lambda self,*args:None})())
    key=PREFIX+DEFAULT_STORE_ID+':one:one'
    with database() as db:
        original={'product_id':'one','offer_id':'one','sku':'one','enabled':False,'write_pending':True,'revision':3}
        db.add(SystemSettings(key=key,value=json.dumps(original)));db.commit()
        monkeypatch.setattr(automatic_pricing,'official_offer',lambda client,key,offer_id:{
            'offer_id':'one','sku':'different','selling_price':91,'stock_at_takealot_total':4})
        with pytest.raises(HTTPException) as error: routes.reconcile('one','one',db,DEFAULT_STORE_ID)
        assert error.value.status_code==409
        assert json.loads(db.query(SystemSettings).filter_by(key=key).first().value)==original

        monkeypatch.setattr(automatic_pricing,'official_offer',lambda client,key,offer_id:{
            'offer_id':'one','sku':'one','selling_price':91,'stock_at_takealot_total':4})
        value=routes.reconcile('one','one',db,DEFAULT_STORE_ID)
        assert not value['write_pending'] and not value['enabled']
        assert value['current_price']==91 and value['stock']==4 and value['revision']==4


def test_preview_exception_does_not_expose_profile_or_token(monkeypatch):
    import httpx
    from contextlib import nullcontext
    import services.automatic_pricing as pricing
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:nullcontext(object()))
    monkeypatch.setattr(routes,'configured_key',lambda db:'fixture')
    monkeypatch.setattr(pricing,'official_offer',lambda *a:{'selling_price':100,'stock_at_takealot_total':5})
    async def failed(*args):
        raise RuntimeError('/private/profile Authorization=fixture-private-token')
    monkeypatch.setattr(pricing,'collect',failed)
    with database() as db:
        result=routes.preview('one','one',db,DEFAULT_STORE_ID)
    assert result['error_type']=='RuntimeError'
    assert result['price_changed'] is False and result['enabled'] is False
    assert 'fixture-private' not in json.dumps(result)
    assert '/private/profile' not in json.dumps(result)


def test_listing_exposes_product_image_for_row_thumbnail(monkeypatch):
    """调价列表每行要带图片地址。

    这条以前没有任何断言，于是「listing 里补 image 字段」这个改动在合并时被
    静默丢掉过一次——列表照样能用，只是每行都显示「无图」，没人报错。
    """
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    with database() as db:
        db.query(Product).filter_by(id='one').update(
            {'product_image_url': 'https://media.takealot.com/covers_images/one.jpg'})
        db.commit()
        rows = {r['sku']: r for r in routes.listing(db, DEFAULT_STORE_ID)['rows']}
        assert rows['one']['image'] == 'https://media.takealot.com/covers_images/one.jpg'
        # 没有图片时给空串而不是 None：前端拿空串判断「无图」，None 会走到 img src=undefined。
        assert rows['two']['image'] == ''


def test_saving_from_the_ui_keeps_parameters_the_form_does_not_show(monkeypatch):
    """界面只显示区间和策略。保存不能把界面上看不见的参数重置成默认值。

    原来的实现是 {**old, **Rule().model_dump()}：model_dump 会把没传的字段
    也按默认值填上，于是用户配的 max_drop_pct=30 会被一次「保存区间」改回 15，
    lead_amount/cooldown/max_daily_changes 同理。改的是 A，坏的是 B。
    """
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    with database() as db:
        routes.configure('one', 'one', routes.Rule(enabled=True), db, DEFAULT_STORE_ID)
        key = PREFIX + DEFAULT_STORE_ID + ':one:one'
        row = db.query(SystemSettings).filter_by(key=key).first()
        value = json.loads(row.value)
        value.update(max_drop_pct=30, lead_amount=2, cooldown_minutes=45, max_daily_changes=3)
        row.value = json.dumps(value); db.commit()
        # 界面「保存区间」实际只带这几个字段
        routes.configure('one', 'one', routes.Rule(
            min_price=90, use_system_min=False, smart_raise=True,
            max_price=200, strategy='takealot_buybox', enabled=True), db, DEFAULT_STORE_ID)
        saved = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert (saved['max_drop_pct'], saved['lead_amount'],
                saved['cooldown_minutes'], saved['max_daily_changes']) == (30, 2, 45, 3)
        assert (saved['min_price'], saved['max_price']) == (90, 200)


def test_new_rule_still_gets_every_default(monkeypatch):
    """只传少量字段也不能让新规则缺参数：缺了 plan() 只能吃硬编码默认。"""
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    with database() as db:
        value = routes.configure('one', 'one', routes.Rule(enabled=True), db, DEFAULT_STORE_ID)
        assert value['max_drop_pct'] == 15 and value['lead_amount'] == 1
        assert value['cooldown_minutes'] == 10 and value['max_daily_changes'] == 10
        assert value['strategy'] == 'takealot_buybox' and value['use_system_min'] is True


class _SellerResponse:
    def __init__(self, status, body=None, raw=None):
        self.status_code = status
        self._body = body
        self._raw = raw
    def json(self):
        if self._raw is not None: raise ValueError('Expecting value')
        return self._body


class _SellerClient:
    def __init__(self, response): self._response = response
    def get(self, *args, **kwargs): return self._response


def test_seller_api_failures_say_which_one_it_is():
    """密钥失效 / 限流 / 上游 5xx 不能长得一模一样（DIAG-OFFER-01）。

    原来 401、429、500 都是「卖家报价读取失败，暂停本轮调价」——用户没法判断
    该去改密钥还是等一会儿，只能反复点重试。状态码不是秘密，可以带出来。
    """
    from services.automatic_pricing import official_offer
    cases = {
        401: ('HTTP 401', '密钥'),
        403: ('HTTP 403', '密钥'),
        429: ('HTTP 429', '限流'),
        503: ('HTTP 503', '上游服务异常'),
    }
    for status, (code, hint) in cases.items():
        with pytest.raises(ValueError) as error:
            official_offer(_SellerClient(_SellerResponse(status)), 'key', 'one')
        message = str(error.value)
        assert code in message and hint in message, (status, message)
        assert 'Authorization' not in message and 'Key ' not in message

def test_seller_api_non_json_body_is_not_a_json_decode_error():
    from services.automatic_pricing import official_offer
    with pytest.raises(ValueError) as error:
        official_offer(_SellerClient(_SellerResponse(200, raw=b'<html>login</html>')), 'key', 'one')
    assert 'JSON' in str(error.value)

def test_reconcile_keeps_the_reason_and_leaks_nothing(monkeypatch):
    """核对失败要能归因，同时不许把路径/密钥带出来。"""
    import httpx as _httpx
    from services import automatic_pricing
    monkeypatch.setattr(routes, 'configured_key', lambda db: 'test')
    monkeypatch.setattr(_httpx, 'Client', lambda **kwargs: type('Client', (), {
        '__enter__': lambda self: self, '__exit__': lambda self, *args: None})())
    key = PREFIX + DEFAULT_STORE_ID + ':one:one'
    with database() as db:
        original = {'product_id': 'one', 'offer_id': 'one', 'sku': 'one', 'enabled': False, 'write_pending': True, 'revision': 3}
        db.add(SystemSettings(key=key, value=json.dumps(original))); db.commit()
        def refused(client, key, offer_id):
            raise ValueError('卖家接口读取报价失败（HTTP 401，卖家接口密钥无效或无权限）')
        monkeypatch.setattr(automatic_pricing, 'official_offer', refused)
        with pytest.raises(HTTPException) as error:
            routes.reconcile('one', 'one', db, DEFAULT_STORE_ID)
        assert error.value.status_code == 503
        detail = error.value.detail
        assert 'HTTP 401' in detail and '密钥' in detail, detail
        # 归因失败时退回「内部错误 ValueError」，等于什么也没说
        assert '内部错误' not in detail
        assert json.loads(db.query(SystemSettings).filter_by(key=key).first().value) == original

        def network(client, key, offer_id):
            raise _httpx.ConnectError('dial tcp: connect to /private/path failed')
        monkeypatch.setattr(automatic_pricing, 'official_offer', network)
        with pytest.raises(HTTPException) as error:
            routes.reconcile('one', 'one', db, DEFAULT_STORE_ID)
        detail = error.value.detail
        assert '连不上上游接口' in detail and '/private/path' not in detail, detail
