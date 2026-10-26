# -*- coding: utf-8 -*-
"""从选品库导入装箱单库：三个计数必须互斥，且「已导入但缺图」不能算失败。

界面上的三句话是靠这三个计数拼出来的，混一个就会误导用户去重复操作：
  - 空 SKU 的选品产品原来 `skipped += 1` 之后又进 `failed`，一个产品被报成
    「跳过 1 条、失败 1 条」——它其实一条都没进来；
  - 图片抓取失败原来也进 `failed`，可产品已经入库了。用户看到「失败 1 条」
    以为没导进来，再导一次又被算成「跳过 1 条」，永远对不上账。

所以这里钉三件事：
  1) 空 SKU 只算 failed，不算 skipped，也不产生装箱单行；
  2) 图片抓取失败算 image_failed，imported 仍 +1，且该行确实落库；
  3) 已存在的 SKU 只算 skipped，不重复插入。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, PackingProduct, Product
from api import packing_routes as routes


def _session(tmp_path, name):
    engine = create_engine(f'sqlite:///{tmp_path}/{name}.db')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _run(Session, ids):
    db = Session()
    try:
        return routes.import_from_products(routes.ImportFromProductsIn(product_ids=ids), db, DEFAULT_STORE_ID)
    finally:
        db.close()


def test_empty_sku_counts_once_as_failed(tmp_path):
    """SKU 为空的选品产品：只记 failed，不能同时记 skipped。"""
    Session = _session(tmp_path, 'empty_sku')
    db = Session()
    db.add(Product(id='p1', store_id=DEFAULT_STORE_ID, sku='', product_name='无 SKU 产品',
                   chinese_product_name='无 SKU 产品', is_archived=False))
    db.commit()
    db.close()

    result = _run(Session, ['p1'])

    assert result['imported'] == 0
    assert result['skipped'] == 0, '空 SKU 不是「跳过」，它根本没进库'
    assert len(result['failed']) == 1 and 'SKU 为空' in result['failed'][0]['reason']
    assert result['image_failed'] == []
    assert Session().query(PackingProduct).count() == 0


def test_image_failure_is_reported_separately_from_import(tmp_path, monkeypatch):
    """图片抓取失败：产品照样入库，只进 image_failed。"""
    Session = _session(tmp_path, 'image_fail')
    db = Session()
    db.add(Product(id='p1', store_id=DEFAULT_STORE_ID, sku='SKU-IMG', product_name='Leather bag',
                   chinese_product_name='皮革包', product_image_url='https://img.example/x.jpg',
                   is_archived=False))
    db.commit()
    db.close()

    def boom(url):
        raise RuntimeError('CDN 403')

    monkeypatch.setattr(routes, '_fetch_takealot_image', boom)

    result = _run(Session, ['p1'])

    assert result['imported'] == 1, '图片失败不该阻断导入'
    assert result['failed'] == [], '已导入的产品不能报成「失败」，否则用户会重复导入'
    assert len(result['image_failed']) == 1
    assert result['image_failed'][0]['sku'] == 'SKU-IMG'
    assert 'CDN 403' in result['image_failed'][0]['reason']
    row = Session().query(PackingProduct).filter(PackingProduct.sku == 'SKU-IMG').first()
    assert row is not None and row.image_file == ''


def test_existing_sku_is_only_skipped(tmp_path):
    """SKU 已存在：只算 skipped，不重复插入、不进 failed。"""
    Session = _session(tmp_path, 'dup')
    db = Session()
    db.add(Product(id='p1', store_id=DEFAULT_STORE_ID, sku='SKU-1', product_name='A',
                   chinese_product_name='甲', is_archived=False))
    db.add(PackingProduct(store_id=DEFAULT_STORE_ID, sku='SKU-1', name='甲'))
    db.commit()
    db.close()

    result = _run(Session, ['p1'])

    assert (result['imported'], result['skipped']) == (0, 1)
    assert result['failed'] == [] and result['image_failed'] == []
    assert Session().query(PackingProduct).count() == 1


def test_missing_catalog_product_is_a_failure_not_a_skip(tmp_path):
    """选品产品不存在（已被删/归档）：算 failed，不算 skipped。"""
    Session = _session(tmp_path, 'missing')
    result = _run(Session, ['not-exist'])
    assert result['imported'] == 0 and result['skipped'] == 0
    assert len(result['failed']) == 1 and '不存在' in result['failed'][0]['reason']
