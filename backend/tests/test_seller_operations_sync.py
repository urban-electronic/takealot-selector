import json
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from database import Base
from models import Product, ProcurementRecord, SystemSettings, DEFAULT_STORE_ID
from services.seller_operations_sync import apply_snapshot, read_pages, eligible
from api.operations_routes import overview, get_profile

NOW = datetime(2026, 10, 13, 20, tzinfo=ZoneInfo('Asia/Shanghai'))

def setup():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([Product(id='bought', store_id=DEFAULT_STORE_ID, sku='a', link_status='已购买'),
                Product(id='never', store_id=DEFAULT_STORE_ID, sku='b', link_status='未购买'),
                Product(id='other', store_id='other-store', sku='a', link_status='已购买')])
    db.commit(); return db

OFFERS = [{'sku': 'a', 'offer_id': 1, 'stock_at_takealot_total': 8, 'date_created': '11 Oct 2026 10:00:00'},
          {'sku': 'b', 'offer_id': 2, 'stock_at_takealot_total': 50}]
SALES = [{'order_item_id': 1, 'sku': 'a', 'order_date': '12 Oct 2026 15:00:00', 'quantity': 5},
         {'order_item_id': 2, 'sku': 'a', 'order_date': '12 Oct 2026 15:00:00', 'quantity': 100, 'sale_status': 'Cancelled'}]

def test_actual_sales_dedup_cancelled_exclusion_unpurchased_and_store_boundary():
    with setup() as db:
        result = apply_snapshot(db, OFFERS, SALES + [SALES[0]], NOW); db.commit()
        assert result['updated_products'] == 1
        rows = db.query(SystemSettings).all(); assert len(rows) == 1
        p = json.loads(rows[0].value)
        assert p['stock'] == 8 and p['sales'] == [{'date': '2026-10-12', 'quantity': 5}]
        assert p['observed_from'] == '2026-10-11' and p['data_source'] == 'seller_api'
        apply_snapshot(db, OFFERS, SALES, NOW); db.commit()
        assert db.query(SystemSettings).count() == 1
        assert [r['id'] for r in overview(db, DEFAULT_STORE_ID)['rows']] == ['bought']
        assert get_profile('never', db, DEFAULT_STORE_ID)['plan'] is None

def test_sync_preserves_shipping_and_never_modifies_procurement_or_price():
    with setup() as db:
        db.add(SystemSettings(key=f'operations_profile:{DEFAULT_STORE_ID}:bought', value=json.dumps({'air_days': 12, 'sea_days': 40, 'safety_days': 10, 'cycle_days': 30, 'group': 'cable', 'related_groups': [], 'allocations': []})))
        db.commit(); apply_snapshot(db, OFFERS, SALES, NOW); db.commit()
        p = json.loads(db.query(SystemSettings).first().value)
        assert p['air_days'] == 12 and p['group'] == 'cable'
        assert db.get(Product, 'bought').actual_sale_price_zar is None
        assert db.query(ProcurementRecord).count() == 0

def test_ambiguous_sku_and_invalid_stock_never_used():
    with setup() as db:
        db.add(Product(id='duplicate', store_id=DEFAULT_STORE_ID, sku='a', link_status='已购买')); db.commit()
        result = apply_snapshot(db, OFFERS, SALES, NOW)
        assert result['updated_products'] == 0 and result['unmatched_products'] == 2
    with setup() as db:
        with pytest.raises(ValueError): apply_snapshot(db, [{**OFFERS[0], 'stock_at_takealot_total': None}], SALES, NOW)
        db.rollback(); assert db.query(SystemSettings).count() == 0

def test_never_bought_with_cancelled_purchase_does_not_become_eligible():
    with setup() as db:
        p = db.get(Product, 'never')
        db.add(ProcurementRecord(id='cancelled', store_id=DEFAULT_STORE_ID, product_id=p.id, quantity=5, status='cancelled')); db.commit()
        assert not eligible(db, DEFAULT_STORE_ID, p)
