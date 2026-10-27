"""以卖家后台为准更正 SKU：提议规则 + 下游迁移 + 留档。

这些用例守的是同一件事的两面：
1. 只有「能确定一一对应」时才提议改名（单规格），多规格宁可报人工；
2. 一旦改名，库存/发运/装箱/调价配置里的旧 SKU 必须一起迁，且留下映射可追溯。
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import (
    DEFAULT_STORE_ID, InventoryAdjustment, PackingProduct, Product, Shipment,
    ShipmentCarton, ShipmentCartonItem, ShipmentLine, SkuRename, SystemSettings,
)
from services.sku_migration import migrate_sku
from services.takealot_seller_api import offer_plan

URL = 'https://www.takealot.com/wig/PLID90000001'
PLID = '90000001'


def offer(sku, **overrides):
    row = {'sku': sku, 'productline_id': PLID, 'title': 'Wig Black', 'tsin_id': 'TSIN1',
           'image_url': '', 'selling_price': 100.0, 'status': 'buyable',
           'length_cm': 30, 'width_cm': 20, 'height_cm': 10, 'weight_grams': 1200}
    row.update(overrides)
    return row


def make_product(**overrides):
    base = dict(id='p1', store_id=DEFAULT_STORE_ID, product_name='Wig',
                takealot_url=URL, sku='9900000000001')
    base.update(overrides)
    return Product(**base)


def fresh_db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --- 提议规则 -------------------------------------------------------------

def test_single_variant_mismatch_is_proposed_as_replacement():
    plan = offer_plan(make_product(sku='OLD-SKU'), [offer('NEW-SKU')])
    change = next(item for item in plan['changes'] if item['field'] == 'sku')
    assert change['value'] == 'NEW-SKU'
    # 替换必须带上旧值，否则界面上看不出「这是改名」而不是「补空白」。
    assert change['current'] == 'OLD-SKU'


def test_multi_variant_mismatch_is_not_guessed():
    plan = offer_plan(make_product(sku='OLD-A\nOLD-B'), [offer('NEW-A'), offer('NEW-B')])
    assert not any(item['field'] == 'sku' for item in plan['changes'])
    assert any('多规格无法自动对应' in message for message in plan['warnings'])


def test_matching_sku_is_never_proposed_again():
    plan = offer_plan(make_product(sku='SKU1'), [offer('SKU1')])
    assert not any(item['field'] == 'sku' for item in plan['changes'])


def test_blank_sku_is_filled_without_current_value():
    plan = offer_plan(make_product(sku=''), [offer('SKU1')])
    change = next(item for item in plan['changes'] if item['field'] == 'sku')
    assert change['value'] == 'SKU1'
    assert 'current' not in change


def test_other_plid_never_proposes_sku():
    # 归属只认 PLID。别的 PLID 的 SKU 再像也不能拿来改本行，否则会跨商品串号。
    plan = offer_plan(make_product(sku='OLD'), [offer('NEW', productline_id='88888888')])
    assert not any(item['field'] == 'sku' for item in plan['changes'])


# --- 下游迁移 -------------------------------------------------------------

def seed_downstream(db):
    db.add(make_product(sku='NEW'))
    db.add(PackingProduct(store_id=DEFAULT_STORE_ID, sku='OLD', name='Wig', weight='120'))
    db.add(InventoryAdjustment(store_id=DEFAULT_STORE_ID, sku='OLD', quantity_delta=5))
    db.add(Shipment(id='s1', store_id=DEFAULT_STORE_ID, draft_key='d1',
                    shipment_no='PK-1', shipment_date='2026-10-27'))
    db.add(ShipmentLine(id='l1', shipment_id='s1', sku='OLD', total_quantity=7))
    db.add(ShipmentCarton(id='c1', shipment_id='s1', seq=1))
    db.add(ShipmentCartonItem(id='i1', carton_id='c1', sku='OLD', quantity=3))
    db.add(SystemSettings(key=f'_pricing_rule:{DEFAULT_STORE_ID}:p1:offer1',
                          value=json.dumps({'sku': 'OLD', 'min_price': 10})))
    db.add(SystemSettings(key=f'_pricing_monitor:{DEFAULT_STORE_ID}:p1:offer1',
                          value=json.dumps({'sku': 'OLD', 'status': 'ok'})))
    db.commit()


def test_migrate_moves_every_downstream_reference():
    with fresh_db() as db:
        seed_downstream(db)
        product = db.query(Product).filter_by(id='p1').one()
        result = migrate_sku(db, product, ['OLD'], ['NEW'])
        db.commit()

        assert result['moved'] == {'packing_products': 1, 'inventory_adjustments': 1,
                                   'shipment_lines': 1, 'shipment_carton_items': 1,
                                   'pricing_configs': 2}
        assert db.query(PackingProduct).filter_by(sku='NEW').one().weight == '120'
        assert db.query(InventoryAdjustment).filter_by(sku='NEW').one().quantity_delta == 5
        assert db.query(ShipmentLine).filter_by(sku='NEW').one().total_quantity == 7
        assert db.query(ShipmentCartonItem).filter_by(sku='NEW').one().quantity == 3
        for prefix in ('_pricing_rule', '_pricing_monitor'):
            row = db.query(SystemSettings).filter_by(
                key=f'{prefix}:{DEFAULT_STORE_ID}:p1:offer1').one()
            assert json.loads(row.value)['sku'] == 'NEW'
        assert not db.query(InventoryAdjustment).filter_by(sku='OLD').count()


def test_rename_is_archived_with_mapping():
    with fresh_db() as db:
        seed_downstream(db)
        product = db.query(Product).filter_by(id='p1').one()
        migrate_sku(db, product, ['OLD'], ['NEW'], source='seller_api')
        db.commit()
        record = db.query(SkuRename).one()
        assert (record.old_sku, record.new_sku) == ('OLD', 'NEW')
        assert (record.product_id, record.store_id) == ('p1', DEFAULT_STORE_ID)
        assert json.loads(record.detail)['shipment_lines'] == 1


def test_packing_conflict_aborts_instead_of_partial_migration():
    with fresh_db() as db:
        db.add(make_product(sku='NEW'))
        db.add(PackingProduct(store_id=DEFAULT_STORE_ID, sku='OLD', name='旧资料', weight='120'))
        db.add(PackingProduct(store_id=DEFAULT_STORE_ID, sku='NEW', name='新资料', weight='999'))
        db.commit()
        from services.sku_migration import SkuMigrationConflict
        with pytest.raises(SkuMigrationConflict):
            migrate_sku(db, db.query(Product).filter_by(id='p1').one(), ['OLD'], ['NEW'])
        db.rollback()
        assert db.query(PackingProduct).filter_by(sku='OLD').one().weight == '120'
        assert db.query(PackingProduct).filter_by(sku='NEW').one().weight == '999'
        assert db.query(SkuRename).count() == 0


def test_other_store_rows_are_untouched():
    with fresh_db() as db:
        db.add(make_product(sku='NEW'))
        db.add(InventoryAdjustment(store_id='other-store', sku='OLD', quantity_delta=9))
        db.add(InventoryAdjustment(store_id=DEFAULT_STORE_ID, sku='OLD', quantity_delta=5))
        db.commit()
        migrate_sku(db, db.query(Product).filter_by(id='p1').one(), ['OLD'], ['NEW'])
        db.commit()
        assert db.query(InventoryAdjustment).filter_by(
            store_id='other-store', sku='OLD').one().quantity_delta == 9


def test_other_store_shipment_rows_are_untouched():
    # 发运行和箱内明细自己不带 store_id，只能经 Shipment 限定；漏了这一步
    # 就会把另一个店铺同名 SKU 的发货记录一起改掉。
    with fresh_db() as db:
        db.add(make_product(sku='NEW'))
        for store, shipment_id, draft_key, line_id, carton_id, item_id in [
            (DEFAULT_STORE_ID, 's1', 'd1', 'l1', 'c1', 'i1'),
            ('other-store', 's2', 'd2', 'l2', 'c2', 'i2'),
        ]:
            db.add(Shipment(id=shipment_id, store_id=store, draft_key=draft_key,
                            shipment_no=f'PK-{shipment_id}', shipment_date='2026-10-27'))
            db.add(ShipmentLine(id=line_id, shipment_id=shipment_id, sku='OLD', total_quantity=7))
            db.add(ShipmentCarton(id=carton_id, shipment_id=shipment_id, seq=1))
            db.add(ShipmentCartonItem(id=item_id, carton_id=carton_id, sku='OLD', quantity=3))
        db.commit()
        migrate_sku(db, db.query(Product).filter_by(id='p1').one(), ['OLD'], ['NEW'])
        db.commit()
        assert db.query(ShipmentLine).filter_by(id='l1').one().sku == 'NEW'
        assert db.query(ShipmentCartonItem).filter_by(id='i1').one().sku == 'NEW'
        assert db.query(ShipmentLine).filter_by(id='l2').one().sku == 'OLD'
        assert db.query(ShipmentCartonItem).filter_by(id='i2').one().sku == 'OLD'


def test_mismatched_lengths_are_rejected():
    with fresh_db() as db:
        db.add(make_product(sku='NEW'))
        db.commit()
        with pytest.raises(ValueError):
            migrate_sku(db, db.query(Product).filter_by(id='p1').one(), ['A', 'B'], ['C'])


def test_noop_pairs_do_nothing():
    with fresh_db() as db:
        db.add(make_product(sku='SAME'))
        db.commit()
        result = migrate_sku(db, db.query(Product).filter_by(id='p1').one(), ['SAME'], ['SAME'])
        db.commit()
        assert result == {'moved': {}, 'conflicts': [], 'renamed': []}
        assert not db.query(SkuRename).count()


# --- 端到端：走真实的 preview → apply 路由 --------------------------------

def test_apply_route_renames_sku_and_migrates_downstream(monkeypatch):
    from types import SimpleNamespace
    import services.takealot_seller_api as service
    from api.product_routes import AssistRequest, seller_assist

    monkeypatch.setattr(service, 'configured_key', lambda db: 'test')
    monkeypatch.setattr(service, '_load_offers', lambda key: [offer('NEW-SKU')])
    request = lambda path: SimpleNamespace(url=SimpleNamespace(path=path))

    with fresh_db() as db:
        db.add(make_product(sku='OLD-SKU'))
        db.add(InventoryAdjustment(store_id=DEFAULT_STORE_ID, sku='OLD-SKU', quantity_delta=5))
        db.commit()

        preview = seller_assist(AssistRequest(ids=['p1']), request('/preview'), db, DEFAULT_STORE_ID)
        item = preview['items'][0]
        change = next(c for c in item['changes'] if c['field'] == 'sku')
        assert (change['current'], change['value']) == ('OLD-SKU', 'NEW-SKU')

        seller_assist(AssistRequest(ids=['p1'], tokens={'p1': item['token']}),
                      request('/apply'), db, DEFAULT_STORE_ID)
        db.expire_all()
        assert db.query(Product).filter_by(id='p1').one().sku == 'NEW-SKU'
        assert db.query(InventoryAdjustment).filter_by(sku='NEW-SKU').one().quantity_delta == 5
        record = db.query(SkuRename).one()
        assert (record.old_sku, record.new_sku) == ('OLD-SKU', 'NEW-SKU')


def test_apply_route_refuses_rename_when_new_sku_belongs_to_another_product(monkeypatch):
    from types import SimpleNamespace
    import services.takealot_seller_api as service
    from api.product_routes import AssistRequest, seller_assist

    monkeypatch.setattr(service, 'configured_key', lambda db: 'test')
    monkeypatch.setattr(service, '_load_offers', lambda key: [offer('NEW-SKU')])
    request = lambda path: SimpleNamespace(url=SimpleNamespace(path=path))

    with fresh_db() as db:
        db.add(make_product(sku='OLD-SKU'))
        db.add(make_product(id='p2', sku='NEW-SKU', takealot_url='https://www.takealot.com/x/PLID99999999'))
        db.commit()
        preview = seller_assist(AssistRequest(ids=['p1']), request('/preview'), db, DEFAULT_STORE_ID)
        item = preview['items'][0]
        assert not any(c['field'] == 'sku' for c in item['changes'])
        assert any('已被其他产品使用' in w for w in item['warnings'])


def test_apply_conflicting_packing_identity_rolls_back_every_change(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    import services.takealot_seller_api as service
    from api.product_routes import AssistRequest, seller_assist
    monkeypatch.setattr(service, 'configured_key', lambda db: 'fixture')
    monkeypatch.setattr(service, '_load_offers', lambda key: [offer('NEW-SKU')])
    request=lambda path:SimpleNamespace(url=SimpleNamespace(path=path))
    with fresh_db() as db:
        db.add(make_product(sku='OLD-SKU'))
        db.add(PackingProduct(store_id=DEFAULT_STORE_ID,sku='OLD-SKU',name='Old',weight='120'))
        db.add(PackingProduct(store_id=DEFAULT_STORE_ID,sku='NEW-SKU',name='Existing new',weight='999'))
        db.add(InventoryAdjustment(store_id=DEFAULT_STORE_ID,sku='OLD-SKU',quantity_delta=5))
        db.commit()
        preview=seller_assist(AssistRequest(ids=['p1']),request('/preview'),db,DEFAULT_STORE_ID)
        token=preview['items'][0]['token']
        with pytest.raises(HTTPException) as caught:
            seller_assist(AssistRequest(ids=['p1'],tokens={'p1':token}),request('/apply'),db,DEFAULT_STORE_ID)
        assert caught.value.status_code == 409
        db.expire_all()
        assert db.query(Product).filter_by(id='p1').one().sku == 'OLD-SKU'
        assert db.query(Product).filter_by(id='p1').one().width_mm is None
        assert db.query(InventoryAdjustment).one().sku == 'OLD-SKU'
        assert db.query(PackingProduct).filter_by(sku='OLD-SKU').one().weight == '120'
        assert db.query(PackingProduct).filter_by(sku='NEW-SKU').one().weight == '999'
        assert db.query(SkuRename).count() == 0


def test_all_pairs_are_preflighted_before_first_write():
    from services.sku_migration import SkuMigrationConflict
    with fresh_db() as db:
        db.add(make_product())
        for sku in ('OLD-A','OLD-B','NEW-B'):
            db.add(PackingProduct(store_id=DEFAULT_STORE_ID,sku=sku,name=sku))
        db.add(InventoryAdjustment(store_id=DEFAULT_STORE_ID,sku='OLD-A',quantity_delta=3))
        db.commit()
        with pytest.raises(SkuMigrationConflict):
            migrate_sku(db,db.query(Product).one(),['OLD-A','OLD-B'],['NEW-A','NEW-B'])
        assert db.query(PackingProduct).filter_by(sku='OLD-A').count() == 1
        assert db.query(PackingProduct).filter_by(sku='NEW-A').count() == 0
        assert db.query(InventoryAdjustment).one().sku == 'OLD-A'
        assert not db.query(SkuRename).count()
