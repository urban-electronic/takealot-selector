import io
import json
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


def generate_stub(items, date, mark, shipping, address, output, boxes=None):
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


def test_erp_boxes_are_per_carton_and_identifiers_stay_unique_across_shipments(db):
    """入仓模板要按**箱**生成，而且标识号跨多个装箱单连续。

    早先的实现按 ShipmentLine 出行，一行一个标识号：一票 2 箱 × 2 个 SKU
    会被拆成 4 个箱子；勾两票时每票又从 1 开始，两票的箱子在 OMS 里会并到一起。
    """
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        for draft, sku in [('ship-1', 'sku-a'), ('ship-2', 'sku-b')]:
            data = routes.ExportIn(date='2026-10-15', record_shipment=False, draft_key=draft,
                                   shipping='海', items=[routes.ExportLine(sku=sku, cartons='2', count='5')])
            assert routes.export_excel(data, db, 'default-store').status_code == 200
    ids = [s.id for s in db.query(Shipment).order_by(Shipment.shipment_no).all()]
    result = export_inbound_template(InboundExportIn(shipment_ids=ids), db, 'default-store')
    assert result.status_code == 200
    summary = json.loads(result.headers['X-Inbound-Summary'])
    assert summary['cartons'] == 4          # 2 票 × 2 箱
    assert summary['pieces'] == 20          # 2 票 × 2 箱 × 5 件
    assert summary['rows'] == 4
    assert summary['overflow'] == []

    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(io.BytesIO(result.body)) as book:
        root = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))

    def cell(ref):
        node = root.find(f'.//m:c[@r="{ref}"]', ns)
        if node.attrib.get('t') == 'inlineStr':
            return node.find('m:is/m:t', ns).text
        return int(node.find('m:v', ns).text)

    assert [cell(f'A{r}') for r in range(2, 6)] == [1, 2, 3, 4]
    assert [cell(f'I{r}') for r in range(2, 6)] == ['sku-a', 'sku-a', 'sku-b', 'sku-b']


def test_erp_warns_when_a_box_exceeds_the_label_rows(db):
    """一箱超过 5 个 SKU 时 OMS 箱唛会多印一页且那页没有箱号，接口要把这几箱报出来。"""
    from models import ShipmentCarton, ShipmentCartonItem
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        routes.export_excel(payload('wide', sku='sku-a'), db, 'default-store')
    shipment = db.query(Shipment).one()
    carton = db.query(ShipmentCarton).filter(ShipmentCarton.shipment_id == shipment.id).first()
    db.query(ShipmentCartonItem).filter(ShipmentCartonItem.carton_id == carton.id).delete()
    for n in range(6):
        db.add(ShipmentCartonItem(id=f'item-{n}', carton_id=carton.id, sku=f'99000000000{n}', quantity=1))
    db.commit()
    result = export_inbound_template(InboundExportIn(shipment_ids=[shipment.id]), db, 'default-store')
    summary = json.loads(result.headers['X-Inbound-Summary'])
    assert summary['cartons'] == 1 and summary['rows'] == 6
    assert summary['overflow'] == [{'box_no': carton.box_no, 'skus': 6, 'overflow': 1}]


def test_erp_rejects_mixed_sea_air(db):
    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=generate_stub):
        routes.export_excel(payload('sea'), db, 'default-store')
        routes.export_excel(payload('air', shipping='空'), db, 'default-store')
    with pytest.raises(HTTPException) as error:
        export_inbound_template(InboundExportIn(shipment_ids=[s.id for s in db.query(Shipment).all()]), db, 'default-store')
    assert error.value.status_code == 400
    assert '海运和空运' in error.value.detail


def test_only_required_columns_are_written(tmp_path):
    """只写模板里带 `*` 的列，C 列「自定义箱号」留空。

    标识号已经说明是哪个箱子了，再填一遍纯属重复；而且填进去的号跟 OMS 生成的
    箱唛号（`INH1501522-001`）不是一回事，容易看混。
    """
    from services.inbound_excel import TEMPLATE, SHEET
    dest = tmp_path / 'inbound.xlsx'
    generate_inbound([(1, [('0012345678901', 3)]),
                      (2, [('9999999999999', 7)])], dest)
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(TEMPLATE) as original, ZipFile(dest) as final:
        for name in original.namelist():
            if name != SHEET:
                assert original.read(name) == final.read(name)
        root = ET.fromstring(final.read(SHEET))
    assert root.find('.//m:c[@r="C2"]', ns) is None
    assert root.find('.//m:c[@r="C3"]', ns) is None
    assert root.find('.//m:c[@r="I2"]/m:is/m:t', ns).text == '0012345678901'
    assert root.find('.//m:c[@r="A2"]/m:v', ns).text == '1'
    assert root.find('.//m:c[@r="B2"]/m:v', ns).text == '1'
    assert root.find('.//m:c[@r="J2"]/m:v', ns).text == '3'


