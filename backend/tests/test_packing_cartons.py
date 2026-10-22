"""逐箱清单（shipment_cartons）：一箱一条，回答「第几箱装了什么」。

装箱单本身是**按 SKU 汇总**的（A 有 3 箱 × 10 件），箱子封上以后外观一样，
靠它查不出「第 3 箱里是什么」。所以导出时同步落一份按箱的记录，箱唛就按它印。

钉死这几件事：
  1) 不传逐箱清单时，按「一箱一个 SKU」自动展开成 C01..Cn；
  2) 混装时以传上来的为准，一箱多行 SKU；
  3) 箱内合计对不上装箱单 → 400，且不产生半票货；
  4) 查询能按日期看当天箱、按 SKU 反查在哪几箱（仍带整票总箱数）；
  5) 店铺隔离：另一个店铺查不到。
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, Shipment, ShipmentCarton, ShipmentCartonItem, ShipmentLine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _export(routes, db, *, items, cartons=None, store='default-store', draft='d1', date='2026-10-22',
            record_shipment=False):
    data = routes.ExportIn(
        date=date,
        record_shipment=record_shipment,
        draft_key=draft,
        items=[routes.ExportLine(**line) for line in items],
        cartons=[routes.CartonIn(**c) for c in (cartons or [])],
    )
    names = {
        '1111111111116': ('皮革黑色包', 'Leather bag'),
        '2222222222222': ('宝马后视镜盖', 'Mirror cover'),
    }
    found = {}
    for line in items:
        sku = line['sku']
        found[sku] = SimpleNamespace(sku=sku)
    with patch.object(routes, '_find', side_effect=lambda _db, _store, sku: found[sku]), \
         patch.object(routes, '_catalog_product_for_sku', return_value=object()), \
         patch.object(routes, '_hydrate_export_product'), \
         patch.object(routes, 'public', side_effect=lambda p: {
             'sku': p.sku, 'name_zh': names.get(p.sku, ('', ''))[0], 'name_en': names.get(p.sku, ('', ''))[1],
         }), \
         patch.object(routes.px, 'generate', side_effect=lambda *a, **k: Path(a[-1]).write_bytes(b'xlsx')), \
         patch.object(routes, 'inventory_balance_for_sku', return_value=9999), \
         patch.object(routes, 'record_operation'):
        return routes.export_excel(data, db, store)


def test_auto_expand_one_sku_per_carton():
    """一箱一个 SKU：填了 3 箱 × 10 件 + 2 箱 × 5 件，逐箱清单自动是 C01..C05。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        resp = _export(routes, db, items=[
            {'sku': '1111111111116', 'cartons': '3', 'count': '10'},
            {'sku': '2222222222222', 'cartons': '2', 'count': '5'},
        ])
        assert resp.status_code == 200
        cartons = db.query(ShipmentCarton).order_by(ShipmentCarton.seq).all()
        assert [c.box_no for c in cartons] == ['C01', 'C02', 'C03', 'C04', 'C05']
        assert [c.seq for c in cartons] == [1, 2, 3, 4, 5]
        items = db.query(ShipmentCartonItem).all()
        assert len(items) == 5
        by_carton = {}
        for item in items:
            by_carton.setdefault(item.carton_id, []).append(item)
        first = by_carton[cartons[0].id]
        assert len(first) == 1 and first[0].sku == '1111111111116' and first[0].quantity == 10
        last = by_carton[cartons[4].id]
        assert last[0].sku == '2222222222222' and last[0].quantity == 5
        # 品名要跟着落到箱明细上，箱唛才能印中文
        assert first[0].name_zh == '皮革黑色包'
        assert cartons[0].store_id == 'default-store'
    finally:
        db.close()
        engine.dispose()


