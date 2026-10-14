import json
import time
import pytest
from fastapi import HTTPException
from api import buybox_routes as routes,pricing_routes
from models import SystemSettings,DEFAULT_STORE_ID
from services.buybox_monitor import PREFIX
from tests.test_pricing_routes import database

def state(db,sku):
    return json.loads(db.query(SystemSettings).filter_by(key=f'{PREFIX}{DEFAULT_STORE_ID}:{sku}:{sku}').first().value)

def test_open_queues_once_without_timer_or_pricing_rule_and_deduplicates(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==2
        value=state(db,'one')
        assert value['request_once'] and not value['enabled'] and not value['web_requested']
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==0
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_rule:')).count()

def test_open_skips_no_stock_explicit_pause_and_site_cooldown(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        db.add(SystemSettings(key=f'{PREFIX}{DEFAULT_STORE_ID}:one:one',value=json.dumps({'status':'paused'})))
        db.add(SystemSettings(key=f'{PREFIX}{DEFAULT_STORE_ID}:two:two',value=json.dumps({'status':'error','error_code':'site_challenge','next_due':time.time()+1800})))
        db.commit()
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==0
        assert state(db,'one')['status']=='paused'
        profile=db.query(SystemSettings).filter_by(key=f'operations_profile:{DEFAULT_STORE_ID}:two').first()
        data=json.loads(profile.value);data['offers'][0]['stock']=0;profile.value=json.dumps(data)
        row=db.query(SystemSettings).filter_by(key=f'{PREFIX}{DEFAULT_STORE_ID}:two:two').first();row.value=json.dumps({'status':'error'});db.commit()
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==0

def test_open_recovers_old_extension_queue_without_heartbeat(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        db.add(SystemSettings(key=f'{PREFIX}{DEFAULT_STORE_ID}:one:one',value=json.dumps({'status':'queued','request_once':True,'web_requested':True})));db.commit()
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==2
        assert not state(db,'one')['web_requested']
        assert not db.query(SystemSettings).filter_by(key='_buybox_browser_collector').count()

def test_missing_key_and_foreign_store_cannot_queue(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'')
    with database() as db:
        for store in (DEFAULT_STORE_ID,'foreign'):
            with pytest.raises(HTTPException):routes.page_open(db,store)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).count()
