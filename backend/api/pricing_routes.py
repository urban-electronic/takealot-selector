import json
import re
import time
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from models import Product, SystemSettings, DEFAULT_STORE_ID
from api.store_routes import get_store_id
from services.automatic_pricing import PREFIX
from services.buybox_monitor import PREFIX as MONITOR_PREFIX
from services.takealot_seller_api import configured_key
from services.replenishment_economics import system_floor

router = APIRouter(prefix='/api/automatic-pricing', tags=['automatic-pricing'])

class Rule(BaseModel):
    min_price: Optional[int] = Field(default=None, gt=0, le=1000000)
    use_system_min: bool = True
    smart_raise: bool = True
    max_price: Optional[int] = Field(default=None, gt=0, le=1000000)
    strategy: Literal['takealot_buybox', 'follow_lowest'] = 'takealot_buybox'
    lead_amount: int = Field(default=1, ge=1, le=100)
    cooldown_minutes: int = Field(default=10, ge=1, le=1440)
    max_daily_changes: int = Field(default=10, ge=1, le=100)
    enabled: bool = False

class Item(BaseModel):
    product_id: str
    offer_id: str

class Bulk(BaseModel):
    action: Literal['group', 'start', 'pause']
    items: list[Item] = Field(min_length=1, max_length=2000)
    group: str = Field(default='', max_length=60)

