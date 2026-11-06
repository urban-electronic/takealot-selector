"""Independent read-only collection API. No price rule or range requirement."""
import json
import math
import time
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from database import get_db
from models import DEFAULT_STORE_ID,SystemSettings
from api.store_routes import get_store_id
from api.pricing_routes import MonitorConfig,MonitorBulk,monitor_configure,monitor_bulk,collect_now
from services.buybox_monitor import catalog,PREFIX
from services.takealot_seller_api import configured_key

router=APIRouter(prefix='/api/buybox',tags=['buybox'])

@router.get('')
def listing(db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return {'rows':catalog(db,store),'configured':bool(configured_key(db))if store==DEFAULT_STORE_ID else False}

@router.post('/bulk')
def bulk(data:MonitorBulk,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return monitor_bulk(data,db,store)

@router.post('/page-open')
def page_open(db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    """One quote round per app opening, no extension heartbeat or pricing writes."""
    if store!=DEFAULT_STORE_ID or not configured_key(db):
        raise HTTPException(400,'本店未绑定采集接口')
    now=time.time();queued=skipped=0
    for item in catalog(db,store):
        stock=item.get('stock')
        if isinstance(stock,bool) or not isinstance(stock,(int,float)) or not math.isfinite(stock) or stock<=0 or not item.get('url'):
            continue
        key=PREFIX+store+':'+item['product_id']+':'+item['offer_id']
        row=db.query(SystemSettings).filter_by(key=key).first()
        original=row.value if row else None
        old=json.loads(original) if original is not None else {}
        # Explicit pauses, in-flight work and website cooldowns survive reopen.
        if (old.get('status') in ('paused','running') or old.get('status')=='queued' and not old.get('web_requested')
            or now-old.get('page_open_requested_at',0)<60
            or old.get('status')=='error' and old.get('next_due',0)>now):
            skipped+=1;continue
        value={**old,'product_id':item['product_id'],'offer_id':item['offer_id'],'sku':item['sku'],
            'enabled':old.get('enabled',False),'request_once':True,'web_requested':False,
            'revision':old.get('revision',0)+1,'status':'queued','next_due':0,
            'page_open_requested_at':now}
        encoded=json.dumps(value,ensure_ascii=False)
        if row:
            if db.query(SystemSettings).filter_by(key=key,value=original).update({'value':encoded})!=1:
                skipped+=1;continue
        else:
            try:
                with db.begin_nested():
                    db.add(SystemSettings(key=key,value=encoded));db.flush()
            except IntegrityError:
                skipped+=1;continue
        queued+=1
    db.commit()
    return {'queued':queued,'skipped':skipped,'price_changed':False}

@router.put('/{product_id}/{offer_id}')
def configure(product_id:str,offer_id:str,data:MonitorConfig,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return monitor_configure(product_id,offer_id,data,db,store)


class VariantLabels(BaseModel):
    labels: list[str] = Field(default_factory=list,max_length=6)


@router.put('/{product_id}/{offer_id}/variant-labels')
def variant_labels(product_id:str,offer_id:str,data:VariantLabels,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    """只改这一条 SKU 的变体维度，不碰 enabled / 排期 / 失败计数。

    商品名里混了型号码时（如 "... - FDJH4 - Blue"），按名字猜变体会多出一个
    页面上根本不存在的维度，每轮必然 variant_unmatched。这里让用户显式指定
    真正的规格（["Blue"]），采集器就不再从标题猜。留空数组 = 清除覆盖，
    退回自动解析——绝不会因为"没填"就放行默认变体的报价。
    """
    if store!=DEFAULT_STORE_ID or not configured_key(db):
        raise HTTPException(400,'本店未绑定采集接口')
    key=PREFIX+store+':'+product_id+':'+offer_id
    row=db.query(SystemSettings).filter_by(key=key).first()
    old=json.loads(row.value) if row else {}
    labels=[str(x).strip()[:40] for x in data.labels if isinstance(x,str) and x.strip()][:6]
    value={**old,'product_id':product_id,'offer_id':offer_id,'revision':old.get('revision',0)+1}
    if labels: value['variant_labels']=labels
    else: value.pop('variant_labels',None)
    encoded=json.dumps(value,ensure_ascii=False)
    if row: row.value=encoded
    else: db.add(SystemSettings(key=key,value=encoded))
    db.commit()
    return {'variant_labels':labels}

@router.post('/{product_id}/{offer_id}/collect-now')
def once(product_id:str,offer_id:str,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return collect_now(product_id,offer_id,db,store)
