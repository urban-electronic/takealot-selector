import json
import pytest
from fastapi import HTTPException
from api import collector_routes as bridge, pricing_routes
from models import DEFAULT_STORE_ID, SystemSettings
from tests.test_pricing_routes import database
from services.buybox_monitor import PREFIX
from services.automatic_pricing import shared_snapshot


def setup(db, monkeypatch):
    monkeypatch.setattr(pricing_routes,'configured_key',lambda db:'test')
    pricing_routes.monitor_configure('one','one',pricing_routes.MonitorConfig(enabled=True,interval_minutes=5),db,DEFAULT_STORE_ID)
    return bridge.products(db,DEFAULT_STORE_ID)['items'][0]


def test_original_collector_roundtrip_keeps_pricing_disabled_and_shares_quote(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        assert bridge.connected(db) and item['sku']=='one'
        result=bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'offers':[{'price':99,'status':'in_stock'}]}]),db,DEFAULT_STORE_ID)
        assert result['processed']==1 and result['computeEnqueued']==0
        assert shared_snapshot(db,'one','one','one')['buyboxPrice']==99
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_rule:')).count()
        assert bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':50}]),db,DEFAULT_STORE_ID)['skipped']==1


def test_pause_invalidates_inflight_local_collection(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        pricing_routes.monitor_configure('one','one',pricing_routes.MonitorConfig(enabled=False),db,DEFAULT_STORE_ID)
        result=bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99}]),db,DEFAULT_STORE_ID)
        assert result['skipped']==1
        row=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        assert not row['enabled'] and 'buybox_price' not in row


def test_variant_error_and_foreign_sku_cannot_publish_prices(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'variantSwitchFailed':True}]),db,DEFAULT_STORE_ID)
        assert shared_snapshot(db,'one','one','one').get('error')
        with pytest.raises(HTTPException):bridge.products(db,'other-store')


def test_web_open_queues_only_stocked_skus_without_enabling_pricing(monkeypatch):
    with database() as db:
        profile=db.query(SystemSettings).filter_by(key='operations_profile:'+DEFAULT_STORE_ID+':two').first()
        value=json.loads(profile.value);value['offers'][0]['stock']=0;profile.value=json.dumps(value);db.commit()
        result=bridge.web_open(bridge.WebOpen(initial=True),db,DEFAULT_STORE_ID)
        assert result['queued']==1 and not result['price_changed']
        item=bridge.products(db,DEFAULT_STORE_ID)['items'][0]
        assert item['sku']=='one'
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_rule:')).count()
        assert bridge.web_open(bridge.WebOpen(),db,DEFAULT_STORE_ID)['queued']==0
        assert bridge.release(bridge.Release(product_ids=[item['productId']]),db,DEFAULT_STORE_ID)['released']==1
        assert bridge.products(db,DEFAULT_STORE_ID)['items'][0]['sku']=='one'


def test_reopening_page_can_retry_failed_quote_without_changing_monitors():
    import time
    with database() as db:
        key=PREFIX+DEFAULT_STORE_ID+':one:one'
        db.add(SystemSettings(key=key,value=json.dumps({'product_id':'one','offer_id':'one','sku':'one','status':'error','enabled':False,'next_due':time.time()+1800,'interval_minutes':5})));db.commit()
        bridge.web_open(bridge.WebOpen(initial=False),db,DEFAULT_STORE_ID)
        assert json.loads(db.query(SystemSettings).filter_by(key=key).first().value)['status']=='error'
        bridge.web_open(bridge.WebOpen(initial=True),db,DEFAULT_STORE_ID)
        result=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert result['request_once'] and not result['enabled'] and result['status']=='queued'


def test_web_open_respects_explicit_per_sku_pause(monkeypatch):
    with database() as db:
        setup(db,monkeypatch)
        pricing_routes.monitor_configure('one','one',pricing_routes.MonitorConfig(enabled=False),db,DEFAULT_STORE_ID)
        bridge.web_open(bridge.WebOpen(initial=True),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        assert value['status']=='paused' and not value['request_once'] and not value['enabled']
