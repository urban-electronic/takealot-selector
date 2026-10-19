# -*- coding: utf-8 -*-
"""装箱单两条静默降级的回归（PACK-RENAME-01 / PACK-DUP-01）。

PACK-RENAME-01：`POST /api/packing/product` 带 old_sku 改 SKU 时，旧实现删旧行再建新行，
image_file / template_row / default_count / 变体归属全部回落到默认值——远程装箱单页
（backend/packing_ui/index.html）每次编辑都带 old_sku，等于改个 SKU 就丢图。
改名是同一行的 SKU 变更，不是新建。

PACK-DUP-01：`duplicate_sku` 只在响应里声明、从未累加，前端「N 条 SKU 重复」恒为 0，
用户看不到同一个 SKU 被两个选品产品同时占用。

零真实出站：只碰内存 sqlite。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api import packing_routes as routes
from models import Base, DEFAULT_STORE_ID, PackingProduct, Product


def _session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return Session(engine)


def test_rename_keeps_image_template_row_and_variant_membership():
    with _session() as db:
        db.add(PackingProduct(
            sku='OLD-1', name='旧名', name_zh='旧名', name_en='old',
            unit='个', weight='0.4', material='塑料', brand='是', battery='无',
            electric='否', magnetic='否', template_row=7, default_count=3,
            image_file='abc123.png', variant_group='g1', variant_label='红色',
            is_primary_variant=False,
        ))
        db.commit()

        routes.upsert_product(routes.ProductIn(
            sku='NEW-1', old_sku='OLD-1', name_zh='新名', name_en='new',
        ), db)
        db.commit()

        rows = {p.sku: p for p in db.query(PackingProduct).all()}
        assert 'OLD-1' not in rows, '改名后旧 SKU 不应残留'
        renamed = rows['NEW-1']
        assert renamed.name_zh == '新名' and renamed.name_en == 'new'
        assert renamed.image_file == 'abc123.png', '改名不该丢掉已上传的图片'
        assert renamed.template_row == 7, '改名不该丢掉模板行'
        assert renamed.default_count == 3, '改名不该丢掉默认件数'
        assert renamed.variant_group == 'g1' and renamed.variant_label == '红色'
        assert renamed.is_primary_variant is False


def test_rename_into_taken_sku_is_rejected_without_touching_either_row():
    with _session() as db:
        db.add_all([
            PackingProduct(sku='A-1', name='A', image_file='a.png'),
            PackingProduct(sku='B-1', name='B', image_file='b.png'),
        ])
        db.commit()

        resp = routes.upsert_product(routes.ProductIn(sku='B-1', old_sku='A-1', name_zh='A'), db)
        assert resp.status_code == 400
        rows = {p.sku: p.image_file for p in db.query(PackingProduct).all()}
        assert rows == {'A-1': 'a.png', 'B-1': 'b.png'}


def test_sync_reports_sku_claimed_by_two_catalog_products():
    """同一 SKU 被两个产品占用时，前端提示里的「N 条 SKU 重复」必须真的数出来。"""
    with _session() as db:
        db.add_all([
            Product(id='p1', store_id=DEFAULT_STORE_ID, sku='111 112',
                    chinese_product_name='红 蓝', is_archived=False),
            Product(id='p2', store_id=DEFAULT_STORE_ID, sku='112',
                    chinese_product_name='蓝', is_archived=False),
        ])
        db.commit()

        result = routes.sync_from_products(db=db, store_id=DEFAULT_STORE_ID)
        assert result['ok'] is True
        assert result['duplicate_sku'] == 1, '112 被 p1 和 p2 同时占用，应计 1 条重复'
        assert db.query(PackingProduct).count() == 2


def test_sync_without_duplicates_reports_zero():
    with _session() as db:
        db.add(Product(id='p1', store_id=DEFAULT_STORE_ID, sku='111 112 113',
                       chinese_product_name='红 蓝 绿', is_archived=False))
        db.commit()

        result = routes.sync_from_products(db=db, store_id=DEFAULT_STORE_ID)
        assert result['duplicate_sku'] == 0
        assert result['imported'] == 3
