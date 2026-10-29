"""
选品池路由
==========

把「上传一份表 → 抓主图/类目 → 填重尺和成本 → 算出利润率 → 一键进产品列表」
做成一条流水线。四个端点对应四步：

    POST /api/selection/import    上传表，按商品聚合去重，落进选品池
    POST /api/selection/enrich    抓主图、类目、价格；自家商品还能抓到重尺
    PATCH /api/selection/items/{id} / POST /items/bulk   人工补重尺和采购价
    POST /api/selection/items/promote                    勾中的才进 products

为什么不写进 `products`
----------------------
`products` 是库存/打包/采购的锚点，草稿进去会被当成真货；而且一轮 1800 条
绝大多数会被淘汰，删掉还会在 `product_no` 上留空洞。草稿留在
`selection_items`，promote 时才复制过去（见 models.SelectionItem 的注释）。

重尺从哪来（这是用户最关心的一点）
----------------------------------
1. **自家店铺的商品**（销售报表里只有 TSIN 的那种）：卖家 API `/v1/offers`
   本来就返回 `length_cm / width_cm / height_cm / weight_grams`，直接换算成
   mm / kg 填进来。`seller_catalog_sync` 早就在用这几个字段，这里只是复用。
2. **竞品商品**：`/v1/offers` 只返回本店 offer，拿不到别人的。公开商品页
   的 `Assembled Dimensions` 实测只有约 10% 的商品有、`Weight` 约 2%，格式
   还乱（"22 x 7.2 x 17 cm"、"M-L"），只能当**预填**，必须人工确认。
   拿不到就留空，绝不用类目默认值冒充——重尺错了利润就全错。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from api.store_routes import get_store_id
from database import get_db
from models import FeeCategory, Product, SelectionItem
from services import takealot_public_api as pub
from services.fee_category_matcher import match_fee_category
from services.product_calculator import (
    calculate_all,
    default_fulfillment_fee_zar,
)
from services.selection_import import build_takealot_url, normalize_row, read_table

router = APIRouter(prefix="/api/selection", tags=["selection"])

# 与 models.Product 的默认值保持一致；改一处要同步另一处
INBOUND_FEE_CNY = 0.75
OUTBOUND_FEE_CNY = 0.70
LAST_MILE_FEE_CNY = 2.00
OTHER_FEE_CNY = 2.00

# 合格线：用户确认按 15% 算
QUALIFY_MARGIN = 0.15

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
ENRICH_CHUNK = 400          # 单次请求最多抓这么多个，剩下的由前端接着调


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _exchange_rate(db: Session) -> float:
    from models import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "cny_per_zar").first()
    if row:
        try:
            return float(row.value)
        except (TypeError, ValueError):
            pass
    return 0.41


def _fee_rate(db: Session, fee_category: Optional[str]) -> Optional[float]:
    if not fee_category:
        return None
    row = db.query(FeeCategory).filter(
        FeeCategory.name == fee_category, FeeCategory.active == True  # noqa: E712
    ).first()
    return row.success_fee_rate if row else None


def _derive_purchase_cost(item: SelectionItem) -> None:
    """供货单价 × 采购数量 = 采购价。

    供货单价有值时，采购价由它算出来，不再单独维护：两个都能手填必然打架，
    而且现实中你常常只知道单价、不知道这批总共花了多少。单价留空时采购价
    照旧手填（老用法不变），这样只拿到总价的那批数据也不会被清掉。
    """
    if item.unit_price_cny is None:
        return
    quantity = item.purchase_quantity or 4
    if quantity <= 0:
        return
    item.purchase_cost_cny = round(item.unit_price_cny * quantity, 2)


def _calc(item: SelectionItem, exchange_rate: float, fee_rate: Optional[float]) -> None:
    """把重尺+成本算成利润率，写回 item。

    `calculate_all` 里任一环缺失后面全 None，所以这里不需要自己判断完整性——
    缺什么就是 None，界面显示「待核算」。
    """
    # 放在最前面：导入、单条改、批量改、enrich、一键新建都会走 _calc，
    # 在这里派生能保证所有入口算出来的采购价是同一个口径。
    _derive_purchase_cost(item)
    payload = {
        "length_mm": item.length_mm,
        "width_mm": item.width_mm,
        "height_mm": item.height_mm,
        "actual_weight_kg": item.actual_weight_kg,
        "actual_sale_price_zar": item.actual_sale_price_zar,
        "purchase_cost_cny": item.purchase_cost_cny,
        "purchase_shipping_cny": item.purchase_shipping_cny,
        "purchase_quantity": item.purchase_quantity or 4,
        "packaging_cost_per_unit_cny": (
            item.packaging_cost_per_unit_cny
            if item.packaging_cost_per_unit_cny is not None else 1.0
        ),
        "shipping_method": item.shipping_method,
        "success_fee_rate": fee_rate,
        "inbound_listing_fee_cny": INBOUND_FEE_CNY,
        "outbound_operation_fee_cny": OUTBOUND_FEE_CNY,
        "last_mile_delivery_fee_cny": LAST_MILE_FEE_CNY,
        "other_fee_cny": OTHER_FEE_CNY,
        "fulfillment_fee_zar": (
            item.fulfillment_fee_zar
            if item.fulfillment_fee_zar is not None
            else default_fulfillment_fee_zar(item.fee_category)
        ),
        "fee_category": item.fee_category,
    }
    calculated = calculate_all(payload, exchange_rate)
    for key, value in calculated.items():
        if hasattr(item, key):
            setattr(item, key, value)
    item.fulfillment_fee_zar = payload["fulfillment_fee_zar"]


def _refresh_status(item: SelectionItem) -> None:
    if item.promoted_product_id:
        item.status = "已建产品"
        item.selected = False
        return
    if item.profit_margin is None:
        item.status = "待核算"
        return
    item.status = "合格" if item.profit_margin >= QUALIFY_MARGIN else "不合格"


def _apply_offer_dims(item: SelectionItem, offer: Dict[str, Any]) -> bool:
    """卖家 API 的 offer 行里有重尺（cm / g），换算成 mm / kg。

    只在本店商品的 offer 上有效。返回是否真的写入了。
    """
    def number(key: str, scale: float, digits: int) -> Optional[float]:
        value = offer.get(key)
        if value in (None, ""):
            return None
        try:
            return round(float(value) * scale, digits)
        except (TypeError, ValueError):
            return None

    # 长度保留 1 位小数够了（0.1mm），重量必须 3 位——380g 写成 0.4kg 会让
    # 计费重量偏 5%，运费跟着偏。
    length = number("length_cm", 10, 1)
    width = number("width_cm", 10, 1)
    height = number("height_cm", 10, 1)
    weight = number("weight_grams", 0.001, 3)
    if length is None and width is None and height is None and weight is None:
        return False
    item.length_mm = length
    item.width_mm = width
    item.height_mm = height
    item.actual_weight_kg = weight
    item.dimensions_source = "seller_api"
    return True


def _copy_from_product_list(
    db: Session, store_id: str, items: List[SelectionItem]
) -> int:
    """自家商品直接从产品主档抄资料，返回抄到重尺的条数。

    为什么这是第一优先：销售报表只有 TSIN，而那些 TSIN 对应的商品**本来就在
    产品列表里**，重尺是你之前一个个填进去的。再走卖家 API 拉一遍既慢（1800 条
    要分块轮询）又可能把人工填的值冲掉。所以先抄主档，抄不到才走网络。

    抄的范围刻意收窄：只填**空着的**字段。报表带来的商品名/销售额不会被覆盖。
    """
    plids = sorted({i.plid for i in items if i.plid})
    tsins = sorted({i.tsin for i in items if i.tsin})
    if not plids and not tsins:
        return 0

    conditions = []
    if plids:
        conditions.append(Product.plid.in_(plids))
    if tsins:
        conditions.append(Product.tsin.in_(tsins))
    rows = (
        db.query(Product)
        .filter(Product.store_id == store_id, or_(*conditions))
        .all()
    )
    by_plid: Dict[str, Product] = {}
    by_tsin: Dict[str, Product] = {}
    for row in rows:
        if row.plid:
            by_plid.setdefault(row.plid, row)
        if row.tsin:
            by_tsin.setdefault(row.tsin, row)

    copied = 0
    for item in items:
        product = by_plid.get(item.plid) if item.plid else None
        if product is None and item.tsin:
            product = by_tsin.get(item.tsin)
        if product is None:
            continue

        if not item.plid and product.plid:
            item.plid = product.plid
        if not item.tsin and product.tsin:
            item.tsin = product.tsin
        if not item.product_name and product.product_name:
            item.product_name = product.product_name
        if not item.chinese_product_name and product.chinese_product_name:
            item.chinese_product_name = product.chinese_product_name
        if not item.product_image_url and product.product_image_url:
            item.product_image_url = product.product_image_url
        if not item.takealot_url and product.takealot_url:
            item.takealot_url = product.takealot_url
        if item.actual_sale_price_zar is None and product.actual_sale_price_zar is not None:
            item.actual_sale_price_zar = product.actual_sale_price_zar
        if item.rating_value is None and product.rating_value is not None:
            item.rating_value = product.rating_value
        if item.review_count is None and product.review_count is not None:
            item.review_count = product.review_count
        if not item.fee_category and product.fee_category:
            # 主档里的类目是人确认过的（新建产品时看过），比推断的可靠
            item.fee_category = product.fee_category
            item.fee_category_confidence = "high"
            item.fee_category_reason = "沿用产品列表里已确认的类目"

        has_dims = (
            item.length_mm is not None and item.width_mm is not None
            and item.height_mm is not None and item.actual_weight_kg is not None
        )
        if not has_dims and not item.dimensions_source:
            dims = (product.length_mm, product.width_mm,
                    product.height_mm, product.actual_weight_kg)
            if all(value is not None for value in dims):
                item.length_mm, item.width_mm, item.height_mm, item.actual_weight_kg = dims
                item.dimensions_source = "product_list"
                copied += 1
        elif item.dimensions_source == "product_list":
            copied += 1
    return copied


def _fill_catalogue_dims(db: Session, items: List[SelectionItem]) -> int:
    """竞品重尺：走卖家后台「Search Takealot's Catalogue」那套目录接口。

    只在探测确认可用时执行（`/api/selection/diag/catalogue` 写入结果）。
    拿不到的留空，等人工填——绝不用类目默认值冒充。
    """
    from services.takealot_catalogue_api import fetch_dims, load_winner, configured_key

    key = configured_key(db)
    winner = load_winner(db) if key else None
    if not key or not winner:
        return 0

    filled = 0
    for item in items:
        if item.dimensions_source or not item.plid:
            continue
        dims = fetch_dims(item.plid, key, winner)
        if not dims:
            continue
        item.length_mm = dims.get("length_mm")
        item.width_mm = dims.get("width_mm")
        item.height_mm = dims.get("height_mm")
        item.actual_weight_kg = dims.get("weight_kg")
        item.dimensions_source = "catalogue_api"
        filled += 1
    return filled


def _apply_public(item: SelectionItem, record: Optional[Dict[str, Any]], db: Session) -> None:
    """把公开接口的一条结果写进 item。"""
    if not record or not record.get("success"):
        # 主档里已经把名字和主图给全的，本来就没走公开接口——这不是失败。
        # 不区分的话，1800 条自家商品会整片标成「没抓到」。
        if item.product_image_url and item.product_name:
            item.enrich_status = "ok"
            item.enrich_message = "资料取自产品列表，未走公开接口"
            return
        item.enrich_status = "failed"
        warnings = (record or {}).get("warnings") or []
        item.enrich_message = "；".join(warnings) or "公开接口没有返回可用数据"
        return

    if not item.product_name:
        item.product_name = record.get("product_name") or ""
    if not item.product_image_url:
        item.product_image_url = record.get("product_image_url") or ""
    images = record.get("image_urls") or []
    if images:
        item.image_urls = json.dumps(images, ensure_ascii=False)
    if record.get("actual_sale_price_zar") is not None:
        item.actual_sale_price_zar = record["actual_sale_price_zar"]
    if record.get("rating_value") is not None:
        item.rating_value = record["rating_value"]
    if record.get("review_count") is not None:
        item.review_count = record["review_count"]
    if record.get("brand"):
        item.brand = record["brand"]
    if record.get("takealot_category_path"):
        item.takealot_category_path = record["takealot_category_path"]
    if not item.tsin and record.get("tsin"):
        item.tsin = record["tsin"]
    if not item.takealot_url:
        item.takealot_url = build_takealot_url(item.plid)

    # 重尺：只在不已有来源时预填。公开页的属性覆盖率和格式都不够可靠，
    # 一旦有人工填过（dimensions_source == manual）就不能被覆盖。
    if not item.dimensions_source:
        dims = record.get("dimensions") or {}
        if dims.get("length_mm") and dims.get("width_mm") and dims.get("height_mm"):
            item.length_mm = dims["length_mm"]
            item.width_mm = dims["width_mm"]
            item.height_mm = dims["height_mm"]
            if dims.get("weight_kg"):
                item.actual_weight_kg = dims["weight_kg"]
            item.dimensions_source = "public_api"

    if not item.fee_category and item.takealot_category_path:
        matched = match_fee_category(db, item.takealot_category_path, item.product_name)
        if matched.get("fee_category"):
            item.fee_category = matched["fee_category"]
            item.fee_category_confidence = matched.get("confidence") or ""
            item.fee_category_reason = matched.get("match_reason") or ""

    item.enrich_status = "ok"
    item.enrich_message = ""


def _assign_tiers(items: List[SelectionItem]) -> None:
    """帕累托分档：按累计净销售额切 S(≤50%) / A(≤80%) / B(≤95%) / C。

    只在有销售数据时执行。竞品表没有销售额，硬套会让所有商品都变 S 档。
    """
    rows = [i for i in items if i.net_sales_zar and i.net_sales_zar > 0]
    if not rows:
        return
    total = sum(i.net_sales_zar for i in rows)
    if total <= 0:
        return
    rows.sort(key=lambda x: -(x.net_sales_zar or 0))
    cumulative = 0.0
    for item in rows:
        cumulative += item.net_sales_zar or 0
        share = cumulative / total
        if share <= 0.5:
            item.tier = "S"
        elif share <= 0.8:
            item.tier = "A"
        elif share <= 0.95:
            item.tier = "B"
        else:
            item.tier = "C"


def _to_dict(item: SelectionItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "batch_name": item.batch_name,
        "plid": item.plid,
        "tsin": item.tsin,
        "takealot_url": item.takealot_url,
        "product_name": item.product_name,
        "chinese_product_name": item.chinese_product_name,
        "brand": item.brand,
        "product_image_url": item.product_image_url,
        "image_urls": json.loads(item.image_urls) if item.image_urls else [],
        "takealot_category_path": item.takealot_category_path,
        "fee_category": item.fee_category,
        "fee_category_confidence": item.fee_category_confidence,
        "fee_category_reason": item.fee_category_reason,
        "actual_sale_price_zar": item.actual_sale_price_zar,
        "rating_value": item.rating_value,
        "review_count": item.review_count,
        "sales_qty": item.sales_qty,
        "net_sales_zar": item.net_sales_zar,
        "gross_sales_zar": item.gross_sales_zar,
        "net_sales_cny": item.net_sales_cny,
        "tier": item.tier,
        "already_in_products": bool(item.already_in_products),
        "promoted_product_id": item.promoted_product_id,
        "length_mm": item.length_mm,
        "width_mm": item.width_mm,
        "height_mm": item.height_mm,
        "actual_weight_kg": item.actual_weight_kg,
        "dimensions_source": item.dimensions_source,
        "purchase_url": item.purchase_url,
        "purchase_cost_cny": item.purchase_cost_cny,
        "purchase_shipping_cny": item.purchase_shipping_cny,
        "purchase_quantity": item.purchase_quantity,
        "unit_price_cny": item.unit_price_cny,
        "packaging_cost_per_unit_cny": item.packaging_cost_per_unit_cny,
        "shipping_method": item.shipping_method,
        "fulfillment_fee_zar": item.fulfillment_fee_zar,
        "success_fee_zar": item.success_fee_zar,
        "chargeable_weight_kg": item.chargeable_weight_kg,
        "unit_product_cost_cny": item.unit_product_cost_cny,
        "international_shipping_per_unit_cny": item.international_shipping_per_unit_cny,
        "cost_to_sa_warehouse_zar": item.cost_to_sa_warehouse_zar,
        "total_cost_zar": item.total_cost_zar,
        "profit_zar": item.profit_zar,
        "profit_margin": item.profit_margin,
        "status": item.status,
        "selected": bool(item.selected),
        "enrich_status": item.enrich_status,
        "enrich_message": item.enrich_message,
        "note": item.note,
    }


# ---------------------------------------------------------------------------
# 1. 导入
# ---------------------------------------------------------------------------

@router.post("/import")
async def import_table(
    file: UploadFile = File(...),
    batch_name: str = Form(""),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """上传 xlsx / csv，按商品聚合后落进选品池。

    **必须聚合**：销售报表是一行一个订单行，直接落库 13,984 行会变成 13,984
    张卡片。这里按 PLID → TSIN → 标题 的优先级归并，销量和净销售额累加，
    归并前的行数记在返回值里，方便核对。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 30MB，请拆分后再上传")

    try:
        parsed = read_table(file.filename or "", content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:  # 损坏的 xlsx 会在 openpyxl 里抛各种异常
        raise HTTPException(status_code=400, detail=f"读表失败：{type(error).__name__}")

    rows = parsed["rows"]
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="没读到有效行。表头里至少要有一列能被识别："
                   "产品链接 / 产品名称 / TSIN / PLID",
        )

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    skipped = 0
    for index, raw in enumerate(rows):
        row = normalize_row(raw)
        if row["plid"]:
            key = f"plid:{row['plid']}"
        elif row["tsin"]:
            key = f"tsin:{row['tsin']}"
        elif row["product_name"]:
            key = f"title:{row['product_name'].strip().lower()}"
        else:
            skipped += 1
            continue

        current = groups.get(key)
        if current is None:
            current = dict(row)
            current["_rows"] = 1
            current["_row_no"] = index + 2      # +2：1 行表头，Excel 行号从 1 起
            groups[key] = current
            order.append(key)
            continue

        current["_rows"] += 1
        for field in ("sales_qty", "net_sales_zar", "gross_sales_zar"):
            value = row[field]
            if value is not None:
                current[field] = (current[field] or 0) + value
        for field in ("actual_sale_price_zar", "rating_value", "review_count",
                      "takealot_url", "chinese_product_name", "product_name"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]

    if not groups:
        raise HTTPException(status_code=400, detail="所有行都没能识别出商品身份")

    exchange_rate = _exchange_rate(db)
    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    label = (batch_name or "").strip() or (file.filename or "未命名批次")

    # 已在产品列表里的，提前标出来，避免重复建
    candidate_plids = [g["plid"] for g in groups.values() if g["plid"]]
    existing_plids: set = set()
    for start in range(0, len(candidate_plids), 500):
        chunk = candidate_plids[start:start + 500]
        existing_plids |= {
            row[0] for row in db.query(Product.plid).filter(
                Product.store_id == store_id, Product.plid.in_(chunk)
            ).all()
        }

    items: List[SelectionItem] = []
    duplicates = 0
    for key in order:
        group = groups[key]
        already = bool(group["plid"]) and group["plid"] in existing_plids
        if already:
            duplicates += 1
        item = SelectionItem(
            store_id=store_id,
            batch_id=batch_id,
            batch_name=label,
            source_row=group["_row_no"],
            plid=group["plid"],
            tsin=group["tsin"],
            takealot_url=group["takealot_url"] or build_takealot_url(group["plid"]),
            product_name=group["product_name"],
            chinese_product_name=group["chinese_product_name"],
            actual_sale_price_zar=group["actual_sale_price_zar"],
            rating_value=group["rating_value"],
            review_count=group["review_count"],
            sales_qty=group["sales_qty"],
            net_sales_zar=group["net_sales_zar"],
            gross_sales_zar=group["gross_sales_zar"],
            net_sales_cny=(
                round(group["net_sales_zar"] * exchange_rate, 2)
                if group["net_sales_zar"] is not None else None
            ),
            already_in_products=already,
            note=group["note"],
            status="待核算",
        )
        items.append(item)

    _assign_tiers(items)
    db.add_all(items)
    db.commit()

    return {
        "batch_id": batch_id,
        "batch_name": label,
        "sheet": parsed["sheet"],
        "source_rows": parsed["total_rows"],
        "merged_rows": len(rows),
        "created": len(items),
        "already_in_products": duplicates,
        "skipped": skipped,
        "with_sales_data": sum(1 for i in items if i.net_sales_zar),
        "columns": {k: v for k, v in parsed["mapping"].items()},
        "unknown_headers": parsed["unknown_headers"],
    }


