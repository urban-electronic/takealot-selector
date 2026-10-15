from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api import packing_routes as routes
from api.inventory_routes import (
    InboundExportIn, VoidIn, export_inbound_template, inventory_balance_for_sku, void_shipment,
)
from models import Base, InventoryAdjustment, PackingProduct, Product, Shipment, ShipmentLine
from services.inbound_excel import generate_inbound


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for sku, quantity in [('sku-a', 3), ('sku-b', 25)]:
            session.add(Product(id=sku, store_id='default-store', sku=sku, is_archived=False))
            session.add(PackingProduct(sku=sku, name_zh=sku))
            session.add(InventoryAdjustment(sku=sku, store_id='default-store', quantity_delta=quantity,
                                            occurred_at='2026-10-15'))
        session.commit()
        yield session
    engine.dispose()


def payload(draft='export-test', record=False, sku='sku-a', count='10', shipping='海'):
    return routes.ExportIn(date='2026-10-15', record_shipment=record, draft_key=draft,
                           shipping=shipping, items=[routes.ExportLine(sku=sku, cartons='1', count=count)])


def generate_stub(items, date, mark, shipping, address, output):
    output.write_bytes(b'packing-workbook')


def test_export_keeps_snapshot_without_deducting_and_retry_does_not_duplicate(db):
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        assert routes.export_excel(payload(), db, 'default-store').status_code == 200
        assert routes.export_excel(payload(), db, 'default-store').status_code == 200
        assert db.query(Shipment).count() == 1
        assert db.query(ShipmentLine).count() == 1
        assert db.query(Shipment).one().status == 'exported'
        assert db.query(ShipmentLine).one().total_quantity == 10
        assert inventory_balance_for_sku(db, 'sku-a', 'default-store') == 3
        assert routes.export_excel(payload('second-export'), db, 'default-store').status_code == 200
        assert db.query(Shipment).count() == 2
        assert inventory_balance_for_sku(db, 'sku-a', 'default-store') == 3


def test_only_explicit_shipment_checks_and_deducts_its_sku(db):
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        assert routes.export_excel(payload(record=True), db, 'default-store').status_code == 400
        assert db.query(Shipment).count() == 0
        data = payload('ship-b', record=True, sku='sku-b', count='5')
        assert routes.export_excel(data, db, 'default-store').status_code == 200
        assert routes.export_excel(data, db, 'default-store').status_code == 200
        assert inventory_balance_for_sku(db, 'sku-b', 'default-store') == 20
        assert inventory_balance_for_sku(db, 'sku-a', 'default-store') == 3


def test_export_snapshot_can_generate_erp_and_void_without_stock_changes(db):
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        routes.export_excel(payload(), db, 'default-store')
    shipment = db.query(Shipment).one()
    result = export_inbound_template(InboundExportIn(shipment_ids=[shipment.id]), db, 'default-store')
    assert result.status_code == 200 and result.body.startswith(b'PK')
    assert inventory_balance_for_sku(db, 'sku-a', 'default-store') == 3
    void_shipment(shipment.id, VoidIn(), db, 'default-store')
    assert inventory_balance_for_sku(db, 'sku-a', 'default-store') == 3
    with pytest.raises(HTTPException):
        export_inbound_template(InboundExportIn(shipment_ids=[shipment.id]), db, 'default-store')


def test_erp_rejects_mixed_sea_air(db):
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        routes.export_excel(payload('sea'), db, 'default-store')
        routes.export_excel(payload('air', shipping='空'), db, 'default-store')
    with pytest.raises(HTTPException) as error:
        export_inbound_template(InboundExportIn(shipment_ids=[s.id for s in db.query(Shipment).all()]), db, 'default-store')
    assert error.value.status_code == 400
    assert '海运和空运' in error.value.detail


def test_box_number_is_text_with_two_characters_and_template_preserved(tmp_path):
    from services.inbound_excel import TEMPLATE, SHEET
    dest = tmp_path / 'inbound.xlsx'
    generate_inbound([(1, SimpleNamespace(sku='0012345678901', total_quantity=3)),
                      (2, SimpleNamespace(sku='9999999999999', total_quantity=7))], dest)
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(TEMPLATE) as original, ZipFile(dest) as final:
        for name in original.namelist():
            if name != SHEET:
                assert original.read(name) == final.read(name)
        root = ET.fromstring(final.read(SHEET))
    for row, number in [(2, '01'), (3, '02')]:
        cell = root.find(f'.//m:c[@r="C{row}"]', ns)
        assert cell.attrib['t'] == 'inlineStr'
        assert cell.find('m:is/m:t', ns).text == number
    assert root.find('.//m:c[@r="I2"]/m:is/m:t', ns).text == '0012345678901'
