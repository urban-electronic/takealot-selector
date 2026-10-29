"""选品池：导入 → 抓资料 → 补重尺 → 一键新建产品。

这里钉的是几个「错了会真亏钱」的点：
1. **聚合**。销售报表一行一个订单行，不聚合的话 13,984 行会变成 13,984 张卡片，
   而且同一商品的价格/评分会被最后一行覆盖。
2. **单位换算**。卖家 API 给的是 cm / g，库里存 mm / kg；换算错了运费按 10 倍
   或 1/1000 算，利润率全废。
3. **重尺不能被覆盖**。人工填过（dimensions_source=manual）之后，公开页那些
   不可靠的属性不许再写回来。
4. **重复新建**。同一 PLID 已在产品列表时不能再建一条。
5. **15% 合格线**。低于 15% 的必须落到「不合格」，不能因为算不出就默认放行。
"""

import asyncio
import io
from datetime import datetime

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

from api.selection_routes import (
    BulkPatch,
    EnrichRequest,
    IdList,
    ItemPatch,
    bulk_patch,
    delete_batch,
    enrich,
    import_table,
    patch_item,
    promote,
    query_items,
    summarize,
)
from database import Base
from models import (
    DEFAULT_STORE_ID,
    FeeCategory,
    FeeMappingRule,
    Product,
    SelectionItem,
    Store,
)
from services import takealot_public_api as pub
from services.selection_import import to_number

STORE = DEFAULT_STORE_ID


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id=STORE, name="默认店铺", status="active"))
    session.add(FeeCategory(name="Homeware", success_fee_rate=0.15, active=True))
    session.add(FeeCategory(name="Games", success_fee_rate=0.12, active=True))
    session.add(FeeMappingRule(
        takealot_category_pattern="Home & Kitchen", fee_category="Homeware",
        priority=85, active=True,
    ))
    session.commit()
    yield session
    session.close()


def make_xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(content, filename="选品.xlsx"):
    return UploadFile(file=io.BytesIO(content), filename=filename)


def do_import(db, rows, name="测试批次"):
    return asyncio.run(import_table(
        file=upload(make_xlsx(rows)), batch_name=name, db=db, store_id=STORE,
    ))


# ---------------------------------------------------------------------------
# 数字解析
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("R 1,234.56", 1234.56),
    ("1 234,56", 1234.56),      # 欧洲写法：空格千分位 + 逗号小数
    ("1.234,56", 1234.56),
    ("R325", 325.0),
    ("1,234", 1234.0),          # 纯逗号且末段 3 位 → 千分位
    ("0,5", 0.5),               # 纯逗号且末段 1 位 → 小数
    ("R325 (含税)", 325.0),
    ("-12,5", -12.5),
    ("", None),
    (None, None),
])
def test_to_number(raw, expected):
    assert to_number(raw) == expected


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def test_import_aggregates_order_rows_by_tsin(db):
    """一行一个订单行 → 同一个 TSIN 只留一条，销量和净销售额累加。"""
    result = do_import(db, [
        ["Product Title", "TSIN", "Qty", "Net Sales Amount"],
        ["Makeup Brush Holder", "97400904", 1, 194.0],
        ["Makeup Brush Holder", "97400904", 2, 388.0],
        ["Other Thing", "97400905", 1, 50.0],
    ])
    assert result["created"] == 2
    assert result["source_rows"] == 3

    items = db.query(SelectionItem).order_by(SelectionItem.tsin).all()
    holder = items[0]
    assert holder.tsin == "97400904"
    assert holder.sales_qty == 3
    assert holder.net_sales_zar == 582.0
    assert holder.net_sales_cny == pytest.approx(582.0 * 0.41, abs=0.01)
    # 帕累托是按**累计**占比切档：582 已经占 632 的 92%，落在 ≤95% 这一档，
    # 所以是 B 而不是 S。只有商品足够多、头名占比不到 50% 时才出现 S。
    assert holder.tier == "B"
    assert items[1].tier == "C"


