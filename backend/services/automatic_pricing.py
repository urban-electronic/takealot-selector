"""PricePilot rule port + per-offer opt-in worker. No enabled rules by default."""
import asyncio
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from sqlalchemy.exc import IntegrityError
import httpx
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services.takealot_seller_api import configured_key

PREFIX = '_pricing_rule:'
_stop = threading.Event()
_thread = None

def decide(current, stock, rule, snapshot):
    if stock <= 0: return None, '无库存，跳过'
    if snapshot.get('error'): return None, '公开页采集失败，保持现价'
    raw_offers = snapshot.get('offers') or []
    offers = raw_offers
    typed = any(o.get('offerType') in ('fastest', 'best_price') for o in offers)
    offers = [o for o in offers if not o.get('isMine') and o.get('status') == 'in_stock'
              and (not typed or o.get('offerType') != 'buybox')
              and isinstance(o.get('price'), (int, float)) and math.isfinite(o['price']) and o['price'] > 0]
    buybox = snapshot.get('buyboxPrice')
    holding = isinstance(buybox, (int, float)) and math.isfinite(buybox) and abs(current - buybox) < .01
    if holding and time.time() - rule.get('last_changed_epoch', 0) < rule.get('cooldown_minutes', 10) * 60:
        return None, '持有 Buybox，处于冷却期'
    day = datetime.now(ZoneInfo('Africa/Johannesburg')).date().isoformat()
    if rule.get('change_day') == day and rule.get('today_changes', 0) >= rule['max_daily_changes']:
        return None, '达到每日调价次数上限'
    if not offers:
        foreign = [o for o in raw_offers if not o.get('isMine') and (not typed or o.get('offerType') != 'buybox')]
        verified = foreign and all(o.get('status') in ('pre_order', 'backorder', 'out_of_stock') for o in foreign)
        if not (verified and holding and rule.get('smart_raise')):
            return None, '没有明确的竞品缺货证据，保持现价'
        now = time.time()
        trial = rule.get('scarcity_trial_started', 0)
        if trial and now - trial >= 48 * 3600 and rule.get('sales_recent') and rule.get('sku_last_sale_epoch') is not None and rule['sku_last_sale_epoch'] <= trial:
            target = rule.get('scarcity_base_price', current)
            if target >= rule['min_price'] and target < current:
                return target, '试涨48小时未见新成交，回退试涨前价格并暂停试涨24小时'
        if now < rule.get('scarcity_pause_until', 0) or now - rule.get('last_scarcity_raise', 0) < 12 * 3600:
            return None, '缺货试涨观察中，暂不重复涨价'
        reference = rule.get('reference_price') or current
        ceiling = min(rule.get('max_price') or math.inf, math.floor(reference * 1.20))
        target = min(ceiling, max(current + 1, math.floor(current * 1.03)))
        if target <= current or target < rule['min_price']: return None, '已达智能试涨上限，保持现价'
        return target, '确认竞品非现货，智能试涨（每次约3%，累计不超过参考价20%）'
    raw = None
    if holding:
        if time.time() - rule.get('last_changed_epoch', 0) < rule.get('cooldown_minutes', 10) * 60:
            return None, '持有 Buybox，处于冷却期'
        low = min(o['price'] for o in offers)
        if low == current: return None, '现货竞品持平，保持现价'
        raw = low if rule['strategy'] == 'follow_lowest' else math.floor(low - rule['lead_amount'])
    elif rule['strategy'] == 'follow_lowest':
        raw = math.floor(min(o['price'] for o in offers) - rule['lead_amount'])
        if raw >= current: return None, '未持有 Buybox，只降不升'
    else:
        fastest = [o for o in offers if o.get('offerType') == 'fastest']
        if not fastest: fastest = [o for o in offers if o.get('offerType') == 'buybox']
        if not typed and not fastest: fastest = offers
        best = [o for o in offers if o.get('offerType') == 'best_price']
        for pool in [fastest, best]:
            if not pool: continue
            candidate = math.floor(min(o['price'] for o in pool) - rule['lead_amount'])
            if rule['min_price'] <= candidate < current:
                raw = candidate; break
        if raw is None: return None, '两个现货卡位均不可达，保持现价'
    if raw < rule['min_price']: return None, '建议价低于最低价，放弃竞争'
    if holding and raw > current and rule.get('smart_raise'):
        raw = min(raw, max(current + 1, math.floor(current * 1.03)))
    target = min(math.floor(raw), rule.get('max_price') or math.inf)
    if not holding and target >= current: return None, '未持有时不跟涨'
    if abs(target - current) < rule.get('min_change', 1): return None, '变化小于调整阈值'
    return target, '按 PricePilot 现货 Buybox 规则调价'

