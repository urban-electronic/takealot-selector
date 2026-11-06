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

def test_open_does_not_bypass_the_interval_for_skus_already_collected(monkeypatch):
    """打开页面不能把「刚采过」的 SKU 再拉一遍：这是最小间隔被绕过的入口。

    前端每次加载都会调这个接口。原实现对着有库存的 SKU 无条件 `next_due=0`、
    强制排队，等于每开一次页面就把全部 SKU 采一轮——后台把间隔从 5 分钟调到
    30 分钟也拦不住，排期形同虚设。
    """
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        now=time.time()
        db.add(SystemSettings(key=f'{PREFIX}{DEFAULT_STORE_ID}:one:one',value=json.dumps(
            {'status':'success','enabled':True,'interval_minutes':30,'last_success_at':now,'next_due':now+1500})))
        db.commit()
        # 只补那条从没采过的，未到期的那条不许动
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==1
        value=state(db,'one')
        assert value['status']=='success' and value['next_due']>now
        assert not value.get('request_once')

def test_open_queues_a_due_sku_and_records_now_as_next_due(monkeypatch):
    """到期的记录仍可顺手补采，但 next_due 写 now，不写 0。

    0 是「还没排期」的哨兵值：写进去会掩盖这条记录的真实到期时间，界面会把它
    算成「已延迟 1792265800 秒」。到期与 0 在选取上完全等价，语义却差很远。
    """
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with database() as db:
        now=time.time()
        db.add(SystemSettings(key=f'{PREFIX}{DEFAULT_STORE_ID}:one:one',value=json.dumps(
            {'status':'success','enabled':True,'interval_minutes':30,'next_due':now-10})))
        db.commit()
        assert routes.page_open(db,DEFAULT_STORE_ID)['queued']==2
        value=state(db,'one')
        assert value['status']=='queued' and value['request_once']
        assert value['next_due']>=now-1
