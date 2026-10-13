"""Original PricePilot collector protocol; authenticated, default-store, quote-only."""
import json
import math
import time
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from models import SystemSettings, DEFAULT_STORE_ID
from api.store_routes import get_store_id
from services.buybox_monitor import PREFIX, catalog

router = APIRouter(prefix='/api/collector', tags=['collector'])
HEARTBEAT = '_buybox_local_collector'
CLAIM_PREFIX = '_buybox_collector_claim:'

def connected(db):
    row=db.query(SystemSettings).filter_by(key=HEARTBEAT).first()
    return bool(row and json.loads(row.value).get('expires_at',0)>time.time())

def touch(db):
    row=db.query(SystemSettings).filter_by(key=HEARTBEAT).first()
    value=json.dumps({'last_seen_at':datetime.now(timezone.utc).isoformat(),'expires_at':time.time()+900})
    if row: row.value=value
    else: db.add(SystemSettings(key=HEARTBEAT,value=value))

def assert_default(store):
    if store != DEFAULT_STORE_ID: raise HTTPException(400,'采集器仅绑定自营店铺')

@router.get('/products')
def products(db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    assert_default(store);touch(db)
    now=time.time();items=[]
    # Remove old leases; each job is a one-use opaque token, preventing late reports after edits.
    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(CLAIM_PREFIX)).all():
        if json.loads(row.value).get('expires_at',0)<now: db.delete(row)
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
        db.add(SystemSettings(key=CLAIM_PREFIX+token,value=json.dumps({'monitor_key':key,'sku':item['sku'],'claim':claimed,'expires_at':now+900})))
        items.append({'productId':token,'shopId':store,'sku':item['sku'],'name':item['name'],'url':item['url'],'myPrice':item['price'],'myStock':item['stock']})
        if len(items)>=20: break
    db.commit()
    return {'items':items}

class Report(BaseModel):
    items:list[dict]=Field(max_length=100)

@router.post('/offers')
def offers(data:Report,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    assert_default(store);touch(db);processed=skipped=0
    for item in data.items:
        lease=db.query(SystemSettings).filter_by(key=CLAIM_PREFIX+str(item.get('productId',''))).first()
        if not lease: skipped+=1;continue
        job=json.loads(lease.value)
        row=db.query(SystemSettings).filter_by(key=job['monitor_key']).first()
        if job['expires_at']<time.time() or item.get('sku')!=job['sku'] or not row or row.value!=job['claim']:
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
            own=config.get('current_price')
            config.update(status='success',snapshot=snapshot,buybox_price=bb,holding_buybox=None,
                price_matches_buybox=abs(bb-own)<.01 if isinstance(own,(int,float)) else False,
                competitor_price=min((o['price'] for o in quotes if o.get('status')=='in_stock' and not o.get('isMine')),default=None),
                last_success_at=at,error='',error_code='',failures=0,source='local_collector')
        else:
            config.update(status='error',error='本机采集未取得有效行情，请检查该机器浏览器登录、配送地址及商品规格',error_code='local_collection_failed',failures=config.get('failures',0)+1)
        config.update(request_once=False,last_attempt_at=at,next_due=now+config.get('interval_minutes',5)*60)
        if db.query(SystemSettings).filter_by(key=row.key,value=job['claim']).update({'value':json.dumps(config,ensure_ascii=False)})==1: processed+=1
        else: skipped+=1
        db.delete(lease)
    db.commit()
    return {'processed':processed,'skipped':skipped,'computeEnqueued':0}