def test_import_accepts_links_only_and_extracts_plid(db):
    """竞品表没有 TSIN，只能靠链接；PLID 必须从链接里取出来。"""
    result = do_import(db, [
        ["产品名称", "产品链接", "产品价格(R)", "产品评分", "评分数量"],
        ["BMW Timing Tool", "https://www.takealot.com/bmw-timing-tool/PLID102501867",
         3298.0, 4.5, 12],
        ["Some Bag", "https://www.takealot.com/some-bag/PLID999", 199.0, "-", 0],
    ])
    assert result["created"] == 2
    item = db.query(SelectionItem).filter(SelectionItem.plid == "102501867").first()
    assert item is not None
    assert item.product_name == "BMW Timing Tool"
    assert item.actual_sale_price_zar == 3298.0
    assert item.review_count == 12
    assert item.rating_value == 4.5
    # 没有销售数据就不该分档，否则竞品表全是 S
    assert item.tier == ""


def test_import_marks_products_already_in_catalog(db):
    db.add(Product(store_id=STORE, plid="102501867", product_name="已有商品"))
    db.commit()
    result = do_import(db, [
        ["产品名称", "产品链接"],
        ["BMW Timing Tool", "https://www.takealot.com/x/PLID102501867"],
        ["New Thing", "https://www.takealot.com/y/PLID555"],
    ])
    assert result["already_in_products"] == 1
    flags = {i.plid: i.already_in_products for i in db.query(SelectionItem).all()}
    assert flags == {"102501867": True, "555": False}


def test_import_rejects_table_without_identity_columns(db):
    with pytest.raises(HTTPException) as error:
        do_import(db, [["随便一列", "另一列"], ["a", "b"]])
    assert error.value.status_code == 400


# ---------------------------------------------------------------------------
# 抓资料
# ---------------------------------------------------------------------------

def _fake_public(plid):
    return {
        "plid": int(plid),
        "success": True,
        "product_name": "Public Title",
        "product_image_url": f"https://media.takealot.com/x/{plid}.jpg",
        "actual_sale_price_zar": 500.0,
        "tsin": "111",
        "takealot_category_path": "Home & Kitchen > Kitchen Storage",
        "brand": "Acme",
        "rating_value": 4.6,
        "review_count": 33,
        "image_urls": [f"https://media.takealot.com/x/{plid}.jpg"],
        "dimensions": {},
        "warnings": [],
    }


def test_enrich_fills_image_category_and_fee_category(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [_fake_public(p) for p in plids])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert result["processed"] == 1
    assert result["ok"] == 1

    item = db.query(SelectionItem).first()
    assert item.product_image_url.endswith("700.jpg")
    assert item.takealot_category_path == "Home & Kitchen > Kitchen Storage"
    # 类目 → 费率类目靠 fee_mapping_rules 推断
    assert item.fee_category == "Homeware"
    assert item.fee_category_confidence == "high"
    assert item.actual_sale_price_zar == 500.0
    # 商品名原来有值，不能被公开接口覆盖
    assert item.product_name == "Tool"


def test_enrich_reads_dimensions_from_seller_offers(db, monkeypatch):
    """自家商品：卖家 API 的 cm / g 要换算成 mm / kg，并且 TSIN 能反查 PLID。"""
    do_import(db, [["Product Title", "TSIN"],
                   ["My Own Product", "88880001"]])

    offers = [{
        "productline_id": 424242,
        "tsin_id": "88880001",
        "title": "My Own Product",
        "image_url": "https://media.takealot.com/y.jpg",
        "length_cm": 22.0, "width_cm": 7.2, "height_cm": 17.0,
        "weight_grams": 380.0,
    }]
    monkeypatch.setattr(
        "services.takealot_seller_api.configured_key", lambda db: "k" * 128
    )
    monkeypatch.setattr(
        "services.takealot_seller_api._load_offers", lambda key: offers
    )
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert result["with_dimensions"] == 1

    item = db.query(SelectionItem).first()
    assert item.plid == "424242"
    assert item.length_mm == 220.0
    assert item.width_mm == 72.0
    assert item.height_mm == 170.0
    assert item.actual_weight_kg == 0.38
    assert item.dimensions_source == "seller_api"