def test_mixed_box_shares_one_identifier(tmp_path):
    """混装箱：同一箱的多个 SKU 必须共用标识号，否则 OMS 会把它拆成好几个箱子。

    这是 OMS 的认箱规则（标识号相同 = 同一个箱子），箱唛上那张
    「SKU / 单箱数量」表就是按标识号分组印出来的。
    """
    from services.inbound_excel import SHEET, generate_inbound
    dest = tmp_path / 'inbound.xlsx'
    generate_inbound([
        (1, [('sku-a', 17), ('sku-b', 6)]),
        (2, [('sku-c', 5)]),
        (3, [('sku-d', 16), ('sku-e', 31), ('sku-f', 15)]),
    ], dest)
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(dest) as final:
        root = ET.fromstring(final.read(SHEET))

    def text(ref):
        cell = root.find(f'.//m:c[@r="{ref}"]', ns)
        if cell.attrib.get('t') == 'inlineStr':
            return cell.find('m:is/m:t', ns).text
        return int(cell.find('m:v', ns).text)

    # 6 行明细：箱 1 两行、箱 2 一行、箱 3 三行
    assert [text(f'A{r}') for r in range(2, 8)] == [1, 1, 2, 3, 3, 3]
    assert [text(f'I{r}') for r in range(2, 8)] == ['sku-a', 'sku-b', 'sku-c', 'sku-d', 'sku-e', 'sku-f']
    assert [text(f'J{r}') for r in range(2, 8)] == [17, 6, 5, 16, 31, 15]
    assert all(text(f'B{r}') == 1 for r in range(2, 8))
    # C 列（自定义箱号）不需要，一行都不该有
    assert all(root.find(f'.//m:c[@r="C{r}"]', ns) is None for r in range(2, 8))


def _sheet_cells(path):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as book:
        root = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))

    def read(ref):
        node = root.find(f'.//m:c[@r="{ref}"]', ns)
        if node is None:
            return None
        if node.attrib.get('t') == 'inlineStr':
            text = node.find('m:is/m:t', ns)
            return None if text is None else text.text
        value = node.find('m:v', ns)
        return None if value is None else value.text

    return read


def test_packing_list_expands_by_carton_and_writes_box_number(tmp_path):
    """装箱单要按箱展开，A 列（箱唛）只写在每箱第一行。

    照抄鲲鹏那份 `KPLDJ_海运装箱单_8箱.xlsx`：一箱混装 2 个 SKU 时，
    第 1 行写 INH1501522-001 + 箱数 1，第 2 行箱唛和箱数都留空。
    每行都写 1 的话物流会把箱数累加成 2 箱。
    """
    from services import packing_excel as px
    product = dict(sku='9902367061100', name_zh='皮革黑色包', name_en='Leather bag', unit='个',
                   weight='0.3', material='皮革', brand='', battery='', electric='否',
                   magnetic='否', image_file='', template_row=4)
    other = dict(product, sku='9902388560071', name_zh='漏斗', name_en='Funnel', template_row=5)
    dest = tmp_path / 'packing.xlsx'
    px.generate(
        [(product, '1', '17'), (other, '1', '6')], '2026-10-23', 'CK-01', '海', '约堡仓', dest,
        boxes=[('INH1501522-001', [('9902367061100', '17'), ('9902388560071', '6')]),
               ('INH1501522-002', [('9902388560071', '5')])],
    )
    read = _sheet_cells(dest)
    assert read('A4') == 'INH1501522-001'
    assert read('D4') == '1'
    assert read('E4') == '17'
    assert read('B5') == '9902388560071'
    assert read('A5') in (None, '')      # 同一箱的后续行不重复写箱唛号
    assert read('D5') in (None, '')      # 箱数只在第一行算一次
    assert read('E5') == '6'
    assert read('A6') == 'INH1501522-002'
    assert read('D6') == '1'


def test_packing_list_without_cartons_keeps_sku_aggregate(tmp_path):
    """没有逐箱清单时保持原样：一个 SKU 一行，D 列写箱数、E 列写每箱件数。"""
    from services import packing_excel as px
    product = dict(sku='9902367061100', name_zh='皮革黑色包', name_en='Leather bag', unit='个',
                   weight='0.3', material='皮革', brand='', battery='', electric='否',
                   magnetic='否', image_file='', template_row=4)
    dest = tmp_path / 'packing.xlsx'
    px.generate([(product, '3', '10')], '2026-10-23', 'CK-01', '海', '', dest)
    read = _sheet_cells(dest)
    assert read('A4') == 'CK-01'         # 没有箱号时退回唛头
    assert read('D4') == '3'
    assert read('E4') == '10'


def test_mark_falls_back_to_inbound_no_then_kpl_dj(db):
    """唛头留空就用 OMS 入库单号，都没有才退回 KPLDJ。

    装箱单模板里「客户唛头」（B2）和 A 列「箱唛」是同一套编号：用户只填一次
    入库单号，两处就该自动一致，不用记着再抄一遍。手填的唛头优先级最高。
    """
    seen = []

    def stub(items, date, mark, shipping, address, output, boxes=None):
        seen.append(mark)
        output.write_bytes(b'packing-workbook')

    def payload(draft, **extra):
        return routes.ExportIn(date='2026-10-15', draft_key=draft, shipping='海运',
                               items=[routes.ExportLine(sku='sku-a', cartons='1', count='10')], **extra)

    with patch.object(routes, '_hydrate_export_product'), patch.object(routes.px, 'generate', side_effect=stub):
        assert routes.export_excel(payload('m-inbound', inbound_no='INH1501522'), db, 'default-store').status_code == 200
        assert routes.export_excel(payload('m-none'), db, 'default-store').status_code == 200
        assert routes.export_excel(payload('m-manual', inbound_no='INH1501522', mark='CK-01'), db, 'default-store').status_code == 200
    assert seen == ['INH1501522', 'KPLDJ', 'CK-01']
    assert [s.mark for s in db.query(Shipment).order_by(Shipment.draft_key).all()] == ['INH1501522', 'CK-01', 'KPLDJ']