async def collect(product, offer):
    from playwright.async_api import async_playwright
    from services.pricepilot_collector import scrape_product
    url = product.takealot_url or ''
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.takealot.com', 'takealot.com') or not re.search(r'PLID\d+', parsed.path, re.I):
        return {'error': 'invalid_url', 'offers': []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            context = await browser.new_context(locale='en-ZA', timezone_id='Africa/Johannesburg')
            page = await context.new_page()
            from services.collection_diagnostics import safe_url, classify
            responses=[]
            def on_response(response):
                if response.request.resource_type=='document': responses.append(response.status)
            page.on('response', on_response)
            snapshot=await scrape_product(page, {'url': url, 'productId': product.id, 'sku': offer['sku'],
                'name': offer.get('title') or product.product_name, 'myPrice': offer['selling_price'],
                'myStock': offer.get('stock_at_takealot_total'), 'shopId': DEFAULT_STORE_ID})
            diagnostics={'http_status':responses[-1] if responses else None,'requested_url':safe_url(url),'final_url':safe_url(page.url)}
            try:
                diagnostics['page_title']=(await page.title())[:160]
                text=(await page.locator('body').inner_text(timeout=2000)).lower()
                markers=['verify you are human','checking your browser','automated traffic','unusual traffic','complete the captcha']
                diagnostics['block_reason']=next((m for m in markers if m in text),None)
                diagnostics['buybox_cards']=await page.locator('[data-ref="buybox-offer"]').count()
            except Exception: pass
            diagnostics['raw_error']=re.sub(r'https?://[^\s]+',lambda m:safe_url(m.group()),str(snapshot.get('error') or ''))[:200]
            snapshot['diagnostics']=diagnostics
            code,message=classify(snapshot)
            if code: snapshot.update(error=code,error_message=message)
            return snapshot
        finally: await browser.close()

def official_offer(client, key, offer_id):
    response = client.get(f'https://seller-api.takealot.com/v2/offers/offer/ID{offer_id}', headers={'Authorization': f'Key {key}'})
    if response.status_code != 200: raise ValueError('卖家报价读取失败，暂停本轮调价')
    payload = response.json()
    data = payload.get('offer', payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict) or str(data.get('offer_id')) != str(offer_id): raise ValueError('卖家商品身份核对失败')
    for field in ['selling_price', 'stock_at_takealot_total']:
        if isinstance(data.get(field), bool) or not isinstance(data.get(field), (int, float)) or not math.isfinite(data[field]):
            raise ValueError('卖家现价或库存无效')
    if data['selling_price'] <= 0: raise ValueError('卖家现价无效')
    return data

def shared_snapshot(db, product_id, offer_id, sku, max_age=600):
    """Repricing consumes collection output without scheduling or launching collection."""
    row = db.query(SystemSettings).filter(SystemSettings.key == f'_pricing_monitor:{DEFAULT_STORE_ID}:{product_id}:{offer_id}').first()
    monitor = json.loads(row.value) if row else {}
    try:
        age = time.time() - datetime.fromisoformat(monitor['last_success_at']).timestamp()
    except (KeyError, ValueError, TypeError):
        age = math.inf
    if monitor.get('sku') != sku or monitor.get('status') == 'error' or not 0 <= age <= max_age:
        return {'error': '行情缺失、失败或已过期，等待Buybox采集', 'offers': []}
    snapshot = monitor.get('snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('error'):
        return {'error': '等待Buybox完整行情采集', 'offers': []}
    return snapshot

def process(row_id):
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
        if not row: return
        rule = json.loads(row.value)
        if not rule.get('enabled') or rule.get('next_due', 0) > time.time(): return
        original = row.value
        rule['next_due'] = time.time() + 900
        if db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == original).update({'value': json.dumps(rule)}) != 1:
            db.rollback(); return
        db.commit(); claim = json.dumps(rule)
        result = dict(rule); changed = False
        try:
            product = db.query(Product).filter(Product.id == rule['product_id'], Product.store_id == DEFAULT_STORE_ID, Product.is_archived == False).first()
            if not product: raise ValueError('商品已归档或不可用')
            if rule.get('use_system_min'):
                from services.replenishment_economics import system_floor
                floor = system_floor(product)
                if floor is None: raise ValueError('系统利润底价尚不可用，请补成本或设置该SKU最低价')
                rule['min_price'] = floor; result['min_price'] = floor
                if rule.get('max_price') and floor > rule['max_price']: raise ValueError('利润底价已高于人工上限，请调整区间')
            key = configured_key(db)
            if not key: raise ValueError('卖家接口未配置')
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                offer = official_offer(client, key, rule['offer_id'])
                if offer.get('sku') != rule['sku']: raise ValueError('卖家SKU变更，请重新绑定')
                if not rule.get('reference_price'):
                    rule['reference_price'] = offer['selling_price']; result['reference_price'] = offer['selling_price']
                profile_row = db.query(SystemSettings).filter(SystemSettings.key == f'operations_profile:{DEFAULT_STORE_ID}:{product.id}').first()
                profile = json.loads(profile_row.value) if profile_row else {}
                metrics = next((o for o in profile.get('offers', []) if o['id'] == rule['offer_id']), {})
                rule['sku_last_sale_epoch'] = metrics.get('last_sale_epoch')
                try: rule['sales_recent'] = time.time() - datetime.fromisoformat(profile['synced_at']).timestamp() < 7200
                except (KeyError, ValueError): rule['sales_recent'] = False
                if offer['stock_at_takealot_total'] <= 0:
                    snapshot = {'offers': []}; target, reason = None, '无库存，跳过'
                else:
                    snapshot = shared_snapshot(db, product.id, rule['offer_id'], rule['sku'])
                    target, reason = decide(offer['selling_price'], offer['stock_at_takealot_total'], rule, snapshot)
                result.update(current_price=offer['selling_price'], stock=offer['stock_at_takealot_total'],
                    buybox_price=snapshot.get('buyboxPrice'), holding_buybox=bool(snapshot.get('holdingBuybox')),
                    competitor_price=min((o['price'] for o in snapshot.get('offers', []) if o.get('status') == 'in_stock' and not o.get('isMine') and isinstance(o.get('price'), (int, float))), default=None))
                if target is not None:
                    # A pause or edited range invalidates the claimed cycle before money-affecting writes.
                    db.expire_all(); live = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
                    if not live or live.value != claim: return
                    time.sleep(10)
                    fresh = official_offer(client, key, rule['offer_id'])
                    if fresh['selling_price'] != offer['selling_price'] or fresh['stock_at_takealot_total'] <= 0:
                        raise ValueError('采集期间现价或库存已变化，下一轮重新评估')
                    # Persist intent before PATCH; uncertainty pauses the rule, preventing duplicate retries.
                    result['enabled'] = False; result['write_pending'] = True; result['pending_target'] = target; result['pending_from'] = offer['selling_price']; result['last_result'] = '正在提交改价'
                    intent = json.dumps(result)
                    if db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == claim).update({'value': intent}) != 1:
                        db.rollback(); return
                    db.commit()
                    if not db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == intent).first(): return
                    time.sleep(10)
                    response = client.patch(f'https://seller-api.takealot.com/v2/offers/offer/ID{rule["offer_id"]}',
                        headers={'Authorization': f'Key {key}'}, json={'selling_price': target})
                    if response.status_code not in (200, 202) or response.json().get('validation_errors'):
                        raise ValueError('平台改价未成功，已暂停，请核对店铺现价后再启动')
                    time.sleep(10)
                    actual = official_offer(client, key, rule['offer_id'])
                    if actual['selling_price'] != target: raise ValueError('平台改价尚未核验，已暂停，请核对店铺现价')
                    changed = True; result['current_price'] = target; result['write_pending'] = False
                    result['last_changed_epoch'] = time.time(); result['last_changed_at'] = datetime.now(timezone.utc).isoformat()
                    day = datetime.now(ZoneInfo('Africa/Johannesburg')).date().isoformat()
                    result['today_changes'] = rule.get('today_changes', 0) + 1 if rule.get('change_day') == day else 1
                    result['change_day'] = day; result['enabled'] = True
                    result['history'] = ([{'at': result['last_changed_at'], 'from': offer['selling_price'], 'to': target}] + rule.get('history', []))[:50]
                    if reason.startswith('确认竞品非现货'):
                        result['last_scarcity_raise'] = time.time()
                        if not rule.get('scarcity_trial_started'):
                            result['scarcity_trial_started'] = time.time(); result['scarcity_base_price'] = offer['selling_price']
                    elif reason.startswith('试涨48小时'):
                        result['scarcity_trial_started'] = 0; result['scarcity_pause_until'] = time.time() + 86400
                    reason = f'已核验改价 R {offer["selling_price"]} → R {target}'
                result['last_result'] = reason
        except Exception as error:
            if result.get('write_pending'): result['enabled'] = False
            result['last_result'] = str(error) if isinstance(error, ValueError) else '本轮采集或接口不可用，未确认改价；查看后重试'
        finally:
            db.expire_all(); live = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
            # Preserve a concurrently requested pause and new configuration.
            if live:
                active = json.loads(live.value)
                if active.get('revision') == rule.get('revision'):
                    result['last_evaluated_at'] = datetime.now(timezone.utc).isoformat()
                    result['next_due'] = time.time() + 600
                    live.value = json.dumps(result, ensure_ascii=False); db.commit()

def start_worker():
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()
    def run():
        from database import SessionLocal
        while not _stop.is_set():
            try:
                with SessionLocal() as db:
                    ids = [r.key for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX + DEFAULT_STORE_ID + ':')).all() if json.loads(r.value).get('enabled')]
                for row_id in ids:
                    if _stop.is_set(): break
                    process(row_id)
            except Exception:
                print('Automatic pricing worker unavailable; will retry', flush=True)
            _stop.wait(30)
    _thread = threading.Thread(target=run, daemon=True, name='automatic-pricing'); _thread.start()

def stop_worker(): _stop.set()
