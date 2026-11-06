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


def run(monkeypatch, error=False, pause=False, enabled=True, once=False, initial_source=None, error_payload=None, failures=0, raises=None, prior=None):
    engine=create_engine('sqlite://');Base.metadata.create_all(engine);factory=sessionmaker(bind=engine)
    key=monitor.PREFIX+DEFAULT_STORE_ID+':p:1'
    with factory() as db:
        db.add(Product(id='p',store_id=DEFAULT_STORE_ID,takealot_url='https://www.takealot.com/p/PLID1'))
        db.add(SystemSettings(key=key,value=json.dumps(dict(product_id='p',offer_id='1',sku='a',enabled=enabled,request_once=once,interval_minutes=30,revision=1,buybox_price=95,last_success_at='2026-10-12T00:00:00+00:00',failures=failures))));db.commit()
        if initial_source:
            row=db.query(SystemSettings).filter_by(key=key).first()
            value=json.loads(row.value)
            value.update(source=initial_source,quote_time_source='collector_timestamp')
            row.value=json.dumps(value);db.commit()
        if prior:
            row=db.query(SystemSettings).filter_by(key=key).first()
            value=json.loads(row.value);value.update(prior);row.value=json.dumps(value);db.commit()
    import database
    monkeypatch.setattr(database,'SessionLocal',factory)
    monkeypatch.setattr(monitor,'configured_key',lambda db:'test')
    client=MagicMock();client.__enter__.return_value=client
    monkeypatch.setattr(monitor.httpx,'Client',lambda **kwargs:client)
    client.get.return_value=MagicMock(status_code=200,json=lambda:{'offer_id':1,'sku':'a','selling_price':100,'stock_at_takealot_total':5})
    async def collect(product,offer,seller_name=None):
        if pause:
            with factory()as db:
                row=db.query(SystemSettings).filter_by(key=key).first()
                value=json.loads(row.value);value.update(enabled=False,revision=2,status='paused');row.value=json.dumps(value);db.commit()
        if error_payload is not None: return error_payload
        if raises is not None: raise raises
        return {'error':'unavailable'} if error else {'buyboxPrice':100,'holdingBuybox':True,'offers':[{'price':90,'status':'in_stock'}]}
    monkeypatch.setattr(monitor,'collect',collect)
    monitor.process(key)
    if once:
        with factory() as db:
            row=db.query(SystemSettings).filter_by(key=key).first()
            value=json.loads(row.value);value['next_due']=0;row.value=json.dumps(value);db.commit()
        monitor.process(key)
    with factory()as db:value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
    return client,value

def test_monitor_reads_quotes_without_price_patch(monkeypatch):
    client,result=run(monkeypatch)
    assert result['status']=='success' and result['buybox_price']==100 and result['competitor_price']==90
    assert result['holding_buybox'] is None and result['price_matches_buybox'] is True
    assert result['source']=='cloud_browser' and result['quote_time_source']=='cloud_completion'
    assert client.get.call_count==1 and not client.patch.called and not client.post.called


def test_cloud_mismatch_rechecks_api_and_confirms_loss_in_two_rounds(monkeypatch):
    import database
    client,first=run(monkeypatch)
    assert first['price_ownership']['state']=='matched'
    async def lower_quote(product,offer,seller_name=None):
        return {'buyboxPrice':99,'offers':[]}
    monkeypatch.setattr(monitor,'collect',lower_quote)
    key=monitor.PREFIX+DEFAULT_STORE_ID+':p:1'
    for state in ['mismatch_pending','mismatch']:
        with database.SessionLocal() as db:
            row=db.query(SystemSettings).filter_by(key=key).first()
            value=json.loads(row.value);value['next_due']=0;row.value=json.dumps(value);db.commit()
        monitor.process(key)
        with database.SessionLocal() as db:
            value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert value['status']=='success' and value['price_ownership']['state']==state
        assert value['price_ownership']['own_price_source']=='official_live'
    assert client.get.call_count==5 and not client.patch.called and not client.post.called

def test_cloud_success_records_origin_and_replaces_prior_local_origin(monkeypatch):
    client,result=run(monkeypatch,initial_source='local_collector')
    assert result['source']=='cloud_browser'
    assert result['last_attempt_source']=='cloud_browser'
    assert result['quote_time_source']=='cloud_completion'
    assert not client.patch.called and not client.post.called

def test_cloud_failure_keeps_old_quote_origin_separate_from_attempt(monkeypatch):
    _,result=run(monkeypatch,error=True,initial_source='local_collector')
    assert result['source']=='local_collector' and result['quote_time_source']=='collector_timestamp'
    assert result['last_attempt_source']=='cloud_browser'
    assert result['last_success_at']=='2026-10-12T00:00:00+00:00'

def test_failed_collection_preserves_old_quote_and_backs_off(monkeypatch):
    client,result=run(monkeypatch,error=True)
    assert result['status']=='error' and result['buybox_price']==95 and result['failures']==1
    assert result['last_success_at']=='2026-10-12T00:00:00+00:00' and not client.patch.called
    assert result['next_due']>monitor.time.time()+1700

