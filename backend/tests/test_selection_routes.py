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
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

from api.selection_routes import (
    BulkPatch,
    EnrichRequest,
    IdList,
    ItemPatch,
    SelectQualifiedRequest,
    bulk_patch,
    delete_batch,
    diag_public,
    enrich,
    import_table,
    patch_item,
    promote,
    query_items,
    select_qualified,
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


def _column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def make_xlsx(rows):
    """手写一个最小 xlsx。

    不用 openpyxl 的 Workbook 写：它的 worksheet writer 要 `NamedTemporaryFile`，
    在禁止写临时目录的环境（CI 沙箱、只读容器）里会直接 FileNotFoundError，
    测试就变成了「环境检测」。顺带还钉住了读取端对 inlineStr 的处理——真实导出
    里 sharedStrings 和 inlineStr 都出现过。
    """
    body = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            if value is None or value == "":
                continue
            ref = f"{_column_name(column_index)}{row_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                )
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>',
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
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


def test_enrich_copies_dimensions_from_product_list(db, monkeypatch):
    """自家商品的重尺直接取自产品列表，不再调卖家 API。

    销售报表只有 TSIN，那些商品本来就在主档里、重尺是人工填过的。走网络既慢
    又可能把人工值冲掉，所以主档是第一优先。
    """
    db.add(Product(
        store_id=STORE, tsin="88880002", plid="555001",
        product_name="Own Product", product_image_url="https://media.takealot.com/a.jpg",
        takealot_url="https://www.takealot.com/a/PLID555001",
        fee_category="Homeware", actual_sale_price_zar=500.0,
        length_mm=310.0, width_mm=210.0, height_mm=110.0, actual_weight_kg=1.35,
    ))
    db.commit()
    do_import(db, [["Product Title", "TSIN"], ["Own Product", "88880002"]])

    called = []
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: called.append(list(plids)) or [])
    # 卖家接口故意给一组不同的值：如果被它覆盖，说明优先级错了
    offers = [{"productline_id": 555001, "tsin_id": "88880002",
               "length_cm": 1.0, "width_cm": 1.0, "height_cm": 1.0, "weight_grams": 1.0}]
    monkeypatch.setattr("services.takealot_seller_api.configured_key", lambda db: "k" * 128)
    monkeypatch.setattr("services.takealot_seller_api._load_offers", lambda key: offers)

    result = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert result["from_product_list"] == 1

    item = db.query(SelectionItem).first()
    assert item.plid == "555001"          # TSIN → PLID 从主档补上
    assert item.length_mm == 310.0        # 主档的值，不是 offer 里的 10mm
    assert item.actual_weight_kg == 1.35
    assert item.dimensions_source == "product_list"
    assert item.fee_category == "Homeware"
    assert item.fee_category_confidence == "high"
    # 主档里图/类目/价格都齐了，不该再打公开接口
    assert result["skipped_network"] == 1
    assert called == []


def test_enrich_falls_back_to_network_when_product_incomplete(db, monkeypatch):
    """主档里只有价格、没有重尺和主图 → 还是要走网络。"""
    db.add(Product(store_id=STORE, tsin="88880003", plid="555002",
                   product_name="Half Product", actual_sale_price_zar=500.0))
    db.commit()
    do_import(db, [["Product Title", "TSIN"], ["Half Product", "88880003"]])
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [_fake_public(p) for p in plids])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert result["from_product_list"] == 0
    assert result["skipped_network"] == 0
    item = db.query(SelectionItem).first()
    assert item.product_image_url.endswith("555002.jpg")
    assert item.takealot_category_path == "Home & Kitchen > Kitchen Storage"


def test_enrich_defers_network_when_throttled(db, monkeypatch):
    """被限流时节流会把间隔放大，一轮抓不完就留给下一轮。

    为什么必须切：`enrich` 是「先整批抓、最后 commit」。间隔被放大到 2 秒时，
    400 个要跑十几分钟，请求会先被代理掐掉，事务没提交——抓到的全白费。
    切一刀之后每轮都能落库，`remaining` 让前端接着调。
    """
    do_import(db, [["产品名称", "产品链接"],
                   ["A", "https://www.takealot.com/a/PLID801"],
                   ["B", "https://www.takealot.com/b/PLID802"],
                   ["C", "https://www.takealot.com/c/PLID803"]])
    calls = []

    def fake(plids, workers=8):
        calls.append(list(plids))
        return [_fake_public(p) for p in plids]

    monkeypatch.setattr(pub, "fetch_many", fake)
    # 间隔 100 秒 → 45 秒预算只够 1 个
    monkeypatch.setattr(pub.PACER, "interval", 100.0)

    first = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert [len(c) for c in calls] == [1]
    assert first["processed"] == 1
    assert first["skipped_network"] == 0     # 推迟的不是「跳过」，不能算成省了网络
    assert first["remaining"] == 2           # 前端会接着调下一轮

    second = enrich(EnrichRequest(), db=db, store_id=STORE)
    assert [len(c) for c in calls] == [1, 1]
    assert second["remaining"] == 1


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("budget", [1, 2])
def test_enrich_budgets_unique_plids_and_applies_to_every_row(db, monkeypatch, refresh, budget):
    # 同一商品可来自不同导入批次；预算按实际请求算，结果仍按选品行计数。
    for index, plid in enumerate(["801", "801", "802"]):
        db.add(SelectionItem(
            id=f"budget-{index}", store_id=STORE, batch_id=f"batch-{index}",
            plid=plid, product_name="", enrich_status="",
            created_at=datetime(2026, 10, 29, 0, 0, index),
        ))
    db.commit()
    monkeypatch.setattr("services.takealot_seller_api.configured_key", lambda db: None)
    monkeypatch.setattr("services.takealot_catalogue_api.load_winner", lambda db: None)
    monkeypatch.setattr(pub, "fetch_budget", lambda seconds: budget)
    calls = []

    def fake(plids, workers=4):
        calls.append(list(plids))
        return [_fake_public(p) for p in plids]

    monkeypatch.setattr(pub, "fetch_many", fake)
    first = enrich(EnrichRequest(refresh=refresh), db=db, store_id=STORE)
    assert calls == [["801", "802"][:budget]]
    assert first["processed"] == (2 if budget == 1 else 3)
    assert first["ok"] == first["processed"]
    assert first["failed"] == 0
    assert first["skipped_network"] == 0
    assert first["remaining"] == (1 if budget == 1 else 0)
    db.expire_all()
    rows = db.query(SelectionItem).order_by(SelectionItem.created_at).all()
    assert [row.enrich_status for row in rows] == ["ok", "ok", "" if budget == 1 else "ok"]
    assert rows[0].product_image_url == rows[1].product_image_url != ""
    if budget == 1:
        second = enrich(EnrichRequest(), db=db, store_id=STORE)
        assert calls == [["801"], ["802"]]
        assert second["processed"] == second["ok"] == 1
        assert second["remaining"] == 0