def test_enrich_does_not_overwrite_manual_dimensions(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID701"]])
    item = db.query(SelectionItem).first()
    item.length_mm, item.width_mm, item.height_mm = 300.0, 200.0, 100.0
    item.actual_weight_kg = 1.2
    item.dimensions_source = "manual"
    db.commit()

    record = _fake_public("701")
    record["dimensions"] = {"length_mm": 10.0, "width_mm": 10.0,
                            "height_mm": 10.0, "weight_kg": 0.1}
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [record])

    enrich(EnrichRequest(), db=db, store_id=STORE)
    db.refresh(item)
    assert item.length_mm == 300.0
    assert item.actual_weight_kg == 1.2
    assert item.dimensions_source == "manual"


def test_enrich_skips_already_done_unless_refreshed(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID702"]])
    calls = []

    def fake(plids, workers=8):
        calls.append(list(plids))
        return [_fake_public(p) for p in plids]

    monkeypatch.setattr(pub, "fetch_many", fake)
    enrich(EnrichRequest(), db=db, store_id=STORE)
    second = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert second["processed"] == 0

    enrich(EnrichRequest(refresh=True), db=db, store_id=STORE)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 补重尺 / 算利润
# ---------------------------------------------------------------------------

def _prepare_item(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID703"]])
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [_fake_public(p) for p in plids])
    enrich(EnrichRequest(), db=db, store_id=STORE)
    return db.query(SelectionItem).first()


def test_patch_computes_margin_and_qualifies_at_15(db, monkeypatch):
    item = _prepare_item(db, monkeypatch)
    updated = patch_item(item.id, ItemPatch(
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=40, purchase_shipping_cny=20, purchase_quantity=4,
        shipping_method="空运普货",
    ), db=db, store_id=STORE)

    assert updated["chargeable_weight_kg"] == 0.5
    assert updated["total_cost_zar"] is not None
    assert updated["profit_margin"] > 0.15
    assert updated["status"] == "合格"
    assert updated["dimensions_source"] == "manual"


def test_patch_below_15_margin_is_rejected(db, monkeypatch):
    item = _prepare_item(db, monkeypatch)
    updated = patch_item(item.id, ItemPatch(
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=400, purchase_shipping_cny=0, purchase_quantity=4,
        shipping_method="空运普货",
    ), db=db, store_id=STORE)
    assert updated["profit_margin"] < 0.15
    assert updated["status"] == "不合格"


def test_missing_dimensions_stays_pending(db, monkeypatch):
    """没有重尺就算不出运费，必须留在「待核算」，不能默认放行。"""
    item = _prepare_item(db, monkeypatch)
    updated = patch_item(item.id, ItemPatch(
        purchase_cost_cny=40, purchase_shipping_cny=20,
    ), db=db, store_id=STORE)
    assert updated["profit_margin"] is None
    assert updated["status"] == "待核算"


def test_bulk_patch_applies_to_many(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["A", "https://www.takealot.com/a/PLID801"],
                   ["B", "https://www.takealot.com/b/PLID802"]])
    ids = [i.id for i in db.query(SelectionItem).all()]
    result = bulk_patch(BulkPatch(
        ids=ids,
        patch=ItemPatch(shipping_method="海运普货", selected=True),
    ), db=db, store_id=STORE)
    assert result["updated"] == 2
    assert all(i.selected for i in db.query(SelectionItem).all())


# ---------------------------------------------------------------------------
# 一键新建产品
# ---------------------------------------------------------------------------