def test_mixed_carton_keeps_manual_layout():
    """混装：一箱里两个 SKU，以传上来的为准。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        resp = _export(
            routes, db,
            items=[
                {'sku': '1111111111116', 'cartons': '1', 'count': '10'},
                {'sku': '2222222222222', 'cartons': '1', 'count': '5'},
            ],
            cartons=[
                {'box_no': 'A-1', 'note': '大箱', 'items': [
                    {'sku': '1111111111116', 'quantity': '7'},
                    {'sku': '2222222222222', 'quantity': '5'},
                ]},
                {'box_no': 'A-2', 'items': [{'sku': '1111111111116', 'quantity': '3'}]},
            ],
        )
        assert resp.status_code == 200
        cartons = db.query(ShipmentCarton).order_by(ShipmentCarton.seq).all()
        assert [c.box_no for c in cartons] == ['A-1', 'A-2']
        assert cartons[0].note == '大箱'
        rows = db.query(ShipmentCartonItem).filter_by(carton_id=cartons[0].id).all()
        assert {r.sku: r.quantity for r in rows} == {'1111111111116': 7, '2222222222222': 5}
        assert db.query(ShipmentCartonItem).filter_by(carton_id=cartons[1].id).one().quantity == 3
    finally:
        db.close()
        engine.dispose()


def test_carton_total_must_match_packing_list():
    """箱内合计比装箱单少 → 400，且不能留下半票货。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        resp = _export(
            routes, db,
            items=[{'sku': '1111111111116', 'cartons': '2', 'count': '10'}],
            cartons=[{'box_no': 'C01', 'items': [{'sku': '1111111111116', 'quantity': '10'}]}],
        )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert '逐箱合计 10 件' in body and '20 件' in body
        assert db.query(Shipment).count() == 0
        assert db.query(ShipmentCarton).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_unknown_sku_and_empty_carton_rejected():
    """箱里塞了不在装箱单的 SKU、或留一个空箱，都要拒绝并说清第几箱。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        resp = _export(
            routes, db,
            items=[{'sku': '1111111111116', 'cartons': '1', 'count': '10'}],
            cartons=[{'box_no': 'C01', 'items': [{'sku': '9999999999999', 'quantity': '10'}]}],
        )
        assert resp.status_code == 400
        assert '不在本次装箱单中' in resp.body.decode()

        resp = _export(
            routes, db, draft='d2',
            items=[{'sku': '1111111111116', 'cartons': '1', 'count': '10'}],
            cartons=[{'box_no': 'C01', 'items': []}],
        )
        assert resp.status_code == 400
        assert '没有装任何产品' in resp.body.decode()
        assert db.query(Shipment).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_query_by_date_and_by_sku():
    """按日期看当天箱；按 SKU 反查只回含它的箱，但总箱数是整票的。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        assert _export(routes, db, date='2026-10-22', items=[
            {'sku': '1111111111116', 'cartons': '2', 'count': '10'},
            {'sku': '2222222222222', 'cartons': '3', 'count': '5'},
        ]).status_code == 200
        assert _export(routes, db, date='2026-10-21', draft='d2',
                       items=[{'sku': '2222222222222', 'cartons': '1', 'count': '5'}]).status_code == 200

        day = routes.list_cartons(date='2026-10-22', sku='', limit=300, db=db, store_id='default-store')
        assert day['total_cartons'] == 5
        assert {c['box_no'] for c in day['cartons']} == {'C01', 'C02', 'C03', 'C04', 'C05'}
        assert all(c['total'] == 5 for c in day['cartons'])

        by_sku = routes.list_cartons(date='2026-10-22', sku='2222222222222', limit=300, db=db, store_id='default-store')
        assert by_sku['total_cartons'] == 3
        assert {c['seq'] for c in by_sku['cartons']} == {3, 4, 5}
        # 箱唛要印「第 3 箱 / 共 5 箱」，所以 total 必须是整票的 5，不是筛选后的 3
        assert all(c['total'] == 5 for c in by_sku['cartons'])
        assert by_sku['cartons'][0]['items'][0]['name_zh'] == '宝马后视镜盖'

        other_day = routes.list_cartons(date='2026-10-21', sku='', limit=300, db=db, store_id='default-store')
        assert other_day['total_cartons'] == 1
    finally:
        db.close()
        engine.dispose()


def test_cartons_are_store_isolated():
    """另一个店铺查不到本店的箱。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        assert _export(routes, db, items=[{'sku': '1111111111116', 'cartons': '2', 'count': '10'}]).status_code == 200
        mine = routes.list_cartons(date='2026-10-22', sku='', limit=300, db=db, store_id='default-store')
        assert mine['total_cartons'] == 2
        theirs = routes.list_cartons(date='2026-10-22', sku='', limit=300, db=db, store_id='store-b')
        assert theirs['total_cartons'] == 0
        assert db.query(ShipmentCarton).filter_by(store_id='store-b').count() == 0
    finally:
        db.close()
        engine.dispose()


def test_repeat_export_same_draft_does_not_duplicate_cartons():
    """同一 draft_key 重复导出（改数量后再导一次）不能把箱翻倍。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        assert _export(routes, db, draft='same', items=[{'sku': '1111111111116', 'cartons': '2', 'count': '10'}]).status_code == 200
        assert _export(routes, db, draft='same', items=[{'sku': '1111111111116', 'cartons': '2', 'count': '10'}]).status_code == 200
        assert db.query(Shipment).count() == 1
        assert db.query(ShipmentCarton).count() == 2
        assert db.query(ShipmentLine).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_reexport_same_draft_refreshes_lines_and_cartons():
    """同一草稿改了数量再导出：明细和箱清单都要跟着变，不能留第一次的快照。

    装箱单 Excel 是按本次 payload 重新生成的；如果箱清单还是旧的，箱唛就会印错。
    """
    from api import packing_routes as routes
    engine, db = _db()
    try:
        assert _export(routes, db, draft='same', items=[
            {'sku': '1111111111116', 'cartons': '2', 'count': '10'},
        ]).status_code == 200
        assert db.query(ShipmentCarton).count() == 2

        assert _export(routes, db, draft='same', items=[
            {'sku': '1111111111116', 'cartons': '3', 'count': '10'},
        ]).status_code == 200
        assert db.query(Shipment).count() == 1
        line = db.query(ShipmentLine).one()
        assert (line.cartons, line.total_quantity) == (3, 30)
        cartons = db.query(ShipmentCarton).order_by(ShipmentCarton.seq).all()
        assert [c.box_no for c in cartons] == ['C01', 'C02', 'C03']
        assert [i.quantity for i in db.query(ShipmentCartonItem).all()] == [10, 10, 10]
    finally:
        db.close()
        engine.dispose()


def test_confirmed_shipment_rejects_quantity_change():
    """已确认发货（库存已扣）不能改数量，否则库存和单据会脱钩。"""
    from api import packing_routes as routes
    engine, db = _db()
    try:
        assert _export(routes, db, draft='confirmed', record_shipment=True, items=[
            {'sku': '1111111111116', 'cartons': '2', 'count': '10'},
        ]).status_code == 200
        resp = _export(routes, db, draft='confirmed', record_shipment=True, items=[
            {'sku': '1111111111116', 'cartons': '3', 'count': '10'},
        ])
        assert resp.status_code == 409
        assert '不能改数量' in resp.body.decode()
        # 原快照保持不动
        assert db.query(ShipmentLine).one().total_quantity == 20
        assert db.query(ShipmentCarton).count() == 2
    finally:
        db.close()
        engine.dispose()
