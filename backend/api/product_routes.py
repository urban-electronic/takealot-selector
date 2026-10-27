"""
产品 CRUD 路由
"""

from datetime import datetime
from typing import Optional, List, Literal, Dict, Any
import asyncio
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator

from database import get_db
from models import Product, SelectionStatus, ScrapeLog
from services.label_pdf import generate_label_files, generate_labels, split_skus, variant_items
from services.product_calculator import calculate_all, determine_selection_status, default_fulfillment_fee_zar
from services.translator import translate_to_chinese_sync
from api.store_routes import get_store_id

router = APIRouter(prefix="/api/products", tags=["products"])


# --- Pydantic Schemas ---

def _check_market_prices(raw: Optional[str]) -> Optional[str]:
    """多市场参考价由前端拼成 JSON 文本，写入前挡掉坏数据。

    只做展示与比价参考，不参与任何计算；这里只保证「是对象、每个市场带数值 price」，
    具体市场集合由前端决定（新增市场不用改后端）。
    """
    if not raw:
        return raw
    try:
        obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("market_prices 必须是合法 JSON")
    if not isinstance(obj, dict):
        raise ValueError("market_prices 必须是 JSON 对象")
    for code, item in obj.items():
        if not isinstance(item, dict):
            raise ValueError(f"market_prices.{code} 必须是对象")
        price = item.get("price")
        # 这里只挡「不是数值」，0 放行——`/api/migrate` 整行搬运的历史行里可能有
        # `price: 0`，而 `POST /api/market-prices/batch` 会把**整个** map 重校验一遍
        # （写 TB 时连 CN 一起验）。改成 `<= 0` 会让一条历史 0 把无关市场的写入也挡死。
        # 新写入的 0 由 `market_price_routes.PriceInput` 挡（`gt=0`），那才是入口。
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            raise ValueError(f"market_prices.{code}.price 必须是非负数值")
    return raw

class ProductCreate(BaseModel):
    takealot_url: Optional[str] = ""
    tsin: Optional[str] = ""
    product_name: Optional[str] = ""
    product_image_url: Optional[str] = ""
    product_image_path: Optional[str] = ""
    actual_sale_price_zar: Optional[float] = None
    fee_category: Optional[str] = None
    fee_category_confirmed: bool = False
    note: Optional[str] = ""
    purchase_url: Optional[str] = ""
    sku: Optional[str] = ""
    chinese_product_name: Optional[str] = ""
    purchase_cost_cny: Optional[float] = None
    unit_price_cny: Optional[float] = None
    purchase_shipping_cny: Optional[float] = None
    purchase_quantity: int = 4
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    actual_weight_kg: Optional[float] = None
    packaging_cost_per_unit_cny: Optional[float] = 1.0
    shipping_method: Optional[str] = None
    inbound_listing_fee_cny: float = 0.75
    outbound_operation_fee_cny: float = 0.70
    last_mile_delivery_fee_cny: float = 2.00
    other_fee_cny: float = 2.00
    fulfillment_fee_zar: float = 42.00
    link_status: str = "未购买"
    # Manual cost overrides
    manual_domestic_forwarding_cny: Optional[float] = None
    manual_international_shipping_cny: Optional[float] = None
    manual_overseas_op_cost_cny: Optional[float] = None
    manual_success_fee_zar: Optional[float] = None
    manual_fulfillment_fee_zar: Optional[float] = None
    manual_total_cost_zar: Optional[float] = None
    # Market signals
    competing_sellers_count: Optional[int] = None
    stock_remaining: Optional[int] = None
    review_count: Optional[int] = None
    rating_value: Optional[float] = None
    # 多市场参考价（JSON 文本，见 models.Product.market_prices）
    market_prices: Optional[str] = ""

    @field_validator("market_prices")
    @classmethod
    def _validate_market_prices(cls, v):
        return _check_market_prices(v)


class ProductUpdate(BaseModel):
    takealot_url: Optional[str] = None
    tsin: Optional[str] = None
    product_name: Optional[str] = None
    product_image_url: Optional[str] = None
    product_image_path: Optional[str] = None
    actual_sale_price_zar: Optional[float] = None
    fee_category: Optional[str] = None
    fee_category_confirmed: Optional[bool] = None
    note: Optional[str] = None
    purchase_url: Optional[str] = None
    sku: Optional[str] = None
    chinese_product_name: Optional[str] = None
    purchase_cost_cny: Optional[float] = None
    purchase_shipping_cny: Optional[float] = None
    purchase_quantity: Optional[int] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    actual_weight_kg: Optional[float] = None
    packaging_cost_per_unit_cny: Optional[float] = None
    shipping_method: Optional[str] = None
    inbound_listing_fee_cny: Optional[float] = None
    outbound_operation_fee_cny: Optional[float] = None
    last_mile_delivery_fee_cny: Optional[float] = None
    other_fee_cny: Optional[float] = None
    fulfillment_fee_zar: Optional[float] = None
    link_status: Optional[str] = None
    product_no: Optional[int] = None
    # Manual cost overrides
    manual_domestic_forwarding_cny: Optional[float] = None
    manual_international_shipping_cny: Optional[float] = None
    manual_overseas_op_cost_cny: Optional[float] = None
    manual_success_fee_zar: Optional[float] = None
    manual_fulfillment_fee_zar: Optional[float] = None
    manual_total_cost_zar: Optional[float] = None
    # Market signals
    competing_sellers_count: Optional[int] = None
    stock_remaining: Optional[int] = None
    review_count: Optional[int] = None
    rating_value: Optional[float] = None
    # 多市场参考价（JSON 文本）
    market_prices: Optional[str] = None

    @field_validator("market_prices")
    @classmethod
    def _validate_market_prices(cls, v):
        return _check_market_prices(v)


