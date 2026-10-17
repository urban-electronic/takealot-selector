"""Opt-in Buybox collection. This module has no price-changing request path."""
import asyncio
import json
import math
import threading
import time
from datetime import datetime, timezone
import httpx
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services.automatic_pricing import collect, official_offer
from services.takealot_seller_api import configured_key

PREFIX = '_pricing_monitor:'
# 这三类错误是网站侧拦截，不是本程序故障，退避策略要分开处理。
THROTTLED_CODES = ('site_challenge', 'rate_limited', 'http_forbidden')
# 采集器自身暂时起不来（浏览器启动失败等）：这是可自愈的瞬时故障，按用户设定的
# 间隔重试就行。用指数退避会把一次启动抖动放大成 6 小时停采——2026-10-17 那次
# 浏览器起不来，界面就一直显示「约 8 小时后自动重试」。
TRANSIENT_CODES = THROTTLED_CODES + ('collector_unavailable',)
_stop = threading.Event()
_thread = None

def process(row_id):
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
        if not row: return
        config = json.loads(row.value)
        if config.get('web_requested') and not config.get('enabled'): return
        if not (config.get('enabled') or config.get('request_once')) or config.get('next_due', 0) > time.time(): return
        original = row.value
        started_at=time.time()
        config.update(status='running', next_due=time.time()+300)
        claim = json.dumps(config, ensure_ascii=False)
        if db.query(SystemSettings).filter(SystemSettings.key==row_id, SystemSettings.value==original).update({'value':claim}) != 1:
            db.rollback(); return
        db.commit(); result = dict(config)
        # 自证标记：这一条记录是哪个版本的采集代码写的。诊断字段有可能被中间层
        # 丢掉，所以标记放在 result 顶层——顶层一定落库。排查「代码上线了但线上
        # 行为没变」时，先看这个字段就能分清是部署没生效还是另有进程在写。
        result['diag_build'] = 'bb-v7'
        stage='catalog'
        try:
            product = db.query(Product).filter(Product.id==config['product_id'], Product.store_id==DEFAULT_STORE_ID, Product.is_archived==False).first()
            if not product: raise ValueError('商品已归档或不属于本店')
            key = configured_key(db)
            if not key: raise ValueError('卖家接口未配置')
            stage='seller_api'
            profile_row=db.query(SystemSettings).filter(SystemSettings.key==f'operations_profile:{DEFAULT_STORE_ID}:{product.id}').first()
            profile=json.loads(profile_row.value) if profile_row else {}
            cached=next((o for o in profile.get('offers',[]) if str(o.get('id'))==str(config['offer_id']) and o.get('sku')==config['sku']),{})
            try: age=time.time()-datetime.fromisoformat(profile['synced_at']).timestamp()
            except (KeyError,ValueError): age=math.inf
            if profile.get('data_source')=='seller_api' and 0<=age<3900 and isinstance(cached.get('price'),(int,float)) and cached['price']>0 and isinstance(cached.get('stock'),(int,float)):
                own={'offer_id':config['offer_id'],'sku':config['sku'],'selling_price':cached['price'],'stock_at_takealot_total':cached['stock'],'title':cached.get('title')}
                result['own_price_source']='official_snapshot';result['own_price_asof']=profile['synced_at']
            else:
                with httpx.Client(timeout=25, follow_redirects=False) as client:
                    own = official_offer(client, key, config['offer_id'])
                result['own_price_source']='official_live';result['own_price_asof']=datetime.now(timezone.utc).isoformat()
            if own.get('sku') != config['sku']: raise ValueError('卖家SKU已变更，需重新绑定')
            stage='public_page'
            snapshot = asyncio.run(collect(product, own))
            from services.collection_diagnostics import classify
            result['diagnostics']=snapshot.get('diagnostics', {})
            code,message=classify(snapshot)
            result['error_code']=code
            bb = snapshot.get('buyboxPrice')
            if snapshot.get('error') or isinstance(bb,bool) or not isinstance(bb,(int,float)) or not math.isfinite(bb) or bb<=0:
                raise ValueError(snapshot.get('error_message') or message or '未读取到有效Buybox报价')
            competitors=[o['price'] for o in snapshot.get('offers',[]) if o.get('status')=='in_stock' and not o.get('isMine') and not isinstance(o.get('price'),bool) and isinstance(o.get('price'),(int,float)) and math.isfinite(o['price']) and o['price']>0]
            # A price match alone is not proof of seller identity; use collector evidence.
            result['snapshot'] = snapshot
            result.update(status='success', buybox_price=bb, competitor_price=min(competitors, default=None),
                          holding_buybox=False if abs(bb-own['selling_price'])>=.01 else None, price_matches_buybox=abs(bb-own['selling_price'])<.01, current_price=own['selling_price'],
                          stock=own['stock_at_takealot_total'], last_success_at=datetime.now(timezone.utc).isoformat(),
                          source='cloud_browser', quote_time_source='cloud_completion', error='', error_code='', error_stage='', failures=0)
            from services.price_ownership import estimate,pending
            check_own=own
            check_source=result['own_price_source'];check_asof=result['own_price_asof']
            try:
                if abs(bb-own['selling_price'])>=.01:
                    with httpx.Client(timeout=5,follow_redirects=False) as client:
                        check_own=official_offer(client,key,config['offer_id'])
                    if check_own.get('sku')!=config['sku']: raise ValueError('SKU changed')
                    check_source='official_live';check_asof=datetime.now(timezone.utc).isoformat()
                result['price_ownership']=estimate(config.get('price_ownership'),bb,check_own['selling_price'],check_source,check_asof)
            except Exception:
                result['price_ownership']=pending(config.get('price_ownership'),failed=True)
        except Exception as error:
            # 非 ValueError 表示这一轮根本没跑完（浏览器起不来、卖家接口超时）。
            # 这时不能沿用上一轮的 error_code 和 diagnostics：上一轮的
            # price_not_found 会让界面把「浏览器坏了」显示成「页面没有 Buybox 价」，
            # 旧计数（buybox_cards=2 等）也会被当成这一轮的结果。2026-10-17 就是
            # 这样被误导了好几轮。清掉，如实记下失败类型。
            finished = isinstance(error, ValueError)
            code = result.get('error_code') if finished else 'collector_unavailable'
            if not finished:
                result['diagnostics'] = {'stage': stage, 'failure': type(error).__name__,
                                         'failure_message': str(error)[:200]}
            result.update(status='error', error_stage=stage, error_code=code or ('seller_read_failed' if stage=='seller_api' else type(error).__name__),
                          error=str(error) if finished else '采集器不可用（浏览器或接口未就绪）；未改价', failures=config.get('failures',0)+1)
        finally:
            result.update(request_once=False, last_attempt_at=datetime.now(timezone.utc).isoformat(),last_attempt_source='cloud_browser')
            delay=config.get('interval_minutes',5)*60
            if result.get('status')=='error':
                if result.get('error_code') in TRANSIENT_CODES:
                    # 网站拦截随时可能解除，指数退避只会把"恢复检测"拖到 6 小时：
                    # 每轮都白等。这里按用户设定的间隔重试，且不低于 30 分钟。
                    delay=max(1800, delay)
                else:
                    # 自身故障（解析器、配置、网络）才用指数退避，避免反复空转。
                    delay=min(6*3600,max(300, delay*2**min(result['failures']-1,4)))
            result['next_due']=time.time()+delay if result.get('status')=='error' else max(time.time()+10,started_at+delay)
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
                    from api.collector_routes import connected
                    if connected(db):
                        _stop.wait(10)
                        continue
                    jobs=[]
                    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+DEFAULT_STORE_ID+':')).all():
                        value=json.loads(row.value)
                        if not (value.get('web_requested') and not value.get('enabled')) and (value.get('enabled') or value.get('request_once')) and value.get('next_due',0)<=time.time(): jobs.append((value.get('next_due',0),row.key))
                # Two bounded jobs; never open an unbounded browser fleet.
                with ThreadPoolExecutor(max_workers=2,thread_name_prefix='buybox-read') as pool:
                    pending=set()
                    for _,key in sorted(jobs):
                        if _stop.is_set(): break
                        while len(pending)>=2 and not _stop.is_set():
                            done,pending=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                            for finished in done: finished.result()
                        if _stop.is_set(): break
                        pending.add(pool.submit(process,key))
                        if _stop.wait(2): break
                    for future in pending: future.result()
            except Exception:
                print('Buybox collection worker unavailable; retry scheduled',flush=True)
            _stop.wait(10)
    _thread=threading.Thread(target=run,daemon=True,name='buybox-monitor');_thread.start()

