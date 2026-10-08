import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_old_stock_export_does_not_check_or_change_inventory():
    from api import packing_routes as routes
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(allow_negative_inventory_shipments=False)
    data = routes.ExportIn(date='2026-10-08', record_shipment=False,
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
    balance.assert_not_called()
    db.add.assert_not_called()
    record.assert_not_called()
    assert routes.ExportIn().record_shipment is True
