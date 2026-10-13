"""Store-scoped saved operations profiles; recalculated on every read without AI."""
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
from database import get_db
from models import Product, ProcurementRecord, SystemSettings
from api.store_routes import get_store_id
from services.operations_planner import plan
from services.replenishment_economics import logistics, profitable_plan
from services.seller_operations_sync import eligible, status as sync_status

router = APIRouter(prefix='/api/operations', tags=['operations'])

class Sale(BaseModel):
    date: date
    quantity: int = Field(gt=0, le=1000000)

class Allocation(BaseModel):
    procurement_id: str
    mode: Literal['air', 'sea']
    quantity: int | None = Field(default=None, gt=0, le=1000000)
    eta: date
    included_in_stock: bool = False

class Profile(BaseModel):
    stock: int = Field(ge=0, le=10000000)
    stock_date: date
    observed_from: date
    air_days: int = Field(default=17, ge=1, le=180)
    air_head_days: int = Field(default=14, ge=1, le=180)
    sea_head_days: int = Field(default=55, ge=2, le=365)
    tail_days: int = Field(default=3, ge=2, le=3)
    minimum_margin: float = Field(default=.15, ge=0, le=.8)
    sea_days: int = Field(default=58, ge=2, le=365)
    safety_days: int = Field(default=7, ge=0, le=90)
    cycle_days: int = Field(default=30, ge=1, le=180)
    dispatch_policy: Literal['balanced', 'sea_only'] = 'balanced'
    adaptive_dispatch: bool = True
    air_share_cap: float = Field(default=.2, ge=0, le=.4)
    first_sea_share: float = Field(default=.75, ge=.5, le=1)
    sea_split_days: int = Field(default=15, ge=1, le=30)
    batch_quantity: int | None = Field(default=None, ge=1, le=1000000)
    allow_emergency_air: bool = True
    emergency_air_days: int = Field(default=21, ge=1, le=30)
    sea_coverage_days: int = Field(default=60, ge=17, le=180)
    planning_days: int = Field(default=120, ge=30, le=180)
    procurement_days: int = Field(default=0, ge=0, le=90)
    group: str = Field(default='', max_length=100)
    related_groups: list[str] = Field(default_factory=list, max_length=20)
    sales: list[Sale] = Field(default_factory=list, max_length=10000)
    allocations: list[Allocation] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def validate_dates(self):
        self.air_days = self.air_head_days + self.tail_days
        self.sea_days = self.sea_head_days + self.tail_days
        if self.sea_coverage_days < max(self.cycle_days, self.air_days):
            raise ValueError('海运覆盖天数须至少覆盖采购周期及空运周期')
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        if self.stock_date > today or self.observed_from > self.stock_date or self.air_days >= self.sea_days:
            raise ValueError('观测开始不得晚于库存日期，日期不得在未来，空运时效须短于海运')
        if any(s.date < self.observed_from or s.date > today for s in self.sales):
            raise ValueError('销量日期必须在观测开始和今天之间')
        if any(a.included_in_stock and a.eta > self.stock_date for a in self.allocations):
            raise ValueError('晚于库存快照的到货不能标记已计入库存')
        groups = {}
        for a in self.allocations: groups.setdefault(a.procurement_id, []).append(a)
        for batches in groups.values():
            if len(batches) > 1 and any(a.quantity is None for a in batches):
                raise ValueError('同一笔采购拆批时，每批必须填写数量')
            if len({(a.mode, a.eta, a.included_in_stock) for a in batches}) != len(batches):
                raise ValueError('相同运输、到货日期的批次请合并，避免重复分配')
        return self

def key(store, product):
    return f'operations_profile:{store}:{product}'

def profiles(db, store):
    prefix = f'operations_profile:{store}:'
    return {r.key[len(prefix):]: logistics(json.loads(r.value)) for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(prefix)).all()}

def purchases_for(db, store, product):
    return [dict(id=r.id, quantity=r.quantity or 0, status=r.status, recorded_at=r.recorded_at, notes=r.notes or '')
            for r in db.query(ProcurementRecord).filter(ProcurementRecord.store_id == store, ProcurementRecord.product_id == product).all()]