class ProductOut(BaseModel):
    id: str
    product_no: Optional[int] = None
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    recorded_at: Optional[datetime] = None
    note: Optional[str] = None
    takealot_url: Optional[str] = None
    tsin: Optional[str] = None
    product_name: Optional[str] = None
    product_image_url: Optional[str] = None
    product_image_path: Optional[str] = None
    actual_sale_price_zar: Optional[float] = None
    fee_category: Optional[str] = None
    fee_category_confirmed: bool = False
    fee_rate_range: Optional[str] = None
    success_fee_rate: Optional[float] = None
    purchase_url: Optional[str] = None
    sku: Optional[str] = None
    chinese_product_name: Optional[str] = None
    purchase_cost_cny: Optional[float] = None
    purchase_shipping_cny: Optional[float] = None
    purchase_quantity: int = 4
    unit_price_cny: Optional[float] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    actual_weight_kg: Optional[float] = None
    packaging_cost_per_unit_cny: Optional[float] = None
    shipping_method: Optional[str] = None
    inbound_listing_fee_cny: float = 0.75
    outbound_operation_fee_cny: float = 0.70
    last_mile_delivery_fee_cny: float = 2.00
    other_fee_cny: float = 2.00
    fulfillment_fee_zar: float = 42.00
    # Manual cost overrides
    manual_domestic_forwarding_cny: Optional[float] = None
    manual_international_shipping_cny: Optional[float] = None
    manual_overseas_op_cost_cny: Optional[float] = None
    manual_success_fee_zar: Optional[float] = None
    manual_fulfillment_fee_zar: Optional[float] = None
    manual_total_cost_zar: Optional[float] = None
    # Market signals
    competing_sellers_count: Optional[int] = None
    stock_remaining: Optional[int] = None
    review_count: Optional[int] = None
    rating_value: Optional[float] = None
    # 多市场参考价（JSON 文本）
    market_prices: Optional[str] = None
    # Calculated
    volume_cbm: Optional[float] = None
    volumetric_weight_kg: Optional[float] = None
    chargeable_weight_kg: Optional[float] = None
    domestic_forwarding_cost_per_unit_cny: Optional[float] = None
    unit_product_cost_cny: Optional[float] = None
    international_shipping_per_unit_cny: Optional[float] = None
    cost_to_sa_warehouse_cny: Optional[float] = None
    cost_to_sa_warehouse_zar: Optional[float] = None
    overseas_warehouse_operation_cost_cny: Optional[float] = None
    overseas_warehouse_operation_cost_zar: Optional[float] = None
    success_fee_zar: Optional[float] = None
    official_total_cost_zar: Optional[float] = None
    total_cost_zar: Optional[float] = None
    profit_zar: Optional[float] = None
    profit_margin: Optional[float] = None
    minimum_price_at_20_margin: Optional[float] = None
    minimum_price_at_15_margin: Optional[float] = None
    link_status: Optional[str] = "未购买"
    selection_status: str = SelectionStatus.DATA_INCOMPLETE.value
    exchange_rate_used: float = 0.41
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


def _get_exchange_rate(db: Session) -> float:
    from models import SystemSettings
    setting = db.query(SystemSettings).filter(SystemSettings.key == "cny_per_zar").first()
    if setting:
        try:
            return float(setting.value)
        except (ValueError, TypeError):
            pass
    return 0.41


def _get_fee_rate(db: Session, fee_category: Optional[str]) -> Optional[float]:
    if not fee_category:
        return None
    from models import FeeCategory
    fc = db.query(FeeCategory).filter(
        FeeCategory.name == fee_category,
        FeeCategory.active == True,
    ).first()
    if fc:
        return fc.success_fee_rate
    return None


def _product_to_dict(p: Product) -> dict:
    return {
        "length_mm": p.length_mm,
        "width_mm": p.width_mm,
        "height_mm": p.height_mm,
        "actual_weight_kg": p.actual_weight_kg,
        "actual_sale_price_zar": p.actual_sale_price_zar,
        "purchase_cost_cny": p.purchase_cost_cny,
        "purchase_shipping_cny": p.purchase_shipping_cny,
        "purchase_quantity": p.purchase_quantity,
        "packaging_cost_per_unit_cny": p.packaging_cost_per_unit_cny,
        "shipping_method": p.shipping_method,
        "success_fee_rate": p.success_fee_rate,
        "inbound_listing_fee_cny": p.inbound_listing_fee_cny,
        "outbound_operation_fee_cny": p.outbound_operation_fee_cny,
        "last_mile_delivery_fee_cny": p.last_mile_delivery_fee_cny,
        "other_fee_cny": p.other_fee_cny,
        "fulfillment_fee_zar": p.fulfillment_fee_zar,
        "fee_category": p.fee_category,
        "fee_category_confirmed": p.fee_category_confirmed,
        "manual_domestic_forwarding_cny": p.manual_domestic_forwarding_cny,
        "manual_international_shipping_cny": p.manual_international_shipping_cny,
        "manual_overseas_op_cost_cny": p.manual_overseas_op_cost_cny,
        "manual_success_fee_zar": p.manual_success_fee_zar,
        "manual_fulfillment_fee_zar": p.manual_fulfillment_fee_zar,
        "manual_total_cost_zar": p.manual_total_cost_zar,
    }


def _apply_calculated_fields(db: Session, p: Product):
    exchange_rate = _get_exchange_rate(db)
    product_dict = _product_to_dict(p)
    calculated = calculate_all(product_dict, exchange_rate)

    for field, value in calculated.items():
        if hasattr(p, field):
            setattr(p, field, value)

    p.exchange_rate_used = exchange_rate
    p.fee_rate_used = p.success_fee_rate
    p.selection_status = determine_selection_status(product_dict, calculated)


class AssistRequest(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=500)
    tokens: Optional[dict[str, str]] = None


