"""Read-only official Seller API sync. Hourly server worker, no AI or browser needed."""
import json
import math
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy.exc import IntegrityError
from models import Product, ProcurementRecord, SystemSettings, DEFAULT_STORE_ID
from services.takealot_seller_api import configured_key

STATE_KEY = '_operations_sync_status'
_stop = threading.Event()
_thread = None

def eligible(db, store, product):
    if product.link_status in ('已购买', '已上架'):
        return True
    return db.query(ProcurementRecord).filter(ProcurementRecord.store_id == store,
        ProcurementRecord.product_id == product.id, ProcurementRecord.status != 'cancelled',
        ProcurementRecord.quantity > 0).first() is not None

def eligible_ids(db, store):
    """一次算出全店可补货的商品 id（ELIGIBLE-N1）。

    eligible() 是单商品判断，放进商品循环里就是 N 条查询：补货总览 318 条、
    待办 317 条，两个都是补货页的首屏请求，商品数涨上去页面跟着变慢。
    这里换成两条聚合查询，循环里只做集合判断。

    结果允许包含已归档或其他店铺的商品 id——调用方遍历的本来就是本店未归档的
    商品，多出来的 id 不会命中，也就不影响判断。
    """
    ids = {pid for (pid,) in db.query(Product.id).filter(
        Product.store_id == store, Product.link_status.in_(('已购买', '已上架'))).all()}
    ids |= {pid for (pid,) in db.query(ProcurementRecord.product_id).filter(
        ProcurementRecord.store_id == store, ProcurementRecord.status != 'cancelled',
        ProcurementRecord.quantity > 0).distinct().all()}
    return ids

def status(db, store):
    if store != DEFAULT_STORE_ID:
        return {'status': 'unconfigured', 'error': '本店尚未绑定独立卖家接口，不使用其他店铺的数据'}
    row = db.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
    return json.loads(row.value) if row else {'status': 'waiting', 'error': ''}

def read_pages(client, key, kind):
    out = []; seen_pages = set()
    for page in range(1, 101):
        if page > 1: time.sleep(10)
        r = client.get(f'https://seller-api.takealot.com/v2/{kind}',
            params={'page_number': page, 'page_size': 100}, headers={'Authorization': f'Key {key}'})
        if r.status_code != 200:
            raise ValueError(f'卖家{kind}接口返回 HTTP {r.status_code}；已停止本轮同步')
        payload = r.json(); rows = payload.get(kind)
        if not isinstance(rows, list): raise ValueError(f'卖家{kind}资料格式异常')
        fingerprint = json.dumps(rows, sort_keys=True)
        if rows and fingerprint in seen_pages: raise ValueError('卖家接口重复分页，未保存不完整数据')
        seen_pages.add(fingerprint); out.extend(rows)
        total = payload.get('total_results') if kind == 'offers' else (payload.get('page_summary') or {}).get('total')
        if len(rows) < 100 or (isinstance(total, int) and total > 0 and len(out) >= total): return out
    raise ValueError('卖家接口超过同步页数上限，未保存不完整数据')

def sale_timestamp(raw):
    if not isinstance(raw, str): raise ValueError('订单日期无效')
    try: dt = datetime.strptime(raw, '%d %b %Y %H:%M:%S').replace(tzinfo=ZoneInfo('Africa/Johannesburg'))
    except ValueError:
        try: dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError: raise ValueError('订单日期格式无效') from None
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo('Africa/Johannesburg'))
    return dt.timestamp()

def sale_day(raw):
    return datetime.fromtimestamp(sale_timestamp(raw), ZoneInfo('Asia/Shanghai')).date()


