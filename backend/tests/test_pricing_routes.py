import json
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