@pytest.mark.parametrize("scope", ["batch", "ids", "ids_precedence"])
@pytest.mark.parametrize("budget", [1, 10])
def test_enrich_remaining_matches_scope_beyond_chunk(db, monkeypatch, scope, budget):
    for index in range(3):
        db.add(SelectionItem(
            id=f"scope-{index}", store_id=STORE, batch_id="selected",
            plid=str(810 + index), enrich_status=None if index == 2 else "",
            created_at=datetime(2026, 10, 29, 0, 0, index),
        ))
    db.add(Store(id="other-scope-store", name="other", status="active"))
    db.add_all([
        SelectionItem(id="unrelated", store_id=STORE, batch_id="elsewhere", plid="900"),
        SelectionItem(id="other-store", store_id="other-scope-store", batch_id="selected", plid="901"),
        SelectionItem(id="completed", store_id=STORE, batch_id="selected", enrich_status="ok"),
        SelectionItem(id="failed", store_id=STORE, batch_id="selected", enrich_status="failed"),
        SelectionItem(id="promoted", store_id=STORE, batch_id="selected", promoted_product_id="existing"),
    ])
    db.commit()
    monkeypatch.setattr("services.takealot_seller_api.configured_key", lambda db: None)
    monkeypatch.setattr("services.takealot_catalogue_api.load_winner", lambda db: None)
    monkeypatch.setattr(pub, "fetch_budget", lambda seconds: budget)
    calls = []

    def fake(plids, workers=4):
        calls.append(list(plids))
        return [_fake_public(p) for p in plids]

    monkeypatch.setattr(pub, "fetch_many", fake)
    kwargs = {"batch_id": "selected"} if scope == "batch" else {
        "ids": ["scope-0", "scope-1", "scope-2", "other-store", "promoted", "completed", "failed"],
    }
    if scope == "ids_precedence":
        kwargs["batch_id"] = "elsewhere"  # ids优先，与实际处理范围保持相同规则
    results = []
    for _ in range(4):
        result = enrich(EnrichRequest(limit=2, **kwargs), db=db, store_id=STORE)
        results.append(result)
        if not result["remaining"]:
            break
    assert results[0]["remaining"] == (2 if budget == 1 else 1)
    assert sum(r["processed"] for r in results) == 3
    assert all(r["processed"] > 0 for r in results)
    assert results[-1]["remaining"] == 0
    assert [p for call in calls for p in call] == ["810", "811", "812"]
    db.expire_all()
    assert db.get(SelectionItem, "scope-2").enrich_status == "ok"
    assert not db.get(SelectionItem, "unrelated").enrich_status
    assert not db.get(SelectionItem, "other-store").enrich_status
    assert db.get(SelectionItem, "failed").enrich_status == "failed"


def test_enrich_budget_expiry_keeps_deferred_rows_pending_and_saved_data(db, monkeypatch):
    db.add_all([
        SelectionItem(id="deadline-kept", store_id=STORE, batch_id="deadline", plid="811"),
        SelectionItem(id="deadline-later", store_id=STORE, batch_id="deadline", plid="812",
                      product_name="saved", product_image_url="saved-image", actual_sale_price_zar=77),
    ])
    db.commit()
    monkeypatch.setattr("services.takealot_seller_api.configured_key", lambda db: None)
    monkeypatch.setattr("services.takealot_catalogue_api.load_winner", lambda db: None)
    monkeypatch.setattr(pub, "fetch_many", lambda *a, **k: [
        _fake_public("811"),
        {"plid": 812, "success": False, "budget_deferred": True},
    ])
    result = enrich(EnrichRequest(batch_id="deadline"), db=db, store_id=STORE)
    assert result["processed"] == result["ok"] == 1
    assert result["failed"] == 0
    assert result["remaining"] == 1
    assert not result["public_access_blocked"]
    db.expire_all()
    later = db.get(SelectionItem, "deadline-later")
    assert not later.enrich_status
    assert (later.product_name, later.product_image_url, later.actual_sale_price_zar) == ("saved", "saved-image", 77)


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