def test_promote_creates_product_once(db, monkeypatch):
    item = _prepare_item(db, monkeypatch)
    patch_item(item.id, ItemPatch(
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=40, purchase_shipping_cny=20, purchase_quantity=4,
        shipping_method="空运普货", selected=True,
    ), db=db, store_id=STORE)

    result = promote(IdList(ids=[item.id]), db=db, store_id=STORE)
    assert result["created"] == 1
    assert result["skipped"] == 0

    product = db.query(Product).first()
    assert product.product_no == 1
    assert product.plid == "703"
    assert product.length_mm == 200
    assert product.fee_category == "Homeware"
    assert product.fee_category_confirmed is True
    assert product.success_fee_rate == 0.15
    assert product.profit_margin is not None

    db.refresh(item)
    assert item.promoted_product_id == product.id
    assert item.status == "已建产品"

    # 再点一次不能又建一条
    again = promote(IdList(ids=[item.id]), db=db, store_id=STORE)
    assert again["created"] == 0
    assert again["skipped"] == 1
    assert db.query(Product).count() == 1


def test_promote_skips_duplicate_plid(db, monkeypatch):
    db.add(Product(store_id=STORE, plid="703", product_name="已有"))
    db.commit()
    item = _prepare_item(db, monkeypatch)
    result = promote(IdList(ids=[item.id]), db=db, store_id=STORE)
    assert result["created"] == 0
    assert result["skip_details"][0]["reason"] == "产品列表里已有同一 PLID"
    assert db.query(Product).count() == 1


def test_promote_keeps_increasing_product_no(db, monkeypatch):
    db.add(Product(store_id=STORE, product_no=7, plid="x", product_name="已有"))
    db.commit()
    item = _prepare_item(db, monkeypatch)
    promote(IdList(ids=[item.id]), db=db, store_id=STORE)
    created = db.query(Product).filter(Product.plid == "703").first()
    assert created.product_no == 8


# ---------------------------------------------------------------------------
# 列表与统计
# ---------------------------------------------------------------------------

def test_list_filters_and_sorts_by_tier(db):
    """累计占比 50 / 75 / 90 / 100% → S / A / B / C，列表按档位升序排。"""
    do_import(db, [
        ["Product Title", "TSIN", "Qty", "Net Sales Amount"],
        ["Top Seller", "1", 10, 500.0],
        ["Second Seller", "2", 5, 250.0],
        ["Third Seller", "3", 3, 150.0],
        ["Tail Seller", "4", 1, 100.0],
    ])
    page = query_items(db, STORE, limit=10)
    assert page["total"] == 4
    assert [i["tier"] for i in page["items"]] == ["S", "A", "B", "C"]
    assert page["items"][0]["product_name"] == "Top Seller"

    only_s = query_items(db, STORE, tier="S", limit=10)
    assert only_s["total"] == 1
    assert only_s["items"][0]["product_name"] == "Top Seller"


def test_summary_counts(db, monkeypatch):
    item = _prepare_item(db, monkeypatch)
    stats = summarize(db, STORE)
    assert stats["total"] == 1
    assert stats["with_image"] == 1
    assert stats["with_dimensions"] == 0
    patch_item(item.id, ItemPatch(
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
    ), db=db, store_id=STORE)
    assert summarize(db, STORE)["with_dimensions"] == 1


def test_delete_batch_keeps_promoted_items(db, monkeypatch):
    """整批删掉时，已经建成产品的记录要留下——否则产品列表里那条就追溯不到来源。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Keep Me", "https://www.takealot.com/a/PLID910"],
        ["Drop Me", "https://www.takealot.com/b/PLID911"],
    ])
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])
    kept = db.query(SelectionItem).filter(SelectionItem.plid == "910").first()
    batch_id = kept.batch_id
    patch_item(kept.id, ItemPatch(
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=40, purchase_shipping_cny=20,
        shipping_method="空运普货",
    ), db=db, store_id=STORE)
    promote(IdList(ids=[kept.id]), db=db, store_id=STORE)

    deleted = delete_batch(batch_id, db=db, store_id=STORE)
    assert deleted["deleted"] == 1
    remaining = db.query(SelectionItem).all()
    assert len(remaining) == 1
    assert remaining[0].plid == "910"
    assert remaining[0].promoted_product_id
