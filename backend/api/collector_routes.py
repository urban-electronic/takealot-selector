"""Original PricePilot collector protocol; authenticated, default-store, quote-only."""
import json
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from models import SystemSettings, DEFAULT_STORE_ID
from api.store_routes import get_store_id
from services.buybox_monitor import PREFIX, catalog, interval_seconds, normalize_interval_minutes

router = APIRouter(prefix='/api/collector', tags=['collector'])
HEARTBEAT = '_buybox_local_collector'
BROWSER_HEARTBEAT = '_buybox_browser_collector'
CLAIM_PREFIX = '_buybox_collector_claim:'
# 云端采集循环只在「本机/扩展采集器真的在干活」时才让位。光有心跳不够——
# 本机采集器崩了、没清心跳、或只轮询不回报，都会让心跳一直活着却什么都不采，
# 于是云端被白白压制、所有 SKU 既不采也不报错，界面还显示「本机采集器接管中，属正常」。
# 用「心跳存活 且 最近 2 分钟内有成功回报」判定本机确实在跑；否则云端接管。
LOCAL_GRACE_SECONDS = 120

def connected(db):
    now=time.time()
    return any(row and json.loads(row.value).get('expires_at',0)>now for row in (
        db.query(SystemSettings).filter_by(key=HEARTBEAT).first(),
        db.query(SystemSettings).filter_by(key=BROWSER_HEARTBEAT).first()))

def local_active(db):
    """本机/扩展采集器是否真的在干活：心跳存活 且 近期有成功回报。

    `connected()` 只看心跳，会被「崩了没清心跳」骗过。采集循环据此让位时必须用这一层，
    否则本机死了云端也起不来，SKU 集体停采却看着像正常交接。前端据此把 source 标成
    cloud_browser，banner 也不会再写「本机接管中，属正常」。"""
    now=time.time()
    for key in (HEARTBEAT, BROWSER_HEARTBEAT):
        row=db.query(SystemSettings).filter_by(key=key).first()
        if not row: continue
        value=json.loads(row.value)
        if value.get('expires_at',0)>now and now-(value.get('last_report_at') or 0) < LOCAL_GRACE_SECONDS:
            return True
    return False

def collector_source(db):
    now=time.time()
    for key,kind in ((HEARTBEAT,'local_collector'),(BROWSER_HEARTBEAT,'browser_extension')):
        row=db.query(SystemSettings).filter_by(key=key).first()
        if row and json.loads(row.value).get('expires_at',0)>now: return kind
    return 'cloud_browser'

def _mark_local_report(db, report_source, now):
    """本机/扩展采集器成功回传一条报价，刷新它的「最近回报时间」。

    这是 `local_active()` 判定本机是否真在干活的唯一证据。浏览器扩展与本地程序都走这里；
    pricepilot_relay 也归到本地采集器一侧。row 已在当前 session（touch 写入过），直接改值即可。"""
    key = BROWSER_HEARTBEAT if report_source=='browser_extension' else HEARTBEAT
    row=db.query(SystemSettings).filter_by(key=key).first()
    if not row: return
    value=json.loads(row.value)
    value['last_report_at']=now
    row.value=json.dumps(value,ensure_ascii=False)

def touch(db, kind=None):
    mode=kind if kind in ('local_collector','browser_extension') else 'local_collector'
    key=BROWSER_HEARTBEAT if mode=='browser_extension' else HEARTBEAT
    row=db.query(SystemSettings).filter_by(key=key).first()
    value=json.dumps({'kind':mode,'last_seen_at':datetime.now(timezone.utc).isoformat(),'expires_at':time.time()+(90 if mode=='browser_extension' else 900)})
    if row: row.value=value
    else: db.add(SystemSettings(key=key,value=value))

def collector_kind(value):
    """Legacy PricePilot sends no marker; only the bundled extension identifies itself."""
    return 'browser_extension' if value == 'browser_extension' else 'local_collector'

