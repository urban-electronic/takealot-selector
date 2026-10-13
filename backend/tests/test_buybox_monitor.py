import json
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import Product,SystemSettings,DEFAULT_STORE_ID
from services import buybox_monitor as monitor
from api import pricing_routes as routes
from tests.test_pricing_routes import database as route_db
import pytest
from fastapi import HTTPException


def run(monkeypatch, error=False, pause=False, enabled=True):
    engine=create_engine('sqlite://');Base.metadata.create_all(engine);factory=sessionmaker(bind=engine)
    key=monitor.PREFIX+DEFAULT_STORE_ID+':p:1'
    with factory() as db:
        db.add(Product(id='p',store_id=DEFAULT_STORE_ID,takealot_url='https://www.takealot.com/p/PLID1'))
        db.add(SystemSettings(key=key,value=json.dumps(dict(product_id='p',offer_id='1',sku='a',enabled=enabled,interval_minutes=30,revision=1,buybox_price=95,last_success_at='2026-10-12T00:00:00+00:00'))));db.commit()
    import database
    monkeypatch.setattr(database,'SessionLocal',factory)
    monkeypatch.setattr(monitor,'configured_key',lambda db:'test')
    client=MagicMock();client.__enter__.return_value=client
    monkeypatch.setattr(monitor.httpx,'Client',lambda **kwargs:client)
    client.get.return_value=MagicMock(status_code=200,json=lambda:{'offer_id':1,'sku':'a','selling_price':100,'stock_at_takealot_total':5})
    async def collect(product,offer):
        if pause:
            with factory()as db:
                row=db.query(SystemSettings).filter_by(key=key).first()
                value=json.loads(row.value);value.update(enabled=False,revision=2,status='paused');row.value=json.dumps(value);db.commit()
        return {'error':'unavailable'} if error else {'buyboxPrice':100,'holdingBuybox':True,'offers':[{'price':90,'status':'in_stock'}]}
    monkeypatch.setattr(monitor,'collect',collect)
    monitor.process(key)
    with factory()as db:value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
    return client,value

def test_monitor_reads_quotes_without_price_patch(monkeypatch):
    client,result=run(monkeypatch)
    assert result['status']=='success' and result['buybox_price']==100 and result['competitor_price']==90
    assert result['holding_buybox'] is None and result['price_matches_buybox'] is True
    assert client.get.call_count==1 and not client.patch.called and not client.post.called

def test_failed_collection_preserves_old_quote_and_backs_off(monkeypatch):
    client,result=run(monkeypatch,error=True)
    assert result['status']=='error' and result['buybox_price']==95 and result['failures']==1
    assert result['last_success_at']=='2026-10-12T00:00:00+00:00' and not client.patch.called
    assert result['next_due']>monitor.time.time()+1700

def test_concurrent_stop_is_not_overwritten(monkeypatch):
    client,result=run(monkeypatch,pause=True)
    assert not result['enabled'] and result['revision']==2 and result['status']=='paused'
    assert not client.patch.called

def test_disabled_monitor_has_no_network_calls(monkeypatch):
    client,result=run(monkeypatch,enabled=False)
    assert not client.get.called and result.get('status') is None

def test_monitor_can_start_without_profit_floor_and_keeps_pricing_off(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with route_db()as db:
        routes.monitor_configure('two','two',routes.MonitorConfig(enabled=True,interval_minutes=30),db,DEFAULT_STORE_ID)
        item=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows']if r['product_id']=='two')
        assert item['monitor']['enabled'] and not item['rule'].get('enabled')
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(routes.PREFIX)).count()
        routes.monitor_configure('two','two',routes.MonitorConfig(),db,DEFAULT_STORE_ID)
        routes.collect_now('two','two',db,DEFAULT_STORE_ID)
        item=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows']if r['product_id']=='two')
        assert not item['monitor']['enabled'] and item['monitor']['request_once']

def test_monitor_bulk_validation_is_atomic_and_store_scoped(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with route_db()as db:
        data=routes.MonitorBulk(enabled=True,items=[{'product_id':'one','offer_id':'one'},{'product_id':'unknown','offer_id':'x'}])
        with pytest.raises(HTTPException):routes.monitor_bulk(data,db,DEFAULT_STORE_ID)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(monitor.PREFIX)).count()
        with pytest.raises(HTTPException):routes.monitor_configure('one','one',routes.MonitorConfig(enabled=True),db,'other-store')

def test_five_minute_segment_settings_do_not_activate_pricing(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with route_db()as db:
        data=routes.MonitorBulk(enabled=True,interval_minutes=5,segment='第一段',items=[{'product_id':'two','offer_id':'two'}])
        routes.monitor_bulk(data,db,DEFAULT_STORE_ID)
        item=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows']if r['product_id']=='two')
        assert item['monitor']['interval_minutes']==5 and item['monitor']['segment']=='第一段'
        assert not item['rule'].get('enabled')
        routes.monitor_configure('two','two',routes.MonitorConfig(enabled=False),db,DEFAULT_STORE_ID)
        item=next(r for r in routes.listing(db,DEFAULT_STORE_ID)['rows']if r['product_id']=='two')
        assert item['monitor']['segment']=='第一段' and not item['monitor']['enabled']