@router.post('/seller-assist/preview')
@router.post('/seller-assist/apply')
def seller_assist(data: AssistRequest, request: Request, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    from services.takealot_seller_api import configured_key, _load_offers, offer_plan
    if store_id != 'default-store':
        raise HTTPException(400, '当前店铺尚未接入卖家资料')
    key = configured_key(db)
    if not key:
        raise HTTPException(400, '卖家资料尚未接入，请联系管理员')
    try:
        offers = _load_offers(key)
    except Exception as error:
        # 原来这里只回一句「暂时无法读取」：密钥失效、被限流、网络不通、代码报错
        # 四种情况长得一模一样，用户只能反复点「核对本店资料」。同文件里刷新售价
        # 的两条分支已经带上了 _read_reason，这条补齐（SELLER-ASSIST-REASON-01）。
        raise HTTPException(503, f'卖家资料暂时无法读取（{_read_reason(error)}），请稍后重试')
    ids = set(data.ids)
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.id.in_(ids)).all()
    if len(products) != len(ids):
        raise HTTPException(409, '产品已删除或属于其他店铺，请刷新列表')
    plans = [offer_plan(p, offers) for p in products]
    # A blank SKU must not introduce a duplicate identity into this store.
    occupied = {}
    for other in db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).all():
        for sku in re.split(r'[\s,，;；]+', other.sku or ''):
            if sku:
                occupied.setdefault(sku, set()).add(other.id)
    for plan in plans:
        for change in list(plan['changes']):
            if change['field'] != 'sku':
                continue
            proposed = change['value'].split()
            if any(occupied.get(sku, set()) - {plan['id']} for sku in proposed):
                plan['changes'].remove(change)
                plan['warnings'].append('SKU 已被其他产品使用，请人工核对重复记录。')
            else:
                for sku in proposed:
                    occupied.setdefault(sku, set()).add(plan['id'])
    import hashlib
    for plan in plans:
        plan['token'] = hashlib.sha256(repr((plan['token'], plan['changes'])).encode()).hexdigest()
    if request.url.path.endswith('/apply'):
        if not data.tokens or any(data.tokens.get(p['id']) != p['token'] for p in plans):
            raise HTTPException(409, '资料已变更，请重新预览后再补齐')
        for product, plan in zip(products, plans):
            values = {change['field']: change['value'] for change in plan['changes']}
            if not values:
                continue
            # 带 current 的 sku 变更表示「以卖家后台为准更正」，不是补空白。
            rename = next((change for change in plan['changes']
                           if change['field'] == 'sku' and change.get('current')), None)
            # Conditional update protects concurrent edits after the preview check.
            count = db.query(Product).filter(Product.id == product.id, Product.store_id == store_id, Product.updated_at == product.updated_at).update({**values, 'updated_at': datetime.utcnow()}, synchronize_session=False)
            if count != 1:
                db.rollback()
                raise HTTPException(409, '同事刚修改了资料，请重新预览')
            db.refresh(product)
            if rename:
                # SKU 是库存主键、发运行和装箱资料的关联键，只改主档会让旧 SKU 的
                # 库存挂在空气上；下游一起迁，并留一条可追溯的映射。
                from services.sku_migration import migrate_sku
                migration = migrate_sku(db, product, [str(rename['current'])], [str(values['sku'])], store_id)
                plan['migrated'] = migration['moved']
                plan['warnings'].extend(migration['conflicts'])
            if any(field in values for field in ('length_mm', 'width_mm', 'height_mm', 'actual_weight_kg')):
                _apply_calculated_fields(db, product)
        db.commit()
    return {'items': plans, 'fillable': sum(bool(p['changes']) for p in plans)}


_SKU_SPLIT = re.compile(r"[\s,，;；]+")


def sku_tokens(value: Optional[str]) -> List[str]:
    """多规格商品的 SKU 用换行/空格/逗号分号拼成一整串，这里统一拆开。"""
    return [token.strip() for token in _SKU_SPLIT.split(value or "") if token.strip()]


def is_custom_sku(value: Optional[str]) -> bool:
    """自建 SKU：任一段不是纯数字。

    官网与平台抓来的 SKU 都是 13 位数字（TSIN 体系），运营按品类自己的命名
    规则起的 SKU 一定带字母或符号。空 SKU 不算自建——那是没填，不是自己起的。
    多规格里只要有一段是自建的，整条就算自建（列表里要能找到它）。
    """
    return any(not re.fullmatch(r"[0-9]+", token) for token in sku_tokens(value))


@router.get("", response_model=List[ProductOut])
def list_products(
    selection_status: Optional[str] = Query(None),
    fee_category: Optional[str] = Query(None),
    shipping_method: Optional[str] = Query(None),
    link_status: Optional[str] = Query(None),
    min_margin: Optional[float] = Query(None),
    max_margin: Optional[float] = Query(None),
    search: Optional[str] = Query(None),
    missing_field: Optional[str] = Query(None),
    multi_sku: bool = Query(False),
    duplicate_sku: bool = Query(False),
    custom_sku: bool = Query(False),
    archived: bool = Query(False),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    query = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == archived)

    if selection_status:
        query = query.filter(Product.selection_status == selection_status)
    if fee_category:
        query = query.filter(Product.fee_category == fee_category)
    if shipping_method:
        values = [v.strip() for v in shipping_method.split(",") if v.strip()]
        if values:
            query = query.filter(Product.shipping_method.in_(values))
    if link_status:
        values = [v.strip() for v in link_status.split(",") if v.strip()]
        if values:
            query = query.filter(Product.link_status.in_(values))
    if min_margin is not None:
        query = query.filter(Product.profit_margin >= min_margin)
    if max_margin is not None:
        query = query.filter(Product.profit_margin <= max_margin)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (Product.product_name.ilike(like))
            | (Product.chinese_product_name.ilike(like))
            | (Product.sku.ilike(like))
            | (Product.tsin.ilike(like))
            | (Product.takealot_url.ilike(like))
        )
    if missing_field == "sku":
        query = query.filter((Product.sku.is_(None)) | (Product.sku == ""))
    elif missing_field == "image":
        query = query.filter((Product.product_image_url.is_(None)) | (Product.product_image_url == ""))
    elif missing_field == "chinese_name":
        query = query.filter((Product.chinese_product_name.is_(None)) | (Product.chinese_product_name == ""))
    elif missing_field == "shipping":
        query = query.filter((Product.shipping_method.is_(None)) | (Product.shipping_method == ""))

    # 排序
    sort_field = getattr(Product, sort_by, Product.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_field.asc())
    else:
        query = query.order_by(sort_field.desc())

    products = query.all()

    if multi_sku:
        products = [product for product in products if len(sku_tokens(product.sku)) > 1]

    if custom_sku:
        products = [product for product in products if is_custom_sku(product.sku)]

    if duplicate_sku:
        # 重复冲突必须基于完整产品库判断，不能只统计当前筛选结果。
        owners = {}
        for product in db.query(Product).filter(Product.store_id == store_id, Product.is_archived == archived).all():
            for sku in set(sku_tokens(product.sku)):
                owners.setdefault(sku, set()).add(product.id)
        conflicting = {sku for sku, ids in owners.items() if len(ids) > 1}
        products = [product for product in products if any(sku in conflicting for sku in sku_tokens(product.sku))]
    return products

