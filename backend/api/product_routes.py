"""
产品 CRUD 路由
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db
from models import Product, SelectionStatus, ScrapeLog
from services.product_calculator import calculate_all, determine_selection_status
from services.translator import translate_to_chinese_sync

router = APIRouter(prefix="/api/products", tags=["products"])


# --- Pydantic Schemas ---

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
    purchase_shipping_cny: Optional[float] = None
    purchase_quantity: int = 4
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    actual_weight_kg: Optional[float] = None
    packaging_cost_per_unit_cny: float = 1.0
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
    # Manual cost overrides
    manual_domestic_forwarding_cny: Optional[float] = None
    manual_international_shipping_cny: Optional[float] = None
    manual_overseas_op_cost_cny: Optional[float] = None
    manual_success_fee_zar: Optional[float] = None
    manual_fulfillment_fee_zar: Optional[float] = None
    manual_total_cost_zar: Optional[float] = None


class ProductOut(BaseModel):
    id: str
    product_no: Optional[int] = None
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


@router.get("", response_model=List[ProductOut])
def list_products(
    selection_status: Optional[str] = Query(None),
    fee_category: Optional[str] = Query(None),
    shipping_method: Optional[str] = Query(None),
    link_status: Optional[str] = Query(None),
    min_margin: Optional[float] = Query(None),
    max_margin: Optional[float] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    db: Session = Depends(get_db),
):
    query = db.query(Product)

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
            | (Product.tsin.ilike(like))
            | (Product.takealot_url.ilike(like))
        )

    # 排序
    sort_field = getattr(Product, sort_by, Product.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_field.asc())
    else:
        query = query.order_by(sort_field.desc())

    return query.all()


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    return p


@router.post("", response_model=ProductOut)
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    # 检查重复
    from services.takealot_scraper import normalize_takealot_url
    normalized = normalize_takealot_url(data.takealot_url or "")
    if normalized:
        existing = db.query(Product).filter(Product.takealot_url == normalized).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"该 Takealot 链接已存在 (产品: {existing.product_name})",
            )

    p = Product(**data.model_dump())
    if normalized:
        p.takealot_url = normalized

    # 自动编号
    max_no = db.query(Product.product_no).order_by(Product.product_no.desc()).first()
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
    return p


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: str, data: ProductUpdate, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
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
    for mf in manual_fields:
        if mf not in update_data:
            setattr(p, mf, None)

    _apply_calculated_fields(db, p)
    db.commit()
    db.refresh(p)
    return p


@router.post("/batch-import", response_model=dict)
def batch_import(data: List[ProductCreate], db: Session = Depends(get_db)):
    """批量导入产品"""
    from services.takealot_scraper import normalize_takealot_url
    
    success_count = 0
    failed_count = 0
    errors = []
    
    for item in data:
        try:
            normalized = normalize_takealot_url(item.takealot_url or "")
            if normalized:
                existing = db.query(Product).filter(Product.takealot_url == normalized).first()
                if existing:
                    failed_count += 1
                    errors.append(f"链接已存在: {item.product_name or item.takealot_url}")
                    continue
            
            p = Product(**item.model_dump())
            if normalized:
                p.takealot_url = normalized
            
            max_no = db.query(Product.product_no).order_by(Product.product_no.desc()).first()
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
    actual_sale_price_zar: Optional[float] = None
    in_stock_price: Optional[float] = None
    product_image_url: Optional[str] = None
    profit_margin: Optional[float] = None
    profit_zar: Optional[float] = None
    total_cost_zar: Optional[float] = None
    minimum_price_at_20_margin: Optional[float] = None
    minimum_price_at_15_margin: Optional[float] = None
    selection_status: Optional[str] = None


@router.post("/{product_id}/refresh-price", response_model=PriceRefreshOut)
async def refresh_price(product_id: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    if not p.takealot_url:
        raise HTTPException(status_code=400, detail="产品没有 Takealot 链接")

    from services.takealot_scraper import scrape_product

    result = await scrape_product(p.takealot_url)

    updated = {}
    if result.get("actual_sale_price_zar") is not None:
        p.actual_sale_price_zar = result["actual_sale_price_zar"]
        updated["actual_sale_price_zar"] = result["actual_sale_price_zar"]

    if result.get("in_stock_price") is not None:
        updated["in_stock_price"] = result["in_stock_price"]

    if result.get("product_image_url") and not p.product_image_url:
        p.product_image_url = result["product_image_url"]
        updated["product_image_url"] = result["product_image_url"]

    if result.get("tsin") and not p.tsin:
        p.tsin = result["tsin"]

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
def delete_product(product_id: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    db.delete(p)
    db.commit()
    return {"detail": "已删除"}


@router.get("/stats/dashboard")
def dashboard(db: Session = Depends(get_db)):
    total = db.query(Product).count()
    data_incomplete = db.query(Product).filter(
        Product.selection_status == SelectionStatus.DATA_INCOMPLETE.value
    ).count()
    category_pending = db.query(Product).filter(
        Product.selection_status == SelectionStatus.CATEGORY_PENDING.value
    ).count()
    qualified = db.query(Product).filter(
        Product.selection_status == SelectionStatus.QUALIFIED.value
    ).count()
    not_recommended = db.query(Product).filter(
        Product.selection_status == SelectionStatus.NOT_RECOMMENDED.value
    ).count()

    # 平均利润率(仅已计算的产品)
    products_with_margin = db.query(Product).filter(Product.profit_margin != None).all()
    avg_margin = (
        sum(p.profit_margin for p in products_with_margin) / len(products_with_margin)
        if products_with_margin
        else 0
    )

    # 利润率最高的产品
    top = (
        db.query(Product)
        .filter(Product.profit_margin != None)
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
