"""一键上架（POST /api/products/{id}/list-offer）的口径守卫。

运营口径是「售价照产品现有售价，RRP = 售价 × 1.5，不预售，SKU 由平台生成」。
这几件事都在后端决定，所以在这里把默认倍数、覆盖能力、拒绝路径钉住；
前端列表里的单条/批量按钮只是把同一份口径传下来。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from database import Base
from models import Product, Store  # noqa: F401  建表前先把 models 注册进来
from api.product_routes import ListOfferIn, RRP_MULTIPLIER, list_offer


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def variant(barcode="6009512345678", label="Black", has_offer=False):
    return {"barcode": barcode, "tsin": "104036518", "label": label, "has_offer": has_offer}


@pytest.fixture()
def env(monkeypatch):
    """把门户目录和卖家 API 都换成桩：这个用例集合只验本地口径，不联外网。"""
    from services import takealot_portal_api as portal
    from services import takealot_seller_api as seller

    calls = {"create": [], "fetch": []}
    offered = []
    state = {"variants": [variant()], "token_expired": False, "create": None}

    monkeypatch.setattr(portal, "configured_token", lambda db: "portal-token")
    monkeypatch.setattr(portal, "token_state", lambda db: {"expired": state["token_expired"]})

    def fake_fetch(plid, token):
        calls["fetch"].append((plid, token))
        return {"success": True, "catalogue_variants": state["variants"]}

    monkeypatch.setattr(portal, "fetch", fake_fetch)
    monkeypatch.setattr(seller, "configured_key", lambda db: "seller-key")

    def fake_create(key, identifier, selling_price, rrp=None, **kwargs):
        calls["create"].append({"barcode": identifier, "selling_price": selling_price, "rrp": rrp, **kwargs})
        plan = state["create"]
        if callable(plan):
            return plan(identifier)
        if plan is not None:
            return plan
        offered.append(identifier)
        # 平台按变体各发一串 SKU：后缀用序号，方便断言「多变体 SKU 是被拼起来的」。
        return {"ok": True, "payload": {"offer": {"sku": f"99027394458{len(offered):02d}",
                                                  "offer_id": f"offer-{len(offered)}", "status": "Not Buyable"}}}

    monkeypatch.setattr(seller, "create_offer", fake_create)
    return {"calls": calls, "state": state}


def product(db, **overrides):
    fields = dict(
        id="p1",
        store_id="default-store",
        product_no=617,
        takealot_url="https://www.takealot.com/scientific-calculator/PLID96357594",
        actual_sale_price_zar=200,
    )
    fields.update(overrides)
    row = Product(**fields)
    db.add(row)
    db.commit()
    return row


def test_rrp_defaults_to_one_and_a_half_times_price(db, env):
    """不传倍数时的默认值就是 1.5：这是这次改动本身，不能悄悄回落到 1.2。"""
    assert RRP_MULTIPLIER == 1.5
    product(db)
    plan = list_offer("p1", ListOfferIn(dry_run=True), db, "default-store")
    assert plan["plan"]["selling_price"] == 200
    assert plan["plan"]["rrp"] == 300
    assert plan["plan"]["leadtime_days"] is None, "不传交期就是不预售"
    assert plan["plan"]["sku"] is None, "SKU 交给平台生成，不预填"
    assert env["calls"]["create"] == [], "dry_run 不能真的建 offer"


def test_batch_style_call_can_override_the_multiplier(db, env):
    """批量与单条走同一个接口，显式传倍数时以传进来的为准（便于 A/B 试探）。"""
    product(db)
    plan = list_offer("p1", ListOfferIn(dry_run=True, rrp_multiplier=2), db, "default-store")
    assert plan["plan"]["rrp"] == 400


def test_creates_offer_with_rrp_and_writes_back_platform_sku(db, env):
    row = product(db)
    result = list_offer("p1", ListOfferIn(), db, "default-store")

    submitted = env["calls"]["create"][0]
    assert submitted["selling_price"] == 200 and submitted["rrp"] == 300
    assert submitted["barcode"] == "6009512345678", "建 offer 用变体 Barcode，不是 PLID"
    assert "sku" not in submitted and "leadtime_days" not in submitted

    assert result["status"] == "Not Buyable"
    assert result["sku"] == row.sku, "平台分配的 SKU 要回写到产品主档"
    assert result["sku"].startswith("99027394458")


def test_existing_self_built_sku_is_not_overwritten(db, env):
    row = product(db, sku="W-S-12-02")
    list_offer("p1", ListOfferIn(), db, "default-store")
    assert row.sku == "W-S-12-02"


def test_already_listed_variant_is_skipped_not_failed(db, env):
    """单变体且本店已有报价：跳过即可，重复创建只会被平台打回。"""
    product(db)
    env["state"]["variants"] = [variant(has_offer=True)]
    result = list_offer("p1", ListOfferIn(), db, "default-store")
    assert result["ok"] is True and result["listed_count"] == 0
    assert result["skipped"][0]["barcode"] == "6009512345678"
    assert env["calls"]["create"] == []


def test_missing_price_is_rejected(db, env):
    product(db, actual_sale_price_zar=None)
    with pytest.raises(HTTPException) as exc:
        list_offer("p1", ListOfferIn(), db, "default-store")
    assert exc.value.status_code == 409
    assert "售价" in exc.value.detail


def test_expired_portal_token_is_reported(db, env):
    product(db)
    env["state"]["token_expired"] = True
    with pytest.raises(HTTPException) as exc:
        list_offer("p1", ListOfferIn(), db, "default-store")
    assert exc.value.status_code == 400
    assert "令牌" in exc.value.detail


def test_platform_rejection_carries_host_and_status(db, env):
    product(db)
    env["state"]["create"] = {"ok": False, "status": 422, "host": "seller-api.takealot.takealot.com", "reason": "Invalid RRP"}
    with pytest.raises(HTTPException) as exc:
        list_offer("p1", ListOfferIn(), db, "default-store")
    assert exc.value.status_code == 502
    assert "Invalid RRP" in exc.value.detail and "422" in exc.value.detail


def test_all_variants_are_listed_in_one_batch_without_barcode(db, env):
    """不传 Barcode 就是「所有变体都自动上」：已有报价的跳过，其余逐个建 offer。"""
    row = product(db)
    env["state"]["variants"] = [
        variant(barcode="6009512345677", label="Gold", has_offer=True),
        variant(barcode="6009512345678", label="Silver"),
        variant(barcode="6009512345679", label="Black"),
    ]
    result = list_offer("p1", ListOfferIn(), db, "default-store")

    assert [c["barcode"] for c in env["calls"]["create"]] == ["6009512345678", "6009512345679"]
    assert result["listed_count"] == 2 and result["failed_count"] == 0
    assert [s["barcode"] for s in result["skipped"]] == ["6009512345677"]
    assert result["unlistable"] == []
    db.refresh(row)
    assert row.sku == "9902739445801 9902739445802", "多变体 SKU 按空格拼回产品主档"


def test_variant_without_barcode_is_reported_not_blocking(db, env):
    """缺 Barcode 的变体单独列进 unlistable，其它变体照上。"""
    row = product(db)
    env["state"]["variants"] = [
        variant(barcode="6009512345678", label="Silver"),
        {"barcode": None, "tsin": "104036599", "label": "No-GTIN", "has_offer": False},
    ]
    result = list_offer("p1", ListOfferIn(), db, "default-store")

    assert [c["barcode"] for c in env["calls"]["create"]] == ["6009512345678"]
    assert result["listed_count"] == 1
    assert result["unlistable"][0]["variant_label"] == "No-GTIN"
    db.refresh(row)
    assert row.sku == "9902739445801"


def test_duplicate_barcode_lists_only_once(db, env):
    """同一 Barcode 出现两次时只建一个 offer，重复的那条进 unlistable 说清楚。"""
    product(db)
    env["state"]["variants"] = [variant(label="A"), variant(label="B")]
    result = list_offer("p1", ListOfferIn(), db, "default-store")

    assert [c["barcode"] for c in env["calls"]["create"]] == ["6009512345678"]
    assert result["listed_count"] == 1
    assert "重复" in result["unlistable"][0]["reason"]


def test_multi_variant_dry_run_lists_every_plan(db, env):
    product(db)
    env["state"]["variants"] = [variant(), variant(barcode="6009512345679", label="Silver")]
    plan = list_offer("p1", ListOfferIn(dry_run=True), db, "default-store")
    assert [p["barcode"] for p in plan["plans"]] == ["6009512345678", "6009512345679"]
    assert plan["listed_count"] == 2
    assert env["calls"]["create"] == [], "dry_run 不能真的建 offer"


def test_all_variants_already_listed_is_not_an_error(db, env):
    product(db)
    env["state"]["variants"] = [variant(has_offer=True), variant(barcode="6009512345679", label="Silver", has_offer=True)]
    result = list_offer("p1", ListOfferIn(), db, "default-store")
    assert result["ok"] is True and result["listed_count"] == 0 and result["failed_count"] == 0
    assert len(result["skipped"]) == 2
    assert env["calls"]["create"] == []


def test_explicit_barcode_selects_only_the_confirmed_variant(db, env):
    product(db)
    env["state"]["variants"] = [variant(), variant(barcode="6009512345679", label="Silver")]
    result = list_offer("p1", ListOfferIn(barcode="6009512345679"), db, "default-store")
    assert result["barcode"] == env["calls"]["create"][0]["barcode"] == "6009512345679"


def test_partial_catalogue_warns_and_still_lists_known_variants(db, env, monkeypatch):
    """门户变体数取不全时只给警告：已经拿到 Barcode 的变体照上，不再整批 409。"""
    from services import takealot_portal_api as portal

    product(db)
    record = portal.extract("96357594", {"variants": [{"gtin": "6009512345678"}, None]})
    assert record["catalogue_variant_count"] == 2 and len(record["catalogue_variants"]) == 1
    monkeypatch.setattr(portal, "fetch", lambda *args: record)

    result = list_offer("p1", ListOfferIn(), db, "default-store")
    assert [c["barcode"] for c in env["calls"]["create"]] == ["6009512345678"]
    assert result["listed_count"] == 1
    assert result["warnings"], "资料不全要留下提示"


def test_duplicate_barcode_cannot_identify_variant(db, env):
    """显式传 Barcode 时仍要求唯一匹配：重复就拒绝，不猜运营想上哪个。"""
    product(db)
    env["state"]["variants"] = [variant(), variant(label="Silver")]
    with pytest.raises(HTTPException) as exc:
        list_offer("p1", ListOfferIn(barcode="6009512345678"), db, "default-store")
    assert exc.value.status_code == 409
    assert env["calls"]["create"] == []