# ---------------------------------------------------------------------------
# 2. 抓资料
# ---------------------------------------------------------------------------

class EnrichRequest(BaseModel):
    batch_id: Optional[str] = None
    ids: Optional[List[str]] = None
    limit: int = Field(default=ENRICH_CHUNK, ge=1, le=800)
    refresh: bool = False   # True 时连已抓过的也重抓


@router.post("/enrich")
def enrich(
    data: EnrichRequest,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """抓主图 / 类目 / 价格；自家商品顺带把重尺也填了。

    分块返回：一次最多 800 个，`remaining` 不为 0 就说明还有，前端接着调。
    """
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if data.ids:
        query = query.filter(SelectionItem.id.in_(data.ids))
    elif data.batch_id:
        query = query.filter(SelectionItem.batch_id == data.batch_id)
    query = query.filter(SelectionItem.promoted_product_id.is_(None))
    if not data.refresh:
        query = query.filter(
            or_(SelectionItem.enrich_status == "", SelectionItem.enrich_status.is_(None))
        )
    targets = query.order_by(SelectionItem.created_at).limit(data.limit).all()

    if not targets:
        return {"processed": 0, "ok": 0, "failed": 0, "with_dimensions": 0,
                "from_product_list": 0, "skipped_network": 0,
                "remaining": 0, "seller_api": "", "failures": []}

    # --- 第一优先：产品主档。自家商品的图/类目/重尺库里已经有了 ---
    #
    # 这是「销售报表只有 TSIN」的主路径：那些商品本来就在产品列表里，重尺是你
    # 之前填过的。再去卖家 API 拉一遍等于重复劳动，还可能把人工填的值覆盖掉。
    # 抄完之后，缺东西的才走网络。
    from_product_list = _copy_from_product_list(db, store_id, targets)

    # --- 第二优先：卖家 API（本店 offer，能把 TSIN 反查成 PLID）---
    offer_by_plid: Dict[str, Dict[str, Any]] = {}
    offer_by_tsin: Dict[str, Dict[str, Any]] = {}
    seller_note = "未配置卖家 API 密钥"
    try:
        from services.takealot_seller_api import _load_offers, configured_key
        key = configured_key(db)
        if key:
            offers = _load_offers(key)
            for offer in offers:
                plid = str(offer.get("productline_id") or "")
                tsin = str(offer.get("tsin_id") or "")
                if plid:
                    offer_by_plid[plid] = offer
                if tsin:
                    offer_by_tsin[tsin] = offer
            seller_note = f"已读取本店 {len(offers)} 个 offer"
        else:
            seller_note = "未配置卖家 API 密钥"
    except Exception as error:
        seller_note = f"卖家接口不可用：{error}"

    # 用 TSIN 反查 PLID，并把本店重尺填上（已经有重尺来源的不动）
    dims_from_seller = 0
    for item in targets:
        offer = None
        if item.plid and item.plid in offer_by_plid:
            offer = offer_by_plid[item.plid]
        elif item.tsin and item.tsin in offer_by_tsin:
            offer = offer_by_tsin[item.tsin]
            if not item.plid:
                item.plid = str(offer.get("productline_id") or "")
                if not item.takealot_url:
                    item.takealot_url = build_takealot_url(item.plid)
        if not offer:
            continue
        if not item.dimensions_source and _apply_offer_dims(item, offer):
            dims_from_seller += 1
        if not item.product_name:
            item.product_name = str(offer.get("title") or "")
        if not item.product_image_url:
            item.product_image_url = str(offer.get("image_url") or "")

    # --- 第三优先：竞品重尺走卖家目录接口（若探测通了）---
    dims_from_catalogue = _fill_catalogue_dims(db, targets)

    # --- 公开接口补齐图/类目/价格（并发，8 线程实测最稳）---
    #
    # 主档里已经把图/类目/价格都给全的，不用再打一次网络——1800 条销售报表
    # 里绝大多数是这种情况，省下来的是几十秒。
    if data.refresh:
        need_fetch = [item.plid for item in targets if item.plid]
    else:
        # 判定只用「有主图 + 有价格 + 有费率类目」三项。不要求
        # takealot_category_path：Product 主档本来就不存前台类目路径（它存的是
        # 已经确认过的 fee_category），拿它当必要条件会让自家商品永远重抓一遍。
        need_fetch = [
            item.plid for item in targets
            if item.plid and not (
                item.product_image_url
                and item.actual_sale_price_zar is not None
                and item.fee_category
            )
        ]
    records: Dict[str, Dict[str, Any]] = {}
    if need_fetch:
        for record in pub.fetch_many(need_fetch, workers=8):
            if record.get("plid") is not None:
                records[str(record["plid"])] = record
    skipped_network = sum(1 for item in targets if item.plid) - len(need_fetch)

    exchange_rate = _exchange_rate(db)
    fee_rates: Dict[str, Optional[float]] = {}
    ok = failed = 0
    failures: List[Dict[str, str]] = []
    for item in targets:
        _apply_public(item, records.get(item.plid or ""), db)
        if item.fee_category and item.fee_category not in fee_rates:
            fee_rates[item.fee_category] = _fee_rate(db, item.fee_category)
        _calc(item, exchange_rate, fee_rates.get(item.fee_category or ""))
        _refresh_status(item)
        if item.enrich_status == "ok":
            ok += 1
        else:
            failed += 1
            if len(failures) < 20:
                failures.append({
                    "id": item.id,
                    "plid": item.plid or "",
                    "tsin": item.tsin or "",
                    "message": item.enrich_message or "未知原因",
                })
    db.commit()

    remaining = (
        db.query(func.count(SelectionItem.id))
        .filter(
            SelectionItem.store_id == store_id,
            SelectionItem.promoted_product_id.is_(None),
            or_(SelectionItem.enrich_status == "", SelectionItem.enrich_status.is_(None)),
        )
        .scalar()
    ) or 0
    if data.ids or data.batch_id:
        remaining = 0   # 指定了范围就只跑这一批，不诱导前端无限循环

    return {
        "processed": len(targets),
        "ok": ok,
        "failed": failed,
        "with_dimensions": dims_from_seller + dims_from_catalogue,
        "from_product_list": from_product_list,
        "skipped_network": skipped_network,
        "remaining": remaining,
        "seller_api": seller_note,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# 3. 列表 / 修改
# ---------------------------------------------------------------------------

def query_items(
    db: Session,
    store_id: str,
    *,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    tier: Optional[str] = None,
    only_selected: bool = False,
    hide_duplicates: bool = False,
    sort: str = "tier",
    limit: int = 60,
    offset: int = 0,
) -> Dict[str, Any]:
    """列表查询本体。

    路由层只负责把 Query 参数转进来——`Query(None)` 这个默认值对象本身是
    真值，直接写在路由函数里再被内部调用（测试、别的模块）时，`if batch_id:`
    会永远成立，静默加上一个 `batch_id IS NULL` 的过滤条件，返回空列表。
    把逻辑拆出来就不会踩这个坑。
    """
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if batch_id:
        query = query.filter(SelectionItem.batch_id == batch_id)
    if status:
        query = query.filter(SelectionItem.status == status)
    if tier:
        query = query.filter(SelectionItem.tier == tier)
    if only_selected:
        query = query.filter(SelectionItem.selected == True)  # noqa: E712
    if hide_duplicates:
        query = query.filter(SelectionItem.already_in_products == False)  # noqa: E712
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(or_(
            SelectionItem.product_name.ilike(like),
            SelectionItem.tsin.ilike(like),
            SelectionItem.plid.ilike(like),
            SelectionItem.chinese_product_name.ilike(like),
        ))

    total = query.count()

    # 默认按档位（S→A→B→C→空）再按净销售额降序，和原来那份分析表一致
    if sort == "profit":
        query = query.order_by(SelectionItem.profit_margin.desc().nullslast())
    elif sort == "price":
        query = query.order_by(SelectionItem.actual_sale_price_zar.desc().nullslast())
    elif sort == "sales":
        query = query.order_by(SelectionItem.sales_qty.desc().nullslast())
    else:
        # 档位必须按 S→A→B→C→空 排，不能直接按字符串排：字母序里 S 排在 C 后面，
        # 直接 `coalesce(tier,'Z').asc()` 会把最好的货压到最后。
        tier_rank = case(
            (SelectionItem.tier == "S", 0),
            (SelectionItem.tier == "A", 1),
            (SelectionItem.tier == "B", 2),
            (SelectionItem.tier == "C", 3),
            else_=4,
        )
        query = query.order_by(
            tier_rank.asc(),
            SelectionItem.net_sales_zar.desc().nullslast(),
            SelectionItem.review_count.desc().nullslast(),
        )
    items = query.offset(offset).limit(limit).all()
    return {"total": total, "items": [_to_dict(i) for i in items]}


@router.get("/items")
def list_items(
    batch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    only_selected: bool = Query(False),
    hide_duplicates: bool = Query(False),
    sort: str = Query("tier"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    return query_items(
        db, store_id,
        batch_id=batch_id, status=status, keyword=keyword, tier=tier,
        only_selected=only_selected, hide_duplicates=hide_duplicates,
        sort=sort, limit=limit, offset=offset,
    )


@router.get("/batches")
def list_batches(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    rows = (
        db.query(
            SelectionItem.batch_id,
            SelectionItem.batch_name,
            func.count(SelectionItem.id),
            func.min(SelectionItem.created_at),
        )
        .filter(SelectionItem.store_id == store_id)
        .group_by(SelectionItem.batch_id, SelectionItem.batch_name)
        .order_by(func.min(SelectionItem.created_at).desc())
        .all()
    )
    return [
        {"batch_id": r[0], "batch_name": r[1], "count": r[2],
         "created_at": r[3].isoformat() if r[3] else ""}
        for r in rows
    ]


def summarize(db: Session, store_id: str, batch_id: Optional[str] = None) -> Dict[str, Any]:
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if batch_id:
        query = query.filter(SelectionItem.batch_id == batch_id)
    items = query.all()
    return {
        "total": len(items),
        "pending": sum(1 for i in items if i.status == "待核算"),
        "qualified": sum(1 for i in items if i.status == "合格"),
        "rejected": sum(1 for i in items if i.status == "不合格"),
        "promoted": sum(1 for i in items if i.status == "已建产品"),
        "selected": sum(1 for i in items if i.selected),
        "duplicates": sum(1 for i in items if i.already_in_products),
        "with_image": sum(1 for i in items if i.product_image_url),
        "with_dimensions": sum(1 for i in items if i.length_mm and i.actual_weight_kg),
        "no_plid": sum(1 for i in items if not i.plid),
    }


@router.get("/summary")
def summary(
    batch_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    return summarize(db, store_id, batch_id)


class ItemPatch(BaseModel):
    product_name: Optional[str] = None
    chinese_product_name: Optional[str] = None
    fee_category: Optional[str] = None
    takealot_url: Optional[str] = None
    actual_sale_price_zar: Optional[float] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    actual_weight_kg: Optional[float] = None
    purchase_url: Optional[str] = None
    purchase_cost_cny: Optional[float] = None
    purchase_shipping_cny: Optional[float] = None
    purchase_quantity: Optional[int] = None
    unit_price_cny: Optional[float] = None
    packaging_cost_per_unit_cny: Optional[float] = None
    shipping_method: Optional[str] = None
    selected: Optional[bool] = None
    note: Optional[str] = None


def _apply_patch(item: SelectionItem, patch: ItemPatch, db: Session, rate: float) -> None:
    fields = patch.model_dump(exclude_unset=True)
    dim_fields = {"length_mm", "width_mm", "height_mm", "actual_weight_kg"}
    if dim_fields & set(fields):
        # 人填过重尺就锁定来源，后续 enrich 不再用公开页的值覆盖
        item.dimensions_source = "manual"
    for key, value in fields.items():
        setattr(item, key, value)
    fee_rate = _fee_rate(db, item.fee_category)
    _calc(item, rate, fee_rate)
    _refresh_status(item)


@router.patch("/items/{item_id}")
def patch_item(
    item_id: str,
    patch: ItemPatch,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    item = db.query(SelectionItem).filter(
        SelectionItem.id == item_id, SelectionItem.store_id == store_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="选品记录不存在")
    _apply_patch(item, patch, db, _exchange_rate(db))
    db.commit()
    db.refresh(item)
    return _to_dict(item)


class BulkPatch(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    patch: ItemPatch


@router.post("/items/bulk")
def bulk_patch(
    data: BulkPatch,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """批量改同一个字段（主要是「统一物流方式」「统一采购数量」「全选」）。"""
    items = db.query(SelectionItem).filter(
        SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id
    ).all()
    rate = _exchange_rate(db)
    for item in items:
        _apply_patch(item, data.patch, db, rate)
    db.commit()
    return {"updated": len(items)}


class SelectQualifiedRequest(BaseModel):
    batch_id: Optional[str] = None
    min_margin: float = QUALIFY_MARGIN
    only_new: bool = True       # 跳过已在产品列表里的重复品


@router.get("/diag/catalogue")
def diag_catalogue(
    plid: str = Query(..., min_length=1, description="任取一个竞品 PLID，用来探测目录接口"),
    save: bool = Query(True, description="探测成功后记住这套鉴权方式，enrich 直接复用"),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """探测「Search Takealot's Catalogue」那套目录接口能不能拿到竞品重尺。

    无 key 探测显示 `/v1/catalogue` 系列返回 401（路径存在、只差鉴权），但它的
    鉴权方案和 `/v1/offers` 不是同一套，光看错误码猜不出 key 该放哪。这里用你
    已经存好的 key 把「端点 × 鉴权位置」的组合挨个试一遍，一次点出结论。

    返回里只有端点、鉴权方式、状态码和字段名，**不含 key**。
    """
    from services import takealot_catalogue_api as catalogue

    report = catalogue.probe(db, plid)
    if report.get("ok") and save:
        catalogue.save_winner(db, report["winner"])
        report["saved"] = True
    return report


@router.get("/diag/public")
def diag_public(
    plid: str = Query(..., min_length=1, description="任取一个 PLID，用来测试公开接口"),
    store_id: str = Depends(get_store_id),
):
    """从**服务器**这一侧测一次公开接口，把真实结果原样返回。

    为什么需要它：抓不到主图时，界面上只有一句「公开接口返回 403」或
    「公开接口读取失败：URLError」，看不出是接口挂了、PLID 不对，还是这台
    服务器到 Takealot 的链路被挡。在你自己的电脑上测又永远成功（家庭 IP
    和机房 IP 在 Cloudflare 眼里不是一回事），所以只能让服务器自己回答。
    """
    from services import takealot_public_api as pub

    record = pub.fetch_product(plid)
    image = record.get("product_image_url") or ""
    return {
        "plid": plid,
        "success": bool(record.get("success")),
        "warnings": record.get("warnings") or [],
        "product_name": record.get("product_name"),
        "product_image_url": image,
        "image_ok": bool(image),
        "takealot_category_path": record.get("takealot_category_path"),
        "actual_sale_price_zar": record.get("actual_sale_price_zar"),
        "dimensions": record.get("dimensions") or {},
        # 图床是另一个域名（media.takealot.com），它的可用性和接口不是一回事：
        # 接口通了但图床被挡，照样会显示成「没抓到主图」。
        "hint": (
            "接口正常，主图和类目都拿到了"
            if record.get("success") and image
            else "接口通了但没返回图库" if record.get("success")
            else "接口这一层就失败了，看 warnings"
        ),
    }


@router.post("/items/select-qualified")
def select_qualified(
    data: SelectQualifiedRequest,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """把「利润率达标且还没建过产品」的整批勾上。

    前端逐页取 id 再批量勾会漏：翻页期间列表在变，而且 1800 条要翻 30 页。
    这里一次 UPDATE 解决，条件与列表筛选同源（都用 profit_margin）。
    """
    query = db.query(SelectionItem).filter(
        SelectionItem.store_id == store_id,
        SelectionItem.profit_margin.isnot(None),
        SelectionItem.profit_margin >= data.min_margin,
        SelectionItem.promoted_product_id.is_(None),
    )
    if data.batch_id:
        query = query.filter(SelectionItem.batch_id == data.batch_id)
    if data.only_new:
        query = query.filter(SelectionItem.already_in_products == False)  # noqa: E712
    selected = query.update({SelectionItem.selected: True}, synchronize_session=False)
    db.commit()
    return {"selected": selected, "min_margin": data.min_margin}


class IdList(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=500)


@router.post("/items/promote")
def promote(
    data: IdList,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """勾中的选品 → 复制进产品列表。

    只复制「核算需要的东西」，不复制销售数据（那是竞品/历史数据，不该进主档）。
    建完之后 item 指向新产品，再点就不会重复建。
    """
    from api.product_routes import _apply_calculated_fields

    items = db.query(SelectionItem).filter(
        SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id
    ).all()
    if not items:
        raise HTTPException(status_code=404, detail="没有找到可新建的选品记录")

    created: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    for item in items:
        if item.promoted_product_id:
            skipped.append({"id": item.id, "reason": "已建过产品"})
            continue
        if item.already_in_products and item.plid:
            skipped.append({"id": item.id, "reason": "产品列表里已有同一 PLID"})
            continue
        if not item.product_name and not item.plid:
            skipped.append({"id": item.id, "reason": "既没有商品名也没有 PLID，无法建产品"})
            continue

        product = Product(
            store_id=store_id,
            takealot_url=item.takealot_url or build_takealot_url(item.plid),
            plid=item.plid or "",
            tsin=item.tsin or "",
            product_name=item.product_name or "",
            chinese_product_name=item.chinese_product_name or "",
            product_image_url=item.product_image_url or "",
            actual_sale_price_zar=item.actual_sale_price_zar,
            fee_category=item.fee_category or None,
            # 类目是自动匹配的：用户在建产品时点过一次「新建」，视为认可；
            # 后面在详情页仍可改，改了会重算。
            fee_category_confirmed=bool(item.fee_category),
            purchase_url=item.purchase_url or "",
            purchase_cost_cny=item.purchase_cost_cny,
            purchase_shipping_cny=item.purchase_shipping_cny,
            purchase_quantity=item.purchase_quantity or 4,
            unit_price_cny=item.unit_price_cny,
            packaging_cost_per_unit_cny=(
                item.packaging_cost_per_unit_cny
                if item.packaging_cost_per_unit_cny is not None else 1.0
            ),
            length_mm=item.length_mm,
            width_mm=item.width_mm,
            height_mm=item.height_mm,
            actual_weight_kg=item.actual_weight_kg,
            shipping_method=item.shipping_method,
            rating_value=item.rating_value,
            review_count=item.review_count,
            note=item.note or "",
            fulfillment_fee_zar=default_fulfillment_fee_zar(item.fee_category),
        )
        product.success_fee_rate = _fee_rate(db, item.fee_category)
        max_no = (
            db.query(Product.product_no)
            .filter(Product.store_id == store_id)
            .order_by(Product.product_no.desc())
            .first()
        )
        product.product_no = (max_no[0] + 1) if max_no and max_no[0] else 1
        _apply_calculated_fields(db, product)
        product.recorded_at = datetime.utcnow()
        db.add(product)
        db.flush()

        item.promoted_product_id = product.id
        item.status = "已建产品"
        item.selected = False
        created.append({"id": item.id, "product_id": product.id,
                        "product_no": str(product.product_no),
                        "product_name": product.product_name})

    db.commit()
    return {"created": len(created), "skipped": len(skipped),
            "products": created, "skip_details": skipped}


@router.delete("/items")
def delete_items(
    data: IdList,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    deleted = (
        db.query(SelectionItem)
        .filter(SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}


@router.delete("/batches/{batch_id}")
def delete_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """整批删掉。已建产品的记录保留，否则产品列表里那条就追溯不到来源了。"""
    deleted = (
        db.query(SelectionItem)
        .filter(
            SelectionItem.batch_id == batch_id,
            SelectionItem.store_id == store_id,
            SelectionItem.promoted_product_id.is_(None),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}
