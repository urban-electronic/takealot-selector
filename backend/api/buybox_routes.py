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
        # 打开页面不再「全量插队」：next_due 是所有排期路径（云端 process、本机
        # /offers 回报、领取、repair）统一推进的字段，它还没到期就说明这条 SKU
        # 在最小间隔内已经采过一轮。原实现对每条有库存的 SKU 无条件排队并置
        # next_due=0，等于每开一次页面就把全部 SKU 拉去采一遍，间隔配置被完全
        # 绕过——这正是「前端 page-open 触发全量排队」。改成只补到期/从未采过的
        # 那些，页面刷新才真的是「顺手补一下缺的行情」而不是一次全量采集。
        due=old.get('next_due')
        if (old.get('status') in ('paused','running') or old.get('status')=='queued' and not old.get('web_requested')
            or now-old.get('page_open_requested_at',0)<60
            or isinstance(due,(int,float)) and due>now):
            skipped+=1;continue
        # next_due 写 now 而不是 0：0 是「没排期」的哨兵值，写它会掩盖这条记录的
        # 真实到期时间，界面上也变成「已延迟 1792265800 秒」。能走到这里的记录
        # 本来就是到期或从未采过的，now 与 0 在选取上完全等价，但语义准确。
        value={**old,'product_id':item['product_id'],'offer_id':item['offer_id'],'sku':item['sku'],
            'enabled':old.get('enabled',False),'request_once':True,'web_requested':False,
            'revision':old.get('revision',0)+1,'status':'queued','next_due':now,
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