def test_select_qualified_only_picks_margin_ok_and_new(db, monkeypatch):
    """预勾选必须同时满足「利润率≥15%」和「还没建过产品」。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Good", "https://www.takealot.com/a/PLID821"],
        ["Bad", "https://www.takealot.com/b/PLID822"],
    ])
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])
    good = db.query(SelectionItem).filter(SelectionItem.plid == "821").first()
    bad = db.query(SelectionItem).filter(SelectionItem.plid == "822").first()
    # 没有费率类目就算不出利润，所以这两条要显式给类目（真实流程里由 enrich 推断）
    patch_item(good.id, ItemPatch(
        fee_category="Homeware", actual_sale_price_zar=500.0,
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=40, purchase_shipping_cny=20,
        shipping_method="空运普货",
    ), db=db, store_id=STORE)
    patch_item(bad.id, ItemPatch(
        fee_category="Homeware", actual_sale_price_zar=500.0,
        length_mm=200, width_mm=150, height_mm=100, actual_weight_kg=0.5,
        purchase_cost_cny=400, purchase_shipping_cny=0,
        shipping_method="空运普货",
    ), db=db, store_id=STORE)

    result = select_qualified(SelectQualifiedRequest(), db=db, store_id=STORE)
    assert result["selected"] == 1
    db.refresh(good)
    db.refresh(bad)
    assert good.selected is True
    assert bad.selected is False


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


class TestDiagPublic:
    """`/diag/public` 是排查「没抓到主图」的唯一手段。

    抓不到图时界面上只有一句「公开接口返回 403」，看不出是 PLID 错、接口变了，
    还是服务器到 Takealot 的链路被挡——本地测又永远成功，所以必须让服务器自己
    回答。这里钉住「成功/失败都要把原因原样带回去」，别让人对着一个空白面板猜。
    """

    @staticmethod
    def _record(**over):
        base = {
            "plid": 94399784,
            "success": True,
            "warnings": [],
            "product_name": "Adjustable Foot Stool",
            "product_image_url": "https://media.takealot.com/covers_images/x/s-zoom.file",
            "takealot_category_path": "Automotive > Exterior Accessories",
            "actual_sale_price_zar": 1158.0,
            "dimensions": {"length_mm": 310, "width_mm": 210, "height_mm": 110},
        }
        base.update(over)
        return base

    def test_reports_image_ready(self, monkeypatch):
        from services import takealot_public_api as pub
        monkeypatch.setattr(pub, "fetch_product", lambda plid: self._record())

        report = diag_public(plid="94399784", store_id=DEFAULT_STORE_ID)

        assert report["success"] is True
        assert report["image_ok"] is True
        assert report["product_image_url"].endswith("s-zoom.file")
        assert "拿到了" in report["hint"]

    def test_surfaces_the_transport_failure(self, monkeypatch):
        from services import takealot_public_api as pub
        monkeypatch.setattr(pub, "fetch_product", lambda plid: self._record(
            success=False, product_image_url=None, product_name=None,
            takealot_category_path=None, warnings=["公开接口返回 403"],
        ))

        report = diag_public(plid="94399784", store_id=DEFAULT_STORE_ID)

        assert report["success"] is False
        assert report["image_ok"] is False
        assert report["warnings"] == ["公开接口返回 403"]

    def test_flags_success_without_gallery(self, monkeypatch):
        """接口通了但图库空，是另一种故障：不算成功，提示要能区分开。"""
        from services import takealot_public_api as pub
        monkeypatch.setattr(pub, "fetch_product", lambda plid: self._record(
            product_image_url=None,
        ))

        report = diag_public(plid="94399784", store_id=DEFAULT_STORE_ID)

        assert report["success"] is True
        assert report["image_ok"] is False
        assert "没返回图库" in report["hint"]


class TestPurchaseCostDerivation:
    """采购价 = 供货单价 × 采购数量。

    两个都手填必然打架：改单价忘了改总价，算出来的利润率就是错的。所以定了
    「单价有值就以它为准」。这里钉住四种情况，尤其是「单价留空时不能把已经
    手填的总价清掉」——只拿到总价的那批数据靠这条才不会被抹掉。
    """

    def test_multiplies_unit_price_by_quantity(self, db, monkeypatch):
        item = _prepare_item(db, monkeypatch)
        updated = patch_item(item.id, ItemPatch(
            unit_price_cny=12.5, purchase_quantity=8,
        ), db=db, store_id=STORE)

        assert updated["purchase_cost_cny"] == 100.0

    def test_follows_quantity_changes(self, db, monkeypatch):
        item = _prepare_item(db, monkeypatch)
        patch_item(item.id, ItemPatch(unit_price_cny=10, purchase_quantity=4),
                   db=db, store_id=STORE)
        updated = patch_item(item.id, ItemPatch(purchase_quantity=10),
                             db=db, store_id=STORE)

        assert updated["purchase_cost_cny"] == 100.0

    def test_overrides_a_manually_entered_total(self, db, monkeypatch):
        """单价一旦有值，手填的总价就作废——否则两个来源会互相矛盾。"""
        item = _prepare_item(db, monkeypatch)
        patch_item(item.id, ItemPatch(purchase_cost_cny=999), db=db, store_id=STORE)
        updated = patch_item(item.id, ItemPatch(unit_price_cny=5, purchase_quantity=4),
                             db=db, store_id=STORE)

        assert updated["purchase_cost_cny"] == 20.0

    def test_keeps_manual_total_when_unit_price_is_empty(self, db, monkeypatch):
        item = _prepare_item(db, monkeypatch)
        updated = patch_item(item.id, ItemPatch(
            purchase_cost_cny=66, purchase_quantity=4,
        ), db=db, store_id=STORE)

        assert updated["purchase_cost_cny"] == 66

    def test_clearing_unit_price_keeps_the_last_total(self, db, monkeypatch):
        """清掉单价只是回到手填模式，不该把已经算好的总价一起清掉。"""
        item = _prepare_item(db, monkeypatch)
        patch_item(item.id, ItemPatch(unit_price_cny=7, purchase_quantity=3),
                   db=db, store_id=STORE)
        updated = patch_item(item.id, ItemPatch(unit_price_cny=None),
                             db=db, store_id=STORE)

        assert updated["unit_price_cny"] is None
        assert updated["purchase_cost_cny"] == 21.0


def test_enrich_reports_access_stop_without_hiding_pending_or_erasing_cache(db, monkeypatch):
    from services import takealot_seller_api as seller
    monkeypatch.setattr(seller, "configured_key", lambda _: None)
    do_import(db, [["产品名称", "产品链接"]] + [
        [f"Item {i}", f"https://www.takealot.com/x/PLID{900+i}"] for i in range(21)
    ])
    first = db.query(SelectionItem).first()
    first.product_image_url = "https://example.invalid/old-image"
    first.actual_sale_price_zar = 269
    db.commit()
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [
        {"plid": int(p), "success": False, "http_status": 403,
         "warnings": ["公开接口访问被拒绝（HTTP 403），已停止读取"]} for p in plids
    ])
    result = enrich(EnrichRequest(limit=20), db=db, store_id=STORE)
    assert result["public_access_blocked"] is True
    assert result["remaining"] == 1
    assert result["processed"] == 20
    assert result["failed"] == 20
    db.refresh(first)
    assert first.product_image_url == "https://example.invalid/old-image"
    assert first.actual_sale_price_zar == 269
    assert first.enrich_status == "failed"
    assert "403" in first.enrich_message


@pytest.mark.parametrize('status',[401,403,429])
def test_catalogue_denial_stops_enrich_and_preserves_committed_data(db,monkeypatch,status):
    from services import takealot_catalogue_api as cat, takealot_seller_api as seller
    monkeypatch.setattr(seller,'configured_key',lambda _:None)
    monkeypatch.setattr(cat,'load_winner',lambda _:{'endpoint':'catalogue_by_id','auth':'header_x_api_key'})
    monkeypatch.setattr(cat,'configured_key',lambda _:'fixture-only')
    do_import(db,[["产品名称","产品链接"],['A','https://www.takealot.com/x/PLID123456'],
                  ['B','https://www.takealot.com/x/PLID234567'],['C','https://www.takealot.com/x/PLID345678']])
    first=db.query(SelectionItem).filter(SelectionItem.plid=='123456').one()
    first.product_image_url='https://example.invalid/saved-image'
    first.actual_sale_price_zar=269
    db.commit()
    calls=[]
    def request(url,*args):
        calls.append(url)
        return (200,{'length_cm':10,'width_cm':20,'height_cm':30,'weight_kg':1}) if len(calls)==1 else (status,None)
    monkeypatch.setattr(cat,'_request',request)
    monkeypatch.setattr(pub,'fetch_many',lambda *a,**k:pytest.fail('public fallback after catalogue denial'))
    with pytest.raises(HTTPException) as error:
        enrich(EnrichRequest(),db=db,store_id=STORE)
    assert error.value.status_code==424
    assert str(status) in error.value.detail
    assert 'fixture-only' not in error.value.detail
    assert len(calls)==2
    db.refresh(first)
    assert first.product_image_url=='https://example.invalid/saved-image'
    assert first.actual_sale_price_zar==269
    assert first.length_mm is None  # explicitly atomic: this uncommitted attempt rolled back
    assert not first.enrich_status


@pytest.mark.parametrize('ok',[True,False])
def test_catalogue_check_never_rewrites_connection_even_with_legacy_save_flag(db,monkeypatch,ok):
    from api.selection_routes import diag_catalogue
    from services import takealot_catalogue_api as cat
    monkeypatch.setattr(cat,'probe',lambda *a:{'ok':ok,'tried':[],'winner':{'endpoint':'catalogue_by_id','auth':'header_x_api_key'} if ok else None})
    monkeypatch.setattr(cat,'save_winner',lambda *a:pytest.fail('read-only check overwrote connection'))
    result=diag_catalogue(plid='123456',save=True,db=db,store_id=STORE)
    assert result['saved'] is False


def _promotion_draft(db, **fields):
    values=dict(store_id=STORE,batch_id='promotion-fixture',product_name='Fixture candidate',
                plid='1001',tsin='',already_in_products=False,purchase_quantity=1)
    values.update(fields)
    item=SelectionItem(**values)
    db.add(item);db.commit()
    return item


@pytest.mark.parametrize('identity', ['plid','tsin','legacy_url'])
def test_promote_rechecks_live_identity_instead_of_import_flag(db,identity):
    item=_promotion_draft(db,plid='1001' if identity!='tsin' else '',tsin='2002' if identity=='tsin' else '')
    old=Product(store_id=STORE,product_name='Preserve silver variant',sku='FIXTURE-SILVER',
                plid='1001' if identity=='plid' else '',tsin='2002' if identity=='tsin' else '',
                takealot_url='https://www.takealot.com/legacy/PLID1001' if identity=='legacy_url' else '',
                purchase_cost_cny=77,actual_sale_price_zar=269)
    db.add(old);db.commit()
    result=promote(IdList(ids=[item.id]),db=db,store_id=STORE)
    assert result['created']==0
    assert result['skipped']==1
    assert db.query(Product).count()==1
    db.refresh(old)
    assert (old.sku,old.purchase_cost_cny,old.actual_sale_price_zar)==('FIXTURE-SILVER',77,269)
    assert not item.promoted_product_id


def test_promote_ignores_stale_positive_flag_when_identity_no_longer_exists(db):
    item=_promotion_draft(db,already_in_products=True)
    result=promote(IdList(ids=[item.id]),db=db,store_id=STORE)
    assert result['created']==1
    assert db.query(Product).count()==1


def test_promote_checks_tsin_for_duplicates_created_in_same_batch(db):
    a=_promotion_draft(db,plid='',tsin='2002',product_name='First')
    b=_promotion_draft(db,plid='',tsin='2002',product_name='Second')
    result=promote(IdList(ids=[a.id,b.id]),db=db,store_id=STORE)
    assert (result['created'],result['skipped'])==(1,1)
    assert db.query(Product).count()==1


def test_promote_identity_is_store_scoped_and_does_not_match_title(db):
    db.add(Store(id='promotion-other-store',name='Other',status='active'))
    db.add(Product(store_id='promotion-other-store',plid='1001',tsin='2002',product_name='Same title'))
    db.add(Product(store_id=STORE,plid='3003',product_name='Same title'))
    db.commit()
    item=_promotion_draft(db,tsin='2002',product_name='Same title')
    assert promote(IdList(ids=[item.id]),db=db,store_id=STORE)['created']==1
    assert db.query(Product).filter(Product.store_id==STORE).count()==2


def test_promote_conflicting_identity_never_creates_or_relinks(db):
    db.add_all([Product(store_id=STORE,plid='1001',tsin='2002',sku='KEEP-A'),
                Product(store_id=STORE,plid='3003',tsin='4004',sku='KEEP-B')])
    db.commit()
    item=_promotion_draft(db,plid='1001',tsin='4004')
    result=promote(IdList(ids=[item.id]),db=db,store_id=STORE)
    assert result['created']==0
    assert '核对' in result['skip_details'][0]['reason']
    assert not item.promoted_product_id
    assert {p.sku for p in db.query(Product).all()}=={'KEEP-A','KEEP-B'}


def test_promote_serializes_two_connections_before_live_identity_check(tmp_path):
    from sqlalchemy import event
    engine=create_engine(f'sqlite:///{tmp_path / "promotion.sqlite"}',connect_args={'timeout':0.02})
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,autoflush=False)
    first=sessions();second=sessions()
    try:
        first.add(Store(id=STORE,name='Fixture store',status='active'))
        first.add_all([
            SelectionItem(id='promotion-first',store_id=STORE,batch_id='a',plid='1001',product_name='First'),
            SelectionItem(id='promotion-second',store_id=STORE,batch_id='b',plid='1001',product_name='Second'),
        ])
        first.commit()
        blocked=[]
        def during_first_commit(session):
            with pytest.raises(HTTPException) as error:
                promote(IdList(ids=['promotion-second']),db=second,store_id=STORE)
            blocked.append(error.value.status_code)
        event.listen(first,'before_commit',during_first_commit,once=True)
        result=promote(IdList(ids=['promotion-first']),db=first,store_id=STORE)
        assert result['created']==1
        assert blocked==[409]
        again=promote(IdList(ids=['promotion-second']),db=second,store_id=STORE)
        assert (again['created'],again['skipped'])==(0,1)
        assert second.query(Product).count()==1
        assert not second.query(SelectionItem).filter_by(id='promotion-second').one().promoted_product_id
    finally:
        first.close();second.close();engine.dispose()


def test_promote_keeps_archived_identity_and_requires_review_for_variant_conflict(db):
    old=Product(store_id=STORE,plid='1001',tsin='2002',sku='KEEP-SILVER',is_archived=True)
    db.add(old);db.commit()
    item=_promotion_draft(db,tsin='4004')
    result=promote(IdList(ids=[item.id]),db=db,store_id=STORE)
    assert result['created']==0
    assert '核对' in result['skip_details'][0]['reason']
    db.refresh(old)
    assert old.is_archived and old.tsin=='2002' and old.sku=='KEEP-SILVER'


def test_promote_refuses_disagreeing_candidate_url_and_plid(db):
    item=_promotion_draft(db,takealot_url='https://www.takealot.com/x/PLID3003')
    result=promote(IdList(ids=[item.id]),db=db,store_id=STORE)
    assert result['created']==0
    assert '核对' in result['skip_details'][0]['reason']
    assert db.query(Product).count()==0


def test_promote_refreshes_preloaded_item_after_other_connection_promoted(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "promotion-cache.sqlite"}',connect_args={'timeout':0.02})
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,autoflush=False)
    first=sessions();second=sessions()
    try:
        first.add(Store(id=STORE,name='Fixture store',status='active'))
        first.add(SelectionItem(id='promotion-title-only',store_id=STORE,batch_id='a',plid='',tsin='',product_name='Manual title'))
        first.commit()
        cached=second.query(SelectionItem).filter_by(id='promotion-title-only').one()
        assert cached.promoted_product_id is None
        assert promote(IdList(ids=[cached.id]),db=first,store_id=STORE)['created']==1
        assert promote(IdList(ids=[cached.id]),db=second,store_id=STORE)['created']==0
        assert second.query(Product).count()==1
        assert cached.promoted_product_id is not None
    finally:
        first.close();second.close();engine.dispose()


def test_catalogue_pending_rolls_back_and_stops_public_fallback(db, monkeypatch):
    from services import takealot_catalogue_api as cat, takealot_seller_api as seller
    monkeypatch.setattr(seller, 'configured_key', lambda _:None)
    monkeypatch.setattr(cat, 'load_winner', lambda _:{'endpoint':'catalogue_by_id','auth':'header_x_api_key'})
    monkeypatch.setattr(cat, 'configured_key', lambda _:'fixture')
    item = SelectionItem(id='cat-pending', store_id=STORE, plid='123', product_name='saved',
                         product_image_url='saved-image', actual_sale_price_zar=269)
    db.add(item)
    db.commit()
    def pending(*args): raise cat.CatalogueReadPending()
    monkeypatch.setattr(cat, 'fetch_dims_many', pending)
    monkeypatch.setattr(pub, 'fetch_many', lambda *a, **k:pytest.fail('public fallback after catalogue pending'))
    with pytest.raises(HTTPException) as error:
        enrich(EnrichRequest(), db=db, store_id=STORE)
    assert error.value.status_code == 504
    db.expire_all()
    saved = db.get(SelectionItem, 'cat-pending')
    assert (saved.product_name, saved.product_image_url, saved.actual_sale_price_zar) == ('saved','saved-image',269)
    assert not saved.dimensions_source and not saved.enrich_status


def test_catalogue_helper_timeout_has_no_late_object_writes(monkeypatch):
    from threading import Event, Thread
    from types import SimpleNamespace
    from api.selection_routes import _fill_catalogue_dims
    from services import takealot_catalogue_api as cat
    release, entered, exited = Event(), Event(), Event()
    monkeypatch.setattr(cat, 'CATALOGUE_BATCH_WAIT_SECONDS', 0.02)
    monkeypatch.setattr(cat, '_active_catalogue_read', None)
    monkeypatch.setattr(cat, 'load_winner', lambda _:{'endpoint':'catalogue_by_id','auth':'header_x_api_key'})
    monkeypatch.setattr(cat, 'configured_key', lambda _:'fixture')
    item = SimpleNamespace(plid='123', dimensions_source=None, length_mm=None,
                           width_mm=None, height_mm=None, actual_weight_kg=None)
    errors = []
    def fetch(*a, **k):
        entered.set()
        assert release.wait(2)
        return {'length_mm':100,'width_mm':20,'height_mm':10,'weight_kg':0.3}
    monkeypatch.setattr(cat, 'fetch_dims', fetch)
    def run():
        try: _fill_catalogue_dims(object(), [item])
        except BaseException as error: errors.append(error)
        finally: exited.set()
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(1)
        assert exited.wait(0.3), 'catalogue helper did not return on budget'
        assert len(errors) == 1 and isinstance(errors[0], cat.CatalogueReadPending)
        item.dimensions_source = 'manual'
        item.length_mm = 999
    finally:
        release.set()
        thread.join(1)
        active = cat._active_catalogue_read
        if active: active['future'].result(timeout=1)
    assert _fill_catalogue_dims(object(), [item]) == 0
    assert item.dimensions_source == 'manual' and item.length_mm == 999
    assert item.actual_weight_kg is None


@pytest.mark.parametrize("fulfillment", [42.0, 0.0])
def test_promote_preserves_calculated_selection_costs(db, monkeypatch, fulfillment):
    item = _prepare_item(db, monkeypatch)
    patch_item(item.id, ItemPatch(length_mm=100, width_mm=100, height_mm=100,
        actual_weight_kg=0.2, actual_sale_price_zar=499, unit_price_cny=10,
        purchase_quantity=2, purchase_shipping_cny=0, shipping_method="海运普货",
        fee_category="Homeware"), db=db, store_id=STORE)
    item.fulfillment_fee_zar = fulfillment
    from api.selection_routes import _calc, _fee_rate, _exchange_rate
    _calc(item, _exchange_rate(db), _fee_rate(db, item.fee_category))
    db.commit()
    before = {key: getattr(item, key) for key in (
        "fulfillment_fee_zar", "total_cost_zar", "profit_zar", "profit_margin",
        "cost_to_sa_warehouse_zar", "success_fee_zar")}
    result = promote(IdList(ids=[item.id]), db=db, store_id=STORE)
    product = db.get(Product, result["products"][0]["product_id"])
    assert {key: getattr(product, key) for key in before} == before
    assert [product.inbound_listing_fee_cny, product.outbound_operation_fee_cny,
            product.last_mile_delivery_fee_cny, product.other_fee_cny] == [0.75, 0.7, 2, 2]
    # Recalculation after ORM defaults materialize must not change the saved quote.
    from api.product_routes import _apply_calculated_fields
    _apply_calculated_fields(db, product)
    assert {key: getattr(product, key) for key in before} == before


def test_import_supplier_columns_without_retyping_or_price_collision(db):
    do_import(db, [["货源链接", "产品链接", "1688价格", "售价", "起订量", "国内运费"],
        ["https://detail.1688.com/offer/123.html", "https://www.takealot.com/p/PLID991234", 20, 499, "50 pcs", 0]])
    item = db.query(SelectionItem).one()
    assert item.plid == "991234"
    assert item.purchase_url == "https://detail.1688.com/offer/123.html"
    assert item.actual_sale_price_zar == 499
    assert item.unit_price_cny == 20
    assert item.purchase_quantity == 50
    assert item.purchase_cost_cny == 1000
    assert item.purchase_shipping_cny == 0
    assert item.profit_margin is None  # Missing dimensions/logistics never guessed.


@pytest.mark.parametrize("header,value", [("供货价", -20), ("国内运费", -1), ("起订量", 0), ("起订量", 1.5), ("起订量", "bad")])
def test_import_rejects_invalid_supplier_values_without_partial_batch(db, header, value):
    with pytest.raises(HTTPException) as error:
        do_import(db, [["产品链接", header], ["https://www.takealot.com/p/PLID991234", value]])
    assert error.value.status_code == 400
    assert db.query(SelectionItem).count() == 0


def test_import_duplicate_supplier_rows_preserve_zero(db):
    do_import(db, [["产品链接", "供货价", "起订量", "国内运费"],
        ["https://www.takealot.com/p/PLID991234", 0, 4, 0],
        ["https://www.takealot.com/p/PLID991234", 20, 8, 18]])
    item = db.query(SelectionItem).one()
    assert (item.unit_price_cny, item.purchase_quantity, item.purchase_shipping_cny, item.purchase_cost_cny) == (0, 4, 0, 0)


@pytest.mark.parametrize("currency", ["USD", "JPY", "HKD", "SGD", "TWD", "$"])
def test_import_does_not_treat_usd_supplier_price_as_cny(db, currency):
    with pytest.raises(HTTPException) as error:
        do_import(db, [["产品链接", f"Unit Price ({currency})"], ["https://www.takealot.com/p/PLID991234", 20]])
    assert error.value.status_code == 400
    assert db.query(SelectionItem).count() == 0


@pytest.mark.parametrize("headers,values,expected", [
    (["长度(cm)", "宽度(cm)", "高度(cm)", "重量(g)"], [25, 10, 5, 350], (250, 100, 50, 0.35)),
    (["Length (mm)", "Width (mm)", "Height (mm)", "Weight (kg)"], [250, 100, 50, 0.35], (250, 100, 50, 0.35)),
    (["length_mm", "width_mm", "height_mm", "actual_weight_kg"], [250, 100, 50, 0.35], (250, 100, 50, 0.35)),
    (["长度(cm)", "宽度(cm)", "高度(cm)", "重量(g)"], ["250mm", "10 cm", "5厘米", "0.35kg"], (250, 100, 50, 0.35)),
])
def test_import_measurements_without_manual_reentry(db, headers, values, expected):
    do_import(db, [["产品链接"] + headers, ["https://www.takealot.com/p/PLID991234"] + values])
    item = db.query(SelectionItem).one()
    assert (item.length_mm, item.width_mm, item.height_mm, item.actual_weight_kg) == pytest.approx(expected)


@pytest.mark.parametrize("value", [-5, 0, "bad", "25-30", "20kg", "NaN", "1e309", "1,000"])
def test_import_invalid_measurements_are_not_silently_saved(db, value):
    with pytest.raises(HTTPException) as error:
        do_import(db, [["产品链接", "长度(cm)"],
            ["https://www.takealot.com/p/PLID991234", 25],
            ["https://www.takealot.com/p/PLID991235", value]])
    assert error.value.status_code == 400
    assert db.query(SelectionItem).count() == 0


def test_import_unitless_measurements_remain_unknown(db):
    result = do_import(db, [["产品链接", "长度", "重量"], ["https://www.takealot.com/p/PLID991234", 25, 350]])
    item = db.query(SelectionItem).one()
    assert item.length_mm is None and item.actual_weight_kg is None
    assert set(result["unknown_headers"]) == {"长度", "重量"}


def test_import_missing_measurements_filled_from_duplicate_row(db):
    do_import(db, [["产品链接", "长度(cm)", "重量(g)"],
        ["https://www.takealot.com/p/PLID991234", "", 350],
        ["https://www.takealot.com/p/PLID991234", 25, ""]])
    item = db.query(SelectionItem).one()
    assert item.length_mm == 250 and item.actual_weight_kg == pytest.approx(0.35)


def test_csv_measurement_units_and_empty_values():
    from services.selection_import import read_table, normalize_row
    table = read_table("fixture.csv", "产品链接;长度(厘米);宽度(毫米);重量(千克)\nhttps://www.takealot.com/p/PLID991234;25;100;0,35\nhttps://www.takealot.com/p/PLID991235;;;\n".encode())
    first = normalize_row(table["rows"][0])
    assert (first["length_mm"], first["width_mm"], first["actual_weight_kg"]) == pytest.approx((250, 100, 0.35))
    assert normalize_row(table["rows"][1])["actual_weight_kg"] is None


def test_import_ambiguous_weight_separator_rejected(db):
    with pytest.raises(HTTPException) as error:
        do_import(db, [["产品链接", "重量(g)"], ["https://www.takealot.com/p/PLID991234", "1,000"]])
    assert error.value.status_code == 400
    assert db.query(SelectionItem).count() == 0


def test_enrich_preserves_imported_measurements(db):
    from api.selection_routes import _apply_public
    do_import(db, [["产品链接", "长度(cm)", "宽度(cm)", "高度(cm)", "重量(g)"],
        ["https://www.takealot.com/p/PLID991234", 25, 10, 5, 350]])
    item = db.query(SelectionItem).one()
    _apply_public(item, {"success": True, "dimensions": {"length_mm": 1, "width_mm": 2, "height_mm": 3, "weight_kg": 0.001}}, db)
    assert (item.length_mm, item.width_mm, item.height_mm, item.actual_weight_kg) == pytest.approx((250, 100, 50, 0.35))
    assert item.dimensions_source == "import"


def test_enrich_keeps_429_rows_pending_for_next_round(db, monkeypatch):
    """429 是限流、不是拒绝：留在待抓队列里，下一轮自动重试。

    标成 failed 的话 `remaining` 不再统计它（只数 enrich_status 为空的行），
    用户点多少次「重抓」都补不上——这就是「反复抓仍有一片没主图」的直接原因。
    401/403 才该标 failed，因为那是真的被拒，重试没用。
    """
    from services import takealot_seller_api as seller
    monkeypatch.setattr(seller, "configured_key", lambda _: None)
    do_import(db, [["产品名称", "产品链接"],
                   ["A", "https://www.takealot.com/x/PLID901"],
                   ["B", "https://www.takealot.com/x/PLID902"]])
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [
        {"plid": int(p), "success": False, "http_status": 429,
         "warnings": ["公开接口请求受限流（HTTP 429），已停止读取"]} for p in plids
    ])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["failed"] == 0
    assert result["remaining"] == 2
    for item in db.query(SelectionItem).all():
        assert item.enrich_status in ("", None)
        assert "限流" in (item.enrich_message or "")


def test_enrich_keeps_stop_affected_rows_pending(db, monkeypatch):
    """被上一批的 stop 波及、自己没拿到状态码的行只是「没轮到」，不标失败。

    它们可能本来可读，只是同批里有别的 PLID 被拒。标 failed 等于替上游
    永久判死，下一轮不会再试。
    """
    from services import takealot_seller_api as seller
    monkeypatch.setattr(seller, "configured_key", lambda _: None)
    do_import(db, [["产品名称", "产品链接"],
                   ["A", "https://www.takealot.com/x/PLID911"],
                   ["B", "https://www.takealot.com/x/PLID912"]])
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [
        {"plid": int(p), "success": False, "public_access_blocked": True,
         "warnings": ["公开接口访问受限，已停止本批后续读取"]} for p in plids
    ])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["failed"] == 0
    assert result["remaining"] == 2
    for item in db.query(SelectionItem).all():
        assert item.enrich_status in ("", None)


def test_catalogue_discover_saves_winning_connection(db, monkeypatch):
    """用户主动点按钮（discover=true）才允许发现并保存连接。

    这是 `takealot_catalogue_auth` **唯一的写入路径**。之前这条路径被删掉，
    `load_winner` 永远是 None，`_fill_catalogue_dims` 直接 return 0——重尺
    功能整体不可用，界面上一片「未抓取」。
    """
    from api.selection_routes import diag_catalogue
    from services import takealot_catalogue_api as cat

    saved = {}
    monkeypatch.setattr(cat, 'probe', lambda *a: {
        'ok': True, 'tried': [],
        'winner': {'endpoint': 'catalogue_by_plid', 'auth': 'header_x_api_key'},
    })
    monkeypatch.setattr(cat, 'save_winner', lambda _db, winner: saved.update(winner))

    result = diag_catalogue(plid='123456', discover=True, db=db, store_id=STORE)

    assert result['saved'] is True
    assert saved == {'endpoint': 'catalogue_by_plid', 'auth': 'header_x_api_key'}


def test_catalogue_discover_failure_does_not_save(db, monkeypatch):
    """没探通就不能写——否则会把一个用不了的连接存下来，之后每次都失败。"""
    from api.selection_routes import diag_catalogue
    from services import takealot_catalogue_api as cat

    monkeypatch.setattr(cat, 'probe', lambda *a: {
        'ok': False, 'tried': [], 'winner': None, 'reason': '所有组合都没拿到完整的长宽高',
    })
    monkeypatch.setattr(cat, 'save_winner', lambda *a: pytest.fail('没探通不该写连接'))

    result = diag_catalogue(plid='123456', discover=True, db=db, store_id=STORE)

    assert result['saved'] is False


# ---------------------------------------------------------------- 卖家门户通道
#
# 门户目录接口是重尺的唯一来源，也是主图绕过公开接口 429 的通道。这几条测的是
# 「有令牌就走门户、没令牌退回公开接口、门户没重尺不要无限重试」。

PORTAL_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sig"


def _fake_portal(plid, dims=None, image=True):
    record = {
        "plid": str(plid),
        "success": True,
        "product_name": f"Portal {plid}",
        "product_image_url": f"https://media.takealot.com/{plid}-zoom.jpg" if image else None,
        "image_urls": [f"https://media.takealot.com/{plid}-zoom.jpg"] if image else [],
        "takealot_category_path": "Home & Kitchen > Kitchen Storage",
        "actual_sale_price_zar": 480.0,
        "tsin": None,
        "brand": None,
        "dimensions": dims if dims is not None else {
            "length_mm": 300.0, "width_mm": 200.0, "height_mm": 100.0,
            "weight_kg": 0.45, "source": "consolidated",
        },
        "warnings": [],
        "source": "seller_portal",
        "http_status": 200,
    }
    return record


MASTER_KEY = "test-master-key-for-portal-token-encryption"


def _configure_portal(db, monkeypatch, records_by_plid):
    from services import takealot_portal_api as portal

    # 令牌是加密存的，派生密钥来自服务端访问密钥；测试里给一个够长的。
    monkeypatch.setenv("API_KEY", MASTER_KEY)
    portal.save_token(db, PORTAL_TOKEN)
    monkeypatch.setattr(
        portal, "fetch_many",
        lambda plids, token, workers=4, budget_seconds=45.0:
            [records_by_plid.get(str(p)) or _fake_portal(p) for p in plids],
    )


def test_enrich_fills_image_and_dimensions_from_portal(db, monkeypatch):
    """门户一个请求就把主图和重尺都给了，公开接口不该再被调用。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {})
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: pytest.fail("门户已补齐，不该再打公开接口"))

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["ok"] == 1
    item = db.query(SelectionItem).first()
    assert item.product_image_url.endswith("700-zoom.jpg")
    assert item.length_mm == 300.0
    assert item.height_mm == 100.0
    assert item.actual_weight_kg == 0.45
    assert item.dimensions_source == "portal"
    assert item.fee_category == "Homeware"