@router.post('/{product_id}/{offer_id}/preview')
def preview(product_id: str, offer_id: str, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    """Read-only readiness check; never enables a rule or sends PATCH."""
    import asyncio
    import httpx
    from services.automatic_pricing import collect, official_offer
    if store != DEFAULT_STORE_ID: raise HTTPException(400, '店铺未绑定接口')
    item = next((r for r in listing(db, store)['rows'] if r['product_id'] == product_id and r['offer_id'] == offer_id), None)
    if not item: raise HTTPException(404, '商品未唯一匹配')
    key = configured_key(db)
    if not key: raise HTTPException(400, '卖家接口未配置')
    try:
        with httpx.Client(timeout=25, follow_redirects=False) as client: own = official_offer(client, key, offer_id)
        product = db.get(Product, product_id)
        snapshot = asyncio.run(collect(product, own))
        return {'current_price': own['selling_price'], 'stock': own['stock_at_takealot_total'],
                'collector_ready': not bool(snapshot.get('error')) and bool(snapshot.get('offers')),
                'offer_count': len(snapshot.get('offers', [])), 'error': '公开页暂不可采集；保持原价，不会改价' if snapshot.get('error') or not snapshot.get('offers') else '',
                'diagnostics':snapshot.get('diagnostics',{}), 'error_code':snapshot.get('error',''), 'error_message':snapshot.get('error_message',''), 'enabled': False, 'price_changed': False}
    except Exception as error:
        return {'collector_ready': False, 'error': str(error) if isinstance(error, ValueError) else '读取或浏览器暂不可用，未改价', 'price_changed': False, 'enabled': False}

@router.get('')
def listing(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    prefix = PREFIX + store + ':'
    rules = {r.key: json.loads(r.value) for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(prefix)).all()}
    groups = {r.key: json.loads(r.value).get('group', '') for r in db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_group:' + store + ':')).all()}
    monitors = {r.key: json.loads(r.value) for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(MONITOR_PREFIX+store+':')).all()}
    rows = []
    for p in db.query(Product).filter(Product.store_id == store, Product.is_archived == False).all():
        setting = db.query(SystemSettings).filter(SystemSettings.key == f'operations_profile:{store}:{p.id}').first()
        profile = json.loads(setting.value) if setting else {}
        offers = profile.get('offers', [])
        # Existing hourly snapshots had IDs before per-SKU display was introduced.
        if not offers and len(profile.get('offer_ids', [])) == 1:
            skus = re.split(r'[\s,，;；]+', (p.sku or '').strip())
            if len(skus) == 1 and skus[0]: offers = [dict(id=profile['offer_ids'][0], sku=skus[0], title=p.product_name, price=profile.get('current_price'), stock=profile.get('stock'))]
        suggested_min = system_floor(p)
        for offer in offers:
            rule = rules.get(prefix + p.id + ':' + offer['id'], {})
            if rule.get('holding_buybox') is True and not rule.get('holding_verified'):
                rule = {**rule, 'holding_buybox':None, 'price_matches_buybox':True}
            rows.append(dict(product_id=p.id, product_no=p.product_no, offer_id=offer['id'], sku=offer['sku'],
                name=offer.get('title') or p.product_name, url=p.takealot_url, price=rule.get('current_price', offer.get('price')),
                stock=rule.get('stock', offer.get('stock')), suggested_min=suggested_min,
                monitor=monitors.get(MONITOR_PREFIX+store+':'+p.id+':'+offer['id'], {}),
                group=groups.get('_pricing_group:' + store + ':' + p.id + ':' + offer['id'], profile.get('group') or '未分类'), rule=rule))
    from api.collector_routes import connected
    connection=db.query(SystemSettings).filter_by(key='_buybox_local_collector').first()
    source=json.loads(connection.value).get('kind','local_collector') if connection and connected(db) else 'cloud_browser'
    return {'collector_source': source, 'rows': rows, 'configured': bool(configured_key(db)) if store == DEFAULT_STORE_ID else False}

@router.post('/bulk')
def bulk(data: Bulk, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    if store != DEFAULT_STORE_ID and data.action == 'start': raise HTTPException(400, '本店未绑定调价接口')
    info = listing(db, store)
    if data.action == 'start' and not info['configured']: raise HTTPException(400, '卖家接口未配置')
    catalog = {(r['product_id'], r['offer_id']): r for r in info['rows']}
    selected = list(dict.fromkeys((i.product_id, i.offer_id) for i in data.items))
    group = data.group.strip()
    if data.action == 'group' and not group: raise HTTPException(400, '请填写分类名称')
    staged = []
    for product_id, offer_id in selected:
        r = catalog.get((product_id, offer_id))
        if not r: raise HTTPException(400, '选中的SKU不属于当前店铺或未唯一匹配')
        if data.action == 'group':
            staged.append(('_pricing_group:' + store + ':' + product_id + ':' + offer_id, {'group': group})); continue
        old = r['rule']
        if data.action == 'pause':
            if old: staged.append((PREFIX + store + ':' + product_id + ':' + offer_id,
                {**old, 'enabled': False, 'revision': old.get('revision', 0)+1, 'last_result': '已批量暂停'}))
            continue
        minimum = r['suggested_min'] if old.get('use_system_min', True) else old.get('min_price')
        if not minimum or not r['url'] or old.get('write_pending'):
            raise HTTPException(400, f'SKU {r["sku"]} 底价、链接或改价核验未就绪，本次未启动任何SKU')
        if old.get('max_price') and minimum > old['max_price']: raise HTTPException(400, f'SKU {r["sku"]} 底价高于上限，本次未启动')
        rule = {**Rule().model_dump(), **old, 'min_price': minimum, 'product_id': product_id, 'offer_id': offer_id,
                'sku': r['sku'], 'enabled': True, 'smart_raise': old.get('smart_raise', True), 'revision': old.get('revision', 0)+1,
                'next_due': 0, 'last_result': '已批量触发，等待采集与评估'}
        if not old.get('enabled'): rule.update(reference_price=None, scarcity_trial_started=0, last_scarcity_raise=0)
        staged.append((PREFIX + store + ':' + product_id + ':' + offer_id, rule))
    for key, value in staged:
        row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if row: row.value = json.dumps(value, ensure_ascii=False)
        else: db.add(SystemSettings(key=key, value=json.dumps(value, ensure_ascii=False)))
    db.commit()
    return {'updated': len(staged), 'action': data.action}

@router.post('/pause-all')
def pause_all(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    count = 0
    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX + store + ':')).all():
        value = json.loads(row.value)
        if value.get('enabled'):
            value.update(enabled=False, revision=value.get('revision', 0)+1, last_result='已紧急停止')
            row.value = json.dumps(value, ensure_ascii=False); count += 1
    db.commit()
    return {'paused': count}

@router.put('/{product_id}/{offer_id}')
def configure(product_id: str, offer_id: str, data: Rule, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    if store != DEFAULT_STORE_ID: raise HTTPException(400, '本店尚未绑定独立调价接口')
    if data.min_price and data.max_price and data.min_price > data.max_price: raise HTTPException(400, '最低价不能高于最高价')
    rows = listing(db, store)['rows']
    match = next((r for r in rows if r['product_id'] == product_id and r['offer_id'] == offer_id), None)
    if not match: raise HTTPException(400, '商品尚未与卖家offer唯一匹配，请先同步店铺')
    if data.enabled and (not configured_key(db) or not match['url']): raise HTTPException(400, '缺少卖家接口或商品公开页，不能启动')
    minimum = match['suggested_min'] if data.use_system_min else data.min_price
    if minimum is None: raise HTTPException(400, f'SKU {match["sku"]} 缺少系统利润底价，请补成本或手动输入最低价')
    if data.max_price and minimum > data.max_price: raise HTTPException(400, '利润底价高于上限')
    key = PREFIX + store + ':' + product_id + ':' + offer_id
    row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    old = json.loads(row.value) if row else {}
    if data.enabled and old.get('write_pending'): raise HTTPException(409, '上次改价尚未核验，请核对平台记录后处理，不重复提交')
    value = {**old, **data.model_dump(), 'min_price': minimum, 'product_id': product_id, 'offer_id': offer_id, 'sku': match['sku'],
             'revision': old.get('revision', 0) + 1, 'next_due': 0,
             'last_result': '已触发，等待首轮采集与评估' if data.enabled else '已暂停'}
    if data.enabled and not old.get('enabled'):
        value.update(reference_price=None, scarcity_trial_started=0, last_scarcity_raise=0)
    if row: row.value = json.dumps(value, ensure_ascii=False)
    else: db.add(SystemSettings(key=key, value=json.dumps(value, ensure_ascii=False)))
    db.commit()
    return value

@router.post('/{product_id}/{offer_id}/pause')
def pause(product_id: str, offer_id: str, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    row = db.query(SystemSettings).filter(SystemSettings.key == PREFIX + store + ':' + product_id + ':' + offer_id).first()
    if not row: raise HTTPException(404, '调价规则不存在')
    value = json.loads(row.value); value.update(enabled=False, revision=value.get('revision', 0)+1, last_result='已暂停')
    row.value = json.dumps(value, ensure_ascii=False); db.commit(); return value

@router.post('/{product_id}/{offer_id}/reconcile')
def reconcile(product_id: str, offer_id: str, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    from services.automatic_pricing import official_offer
    import httpx
    if store != DEFAULT_STORE_ID: raise HTTPException(400, '本店未绑定卖家接口')
    row = db.query(SystemSettings).filter(SystemSettings.key == PREFIX + store + ':' + product_id + ':' + offer_id).first()
    if not row: raise HTTPException(404, '调价规则不存在')
    value = json.loads(row.value)
    if not value.get('write_pending'): return value
    key = configured_key(db)
    if not key: raise HTTPException(400, '卖家接口未配置')
    try:
        with httpx.Client(timeout=25, follow_redirects=False) as client: actual = official_offer(client, key, offer_id)
    except Exception: raise HTTPException(503, '平台现价暂时无法读取，继续暂停，不重复提交')
    value.update(current_price=actual['selling_price'], enabled=False, write_pending=False,
        revision=value.get('revision', 0) + 1, last_result=f'已核验平台现价 R {actual["selling_price"]}，保持暂停；需要时重新启动')
    row.value = json.dumps(value, ensure_ascii=False); db.commit(); return value


class MonitorConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=5, ge=5, le=1440)
    segment: str = Field(default='', max_length=60)

class MonitorBulk(MonitorConfig):
    items: list[Item] = Field(min_length=1, max_length=2000)
    request_once: bool = False


def save_monitors(data, db, store):
    if store != DEFAULT_STORE_ID or not configured_key(db): raise HTTPException(400, '本店未绑定采集接口')
    from services.buybox_monitor import catalog as collection_catalog
    catalog={(r['product_id'],r['offer_id']):r for r in collection_catalog(db,store)}
    staged=[]
    for product_id,offer_id in dict.fromkeys((i.product_id,i.offer_id) for i in data.items):
        item=catalog.get((product_id,offer_id))
        if not item or not item['url']: raise HTTPException(400, '选中SKU未匹配或缺少公开页，本次未更改')
        old=item['monitor'];value={**old,'product_id':product_id,'offer_id':offer_id,'sku':item['sku'],
            'enabled':data.enabled, 'web_requested':False, 'interval_minutes':data.interval_minutes, 'segment':data.segment.strip() or old.get('segment','未分段'), 'request_once':data.request_once,
            'revision':old.get('revision',0)+1,'next_due':0 if data.enabled or data.request_once else old.get('next_due',0),
            'status':'queued' if data.enabled or data.request_once else 'paused'}
        staged.append((MONITOR_PREFIX+store+':'+product_id+':'+offer_id,value))
    for key,value in staged:
        row=db.query(SystemSettings).filter(SystemSettings.key==key).first()
        if row: row.value=json.dumps(value,ensure_ascii=False)
        else: db.add(SystemSettings(key=key,value=json.dumps(value,ensure_ascii=False)))
    db.commit();return {'updated':len(staged),'price_changed':False}

@router.post('/monitor/bulk')
def monitor_bulk(data: MonitorBulk, db: Session=Depends(get_db), store:str=Depends(get_store_id)):
    return save_monitors(data,db,store)

@router.put('/{product_id}/{offer_id}/monitor')
def monitor_configure(product_id:str,offer_id:str,data:MonitorConfig,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return save_monitors(MonitorBulk(**data.model_dump(),items=[Item(product_id=product_id,offer_id=offer_id)]),db,store)

@router.post('/{product_id}/{offer_id}/collect-now')
def collect_now(product_id:str,offer_id:str,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    from services.buybox_monitor import catalog as collection_catalog
    item=next((r for r in collection_catalog(db,store) if r['product_id']==product_id and r['offer_id']==offer_id),None)
    if not item: raise HTTPException(404,'SKU未匹配')
    old=item['monitor']
    return save_monitors(MonitorBulk(items=[Item(product_id=product_id,offer_id=offer_id)], enabled=old.get('enabled',False),
                                    interval_minutes=old.get('interval_minutes',5),segment=old.get('segment',''),request_once=True),db,store)