def assert_default(store):
    if store != DEFAULT_STORE_ID: raise HTTPException(400,'采集器仅绑定自营店铺')

@router.get('/status')
def status(store:str=Depends(get_store_id)):
    """Authenticated preflight: no leases, heartbeat changes, prices or credentials."""
    assert_default(store)
    return {'ready':True,'store':store,'mode':'quote_only'}

def recover_expired(db, now):
    """Return abandoned jobs only when their claimed settings remain unchanged."""
    for lease in db.query(SystemSettings).filter(SystemSettings.key.startswith(CLAIM_PREFIX)).all():
        job=json.loads(lease.value)
        if job.get('expires_at',0)>now: continue
        config=json.loads(job['claim'])
        config.update(status='queued' if config.get('enabled') or config.get('request_once') else 'paused', next_due=now)
        db.query(SystemSettings).filter_by(key=job['monitor_key'],value=job['claim']).update({'value':json.dumps(config,ensure_ascii=False)})
        db.delete(lease)
    db.flush()


def observation_time(item, job, received):
    # Older Python collectors buffer whole batches and have no per-item timestamp.
    # Lease start is a conservative lower bound; receipt must not freshen old quotes.
    started=job.get('claimed_at',job['expires_at']-900)
    try:
        value=datetime.fromisoformat(item['collectedAt'])
        observed=value.timestamp() if value.tzinfo is not None else None
    except (KeyError,TypeError,ValueError,OverflowError):
        observed=None
    if observed is not None and started<=observed<=received:
        return observed, 'collector_timestamp'
    return min(started,received), 'lease_start'

def relay_observation_time(item, received):
    """Forwarded observations predate the lease; never replace their source clock."""
    try:
        value=datetime.fromisoformat(item['collectedAt'].replace('Z','+00:00'))
        observed=value.timestamp() if value.tzinfo is not None else None
    except (KeyError,AttributeError,TypeError,ValueError,OverflowError):
        observed=None
    if observed is None or not 0 <= received-observed <= 600:
        return None
    return observed

@router.get('/products')
def products(db:Session=Depends(get_db),store:str=Depends(get_store_id),
             x_collector_client:Optional[str]=Header(default=None,alias='X-Collector-Client')):
    assert_default(store);touch(db,collector_kind(x_collector_client))
    now=time.time();items=[]
    recover_expired(db,now)
    for item in sorted(catalog(db,store),key=lambda x:x['monitor'].get('next_due',0)):
        monitor=item['monitor']
        if not (monitor.get('enabled') or monitor.get('request_once')) or monitor.get('next_due',0)>now: continue
        key=PREFIX+store+':'+item['product_id']+':'+item['offer_id']
        row=db.query(SystemSettings).filter_by(key=key).first()
        original=row.value
        # 领取时就把 next_due 推到「一次完整间隔」之后，与 /offers 回报写入的
        # 排期用同一个下限（云端最小 30 分钟）。原先这里写死 now+900：900 秒是
        # **租约长度**，不是排期长度，两者混用会让「被领取但没回报」的记录在
        # 15 分钟后就又到期，间隔形同虚设。
        config=json.loads(original);config.update(status='running',next_due=now+interval_seconds(config.get('interval_minutes')),current_price=item['price'],stock=item['stock'])
        claimed=json.dumps(config,ensure_ascii=False)
        if db.query(SystemSettings).filter_by(key=key,value=original).update({'value':claimed})!=1: continue
        token=str(uuid.uuid4())
        db.add(SystemSettings(key=CLAIM_PREFIX+token,value=json.dumps({'monitor_key':key,'sku':item['sku'],'claim':claimed,'claimed_at':now,'expires_at':now+900})))
        task={'productId':token,'shopId':store,'sku':item['sku'],'name':item['name'],'url':item['url'],'myPrice':item['price'],'myStock':item['stock']}
        # 本机/扩展采集器共用同一个解析器，显式变体维度也必须随任务下发，
        # 否则用户在这条 SKU 上填的配置只在云端路径生效。没配时不加字段，
        # 老采集器拿到的任务形状不变。
        labels=monitor.get('variant_labels')
        if isinstance(labels,list) and labels: task['variantLabels']=labels[:6]
        items.append(task)
        if len(items)>=20: break
    db.commit()
    return {'items':items}