def test_enrich_marks_portal_empty_when_takealot_has_no_dimensions(db, monkeypatch):
    """门户通了但没有重尺：记一笔，否则每轮重抓都白跑一趟。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {"700": _fake_portal("700", dims={})})
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    enrich(EnrichRequest(), db=db, store_id=STORE)

    item = db.query(SelectionItem).first()
    assert item.dimensions_source == "portal_empty"
    assert item.length_mm is None


def test_enrich_marks_not_found_plid_so_it_does_not_loop(db, monkeypatch):
    """目录里没有这个 PLID：重尺永远补不上，别让它一直卡在待抓队列。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {"700": {
        "plid": "700", "success": False, "not_found": True,
        "warnings": ["Takealot 目录里没有这个商品"], "http_status": 404,
        "source": "seller_portal",
    }})
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    enrich(EnrichRequest(), db=db, store_id=STORE)

    assert db.query(SelectionItem).first().dimensions_source == "portal_empty"


def test_enrich_falls_back_to_public_without_a_portal_token(db, monkeypatch):
    """没配令牌时行为不变，公开接口照旧兜底。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [_fake_public(p) for p in plids])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["ok"] == 1
    item = db.query(SelectionItem).first()
    assert item.product_image_url.endswith("700.jpg")
    assert item.dimensions_source == ""


def test_portal_token_state_never_returns_the_token(db, monkeypatch):
    """令牌状态只给「配没配、什么时候过期」，不把令牌本身回给前端。"""
    from api.selection_routes import get_portal_token_state
    from services import takealot_portal_api as portal

    monkeypatch.setenv("API_KEY", MASTER_KEY)
    portal.save_token(db, PORTAL_TOKEN)
    state = get_portal_token_state(db=db, store_id=STORE)

    assert state["configured"] is True
    assert state["expires_at"] == 9999999999
    assert PORTAL_TOKEN not in str(state)


def test_portal_token_rejects_something_that_is_not_a_jwt(db):
    from fastapi import HTTPException

    from api.selection_routes import PortalTokenIn, set_portal_token

    with pytest.raises(HTTPException) as error:
        set_portal_token(PortalTokenIn(token="not-a-jwt"), db=db, store_id=STORE)
    assert error.value.status_code == 400


def test_diag_portal_reports_dimensions(db, monkeypatch):
    from api.selection_routes import diag_portal
    from services import takealot_portal_api as portal

    monkeypatch.setenv("API_KEY", MASTER_KEY)
    portal.save_token(db, PORTAL_TOKEN)
    monkeypatch.setattr(portal, "fetch", lambda plid, token, timeout=25.0: _fake_portal(plid))

    report = diag_portal(plid="700", db=db, store_id=STORE)

    assert report["ok"] is True
    assert report["image_count"] == 1
    assert report["dimensions"]["length_mm"] == 300.0


def test_enrich_defers_rows_when_portal_quota_is_exhausted(db, monkeypatch):
    """门户配额用尽时，那批必须留到下一轮，不能记成失败。

    上一版把 429 塞进「整批停下」，1804 个商品在第 201 个开始全部放弃，
    重尺一个都没补上。这里锁住「推迟而不是失败」。
    """
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"],
                   ["Other", "https://www.takealot.com/t/PLID701"]])
    _configure_portal(db, monkeypatch, {
        "700": {"plid": "700", "success": False, "budget_deferred": True,
                "portal_rate_limited": True, "warnings": ["门户接口限流，留到下一轮"],
                "source": "seller_portal"},
        "701": {"plid": "701", "success": False, "budget_deferred": True,
                "portal_rate_limited": True, "warnings": ["门户接口限流，留到下一轮"],
                "source": "seller_portal"},
    })
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["failed"] == 0
    assert result["portal_limited"] is True
    assert result["pending"] == 2
    assert result["remaining"] == 2
    # 留待下一轮的行不能被打成 failed，否则永远退出待抓队列
    assert all(row.enrich_status in ("", None) for row in db.query(SelectionItem).all())


def test_enrich_reports_portal_configured_flag(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {})

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["portal_configured"] is True
    assert result["portal_limited"] is False


def test_enrich_without_portal_token_reports_it_as_unconfigured(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [_fake_public(p) for p in plids])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["portal_configured"] is False


def test_enrich_tells_the_client_how_long_to_wait_for_quota(db, monkeypatch):
    """配额见底时把「还要等多久」回给前端。

    没有这个值，前端会立刻再发一轮、再被整批推迟，几十秒里空转几十次；
    有了它就能实等一个窗口，把 1804 个商品按 200 次/分钟稳稳推完。
    """
    from services import takealot_portal_api as portal

    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {
        "700": {"plid": "700", "success": False, "budget_deferred": True,
                "portal_rate_limited": True, "warnings": ["配额用完"],
                "source": "seller_portal"},
    })
    monkeypatch.setattr(portal.QUOTA, "wait_seconds", lambda: 42.0)
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["portal_limited"] is True
    assert result["portal_retry_after"] == 42.0


def test_enrich_reports_zero_wait_when_quota_is_fine(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _configure_portal(db, monkeypatch, {})

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["portal_retry_after"] == 0.0


# ---------------------------------------------------- 被限流误标成失败的历史行
#
# 线上 1112 行是旧版本留下的 `failed / 公开接口返回 429`。它们既不在目标查询
# 里、也不在 `remaining` 里，于是「点抓取什么也不发生」——用户看到的就是
# 「429 一直没解决、重尺也补不上」。这几条锁住「限流不是结论」。


def _mark_failed(db, message):
    item = db.query(SelectionItem).first()
    item.enrich_status = "failed"
    item.enrich_message = message
    db.commit()
    return item


def test_rate_limited_rows_are_retried_on_the_next_round(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_failed(db, "公开接口返回 429")
    _configure_portal(db, monkeypatch, {})

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 1
    assert result["ok"] == 1
    assert db.query(SelectionItem).first().product_image_url.endswith("700-zoom.jpg")


def test_rate_limited_rows_count_as_remaining_so_the_loop_keeps_going(db, monkeypatch):
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_failed(db, "公开接口返回 429")
    # 门户没配：这一轮抓不到，但行必须还留在队列里，不能就这么算了
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: [dict(plid=p, success=False,
                                                       http_status=429) for p in plids])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["failed"] == 0
    assert result["pending"] == 1
    assert result["remaining"] == 1


def test_genuine_failures_stay_failed(db, monkeypatch):
    """真的没有这个商品，就别每轮再打一次。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_failed(db, "公开接口没有返回可用数据")
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 0
    assert result["remaining"] == 0
    assert db.query(SelectionItem).first().enrich_status == "failed"


