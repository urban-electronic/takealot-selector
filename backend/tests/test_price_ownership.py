import json
import pytest
from contextlib import nullcontext
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from api import collector_routes as bridge
from models import SystemSettings, DEFAULT_STORE_ID
from services import price_ownership as check, automatic_pricing, takealot_seller_api
from tests.test_collector_bridge import database, setup


@pytest.fixture(autouse=True)
def isolated_http(monkeypatch):
    monkeypatch.setattr(check.httpx,'Client',lambda **kwargs:nullcontext(object()))


def test_original_price_inference_and_two_round_loss_are_not_identity():
    first=check.estimate({},269,269,'task_snapshot','old')
    assert first['holding'] and first['state']=='matched' and not first['identity_verified']
    second=check.estimate(first,268,269,'official_live','new')
    assert second['holding'] and second['state']=='mismatch_pending'
    failed=check.pending(second,failed=True)
    assert failed['losing_rounds']==1
    third=check.estimate(failed,268,269,'official_live','newer')
    assert not third['holding'] and third['state']=='mismatch'
    restored=check.estimate(third,269,269,'official_live','last')
    assert restored['holding'] and restored['losing_rounds']==0


def test_invalid_prices_do_not_infer_holding():
    for value in [True,None,0,-1,float('nan'),float('inf')]:
        assert check.estimate({},269,value,'task_snapshot','now')['state']=='verification_failed'


def report(db,monkeypatch,buybox):
    item=setup(db,monkeypatch)
    tasks=BackgroundTasks()
    result=bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':buybox,'holdingBuybox':True}]),db,DEFAULT_STORE_ID,'local_collector',tasks)
    row=db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_monitor:')).first()
    return row.key, result, tasks


def test_same_price_is_inferred_without_trusting_client_identity(monkeypatch):
    with database() as db:
        key,result,tasks=report(db,monkeypatch,100)
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert result['successful']==1 and not tasks.tasks
        assert value['price_ownership']['state']=='matched'
        assert value['price_ownership']['own_price_source']=='task_snapshot'
        assert value['holding_buybox'] is None
        assert not value['snapshot'].get('holdingBuyboxVerified')


def test_mismatch_is_reported_before_live_api_verification(monkeypatch):
    with database() as db:
        key,result,tasks=report(db,monkeypatch,99)
        assert result['successful']==1 and len(tasks.tasks)==1
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert value['price_ownership']['state']=='checking'
        # The store price changed since task claim. API now agrees with the page.
        monkeypatch.setattr('database.SessionLocal',sessionmaker(bind=db.bind))
        monkeypatch.setattr(takealot_seller_api,'configured_key',lambda db:'fixture')
        monkeypatch.setattr(automatic_pricing,'official_offer',lambda *args:{'sku':'one','selling_price':99})
        task=tasks.tasks[0];task.func(*task.args,**task.kwargs)
        db.expire_all(); updated=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert updated['price_ownership']['state']=='matched'
        assert updated['price_ownership']['own_price_source']=='official_live'
        assert updated['last_success_at']==value['last_success_at'] and updated['snapshot']==value['snapshot']
        assert updated['holding_buybox'] is None


def test_official_failure_keeps_valid_quote_and_time(monkeypatch):
    with database() as db:
        key,result,tasks=report(db,monkeypatch,99)
        before=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        monkeypatch.setattr('database.SessionLocal',sessionmaker(bind=db.bind))
        monkeypatch.setattr(takealot_seller_api,'configured_key',lambda db:'fixture')
        def fail(*args): raise ValueError('secret must never enter settings')
        monkeypatch.setattr(automatic_pricing,'official_offer',fail)
        task=tasks.tasks[0];task.func(*task.args,**task.kwargs)
        db.expire_all();after=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert after['price_ownership']['state']=='verification_failed'
        assert after['status']=='success' and after['buybox_price']==99 and after['last_success_at']==before['last_success_at']
        assert 'secret' not in json.dumps(after)


@pytest.mark.parametrize('change_kind',['pause','new_quote'])
def test_concurrent_change_during_official_check_wins(monkeypatch,change_kind):
    with database() as db:
        key,result,tasks=report(db,monkeypatch,99)
        factory=sessionmaker(bind=db.bind)
        monkeypatch.setattr('database.SessionLocal',factory)
        monkeypatch.setattr(takealot_seller_api,'configured_key',lambda db:'fixture')
        def changed(*args):
            with factory() as other:
                row=other.query(SystemSettings).filter_by(key=key).first();v=json.loads(row.value)
                if change_kind=='pause':v.update(status='paused',enabled=False,request_once=False)
                else:v.update(buybox_price=120,last_success_at='2026-10-15T20:00:00+00:00')
                row.value=json.dumps(v);other.commit()
            return {'sku':'one','selling_price':99}
        monkeypatch.setattr(automatic_pricing,'official_offer',changed)
        task=tasks.tasks[0];task.func(*task.args,**task.kwargs)
        db.expire_all();after=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert after['price_ownership']['state']=='checking'
        if change_kind=='pause':assert after['status']=='paused'
        else:assert after['buybox_price']==120 and after['last_success_at']=='2026-10-15T20:00:00+00:00'


def test_mismatched_sku_does_not_confirm_price(monkeypatch):
    with database() as db:
        key,_,tasks=report(db,monkeypatch,99)
        monkeypatch.setattr('database.SessionLocal',sessionmaker(bind=db.bind))
        monkeypatch.setattr(takealot_seller_api,'configured_key',lambda db:'fixture')
        monkeypatch.setattr(automatic_pricing,'official_offer',lambda *args:{'sku':'other','selling_price':99})
        task=tasks.tasks[0];task.func(*task.args,**task.kwargs)
        db.expire_all();value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert value['price_ownership']['state']=='verification_failed'


def test_background_injected_through_http_route(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from database import get_db
    monkeypatch.setattr('tests.test_pricing_routes.create_engine',lambda *args:create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool))
    with database() as db:
        item=setup(db,monkeypatch)
        app=FastAPI();app.include_router(bridge.router)
        app.dependency_overrides[get_db]=lambda:db
        app.dependency_overrides[bridge.get_store_id]=lambda:DEFAULT_STORE_ID
        ran=[]
        monkeypatch.setattr(check,'verify_batch',lambda jobs:ran.extend(jobs))
        # ASGI transport only, no real upstream request.
        client=TestClient(app)
        response=client.post('/api/collector/offers',json={'items':[{**item,'buyboxPrice':99}]})
        assert response.status_code==200 and response.json()['successful']==1
        assert len(ran)==1
