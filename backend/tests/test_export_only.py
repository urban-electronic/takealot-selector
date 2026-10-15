import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from models import Base, Shipment, ShipmentLine, SystemSettings, InventoryAdjustment
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_old_stock_export_records_once_without_deducting_inventory():
    from api import packing_routes as routes
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine)
    data = routes.ExportIn(date='2026-10-08', record_shipment=False, draft_key='clearance-test-draft',
                           items=[routes.ExportLine(sku='real-sku', box_no='BOX-001', cartons='1', count='10')])
    def generate(items, date, mark, shipping, address, output):
        assert items[0][0]['box_no'] == 'BOX-001'
        output.write_bytes(b'original-template-export')
    with patch.object(routes, '_find', return_value=SimpleNamespace(sku='real-sku')), \
         patch.object(routes, '_catalog_product_for_sku', return_value=object()), \
         patch.object(routes, '_hydrate_export_product'), \
         patch.object(routes, 'public', return_value={'sku': 'real-sku'}), \
         patch.object(routes.px, 'generate', side_effect=generate), \
         patch.object(routes, 'inventory_balance_for_sku') as balance, \
         patch.object(routes, 'record_operation') as record:
        response = routes.export_excel(data, db, 'default-store')
        assert response.body == b'original-template-export'
        shipment = db.query(Shipment).one()
        assert shipment.status == 'clearance'
        assert db.query(ShipmentLine).one().total_quantity == 10
        assert db.query(SystemSettings).filter_by(key='old_inventory_clearance_used:default-store').one().value == '1'
        assert db.query(InventoryAdjustment).count() == 0
        assert record.call_args.args[4] == 'clearance_export'
        assert record.call_args.args[6]['inventory_deducted'] is False
        assert routes.export_excel(data, db, 'default-store').status_code == 200
        assert db.query(Shipment).count() == 1
        duplicate = data.model_copy(update={'draft_key': 'another-clearance-draft'})
        assert routes.export_excel(duplicate, db, 'default-store').status_code == 409
        assert db.query(Shipment).count() == 1
        balance.assert_not_called()
    db.close()
    engine.dispose()
    assert routes.ExportIn().record_shipment is True