def _mark_ok_without_dimensions(db):
    """把行标成「图和名字都有了，但没有重尺」——这正是线上 692 行的状态。"""
    item = db.query(SelectionItem).first()
    item.product_image_url = "https://media.takealot.com/existing.jpg"
    item.actual_sale_price_zar = 299.0
    item.fee_category = "Homeware"
    item.enrich_status = "ok"
    item.enrich_message = ""
    item.dimensions_source = ""
    db.commit()
    return item


def test_rows_missing_dimensions_are_revisited_when_the_portal_is_available(db, monkeypatch):
    """主图有了、重尺还没有的行，配了门户就该再跑一轮把它补上。

    线上 692 行是这种状态：图早就有了所以标成 ok，但重尺一个都没有。只看
    `enrich_status` 的话它们永远不会再被选中，重尺也就永远补不上。
    """
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_ok_without_dimensions(db)
    _configure_portal(db, monkeypatch, {})

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 1
    assert result["with_dimensions"] == 1
    item = db.query(SelectionItem).first()
    assert item.length_mm == 300.0
    assert item.dimensions_source == "portal"
    # 补完就收敛，不会每轮再来一次
    assert result["remaining"] == 0


def test_rows_missing_dimensions_are_left_alone_without_a_portal(db, monkeypatch):
    """没有门户通道时不要动它们：公开接口本来就没有重尺，只会白打一轮网络。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_ok_without_dimensions(db)
    monkeypatch.setattr(pub, "fetch_many",
                        lambda plids, workers=8: pytest.fail("没有门户时不该为补重尺重抓"))

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 0
    assert result["remaining"] == 0
    assert db.query(SelectionItem).first().dimensions_source == ""


def test_expired_portal_token_does_not_trigger_dimension_backfill(db, monkeypatch):
    """令牌过期时同样别动：那一轮只会拿到一片 401，然后永远收敛不了。"""
    from services import takealot_portal_api as portal

    do_import(db, [["产品名称", "产品链接"],
                   ["Tool", "https://www.takealot.com/t/PLID700"]])
    _mark_ok_without_dimensions(db)
    monkeypatch.setenv("API_KEY", MASTER_KEY)
    portal.save_token(db, "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.sig")
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: [])

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 0
    assert result["remaining"] == 0


# ---------------------------------------------------------------- 中文品名 / 普货带电
#
# 品名要印标签、进报关单，运输方式决定运费和会不会被货代退运。这两块都不是
# 「算错了再改」的事，所以各自钉住几条。

def test_import_generates_short_chinese_name(db):
    """英文标题进来就带一个五字以内的中文品名，省得 1800 条一条条手打。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Stainless Steel Kitchen Knife Set - Black",
         "https://www.takealot.com/k/PLID800"],
    ])

    item = db.query(SelectionItem).first()
    assert item.chinese_product_name == "厨房刀"
    assert len(item.chinese_product_name) <= 5