def stop_worker(): _stop.set()


def catalog(db, store):
    """Collection-only SKU metadata; does not read pricing rules or calculate floors."""
    import re
    profiles={r.key:json.loads(r.value)for r in db.query(SystemSettings).filter(SystemSettings.key.startswith('operations_profile:'+store+':')).all()}
    monitors={r.key:json.loads(r.value)for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+store+':')).all()}
    from services.product_names import chinese_name
    rows=[]
    for p in db.query(Product).filter(Product.store_id==store,Product.is_archived==False).all():
        profile=profiles.get('operations_profile:'+store+':'+p.id,{})
        offers=profile.get('offers',[])
        if not offers and len(profile.get('offer_ids',[]))==1:
            skus=re.split(r'[\s,，;；]+',(p.sku or '').strip())
            if len(skus)==1 and skus[0]:offers=[dict(id=profile['offer_ids'][0],sku=skus[0],title=p.product_name,price=profile.get('current_price'),stock=profile.get('stock'))]
        for offer in offers:
            from services.collection_diagnostics import displayed_monitor
            monitor=displayed_monitor(monitors.get(PREFIX+store+':'+p.id+':'+str(offer['id']),{}))
            cn, name_status = chinese_name(p, offer['sku'])
            rows.append(dict(product_id=p.id,offer_id=str(offer['id']),sku=offer['sku'],name=offer.get('title')or p.product_name, chinese_name=cn, name_status=name_status,
                url=p.takealot_url,price=offer.get('price'),stock=offer.get('stock'),group=profile.get('group')or'未分类',
                monitor=monitor,rule={}))
    return rows