@router.get("/sku-audit")
def sku_audit(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    from services.sku_audit import audit_skus
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).all()
    return audit_skus(products)


def _plid_of(url: Optional[str]) -> str:
    match = re.search(r'PLID(\d+)', url or '', re.I)
    return match[1] if match else ''


def _seller_skus(rows: List[Dict[str, Any]]) -> List[str]:
    return sorted({str(row.get('sku') or '').strip() for row in rows} - {''})


@router.get("/seller-coverage")
async def seller_coverage(
    tsin: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """比对卖家后台的商品与本地商品，回答「某个商品为什么在系统里找不到」。

    三种情况必须分开，因为下一步动作完全相反：

    - seller_only  ：卖家后台有、本地没有 → 商品还没导入，跟 SKU 无关
    - sku_mismatch ：PLID 对上了但 SKU 不一致 → 点「核对本店资料」同步
    - local_only   ：本地有、卖家后台没有 → 可能已下架或链接失效

    带 `tsin`（可逗号分隔多个）时额外回 `probes`，逐个给出结论——这是回答
    「截图里这几个商品为什么筛不到」用的：同一个「找不到」背后可能是三件完全
    不同的事，混成一句话用户只能猜。

    只读：不写任何数据，也不回传密钥本身。
    """
    from services.takealot_seller_api import configured_key, _load_offers
    if store_id != 'default-store':
        raise HTTPException(400, '当前店铺尚未接入卖家资料')
    key = configured_key(db)
    if not key:
        raise HTTPException(400, '卖家资料尚未接入，请联系管理员')
    try:
        offers = await asyncio.wait_for(asyncio.to_thread(_load_offers, key), timeout=45)
    except Exception as error:
        raise HTTPException(503, f'卖家资料暂时无法读取（{_read_reason(error)}），请稍后重试')

    seller: Dict[str, List[Dict[str, Any]]] = {}
    seller_by_tsin: Dict[str, List[Dict[str, Any]]] = {}
    for row in offers:
        plid = str(row.get('productline_id') or '').strip()
        if plid:
            seller.setdefault(plid, []).append(row)
        for token in sku_tokens(str(row.get('tsin_id') or '')):
            seller_by_tsin.setdefault(token, []).append(row)

    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).all()
    local: Dict[str, List[Product]] = {}
    local_by_tsin: Dict[str, List[Product]] = {}
    for product in products:
        plid = _plid_of(product.takealot_url)
        if not plid:
            candidates = {str(row.get('productline_id') or '').strip()
                          for token in sku_tokens(product.tsin) for row in seller_by_tsin.get(token, [])}
            candidates.discard('')
            if len(candidates) == 1:
                plid = next(iter(candidates))
        if plid:
            local.setdefault(plid, []).append(product)
        for token in sku_tokens(product.tsin):
            local_by_tsin.setdefault(token, []).append(product)

    def relevant_rows(product, rows):
        # TSIN identifies a variant; PLID can have separately stored variants.
        exact = [row for row in rows if str(row.get('tsin_id') or '').strip() in sku_tokens(product.tsin)]
        if exact:
            return exact
        known = set(sku_tokens(product.sku))
        exact = [row for row in rows if str(row.get('sku') or '').strip() in known]
        return exact or rows

    matched = 0
    mismatch = []
    for plid, mine in local.items():
        rows = seller.get(plid)
        if not rows:
            continue
        for product in mine:
            seller_skus = set(_seller_skus(relevant_rows(product, rows)))
            local_skus = set(sku_tokens(product.sku))
            if local_skus == seller_skus:
                matched += 1
            else:
                mismatch.append({
                    'product_id': product.id, 'product_no': product.product_no,
                    'name': product.product_name or '',
                    'local_sku': sorted(local_skus), 'seller_sku': sorted(seller_skus),
                })

    seller_only = []
    for plid, rows in seller.items():
        if plid in local:
            continue
        seller_only.append({
            'plid': plid,
            'skus': _seller_skus(rows),
            'title': next((str(row.get('title') or '').strip() for row in rows if row.get('title')), ''),
            'status': next((str(row.get('status') or '').strip() for row in rows if row.get('status')), ''),
        })

    local_only = sum(1 for plid in local if plid not in seller)
    result = {
        'seller_offer_count': len(offers),
        'seller_plid_count': len(seller),
        'local_product_count': len(products),
        'local_plid_count': len(local),
        'matched_count': matched,
        'sku_mismatch_count': len(mismatch),
        'sku_mismatch': mismatch[:100],
        'seller_only_count': len(seller_only),
        'seller_only': seller_only[:100],
        'local_only_count': local_only,
        'truncated': len(seller_only) > 100 or len(mismatch) > 100,
    }

    probes = []
    for code in sku_tokens(tsin):
        rows = seller_by_tsin.get(code, [])
        mine = list(local_by_tsin.get(code, []))
        seller_plids = sorted({str(row.get('productline_id') or '').strip() for row in rows} - {''})
        if not mine:
            for plid in seller_plids:
                for product in local.get(plid, []):
                    if product not in mine:
                        mine.append(product)
        seller_skus = _seller_skus(rows)
        local_skus = sorted({sku for product in mine for sku in sku_tokens(product.sku)})
        if rows and not mine:
            verdict, advice = 'seller_only', '卖家后台有，按 TSIN/PLID 未匹配到本地记录；核对后再导入，避免重复。'
        elif rows and mine and not set(seller_skus).issubset(set(local_skus)):
            verdict, advice = 'sku_mismatch', '本地有这条商品，但 SKU 还是旧值——预览「核对本店资料」；仅身份对应明确时更正，多规格须人工核对。'
        elif rows and mine:
            verdict, advice = 'matched', '当前核对的 SKU 已在本地记录中；TSIN 是否可搜索取决于本地是否已保存该字段。'
        elif mine:
            verdict, advice = 'local_only', '本地有、卖家后台没有——可能已下架，或链接里的 PLID 失效。'
        else:
            verdict, advice = 'nowhere', '两边都没有——确认 TSIN 是否正确，或该商品属于其他店铺/已删除。'
        probes.append({
            'tsin': code,
            'verdict': verdict,
            'advice': advice,
            'seller': {
                'found': bool(rows),
                'plids': seller_plids,
                'skus': seller_skus,
                'title': next((str(row.get('title') or '').strip() for row in rows if row.get('title')), ''),
                'status': next((str(row.get('status') or '').strip() for row in rows if row.get('status')), ''),
            },
            'local': {
                'found': bool(mine),
                'products': [{
                    'product_no': product.product_no,
                    'name': product.product_name or '',
                    'sku': product.sku or '',
                    'plid': _plid_of(product.takealot_url),
                } for product in mine[:5]],
                'skus': local_skus,
            },
        })
    if probes:
        result['probes'] = probes
    return result


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(p, by_alias=False)



