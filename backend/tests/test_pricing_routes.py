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