class WebOpen(BaseModel):
    initial:bool=False
    heartbeat_only:bool=False

@router.post('/web-open')
def web_open(data:WebOpen,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    """Page-attached read-only collection: refresh stocked SKUs, never enable pricing."""
    assert_default(store)
    touch(db, 'browser_extension')
    if data.heartbeat_only:
        db.commit();return {'queued':0,'price_changed':False}
    now=time.time();queued=0
    recover_expired(db,now)
    for item in catalog(db,store):
        stock=item.get('stock')
        if isinstance(stock,bool) or not isinstance(stock,(int,float)) or stock<=0 or not item.get('url'): continue
        old=item['monitor']
        if old.get('status') in ('running','paused') or (old.get('next_due',0)>now and not (data.initial and old.get('status')=='error')): continue
        value={**old,'product_id':item['product_id'],'offer_id':item['offer_id'],'sku':item['sku'],
            'enabled':old.get('enabled',False),'request_once':True,'web_requested':True,'interval_minutes':normalize_interval_minutes(old.get('interval_minutes')),
            # 与 page-open 同一口径：这条记录的到期门控在上面，能走到这里说明它
            # 本来就到期了，next_due 写 now 即可；写 0 会把这个「没排期」的哨兵值
            # 落库，界面按它算延迟就会得到一个天文数字。
            'revision':old.get('revision',0)+1,'status':'queued','next_due':now}
        key=PREFIX+store+':'+item['product_id']+':'+item['offer_id']
        row=db.query(SystemSettings).filter_by(key=key).first()
        if row: row.value=json.dumps(value,ensure_ascii=False)
        else: db.add(SystemSettings(key=key,value=json.dumps(value,ensure_ascii=False)))
        queued+=1
    db.commit()
    return {'queued':queued,'price_changed':False}

class Report(BaseModel):
    items:list[dict]=Field(max_length=100)

@router.post('/offers')
def offers(data:Report,db:Session=Depends(get_db),store:str=Depends(get_store_id),
           x_collector_client:Optional[str]=Header(default=None,alias='X-Collector-Client'),
           background_tasks:BackgroundTasks=None):
    assert_default(store);touch(db,collector_kind(x_collector_client));processed=skipped=successful=0
    report_source='pricepilot_relay' if x_collector_client=='pricepilot_relay' else collector_kind(x_collector_client)
    recover_expired(db,time.time()); price_checks=[]
    for item in data.items:
        lease=db.query(SystemSettings).filter_by(key=CLAIM_PREFIX+str(item.get('productId',''))).first()
        if not lease: skipped+=1;continue
        job=json.loads(lease.value)
        row=db.query(SystemSettings).filter_by(key=job['monitor_key']).first()
        if item.get('sku')!=job['sku']:
            skipped+=1;continue
        if not row or row.value!=job['claim']:
            skipped+=1;db.delete(lease);continue
        config=json.loads(row.value);now=time.time();bb=item.get('buyboxPrice')
        relayed=x_collector_client=='pricepilot_relay'
        relayed_at=relay_observation_time(item,now) if relayed else None
        from services.collection_diagnostics import classify,safe_diagnostics
        raw=item.get('diagnostics') or {}
        diagnostics=safe_diagnostics(raw)
        code,message=classify({**item,'diagnostics':diagnostics})
        valid=not code and not item.get('error') and not item.get('variantSwitchFailed') and not isinstance(bb,bool) and isinstance(bb,(int,float)) and math.isfinite(bb) and bb>0 and (not relayed or relayed_at is not None)
        snapshot={k:item[k] for k in ('buyboxPrice','buyboxSeller','buyboxType','holdingBuybox','offers','variantSwitchFailed','error','stockMapped','stockStatus') if k in item}
        # The original parser may include other sellers only after location/login is established.
        quotes=snapshot.get('offers') or []
        quotes=[o for o in quotes if isinstance(o,dict) and not isinstance(o.get('price'),bool) and isinstance(o.get('price'),(int,float)) and math.isfinite(o['price']) and o['price']>0]
        snapshot['offers']=quotes
        if diagnostics: snapshot['diagnostics']=diagnostics
        at=datetime.now(timezone.utc).isoformat()
        if valid:
            observed,clock_source=(relayed_at,'source_round_start') if relayed else observation_time(item,job,now)
            asof=datetime.fromtimestamp(observed,timezone.utc).isoformat()
            own=config.get('current_price')
            from services.price_ownership import amount,estimate,pending
            same_price=amount(own) and abs(bb-own)<.01
            inference=estimate(config.get('price_ownership'),bb,own,'task_snapshot',datetime.fromtimestamp(job.get('claimed_at',now),timezone.utc).isoformat()) if same_price else pending(config.get('price_ownership'))
            config.update(status='success',snapshot=snapshot,buybox_price=bb,holding_buybox=None,
                price_ownership=inference,
                price_matches_buybox=abs(bb-own)<.01 if isinstance(own,(int,float)) else False,
                competitor_price=min((o['price'] for o in quotes if o.get('status')=='in_stock' and not o.get('isMine')),default=None),
                last_success_at=asof,quote_time_source=clock_source,error='',error_code='',diagnostics=diagnostics,failures=0,source=report_source)
        else:
            stale=relayed and relayed_at is None
            config.update(status='error',error='原程序报价时间缺失、过期或无效，未作为新行情使用' if stale else message or '本机采集未取得有效行情',error_code='stale_source_quote' if stale else code or 'local_collection_failed',diagnostics=diagnostics,failures=config.get('failures',0)+1)
        config.update(request_once=False,last_attempt_at=at,last_attempt_source=report_source,next_due=now+interval_seconds(config.get('interval_minutes')))
        if db.query(SystemSettings).filter_by(key=row.key,value=job['claim']).update({'value':json.dumps(config,ensure_ascii=False)})==1:
            processed+=1
            if valid:
                successful+=1
                if not same_price: price_checks.append((row.key,json.dumps(config,ensure_ascii=False)))
        else: skipped+=1
        db.delete(lease)
    if successful>0:
        # 本机/扩展采集器确实采到了报价：刷新「最近回报时间」，云端据此判断它仍活着。
        _mark_local_report(db, report_source, time.time())
    db.commit()
    if background_tasks is not None and price_checks:
        from services.price_ownership import verify_batch
        background_tasks.add_task(verify_batch,price_checks)
    return {'processed':processed,'successful':successful,'skipped':skipped,'computeEnqueued':0}

class Release(BaseModel):
    product_ids:list[str]=Field(max_length=20)

@router.post('/release')
def release(data:Release,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    assert_default(store);released=0
    for token in data.product_ids:
        lease=db.query(SystemSettings).filter_by(key=CLAIM_PREFIX+token).first()
        if not lease: continue
        job=json.loads(lease.value)
        row=db.query(SystemSettings).filter_by(key=job['monitor_key']).first()
        if row and row.value==job['claim']:
            value=json.loads(row.value);value.update(status='queued',next_due=0)
            db.query(SystemSettings).filter_by(key=row.key,value=job['claim']).update({'value':json.dumps(value,ensure_ascii=False)})
            released+=1
        db.delete(lease)
    db.commit();return {'released':released,'price_changed':False}