@router.post("", response_model=ProductOut)
def create_product(data: ProductCreate, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    # 检查重复
    from services.takealot_scraper import normalize_takealot_url
    normalized = normalize_takealot_url(data.takealot_url or "")
    if normalized:
        existing = db.query(Product).filter(Product.store_id == store_id, Product.takealot_url == normalized).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"该 Takealot 链接已存在 (产品: {existing.product_name})",
            )

    payload = data.model_dump()
    if "fulfillment_fee_zar" not in data.model_dump(exclude_unset=True):
        payload["fulfillment_fee_zar"] = default_fulfillment_fee_zar(data.fee_category)
    p = Product(**payload, store_id=store_id)
    if normalized:
        p.takealot_url = normalized

    # 自动编号
    max_no = db.query(Product.product_no).filter(Product.store_id == store_id).order_by(Product.product_no.desc()).first()
    p.product_no = (max_no[0] + 1) if max_no and max_no[0] else 1

    # 自动翻译中文品名（如果为空且有英文名）
    if not p.chinese_product_name and p.product_name:
        p.chinese_product_name = translate_to_chinese_sync(p.product_name)

    # 设置 fee_rate
    p.success_fee_rate = _get_fee_rate(db, data.fee_category)

    _apply_calculated_fields(db, p)
    p.recorded_at = datetime.utcnow()
    db.add(p)
    db.commit()
    db.refresh(p)

    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(p, by_alias=False)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: str, data: ProductUpdate, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")

    update_data = data.model_dump(exclude_unset=True)

    # 如果更新了fee_category,更新对应的success_fee_rate
    if "fee_category" in update_data:
        new_category = update_data["fee_category"]
        update_data["success_fee_rate"] = _get_fee_rate(db, new_category)
        from models import FeeCategory
        fc = db.query(FeeCategory).filter(FeeCategory.name == new_category, FeeCategory.active == True).first()
        if fc:
            update_data["fee_rate_range"] = fc.fee_rate_range
            if "fee_category_confirmed" not in update_data:
                update_data["fee_category_confirmed"] = True

    for field, value in update_data.items():
        setattr(p, field, value)

    # 若用户未在本次请求中显式设置 manual_* 字段，则清空它们
    # 以避免残留的 manual 值覆盖重新计算的结果
    manual_fields = [
        "manual_domestic_forwarding_cny",
        "manual_international_shipping_cny",
        "manual_overseas_op_cost_cny",
        "manual_success_fee_zar",
        "manual_fulfillment_fee_zar",
        "manual_total_cost_zar",
    ]
    # 只改这些字段时不必重算成本。purchase_url 是纯链接（补货侧栏「保存采购链接」
    # 就只发这一个字段），漏了它会让一次保存链接顺手清空 manual_* 人工成本覆盖值。
    metadata_fields = {'sku', 'chinese_product_name', 'product_name', 'product_image_url',
                       'product_image_path', 'takealot_url', 'tsin', 'note', 'market_prices',
                       'purchase_url'}
    if not set(update_data).issubset(metadata_fields):
        for mf in manual_fields:
            if mf not in update_data:
                setattr(p, mf, None)
        _apply_calculated_fields(db, p)
    db.commit()
    db.refresh(p)

    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(p, by_alias=False)


@router.post("/batch-import", response_model=dict)
def batch_import(data: List[ProductCreate], db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """批量导入产品"""
    from services.takealot_scraper import normalize_takealot_url
    
    success_count = 0
    failed_count = 0
    errors = []
    
    for item in data:
        try:
            normalized = normalize_takealot_url(item.takealot_url or "")
            if normalized:
                existing = db.query(Product).filter(Product.store_id == store_id, Product.takealot_url == normalized).first()
                if existing:
                    failed_count += 1
                    errors.append(f"链接已存在: {item.product_name or item.takealot_url}")
                    continue
            
            p = Product(**item.model_dump(), store_id=store_id)
            if normalized:
                p.takealot_url = normalized
            
            max_no = db.query(Product.product_no).filter(Product.store_id == store_id).order_by(Product.product_no.desc()).first()
            p.product_no = (max_no[0] + 1) if max_no and max_no[0] else 1
            
            p.success_fee_rate = _get_fee_rate(db, item.fee_category)
            
            _apply_calculated_fields(db, p)
            p.recorded_at = datetime.utcnow()
            db.add(p)
            db.commit()
            db.refresh(p)
            success_count += 1
        except Exception as e:
            db.rollback()
            failed_count += 1
            errors.append(f"{item.product_name or item.takealot_url}: {str(e)}")
    
    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "total": len(data),
        "errors": errors[:20]  # 最多返回前20条错误
    }