def recommendations(source, profile, products, saved):
    group = profile.get('group', '').strip().casefold()
    related = {g.strip().casefold() for g in profile.get('related_groups', []) if g.strip()}
    same = []; accessories = []
    # Coarse fee categories and title similarity alone are insufficient for automatic buying.
    for p in products:
        if p.id == source.id: continue
        candidate_group = saved.get(p.id, {}).get('group', '').strip().casefold()
        kind = 'same' if group and candidate_group == group else 'related' if candidate_group in related else None
        if not kind: continue
        price = p.actual_sale_price_zar
        floor = p.minimum_price_at_20_margin
        if price is None or floor is None or price < floor or p.selection_status != '合格选品': continue
        item = dict(id=p.id, name=p.chinese_product_name or p.product_name, group=candidate_group,
                    price=price, margin=p.profit_margin, reason='同细分品类，且通过本品成本利润筛选' if kind == 'same' else '已设置的关联品类，且通过本品成本利润筛选')
        (same if kind == 'same' else accessories).append(item)
    sort = lambda rows: sorted(rows, key=lambda r: r['margin'] or 0, reverse=True)[:8]
    return {'same_category': sort(same), 'related': sort(accessories),
            'scope': '本店已有选品库；未设置细分品类或未通过利润筛选的商品不自动推荐'}

@router.get('/alerts')
def alerts(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    saved = profiles(db, store); result = []
    for product in db.query(Product).filter(Product.store_id == store, Product.is_archived == False).all():
        if not eligible(db, store, product): continue
        if product.id not in saved: continue
        output = profitable_plan(saved[product.id], purchases_for(db, store, product.id), product)
        for mode, field in [('air', 'air_quantity'), ('sea', 'sea_quantity')]:
            if output[field] > 0:
                result.append(dict(product_id=product.id, name=product.chinese_product_name or product.product_name,
                                   mode=mode, quantity=output[field], stale=output['stale'],
                                   pre_air_shortage=output['pre_air_shortage'], overdue=bool(output['overdue'])))
    return result

@router.get('/overview')
def overview(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    saved = profiles(db, store); rows = []
    for p in db.query(Product).filter(Product.store_id == store, Product.is_archived == False).all():
        if not eligible(db, store, p): continue
        profile = saved.get(p.id)
        rows.append(dict(id=p.id, product_no=p.product_no, name=p.chinese_product_name or p.product_name,
            sku=p.sku, profile=profile, plan=profitable_plan(profile, purchases_for(db, store, p.id), p) if profile else None))
    rows.sort(key=lambda r: (r['plan'] is None, -(r['plan'] or {}).get('sea_quantity', 0), -(r['plan'] or {}).get('pre_sea_shortage', 0)))
    return {'rows': rows, 'sync': sync_status(db, store)}

@router.get('/sync-status')
def get_sync_status(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return sync_status(db, store)

@router.get('/{product_id}')
def get_profile(product_id: str, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    product = db.query(Product).filter(Product.id == product_id, Product.store_id == store, Product.is_archived == False).first()
    if not product: raise HTTPException(404, '商品不存在')
    saved = profiles(db, store); profile = saved.get(product_id)
    products = db.query(Product).filter(Product.store_id == store, Product.is_archived == False).all()
    purchases = purchases_for(db, store, product_id)
    can_replenish = eligible(db, store, product)
    return dict(profile=profile, eligible=can_replenish, plan=profitable_plan(profile, purchases, product) if profile and can_replenish else None, purchases=purchases,
                recommendations=recommendations(product, profile or {}, products, saved))

@router.put('/{product_id}')
def save_profile(product_id: str, data: Profile, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    if not db.query(Product).filter(Product.id == product_id, Product.store_id == store, Product.is_archived == False).first():
        raise HTTPException(404, '商品不存在')
    purchases = {p['id']: p for p in purchases_for(db, store, product_id)}
    for a in data.allocations:
        if a.procurement_id not in purchases:
            raise HTTPException(400, '采购记录不存在或不属于此店铺商品')
    allocated = {}
    for a in data.allocations:
        allocated[a.procurement_id] = allocated.get(a.procurement_id, 0) + (a.quantity if a.quantity is not None else purchases[a.procurement_id]['quantity'])
    if any(q > purchases[id]['quantity'] for id, q in allocated.items()):
        raise HTTPException(400, '发运批次总量不能超过该笔采购数量')
    row = db.query(SystemSettings).filter(SystemSettings.key == key(store, product_id)).first()
    profile = data.model_dump(mode='json')
    if row:
        old = json.loads(row.value)
        if old.get('data_source') == 'seller_api':
            for field in ('stock', 'stock_date', 'sales', 'observed_from', 'data_source', 'synced_at', 'offer_ids', 'platform_on_way', 'current_price', 'offers'):
                if field in old: profile[field] = old[field]
    value = json.dumps(profile, ensure_ascii=False)
    if row: row.value = value
    else: db.add(SystemSettings(key=key(store, product_id), value=value))
    db.commit()
    return get_profile(product_id, db, store)
