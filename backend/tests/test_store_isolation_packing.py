# -*- coding: utf-8 -*-
"""店铺数据隔离回归：装箱产品库与发货单号必须按店铺分开。

背景（2026-10-21）：代理品牌（代运营CK）接入后，两个店铺共用一套后台。
- packing_products 原来以 sku 为全局主键，两个店铺出现同一个 SKU 会互相覆盖
  重量/材质/图片/模板行，属于静默数据污染。
- shipments.shipment_no 原来是全局唯一，两个店铺不能同一天都拥有
  PK-YYYYMMDD-001，编号会被别店占用。

本文件覆盖：同 SKU 跨店互不影响、按店删除、发货单号按店唯一、
以及老库（单列主键 / 单列唯一）的启动期迁移。
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from api import packing_routes as routes
from models import DEFAULT_STORE_ID, Base, PackingProduct, Shipment

STORE_A = DEFAULT_STORE_ID
STORE_B = "store-b"


def _session(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_same_sku_in_two_stores_keeps_separate_rows(tmp_path):
    """同一个 SKU 在两个店铺是两条独立记录，字段互不覆盖。"""
    Session = _session(tmp_path, "iso_pack")
    db = Session()
    routes._upsert(db, STORE_A, routes.ProductIn(sku="CK-1", name_zh="托特包", weight="0.5"))
    routes._upsert(db, STORE_B, routes.ProductIn(sku="CK-1", name_zh="托特包", weight="0.9"))
    db.commit()

    rows = db.query(PackingProduct).filter(PackingProduct.sku == "CK-1").all()
    assert len(rows) == 2
    assert {r.store_id for r in rows} == {STORE_A, STORE_B}
    assert {r.weight for r in rows} == {"0.5", "0.9"}


def test_upsert_in_one_store_does_not_touch_the_other(tmp_path):
    """改 A 店同名 SKU，B 店的记录保持不变。"""
    Session = _session(tmp_path, "iso_pack2")
    db = Session()
    routes._upsert(db, STORE_A, routes.ProductIn(sku="CK-2", name_zh="单肩包", weight="0.4"))
    routes._upsert(db, STORE_B, routes.ProductIn(sku="CK-2", name_zh="单肩包", weight="0.7"))
    db.commit()

    routes._upsert(db, STORE_A, routes.ProductIn(sku="CK-2", name_zh="单肩包改名", weight="0.45"))
    db.commit()

    a = routes._find(db, STORE_A, "CK-2")
    b = routes._find(db, STORE_B, "CK-2")
    assert a.name_zh == "单肩包改名" and a.weight == "0.45"
    assert b.name_zh == "单肩包" and b.weight == "0.7"


def test_delete_only_affects_current_store(tmp_path):
    """删除只作用于当前店铺，别店同 SKU 不受影响。"""
    Session = _session(tmp_path, "iso_del")
    db = Session()
    routes._upsert(db, STORE_A, routes.ProductIn(sku="CK-3", name_zh="水桶包"))
    routes._upsert(db, STORE_B, routes.ProductIn(sku="CK-3", name_zh="水桶包"))
    db.commit()

    resp = routes.delete_product(routes.DeleteIn(sku="CK-3"), db, store_id=STORE_A)
    db.commit()
    assert resp == {"ok": True}
    assert routes._find(db, STORE_A, "CK-3") is None
    assert routes._find(db, STORE_B, "CK-3") is not None


def test_shipment_no_can_repeat_across_stores_but_not_within(tmp_path):
    """发货单号按店铺唯一：两个店铺都能有 001，同店重复则被拒。"""
    Session = _session(tmp_path, "iso_ship")
    db = Session()
    db.add(Shipment(id="s1", store_id=STORE_A, draft_key="d1", shipment_no="PK-20261021-001",
                    shipment_date="2026-10-21"))
    db.add(Shipment(id="s2", store_id=STORE_B, draft_key="d2", shipment_no="PK-20261021-001",
                    shipment_date="2026-10-21"))
    db.commit()
    assert db.query(Shipment).count() == 2

    db.add(Shipment(id="s3", store_id=STORE_A, draft_key="d3", shipment_no="PK-20261021-001",
                    shipment_date="2026-10-21"))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()


OLD_PACKING_DDL = """
CREATE TABLE packing_products (
    sku VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL DEFAULT '',
    name_zh VARCHAR DEFAULT '',
    name_en VARCHAR DEFAULT '',
    unit VARCHAR DEFAULT '',
    weight VARCHAR DEFAULT '',
    material VARCHAR DEFAULT '',
    brand VARCHAR DEFAULT '',
    battery VARCHAR DEFAULT '',
    electric VARCHAR DEFAULT '',
    magnetic VARCHAR DEFAULT '',
    template_row INTEGER,
    default_count INTEGER DEFAULT 1,
    image_file VARCHAR DEFAULT '',
    variant_group VARCHAR DEFAULT '',
    variant_label VARCHAR DEFAULT '',
    is_primary_variant BOOLEAN DEFAULT 1,
    updated_at DATETIME
)
"""

OLD_SHIPMENTS_DDL = """
CREATE TABLE shipments (
    id VARCHAR NOT NULL PRIMARY KEY,
    store_id VARCHAR NOT NULL DEFAULT 'default-store',
    draft_key VARCHAR NOT NULL UNIQUE,
    shipment_no VARCHAR NOT NULL UNIQUE,
    shipment_date VARCHAR NOT NULL,
    mark VARCHAR DEFAULT '',
    shipping VARCHAR DEFAULT '',
    address VARCHAR DEFAULT '',
    status VARCHAR DEFAULT 'confirmed',
    version INTEGER DEFAULT 1,
    void_reason VARCHAR DEFAULT '',
    created_at DATETIME,
    voided_at DATETIME
)
"""


def _legacy_engine(tmp_path, name, statements):
    path = tmp_path / f"{name}.db"
    conn = sqlite3.connect(path)
    for sql in statements:
        conn.execute(sql)
    conn.commit()
    conn.close()
    return create_engine(f"sqlite:///{path}")


def test_migration_moves_packing_products_to_store_scoped_pk(tmp_path, monkeypatch):
    """老库（sku 单列主键）迁移后：补 store_id、老数据归默认店铺、允许跨店同 SKU。"""
    import main

    engine = _legacy_engine(tmp_path, "legacy_pack", [
        OLD_PACKING_DDL,
        "INSERT INTO packing_products (sku, name, name_zh, weight, image_file) "
        "VALUES ('LEGACY-1', '旧产品', '旧产品', '0.33', 'img.png')",
    ])
    monkeypatch.setattr(main, "engine", engine)

    main._migrate_packing_products_store_scope()

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("packing_products")}
    assert "store_id" in cols
    assert list(insp.get_pk_constraint("packing_products")["constrained_columns"]) == ["store_id", "sku"]

    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT store_id, sku, weight, image_file FROM packing_products WHERE sku = 'LEGACY-1'"
        )).first()
    assert tuple(row) == (DEFAULT_STORE_ID, "LEGACY-1", "0.33", "img.png")

    # 迁移后另一个店铺可以拥有同名 SKU
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO packing_products (store_id, sku, name) VALUES (:s, 'LEGACY-1', '另一家')"
        ), {"s": STORE_B})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM packing_products")).scalar() == 2


def test_migration_makes_shipment_no_unique_per_store(tmp_path, monkeypatch):
    """老库（shipment_no 全局唯一）迁移后，两个店铺可以同号，同店仍然唯一。"""
    import main

    engine = _legacy_engine(tmp_path, "legacy_ship", [
        OLD_SHIPMENTS_DDL,
        "INSERT INTO shipments (id, store_id, draft_key, shipment_no, shipment_date) "
        "VALUES ('old-1', 'default-store', 'd-old', 'PK-20261020-001', '2026-10-20')",
    ])
    monkeypatch.setattr(main, "engine", engine)

    main._migrate_shipments_store_scope()

    insp = inspect(engine)
    ucs = [list(u["column_names"]) for u in insp.get_unique_constraints("shipments")]
    assert ["shipment_no"] not in ucs
    assert ["store_id", "shipment_no"] in ucs

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO shipments (id, store_id, draft_key, shipment_no, shipment_date) "
            "VALUES ('new-1', :s, 'd-new', 'PK-20261020-001', '2026-10-20')"
        ), {"s": STORE_B})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM shipments")).scalar() == 2

    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO shipments (id, store_id, draft_key, shipment_no, shipment_date) "
                "VALUES ('dup-1', 'default-store', 'd-dup', 'PK-20261020-001', '2026-10-20')"
            ))


def test_store_list_can_be_limited_to_active_stores(tmp_path):
    """装箱工作台只能选在用店铺：停用店铺一旦被选中，get_store_id 会 400。

    `/api/stores` 默认仍返回全部（店铺管理页要能看到并重新启用停用店铺），
    只有显式 active=1 时才过滤。
    """
    from api import store_routes
    from models import Store

    Session = _session(tmp_path, "iso_stores")
    db = Session()
    db.add(Store(id="store-b", name="代运营CK", status="active"))
    db.add(Store(id="store-c", name="已停用店", status="disabled"))
    db.commit()

    assert {r["id"] for r in store_routes.list_stores(db=db)} == {"store-b", "store-c"}
    assert [r["id"] for r in store_routes.list_stores(active=True, db=db)] == ["store-b"]


def test_packing_ui_only_offers_active_stores():
    """静态装箱工作台的店铺下拉必须走 active=1，否则会列出停用店铺。"""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "packing_ui" / "index.html").read_text(encoding="utf-8")
    assert "/api/stores?active=1" in html