class PriceRefreshOut(BaseModel):
    data_source: str = "takealot"
    warnings: List[str] = Field(default_factory=list)
    price_updated: bool = False
    sku: Optional[str] = None
    fee_category: Optional[str] = None
    fee_category_confirmed: Optional[bool] = None
    product_name: Optional[str] = None
    chinese_product_name: Optional[str] = None
    actual_sale_price_zar: Optional[float] = None
    in_stock_price: Optional[float] = None
    product_image_url: Optional[str] = None
    profit_margin: Optional[float] = None
    profit_zar: Optional[float] = None
    total_cost_zar: Optional[float] = None
    minimum_price_at_20_margin: Optional[float] = None
    minimum_price_at_15_margin: Optional[float] = None
    selection_status: Optional[str] = None
    competing_sellers_count: Optional[int] = None
    stock_remaining: Optional[int] = None
    review_count: Optional[int] = None
    rating_value: Optional[float] = None


def _read_reason(error: BaseException) -> str:
    """把「读取失败」归到一个用户能行动的原因上。

    原来这两个分支都只回一句「暂时无法读取」，密钥失效、被限流、网络不通、
    代码报错四种情况长得一模一样，用户只能反复点重试。这里只做**归因**：
    透出的是固定的类别或上游已经写好的中文提示，不打印路径、密钥、异常正文。
    """
    if isinstance(error, asyncio.TimeoutError):
        return '读取超时，上游没有在时限内返回'
    text = str(error or '').strip()
    # _load_offers 自己写的中文提示已经是「原因 + 建议」，直接用。
    if any(key in text for key in ('暂停请求', '格式异常', '超出本次读取上限')):
        return text
    # 卖家接口的提示同样是「原因 + 建议」（含 HTTP 状态码，见
    # takealot_seller_api.seller_api_reason），不能退化成「内部错误 ValueError」。
    if text.startswith(('卖家接口', '卖家报价')):
        return text
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx 是硬依赖
        httpx = None
    if httpx is not None:
        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            return '连不上上游接口（网络不可达或 DNS 失败）'
        if isinstance(error, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return '上游接口响应超时'
        if isinstance(error, httpx.HTTPStatusError):
            return f'上游接口返回 HTTP {error.response.status_code}'
    if isinstance(error, (ModuleNotFoundError, ImportError)):
        return '运行环境缺少抓取组件（部署问题，不是商品问题）'
    return f'内部错误 {type(error).__name__}'


def _sku_variant_image(result: Dict[str, Any], sku: Optional[str]) -> str:
    """按本行 SKU 取变体图；多 SKU 汇总行与未匹配行返回空串。

    页面主图属于默认变体，分支 SKU 直接用它会把白色分支显示成黑色主图，
    所以只有精确命中本行唯一 SKU 的变体图才可用于覆盖已有图片。
    """
    tokens = [token.strip() for token in re.split(r"[\s,，;；]+", sku or "") if token.strip()]
    if len(tokens) != 1:
        return ""
    for item in result.get("variants") or []:
        if str(item.get("sku") or "").strip() == tokens[0]:
            return str(item.get("image_url") or "").strip()
    return ""


def _is_scraped_image(url: str) -> bool:
    """已自动抓取的官网图片可被新的变体图替换；人工填写的链接不覆盖。"""
    return "media.takealot.com" in (url or "")


@router.post("/{product_id}/refresh-price", response_model=PriceRefreshOut)
async def refresh_price(product_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id), source: Literal['official', 'seller', 'details'] = 'official'):
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    if not p.takealot_url:
        raise HTTPException(status_code=400, detail="产品没有 Takealot 链接")

    from services.takealot_scraper import scrape_product
    from services.takealot_seller_api import configured_key, seller_product, _load_offers
    import math
    result = None
    if source in ('seller', 'details') and store_id == 'default-store':
        key = configured_key(db)
        if key:
            try:
                result = await asyncio.wait_for(asyncio.to_thread(seller_product, p.takealot_url, key), timeout=30)
                if result and source == 'seller':
                    rows = await asyncio.to_thread(_load_offers, key)
                    match = re.search(r'PLID(\d+)', p.takealot_url, re.I)
                    rows = [row for row in rows if match and str(row.get('productline_id')) == match[1]]
                    skus = set(re.split(r'[\s,，;；]+', (p.sku or '').strip())) - {''}
                    if skus:
                        rows = [row for row in rows if str(row.get('sku')) in skus]
                        if {str(row.get('sku')) for row in rows} != skus:
                            raise HTTPException(409, '现有 SKU 与本店卖家资料不完全匹配，请先核对规格；原售价保留')
                    prices = [row.get('selling_price') for row in rows]
                    if not prices or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in prices) or len(set(prices)) != 1:
                        raise HTTPException(409, '卖家报价缺失或不同规格报价不一致，请人工核对；原售价保留')
                    result['actual_sale_price_zar'] = prices[0]
                    result.setdefault('warnings', []).append('卖家资料最多缓存 30 分钟；本次读取不代表官网实时最低价。')
            except HTTPException:
                raise
            except Exception as error:
                if source == 'seller':
                    raise HTTPException(503, f'本店报价暂时无法读取（{_read_reason(error)}）；原售价保留')
        elif source == 'seller':
            raise HTTPException(400, '当前店铺尚未接入卖家 API，请选择官网售价')
    if source == 'seller' and result is None:
        raise HTTPException(404, '本店卖家资料没有此商品，请选择官网售价或人工填写；原售价保留')
    if result is None:
        try:
            # 80 秒：官网读取现在有三段（curl_cffi → 临时浏览器 → 持久化浏览器），
            # 前两段被验证挡住时，第三段才是真正能拿到数据的那一段。
            result = await asyncio.wait_for(scrape_product(p.takealot_url), timeout=80)
        except asyncio.TimeoutError:
            raise HTTPException(504, '官网读取超时，请稍后重试或选择本店报价；原售价保留')
        except Exception as error:
            raise HTTPException(502, f'官网资料暂时无法读取（{_read_reason(error)}）；原售价保留')
    price = result.get('actual_sale_price_zar')
    valid_price = isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0
    if source != 'details' and not valid_price:
        reason = '；'.join(result.get('warnings', [])) or '未获得有效售价'
        raise HTTPException(502, reason + ' 原售价保留。可选择本店报价或人工填写。')
    if source == 'details' and not any(result.get(f) for f in ('product_name', 'product_image_url', 'tsin', 'variants')):
        raise HTTPException(502, '；'.join(result.get('warnings', [])) or '未获取商品资料，请人工补充')

    updated = {'data_source': result.get('data_source') or 'takealot', 'warnings': result.get('warnings', []), 'price_updated': source != 'details' and valid_price}
    official_name = (result.get("product_name") or "").strip()
    if official_name:
        # 英文标题以官网为准；中文人工分支名若已有则保留，避免覆盖颜色/规格信息。
        p.product_name = official_name
        updated["product_name"] = official_name
        if not (p.chinese_product_name or "").strip():
            translated_name = translate_to_chinese_sync(official_name)
            if translated_name:
                p.chinese_product_name = translated_name
                updated["chinese_product_name"] = translated_name

    # 官网只返回一个明确 SKU 时才补录；多个变体不按顺序猜测。
    if not (p.sku or "").strip():
        official_skus = {str(item.get("sku") or "").strip() for item in result.get("variants", []) if item.get("sku")}
        official_skus.discard("")
        if len(official_skus) == 1:
            official_sku = next(iter(official_skus))
            conflict = db.query(Product).filter(Product.store_id == store_id, Product.sku == official_sku, Product.id != p.id).first()
            if not conflict:
                p.sku = official_sku
                updated["sku"] = official_sku

    # 仅高置信度官网分类自动确认，中低置信度继续交给人工。
    if not p.fee_category_confirmed:
        from services.fee_category_matcher import match_fee_category
        fee_match = match_fee_category(db, result.get("takealot_category_path"), official_name or p.product_name)
        if fee_match.get("confidence") == "high" and fee_match.get("fee_category"):
            p.fee_category = fee_match["fee_category"]
            p.fee_category_confirmed = True
            p.success_fee_rate = _get_fee_rate(db, p.fee_category)
            updated["fee_category"] = p.fee_category
            updated["fee_category_confirmed"] = True
    if source != "details" and valid_price:
        p.actual_sale_price_zar = result["actual_sale_price_zar"]
        updated["actual_sale_price_zar"] = result["actual_sale_price_zar"]

    if source != "details" and result.get("in_stock_price") is not None:
        updated["in_stock_price"] = result["in_stock_price"]

    # 变体图优先：本行 SKU 在页面变体里有自己的图就用它，且允许替换上次自动抓的图
    # （否则分支变体一旦被抓成主图，就永远是那张"老图片"）。
    # 没有变体图时，页面主图只补空位，不覆盖已有图片——主图属于默认变体。
    variant_image = _sku_variant_image(result, p.sku)
    page_image = (result.get("product_image_url") or "").strip()
    current_image = (p.product_image_url or "").strip()
    if variant_image and variant_image != current_image and (not current_image or _is_scraped_image(current_image)):
        p.product_image_url = variant_image
        updated["product_image_url"] = variant_image
    elif page_image and not current_image:
        p.product_image_url = page_image
        updated["product_image_url"] = page_image

    if result.get("tsin") and not p.tsin:
        p.tsin = result["tsin"]

    # 保存新抓取的竞品信号
    for field in ["competing_sellers_count", "stock_remaining", "review_count", "rating_value"]:
        val = result.get(field)
        if val is not None:
            setattr(p, field, val)
            updated[field] = val

    # 重新计算利润
    try:
        _apply_calculated_fields(db, p)
        db.commit()
        db.refresh(p)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"利润计算失败: {str(e)}")

    updated["profit_margin"] = p.profit_margin
    updated["profit_zar"] = p.profit_zar
    updated["total_cost_zar"] = p.total_cost_zar
    updated["minimum_price_at_20_margin"] = p.minimum_price_at_20_margin
    updated["minimum_price_at_15_margin"] = p.minimum_price_at_15_margin
    updated["selection_status"] = p.selection_status

    return updated