def test_site_challenge_retries_at_user_interval_instead_of_six_hours(monkeypatch):
    # 复现：站点持续拦截时，第 5 次失败后旧逻辑排到 6 小时后（17:26 -> 03:44）。
    _,result=run(monkeypatch,error_payload={'error':'site_challenge'},failures=5)
    assert result['status']=='error' and result['error_code']=='site_challenge'
    assert result['failures']==6
    # 30 分钟间隔、已失败 6 次：新逻辑仍按间隔重试，不再放大到 6 小时。
    assert monitor.time.time()+1700 < result['next_due'] <= monitor.time.time()+1830

def test_rate_limited_and_forbidden_are_both_treated_as_site_block(monkeypatch):
    for payload in ({'diagnostics':{'http_status':429}},{'diagnostics':{'http_status':403}}):
        _,result=run(monkeypatch,error_payload=payload,failures=5)
        assert result['error_code'] in monitor.THROTTLED_CODES
        assert result['next_due'] <= monitor.time.time()+1830

def test_genuine_fault_keeps_exponential_backoff(monkeypatch):
    # 解析器异常是本程序故障，指数退避仍然生效：5 次失败后间隔 30 分钟 -> 8 小时封顶 6 小时。
    _,result=run(monkeypatch,error_payload={'error':'TypeError: cannot read price'},failures=5)
    assert result['error_code']=='collector_error' and result['error_code'] not in monitor.THROTTLED_CODES
    assert result['next_due'] > monitor.time.time()+21000

def test_collector_crash_does_not_inherit_stale_price_not_found(monkeypatch):
    # 线上复现（2026-10-17）：浏览器 launch_persistent_context 抛 TargetClosedError，
    # collect() 根本没跑完，页面从未打开。旧逻辑却沿用上一轮的 error_code 和旧诊断，
    # 界面把「浏览器坏了」显示成「页面打开了但没有 Buybox 价」，连续几轮都查错了方向。
    _,result=run(monkeypatch, raises=RuntimeError('Target page, context or browser has been closed'),
                 prior={'error_code':'price_not_found','diagnostics':{'buybox_cards':2,'price_nodes':0}})
    assert result['error_code']=='collector_unavailable'
    assert result['error_stage']=='public_page'
    assert result['error'].startswith('采集器不可用')
    # 上一轮的计数必须清掉：留着 buybox_cards=2，界面会继续声称「Buybox 区块 2 个」。
    assert 'buybox_cards' not in result['diagnostics']
    assert result['diagnostics']['failure']=='RuntimeError'
    # 采集器不可用不是网站拦截，别混进「等验证解除」的分支；但也不该指数退避——
    # 它是可自愈的瞬时故障，按用户间隔重试即可，否则一次启动抖动就是 6 小时停采。
    assert result['error_code'] not in monitor.THROTTLED_CODES
    assert result['next_due'] <= monitor.time.time() + 1830

def test_busy_browser_retries_at_user_interval_instead_of_backing_off_for_hours(monkeypatch):
    # 复现：浏览器被一个卡死任务占着，process() 记 collector_busy。它既不是网站
    # 拦截、也不是确定性故障，但如果落进「自身故障」的指数退避，第 5 次失败
    # （30 分钟间隔）就排到 80 分钟后、第 7 次封顶 6 小时——浏览器恢复了这一行
    # 还在睡，而界面文案写的是「等下一轮自动重试」。
    from services.quote_browser import CollectorBusy
    _,result=run(monkeypatch,raises=CollectorBusy('采集浏览器被上一个任务占用超过 90 秒，本轮跳过'),failures=5)
    assert result['status']=='error' and result['error_code']=='collector_busy'
    assert result['error'].startswith('采集浏览器被上一个任务占用')
    assert result['error_code'] not in monitor.THROTTLED_CODES
    assert result['next_due'] <= monitor.time.time() + 1830


def test_value_error_still_keeps_classified_code(monkeypatch):
    # 跑完了但判定无有效行情（ValueError）：仍沿用 classify 给出的具体码，
    # 不能一律变成 collector_unavailable，否则又丢失了「页面结构变了」这类判断。
    _,result=run(monkeypatch,error_payload={'error':'price_not_found','diagnostics':{'buybox_cards':2,'price_nodes':0}})
    assert result['error_code']=='price_not_found'
    assert result['diagnostics']['buybox_cards']==2

