"""Original PricePilot collector protocol; authenticated, default-store, quote-only."""
import json
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from models import SystemSettings, DEFAULT_STORE_ID
from api.store_routes import get_store_id
from services.buybox_monitor import PREFIX, catalog

router = APIRouter(prefix='/api/collector', tags=['collector'])
HEARTBEAT = '_buybox_local_collector'
BROWSER_HEARTBEAT = '_buybox_browser_collector'
CLAIM_PREFIX = '_buybox_collector_claim:'

def connected(db):
    now=time.time()
    return any(row and json.loads(row.value).get('expires_at',0)>now for row in (
        db.query(SystemSettings).filter_by(key=HEARTBEAT).first(),
        db.query(SystemSettings).filter_by(key=BROWSER_HEARTBEAT).first()))

def collector_source(db):
    now=time.time()
    for key,kind in ((HEARTBEAT,'local_collector'),(BROWSER_HEARTBEAT,'browser_extension')):
        row=db.query(SystemSettings).filter_by(key=key).first()
        if row and json.loads(row.value).get('expires_at',0)>now: return kind
    return 'cloud_browser'

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
        config=json.loads(original);config.update(status='running',next_due=now+900,current_price=item['price'],stock=item['stock'])
        claimed=json.dumps(config,ensure_ascii=False)
        if db.query(SystemSettings).filter_by(key=key,value=original).update({'value':claimed})!=1: continue
        token=str(uuid.uuid4())
        db.add(SystemSettings(key=CLAIM_PREFIX+token,value=json.dumps({'monitor_key':key,'sku':item['sku'],'claim':claimed,'claimed_at':now,'expires_at':now+900})))
        items.append({'productId':token,'shopId':store,'sku':item['sku'],'name':item['name'],'url':item['url'],'myPrice':item['price'],'myStock':item['stock']})
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
            'enabled':old.get('enabled',False),'request_once':True,'web_requested':True,'interval_minutes':old.get('interval_minutes',5),
            'revision':old.get('revision',0)+1,'status':'queued','next_due':0}
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
           x_collector_client:Optional[str]=Header(default=None,alias='X-Collector-Client')):
    assert_default(store);touch(db,collector_kind(x_collector_client));processed=skipped=0
    recover_expired(db,time.time())
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
        valid=not item.get('error') and not item.get('variantSwitchFailed') and not isinstance(bb,bool) and isinstance(bb,(int,float)) and math.isfinite(bb) and bb>0
        snapshot={k:item[k] for k in ('buyboxPrice','buyboxSeller','buyboxType','holdingBuybox','offers','variantSwitchFailed','error','stockMapped','stockStatus') if k in item}
        # The original parser may include other sellers only after location/login is established.
        quotes=snapshot.get('offers') or []
        quotes=[o for o in quotes if isinstance(o,dict) and not isinstance(o.get('price'),bool) and isinstance(o.get('price'),(int,float)) and math.isfinite(o['price']) and o['price']>0]
        snapshot['offers']=quotes
        at=datetime.now(timezone.utc).isoformat()
        if valid:
            observed,clock_source=observation_time(item,job,now)
            asof=datetime.fromtimestamp(observed,timezone.utc).isoformat()
            own=config.get('current_price')
            config.update(status='success',snapshot=snapshot,buybox_price=bb,holding_buybox=None,
                price_matches_buybox=abs(bb-own)<.01 if isinstance(own,(int,float)) else False,
                competitor_price=min((o['price'] for o in quotes if o.get('status')=='in_stock' and not o.get('isMine')),default=None),
                last_success_at=asof,quote_time_source=clock_source,error='',error_code='',diagnostics={},failures=0,source='local_collector')
        else:
            config.update(status='error',error='本机采集未取得有效行情，请检查该机器浏览器登录、配送地址及商品规格',error_code='local_collection_failed',failures=config.get('failures',0)+1)
        config.update(request_once=False,last_attempt_at=at,next_due=now+config.get('interval_minutes',5)*60)
        if db.query(SystemSettings).filter_by(key=row.key,value=job['claim']).update({'value':json.dumps(config,ensure_ascii=False)})==1: processed+=1
        else: skipped+=1
        db.delete(lease)
    db.commit()
    return {'processed':processed,'skipped':skipped,'computeEnqueued':0}

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