@router.delete("/{product_id}")
def delete_product(product_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    p.is_archived = True
    p.archived_at = datetime.utcnow()
    db.commit()
    return {"detail": "已移入废品库", "product_no": p.product_no}


@router.post("/{product_id}/restore")
def restore_product(product_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id, Product.is_archived == True).first()
    if not p:
        raise HTTPException(status_code=404, detail="废品库中没有该产品")
    p.is_archived = False
    p.archived_at = None
    db.commit()
    return {"detail": "已恢复", "product_no": p.product_no}


@router.get("/stats/dashboard")
def dashboard(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    active = (Product.is_archived == False)
    base = db.query(Product).filter(Product.store_id == store_id, active)
    total = base.count()
    data_incomplete = db.query(Product).filter(
        Product.store_id == store_id,
        active,
        Product.selection_status == SelectionStatus.DATA_INCOMPLETE.value
    ).count()
    category_pending = db.query(Product).filter(
        Product.store_id == store_id,
        active,
        Product.selection_status == SelectionStatus.CATEGORY_PENDING.value
    ).count()
    qualified = db.query(Product).filter(
        Product.store_id == store_id,
        active,
        Product.selection_status == SelectionStatus.QUALIFIED.value
    ).count()
    not_recommended = db.query(Product).filter(
        Product.store_id == store_id,
        active,
        Product.selection_status == SelectionStatus.NOT_RECOMMENDED.value
    ).count()

    # 平均利润率(仅已计算的产品)
    products_with_margin = db.query(Product).filter(Product.store_id == store_id, active, Product.profit_margin != None).all()
    avg_margin = (
        sum(p.profit_margin for p in products_with_margin) / len(products_with_margin)
        if products_with_margin
        else 0
    )

    # 利润率最高的产品
    top = (
        db.query(Product)
        .filter(Product.store_id == store_id, active, Product.profit_margin != None)
        .order_by(Product.profit_margin.desc())
        .first()
    )

    return {
        "total": total,
        "data_incomplete": data_incomplete,
        "category_pending": category_pending,
        "qualified": qualified,
        "not_recommended": not_recommended,
        "avg_profit_margin": round(avg_margin, 4),
        "top_product_name": top.product_name if top else None,
        "top_profit_margin": top.profit_margin if top else None,
    }


class TranslateRequest(BaseModel):
    text: str


@router.post("/translate")
def translate_text(data: TranslateRequest):
    """翻译英文产品名到中文"""
    result = translate_to_chinese_sync(data.text)
    return {"chinese_name": result}


class LabelsRequest(BaseModel):
    skus: list[str] = Field(default_factory=list, max_length=2000)
    # 产品详情页按 id 下载：一个产品有几个 SKU 就出几个文件，前端不用自己拆
    # Product.id 是 UUID 字符串，不是自增数字
    product_ids: list[str] = Field(default_factory=list, max_length=500)
    size: Literal["40x30", "30x40"] = "40x30"
    copies: int = Field(default=1, ge=1, le=99)
    # single = 每个 SKU 一个 40x30 的独立 PDF（标签打印机直接打，多个打 zip）
    # sheet  = A4 拼版，45 张/页带裁切角标（给普通打印机 + 手工裁）
    mode: Literal["single", "sheet"] = "single"


@router.post("/labels")
def download_labels(data: LabelsRequest, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """生成 Takealot 风格产品标签。

    一个 SKU 一张 40x30mm（官方 Product Label 的尺寸），版式对齐官方
    product_labels.pdf：产品名 3 行 + EAN-13 条码 + 1-6-6 分组数字 + M/P 标记。
    没有 13 位合法条码的 SKU 会被跳过——印出来扫不出的标签是废纸。
    """
    wanted = [s.strip() for s in data.skus if s and s.strip()]
    ids = [i.strip() for i in data.product_ids if isinstance(i, str) and i.strip()]
    if not wanted and not ids:
        raise HTTPException(status_code=400, detail="请先勾选产品或指定产品")

    query = db.query(Product).filter(Product.store_id == store_id)
    items = []

    if ids:
        # 详情页：按产品 id 取，有几个 SKU 就出几个文件
        products = query.filter(Product.id.in_(ids)).all()
        for p in products:
            items.extend(variant_items(p, data.copies))
    else:
        # 列表页勾的是「单个 SKU」，而多规格产品的 sku 字段是空格拼起来的一整串，
        # 精确 in_ 匹配不上，得用 like 粗筛再按拆分结果精确比对。
        conds = [Product.sku.like(f"%{s}%") for s in wanted]
        products = query.filter(or_(*conds)).all()
        by_variant = {}
        for p in products:
            for v in split_skus(p.sku):
                by_variant.setdefault(v, p)
        for s in wanted:
            hit = by_variant.get(s)
            if hit:
                items.append({'id': hit.id, 'sku': s, 'name': hit.product_name, 'copies': data.copies})
                continue
            # 兼容传整串 sku 的老调用方
            for p in products:
                if (p.sku or '').strip() == s:
                    items.extend(variant_items(p, data.copies))
                    break

    # 「产品查不到」与「产品在、但没有 SKU」是两回事：前者是选错了对象（已删除、
    # 或不属于当前店铺），后者是数据缺字段。混成一句「都没有 SKU」会让人去补 SKU，
    # 而真正该做的是刷新列表、重新勾选。
    if not products:
        raise HTTPException(status_code=404, detail="没有找到对应的产品（可能已被删除，或不属于当前店铺）")
    if not items:
        raise HTTPException(status_code=400, detail="选中的产品没有可用的 SKU（SKU 为空或已被修改）")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        if data.mode == "single":
            # 每个 SKU 一个独立 PDF，页面就是 40x30mm —— 直接丢标签打印机，不用裁。
            files, skipped, count = generate_label_files(items, tmp_dir, size=data.size)
            if not files:
                sample = "；".join(f"{sku or '无SKU'}：{why}" for sku, why in skipped[:5])
                raise HTTPException(status_code=400, detail=f"没有可生成的标签（{sample}）")
            if len(files) == 1:
                content = files[0].read_bytes()
                media_type = "application/pdf"
                filename = files[0].name
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in files:
                        zf.write(f, arcname=f.name)
                content = buf.getvalue()
                media_type = "application/zip"
                # HTTP 头只能放 latin-1，文件名不能带中文
                filename = f"product_labels_{len(files)}_skus.zip"
        else:
            output = tmp_dir / "labels.pdf"
            count, skipped = generate_labels(items, output, size=data.size)
            if not count:
                sample = "；".join(f"{sku or '无SKU'}：{why}" for sku, why in skipped[:5])
                raise HTTPException(status_code=400, detail=f"没有可生成的标签（{sample}）")
            content = output.read_bytes()
            media_type = "application/pdf"
            filename = f"product_labels_{data.size}_{count}.pdf"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Label-Count": str(count),
            "X-Label-Skipped": str(len(skipped)),
        },
    )
