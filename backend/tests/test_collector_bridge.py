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

def test_native_challenge_is_not_accepted_as_success_even_with_old_price(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        result=bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':269,'diagnostics':{'http_status':403,'page_title':'Just a moment...','requested_url':'https://www.takealot.com/p/PLID1?secret=discard'}}]),db,DEFAULT_STORE_ID)
        assert result['processed']==1 and result['successful']==0
        row=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        assert row['error_code']=='site_challenge' and not row.get('last_success_at')
        assert 'discard' not in str(row['diagnostics'])


def test_variant_error_and_foreign_sku_cannot_publish_prices(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'variantSwitchFailed':True}]),db,DEFAULT_STORE_ID)
        assert shared_snapshot(db,'one','one','one').get('error')
        with pytest.raises(HTTPException):bridge.products(db,'other-store')


def test_variant_failure_keeps_control_count_for_diagnosis(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'variantSwitchFailed':True,
            'diagnostics':{'http_status':200,'buybox_cards':1,'variant_controls':3}}]),db,DEFAULT_STORE_ID)
        row=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        assert row['diagnostics']['variant_controls']==3 and '3 个变体控件' in row['error']
        assert row['error_code']=='variant_unmatched' and not row.get('last_success_at')


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


def test_waiting_for_browser_verification_only_updates_heartbeat():
    with database() as db:
        result=bridge.web_open(bridge.WebOpen(heartbeat_only=True),db,DEFAULT_STORE_ID)
        assert result['queued']==0 and bridge.connected(db)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).count()


def test_preflight_has_no_task_or_heartbeat_side_effects():
    with database() as db:
        count=db.query(SystemSettings).count()
        assert bridge.status(DEFAULT_STORE_ID)=={'ready':True,'store':DEFAULT_STORE_ID,'mode':'quote_only'}
        assert db.query(SystemSettings).count()==count and not bridge.connected(db)
        with pytest.raises(HTTPException): bridge.status('other-store')


def test_legacy_and_browser_clients_keep_their_own_heartbeat_lease():
    import time
    with database() as db:
        bridge.web_open(bridge.WebOpen(heartbeat_only=True),db,DEFAULT_STORE_ID)
        browser=json.loads(db.query(SystemSettings).filter_by(key=bridge.BROWSER_HEARTBEAT).first().value)
        assert browser['kind']=='browser_extension' and 80 < browser['expires_at']-time.time() <= 90

        bridge.products(db,DEFAULT_STORE_ID)
        local=json.loads(db.query(SystemSettings).filter_by(key=bridge.HEARTBEAT).first().value)
        assert local['kind']=='local_collector' and 890 < local['expires_at']-time.time() <= 900

        local_expiry=local['expires_at']
        bridge.web_open(bridge.WebOpen(heartbeat_only=True),db,DEFAULT_STORE_ID)
        assert json.loads(db.query(SystemSettings).filter_by(key=bridge.HEARTBEAT).first().value)['expires_at']==local_expiry
        assert bridge.connected(db) and bridge.collector_source(db)=='local_collector'

        bridge.products(db,DEFAULT_STORE_ID,'browser_extension')
        browser=json.loads(db.query(SystemSettings).filter_by(key=bridge.BROWSER_HEARTBEAT).first().value)
        assert browser['kind']=='browser_extension' and 80 < browser['expires_at']-time.time() <= 90
        local_row=db.query(SystemSettings).filter_by(key=bridge.HEARTBEAT).first()
        local=json.loads(local_row.value);local['expires_at']=time.time()-1;local_row.value=json.dumps(local);db.commit()
        assert bridge.connected(db) and bridge.collector_source(db)=='browser_extension'
        browser_row=db.query(SystemSettings).filter_by(key=bridge.BROWSER_HEARTBEAT).first()
        browser=json.loads(browser_row.value);browser['expires_at']=time.time()-1;browser_row.value=json.dumps(browser);db.commit()
        assert not bridge.connected(db) and bridge.collector_source(db)=='cloud_browser'


def age_lease(db,item,seconds):
    import time
    lease=db.query(SystemSettings).filter_by(key=bridge.CLAIM_PREFIX+item['productId']).first()
    job=json.loads(lease.value);job.update(claimed_at=time.time()-seconds,expires_at=time.time()-seconds+900)
    config=json.loads(job['claim']);config['next_due']=job['expires_at'];job['claim']=json.dumps(config,ensure_ascii=False)
    db.query(SystemSettings).filter_by(key=job['monitor_key']).first().value=job['claim']
    lease.value=json.dumps(job);db.commit()


def test_buffered_legacy_quote_is_not_freshened_at_receipt(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch);age_lease(db,item,660)
        result=bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99}]),db,DEFAULT_STORE_ID)
        assert result['processed']==1
        assert shared_snapshot(db,'one','one','one').get('error')


def test_bounded_per_item_timestamp_keeps_recent_quote_usable(monkeypatch):
    from datetime import datetime,timezone
    with database() as db:
        item=setup(db,monkeypatch);age_lease(db,item,660)
        bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'collectedAt':datetime.now(timezone.utc).isoformat()}]),db,DEFAULT_STORE_ID)
        assert shared_snapshot(db,'one','one','one')['buyboxPrice']==99


def test_future_timestamp_cannot_make_old_quote_fresh(monkeypatch):
    from datetime import datetime,timezone,timedelta
    with database() as db:
        item=setup(db,monkeypatch);age_lease(db,item,660)
        bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99,'collectedAt':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}]),db,DEFAULT_STORE_ID)
        assert shared_snapshot(db,'one','one','one').get('error')


def test_wrong_sku_report_does_not_consume_the_valid_job(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch)
        assert bridge.offers(bridge.Report(items=[{**item,'sku':'wrong','buyboxPrice':99}]),db,DEFAULT_STORE_ID)['skipped']==1
        assert bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':99}]),db,DEFAULT_STORE_ID)['processed']==1


def test_expired_job_is_recovered_and_late_report_is_ignored(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch);age_lease(db,item,901)
        bridge.web_open(bridge.WebOpen(initial=True),db,DEFAULT_STORE_ID)
        next_item=next(r for r in bridge.products(db,DEFAULT_STORE_ID)['items'] if r['sku']=='one')
        assert next_item['productId']!=item['productId']
        assert bridge.offers(bridge.Report(items=[{**item,'buyboxPrice':70}]),db,DEFAULT_STORE_ID)['skipped']==1
        assert bridge.offers(bridge.Report(items=[{**next_item,'buyboxPrice':99}]),db,DEFAULT_STORE_ID)['processed']==1


def test_expired_job_cannot_restore_a_concurrent_pause(monkeypatch):
    with database() as db:
        item=setup(db,monkeypatch);age_lease(db,item,901)
        pricing_routes.monitor_configure('one','one',pricing_routes.MonitorConfig(enabled=False),db,DEFAULT_STORE_ID)
        bridge.web_open(bridge.WebOpen(initial=True),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        assert value['status']=='paused' and not value['enabled'] and not value['request_once']