def test_manual_retry_clears_failure_counter(monkeypatch):
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    key=monitor.PREFIX+DEFAULT_STORE_ID+':one:one'
    def seed(db,count):
        row=db.query(SystemSettings).filter_by(key=key).first()
        value=json.loads(row.value);value.update(failures=count,status='error',next_due=0);row.value=json.dumps(value);db.commit()
    with route_db()as db:
        routes.monitor_bulk(routes.MonitorBulk(enabled=True,interval_minutes=30,items=[{'product_id':'one','offer_id':'one'}]),db,DEFAULT_STORE_ID)
        seed(db,9)
        routes.monitor_bulk(routes.MonitorBulk(enabled=True,interval_minutes=30,items=[{'product_id':'one','offer_id':'one'}]),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        # 重试等于重新开始：计数归零、立即到期，否则下一次失败会直接跳回退避上限。
        assert value['failures']==0 and value['next_due']==0 and value['status']=='queued'
        seed(db,9)
        routes.monitor_bulk(routes.MonitorBulk(enabled=False,interval_minutes=30,items=[{'product_id':'one','offer_id':'one'}]),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        # 只是暂停不是重试，历史失败次数要保留，恢复后才知道真实状况。
        assert value['failures']==9 and not value['enabled']

def test_keep_backoff_changes_settings_without_retrying_blocked_pages(monkeypatch):
    # 被网站验证挡住时，界面劝用户"反复点立即采集没有用"，所以"改为 120 分钟"
    # 这个动作本身也不能顺手再打开一次被挡住的页面。
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    key=monitor.PREFIX+DEFAULT_STORE_ID+':one:one'
    items=[{'product_id':'one','offer_id':'one'}]
    with route_db()as db:
        routes.monitor_bulk(routes.MonitorBulk(enabled=True,interval_minutes=30,items=items),db,DEFAULT_STORE_ID)
        row=db.query(SystemSettings).filter_by(key=key).first()
        value=json.loads(row.value);value.update(failures=5,status='error',next_due=1234567.0,error_code='site_challenge')
        row.value=json.dumps(value);db.commit()
        routes.monitor_bulk(routes.MonitorBulk(enabled=True,keep_backoff=True,interval_minutes=120,items=items),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert value['interval_minutes']==120 and value['enabled']
        assert value['next_due']==1234567.0 and value['failures']==5 and value['status']=='error'
        # 不带 keep_backoff 时仍是"重新开始"，两种语义必须分得开。
        routes.monitor_bulk(routes.MonitorBulk(enabled=True,interval_minutes=120,items=items),db,DEFAULT_STORE_ID)
        value=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert value['next_due']==0 and value['failures']==0 and value['status']=='queued'


def test_concurrent_stop_is_not_overwritten(monkeypatch):
    client,result=run(monkeypatch,pause=True)
    assert not result['enabled'] and result['revision']==2 and result['status']=='paused'
    assert not client.patch.called

def test_disabled_monitor_has_no_network_calls(monkeypatch):
    client,result=run(monkeypatch,enabled=False)
    assert not client.get.called and result.get('status') is None

def test_one_shot_collects_once_and_does_not_repeat_after_due_time(monkeypatch):
    client,result=run(monkeypatch,enabled=False,once=True)
    assert result['status']=='success' and result['buybox_price']==100
    assert not result['enabled'] and not result['request_once']
    assert client.get.call_count==1 and not client.patch.called

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

def test_buybox_catalog_does_not_read_price_settings_or_compute_floors(monkeypatch):
    monkeypatch.setattr(routes,'system_floor',lambda product:(_ for _ in ()).throw(AssertionError('no pricing calculation allowed')))
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with route_db()as db:
        info=monitor.catalog(db,DEFAULT_STORE_ID)
        assert len(info)==2 and all(r['rule']=={}for r in info)
        routes.monitor_configure('two','two',routes.MonitorConfig(enabled=True,interval_minutes=5),db,DEFAULT_STORE_ID)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(routes.PREFIX)).count()


def test_collector_exception_keeps_type_without_private_message(monkeypatch):
    _,result=run(monkeypatch,raises=RuntimeError('/private/profile token=fixture-private'))
    assert result['diagnostics']['failure']=='RuntimeError'
    assert 'fixture-private' not in json.dumps(result)
    assert '/private/profile' not in json.dumps(result)


def test_collect_now_survives_a_null_interval(monkeypatch):
    """历史行的 interval_minutes 是 null 时，「立即采集」不能 422。

    原实现把 `old.get('interval_minutes',5)` 直接塞回 MonitorBulk：`.get` 只在
    键缺失时给默认值，键存在但值是 null 时返回 None，Pydantic 判「不是整数」，
    FastAPI 转 422。这条记录本身完全能采，只是配置写在 interval 字段存在之前。
    写路径不堵，null 还会被 web-open 原样写回库里，让读路径的归一永远有货。
    """
    monkeypatch.setattr(routes,'configured_key',lambda db:'test')
    with route_db()as db:
        routes.monitor_configure('two','two',routes.MonitorConfig(enabled=True,interval_minutes=30),db,DEFAULT_STORE_ID)
        key=monitor.PREFIX+DEFAULT_STORE_ID+':two:two'
        row=db.query(SystemSettings).filter_by(key=key).first()
        value=json.loads(row.value);value['interval_minutes']=None;row.value=json.dumps(value);db.commit()
        routes.collect_now('two','two',db,DEFAULT_STORE_ID)
        saved=json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        assert saved['interval_minutes']==5 and saved['request_once']
