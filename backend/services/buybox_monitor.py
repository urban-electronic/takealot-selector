"""Opt-in Buybox collection. This module has no price-changing request path."""
import asyncio
import json
import math
import threading
import time
from datetime import datetime, timezone
import httpx
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services.automatic_pricing import collect, official_offer
from services.takealot_seller_api import configured_key

PREFIX = '_pricing_monitor:'
_stop = threading.Event()
_thread = None

def process(row_id):
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
        if not row: return
        config = json.loads(row.value)
        if not (config.get('enabled') or config.get('request_once')) or config.get('next_due', 0) > time.time(): return
        original = row.value
        config.update(status='running', next_due=time.time()+300)
        claim = json.dumps(config, ensure_ascii=False)
        if db.query(SystemSettings).filter(SystemSettings.key==row_id, SystemSettings.value==original).update({'value':claim}) != 1:
            db.rollback(); return
        db.commit(); result = dict(config)
        try:
            product = db.query(Product).filter(Product.id==config['product_id'], Product.store_id==DEFAULT_STORE_ID, Product.is_archived==False).first()
            if not product: raise ValueError('商品已归档或不属于本店')
            key = configured_key(db)
            if not key: raise ValueError('卖家接口未配置')
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                own = official_offer(client, key, config['offer_id'])
            if own.get('sku') != config['sku']: raise ValueError('卖家SKU已变更，需重新绑定')
            snapshot = asyncio.run(collect(product, own))
            bb = snapshot.get('buyboxPrice')
            if snapshot.get('error') or isinstance(bb,bool) or not isinstance(bb,(int,float)) or not math.isfinite(bb) or bb<=0:
                raise ValueError('公开报价页暂不可采集，保留上次报价；未改价')
            competitors=[o['price'] for o in snapshot.get('offers',[]) if o.get('status')=='in_stock' and not o.get('isMine') and not isinstance(o.get('price'),bool) and isinstance(o.get('price'),(int,float)) and math.isfinite(o['price']) and o['price']>0]
            # A price match alone is not proof of seller identity; use collector evidence.
            result.update(status='success', buybox_price=bb, competitor_price=min(competitors, default=None),
                          holding_buybox=False if abs(bb-own['selling_price'])>=.01 else None, price_matches_buybox=abs(bb-own['selling_price'])<.01, current_price=own['selling_price'],
                          stock=own['stock_at_takealot_total'], last_success_at=datetime.now(timezone.utc).isoformat(), error='', failures=0)
        except Exception as error:
            result.update(status='error', error=str(error) if isinstance(error,ValueError) else '采集或接口暂不可用；未改价', failures=config.get('failures',0)+1)
        finally:
            result.update(request_once=False, last_attempt_at=datetime.now(timezone.utc).isoformat())
            delay=config.get('interval_minutes',30)*60
            if result.get('status')=='error': delay=min(6*3600, delay*2**min(result['failures']-1,4))
            result['next_due']=time.time()+delay
            db.expire_all()
            # Keep a concurrent stop or changed schedule intact.
            db.query(SystemSettings).filter(SystemSettings.key==row_id,SystemSettings.value==claim).update({'value':json.dumps(result,ensure_ascii=False)})
            db.commit()

def start_worker():
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()
    def run():
        from database import SessionLocal
        while not _stop.is_set():
            try:
                with SessionLocal() as db:
                    jobs=[]
                    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+DEFAULT_STORE_ID+':')).all():
                        value=json.loads(row.value)
                        if (value.get('enabled') or value.get('request_once')) and value.get('next_due',0)<=time.time(): jobs.append((value.get('next_due',0),row.key))
                for _,key in sorted(jobs):
                    if _stop.is_set(): break
                    process(key)
                    if _stop.wait(10): break  # pace official reads and browser work
            except Exception:
                print('Buybox collection worker unavailable; retry scheduled',flush=True)
            _stop.wait(10)
    _thread=threading.Thread(target=run,daemon=True,name='buybox-monitor');_thread.start()

def stop_worker(): _stop.set()