def apply_snapshot(db, offers, sales, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    today = now.date(); start = today - timedelta(days=29)
    products = db.query(Product).filter(Product.store_id == DEFAULT_STORE_ID, Product.is_archived == False).all()
    import re
    sku_sets = {p.id: set(re.split(r'[\s,，;；]+', (p.sku or '').strip())) - {''} for p in products}
    for p in products:
        if sku_sets[p.id]: continue
        candidates = [o for o in offers if p.tsin and str(o.get('tsin_id') or '') == str(p.tsin)]
        if not candidates:
            local_plid = re.search(r'PLID(\d+)', p.takealot_url or '', re.I)
            if local_plid:
                candidates = [o for o in offers if re.search(r'PLID' + local_plid[1] + r'(?!\d)', o.get('offer_url') or '', re.I)]
        if len(candidates) == 1 and candidates[0].get('sku'):
            sku_sets[p.id] = {str(candidates[0]['sku'])}
    owners = Counter(sku for skus in sku_sets.values() for sku in skus)
    offer_map = defaultdict(list)
    for o in offers:
        if o.get('sku'): offer_map[str(o['sku'])].append(o)
    by_offer = {str(o.get('offer_id')): str(o.get('sku') or '') for o in offers if o.get('offer_id') is not None}
    daily = defaultdict(Counter); seen = set(); latest_sale = {}
    for s in sales:
        oid = str(s.get('order_item_id') or '')
        if not oid: raise ValueError('订单缺少唯一编号，未保存销量快照')
        if oid in seen: continue
        seen.add(oid)
        if str(s.get('sale_status') or '').lower() in ('cancelled', 'canceled', 'returned', 'refunded'): continue
        day = sale_day(s.get('order_date')); q = s.get('quantity', 1)
        if isinstance(q, bool) or not isinstance(q, (int, float)) or not math.isfinite(q) or q < 0 or q != int(q):
            raise ValueError('订单数量无效，未保存销量快照')
        sku = str(s.get('sku') or by_offer.get(str(s.get('offer_id')), '')).strip()
        if start <= day <= today:
            daily[sku][day.isoformat()] += int(q)
            timestamp = sale_timestamp(s.get('order_date'))
            latest_sale[sku] = max(latest_sale.get(sku, 0), timestamp)
    updated = 0; unmatched = 0
    can_replenish = eligible_ids(db, DEFAULT_STORE_ID)
    for p in products:
        if p.id not in can_replenish: continue
        skus = sku_sets[p.id]
        if not skus or any(owners[sku] != 1 or len(offer_map[sku]) != 1 for sku in skus):
            unmatched += 1; continue
        matches = [offer_map[sku][0] for sku in skus]
        stock = 0
        for o in matches:
            value = o.get('stock_at_takealot_total')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value != int(value):
                raise ValueError('店铺可售库存缺失或无效，未覆盖原快照')
            stock += int(value)
        counts = Counter()
        for sku in skus: counts.update(daily[sku])
        name = f'operations_profile:{DEFAULT_STORE_ID}:{p.id}'
        row = db.query(SystemSettings).filter(SystemSettings.key == name).first()
        profile = json.loads(row.value) if row else dict(air_days=15, sea_days=45, safety_days=7,
            cycle_days=30, group='', related_groups=[], allocations=[])
        # Keep shipping configuration and procurement allocations, replace only official metrics.
        observed = start
        created = [sale_day(o['date_created']) for o in matches if o.get('date_created')]
        if len(created) == len(matches): observed = max(start, min(today, min(created)))
        profile.update(current_price=min((o.get('selling_price') for o in matches if isinstance(o.get('selling_price'), (int, float)) and o['selling_price'] > 0), default=None),
            offers=[{'id': str(o['offer_id']), 'sku': str(o['sku']), 'title': o.get('title') or p.product_name, 'price': o.get('selling_price'), 'stock': o.get('stock_at_takealot_total'), 'status': o.get('status'), 'last_sale_epoch': latest_sale.get(str(o['sku']), 0)} for o in matches],
            stock=stock, stock_date=today.isoformat(), observed_from=observed.isoformat(),
            sales=[{'date': d, 'quantity': q} for d, q in sorted(counts.items()) if q > 0],
            data_source='seller_api', synced_at=now.isoformat(), offer_ids=[str(o['offer_id']) for o in matches],
            platform_on_way=sum(int(o.get('total_stock_on_way') or 0) for o in matches))
        if row: row.value = json.dumps(profile, ensure_ascii=False)
        else: db.add(SystemSettings(key=name, value=json.dumps(profile, ensure_ascii=False)))
        updated += 1
    return {'updated_products': updated, 'unmatched_products': unmatched, 'sales_items': len(seen)}

def sync_once():
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
        now = time.time(); prior = json.loads(row.value) if row else {}
        if prior.get('next_due', 0) > now and (prior.get('status') != 'success' or prior.get('schema_version') == 2): return
        key = configured_key(db)
        if not key:
            value = {'status': 'unconfigured', 'error': '尚未配置卖家接口密钥', 'next_due': now + 300}
            if row: row.value = json.dumps(value)
            else: db.add(SystemSettings(key=STATE_KEY, value=json.dumps(value)))
            db.commit(); return
        lease = json.dumps({**prior, 'status': 'running', 'next_due': now + 3600, 'schema_version': 2, 'last_attempt': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()})
        if row:
            old = row.value
            if db.query(SystemSettings).filter(SystemSettings.key == STATE_KEY, SystemSettings.value == old).update({'value': lease}) != 1:
                db.rollback(); return
        else: db.add(SystemSettings(key=STATE_KEY, value=lease))
        try: db.commit()
        except IntegrityError: db.rollback(); return
        try:
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                offers = read_pages(client, key, 'offers'); sales = read_pages(client, key, 'sales')
            result = apply_snapshot(db, offers, sales)
            value = {**result, 'schema_version': 2, 'status': 'success', 'last_success': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'error': '', 'next_due': time.time() + 3600}
        except Exception as e:
            db.rollback()
            # Never persist third-party response bodies, URLs or credential-bearing exceptions.
            message = str(e) if isinstance(e, ValueError) else '卖家接口同步失败，保留上次成功数据，稍后自动重试'
            value = {**prior, 'schema_version': 2, 'status': 'error', 'error': message, 'next_due': time.time() + 1800}
        row = db.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
        row.value = json.dumps(value, ensure_ascii=False); db.commit()

def start_worker():
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()
    def run():
        while not _stop.is_set():
            try: sync_once()
            except Exception as error:
                # Surface startup/DB dependency failures without exception messages or secrets.
                try:
                    from database import SessionLocal
                    with SessionLocal() as db:
                        row = db.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
                        value = json.dumps({'status': 'error', 'error': f'后台同步启动异常（{type(error).__name__}），稍后重试', 'next_due': time.time() + 300})
                        if row: row.value = value
                        else: db.add(SystemSettings(key=STATE_KEY, value=value))
                        db.commit()
                except Exception:
                    print('Seller operations sync: database unavailable; retrying next minute', flush=True)
            _stop.wait(60)
    _thread = threading.Thread(target=run, daemon=True, name='seller-operations-sync'); _thread.start()

def stop_worker():
    _stop.set()