def test_import_keeps_chinese_name_from_the_sheet(db):
    """表里自带中文品名时以表为准，不能被自动生成覆盖。"""
    do_import(db, [
        ["产品名称", "中文品名", "产品链接"],
        ["Yoga Mat - Black", "瑜伽垫（加厚）", "https://www.takealot.com/y/PLID801"],
    ])

    assert db.query(SelectionItem).first().chinese_product_name == "瑜伽垫（加厚）"


def test_import_leaves_name_empty_when_nothing_matches(db):
    """一个词都对不上就留空，让人工填——填个错的比空着更麻烦。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Qwertyuiop Zxcvbn", "https://www.takealot.com/x/PLID802"],
    ])

    assert db.query(SelectionItem).first().chinese_product_name == ""


def test_import_defaults_shipping_to_sea(db):
    """默认海运：货代走海运为主，空运只在赶时效时用。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Yoga Mat - Black", "https://www.takealot.com/y/PLID803"],
    ])

    item = db.query(SelectionItem).first()
    assert item.shipping_method == "海运普货"
    assert item.battery_status == ""


def test_import_flags_battery_and_switches_to_battery_shipping(db):
    """确定带电的直接切「海运带电」，不用等人一条条改。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["USB Rechargeable LED Torch", "https://www.takealot.com/t/PLID804"],
    ])

    item = db.query(SelectionItem).first()
    assert item.battery_status == "yes"
    assert item.battery_reason
    assert item.shipping_method == "海运带电"


def test_import_keeps_general_cargo_for_accessories(db):
    """充电器、遥控器收纳架本身不带电池，不能因为标题里有 battery 就报带电。"""
    do_import(db, [
        ["产品名称", "产品链接"],
        ["Battery Charger for AA AAA", "https://www.takealot.com/c/PLID805"],
    ])

    item = db.query(SelectionItem).first()
    assert item.battery_status == ""
    assert item.shipping_method == "海运普货"


def test_enrich_backfills_chinese_names_without_network(db, monkeypatch):
    """品名是纯本地词表匹配，不发请求；即使这一轮没东西可抓也要补上。"""
    item = SelectionItem(
        store_id=STORE, product_name="Stainless Steel Kitchen Knife Set",
        chinese_product_name="", enrich_status="ok", dimensions_source="manual",
    )
    db.add(item)
    db.commit()
    monkeypatch.setattr(pub, "fetch_many", lambda plids, workers=8: pytest.fail("不该打网络"))

    result = enrich(EnrichRequest(), db=db, store_id=STORE)

    assert result["processed"] == 0
    assert result["names_filled"] == 1
    assert db.query(SelectionItem).first().chinese_product_name == "厨房刀"


def test_portal_description_upgrades_battery_guess(db, monkeypatch):
    """标题只写 LED 灯 → 待确认；描述写了内置电池 → 带电，运输方式跟着切。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["LED Desk Lamp", "https://www.takealot.com/l/PLID806"]])
    record = _fake_portal(806)
    record["product_name"] = "LED Desk Lamp"
    record["description"] = "Built-in rechargeable battery, 8 hours runtime."
    _configure_portal(db, monkeypatch, {"806": record})

    enrich(EnrichRequest(), db=db, store_id=STORE)

    item = db.query(SelectionItem).first()
    assert item.battery_status == "yes"
    assert item.shipping_method == "海运带电"


def test_portal_does_not_override_manual_shipping_choice(db, monkeypatch):
    """用户已经手改成空运的，重判带电也不许把运输方式冲掉。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["LED Desk Lamp", "https://www.takealot.com/l/PLID807"]])
    item = db.query(SelectionItem).first()
    item.shipping_method = "空运普货"
    db.commit()

    record = _fake_portal(807)
    record["product_name"] = "LED Desk Lamp"
    record["description"] = "Built-in rechargeable battery."
    _configure_portal(db, monkeypatch, {"807": record})

    enrich(EnrichRequest(), db=db, store_id=STORE)

    db.refresh(item)
    assert item.battery_status == "yes"
    assert item.shipping_method == "空运普货"


def test_patch_can_confirm_battery_status(db):
    """界面上点一下就能人工确认普货/带电。"""
    do_import(db, [["产品名称", "产品链接"],
                   ["LED Outdoor 30W Flood Light", "https://www.takealot.com/f/PLID808"]])
    item = db.query(SelectionItem).first()
    assert item.battery_status == "maybe"

    updated = patch_item(item.id, ItemPatch(battery_status="no"), db=db, store_id=STORE)

    assert updated["battery_status"] == "no"
