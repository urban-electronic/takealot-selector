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

def test_buyable_without_local_purchase_enters_and_remains_after_not_buyable():
    with setup() as db:
        offers = [{**OFFERS[1], 'status': 'buyable'}]
        apply_snapshot(db, offers, [], NOW); db.commit()
        assert 'never' in {r['id'] for r in overview(db, DEFAULT_STORE_ID)['rows']}
        apply_snapshot(db, [{**offers[0], 'status': 'not_buyable', 'stock_at_takealot_total': 0}], [], NOW); db.commit()
        row = next(r for r in overview(db, DEFAULT_STORE_ID)['rows'] if r['id'] == 'never')
        assert row['profile']['offers'][0]['status'] == 'not_buyable'
        assert row['profile']['stock'] == 0
        assert row['profile']['ever_buyable_skus'] == ['b']
        coverage = overview(db, DEFAULT_STORE_ID)['coverage']
        assert coverage == dict(products=2, skus=2, buyable=0, formerly_buyable=1, unknown=1)
        assert eligible(db, DEFAULT_STORE_ID, db.get(Product, 'never'))


def test_never_buyable_or_ambiguous_offer_does_not_enroll():
    with setup() as db:
        apply_snapshot(db, [{**OFFERS[1], 'status': 'not_buyable'}], [], NOW); db.commit()
        assert not eligible(db, DEFAULT_STORE_ID, db.get(Product, 'never'))
        db.add(Product(id='duplicate', store_id=DEFAULT_STORE_ID, sku='b')); db.commit()
        apply_snapshot(db, [{**OFFERS[1], 'status': 'buyable'}], [], NOW); db.commit()
        assert not eligible(db, DEFAULT_STORE_ID, db.get(Product, 'never'))


def test_old_buyable_snapshot_is_kept_and_shipping_save_preserves_history():
    from api.operations_routes import Profile, save_profile
    with setup() as db:
        key = f'operations_profile:{DEFAULT_STORE_ID}:never'
        old = {'data_source': 'seller_api', 'offers': [{'sku': 'b', 'status': 'buyable'}]}
        db.add(SystemSettings(key=key, value=json.dumps(old))); db.commit()
        assert eligible(db, DEFAULT_STORE_ID, db.get(Product, 'never'))
        apply_snapshot(db, [{**OFFERS[1], 'status': 'not_buyable'}], [], NOW); db.commit()
        day = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        save_profile('never', Profile(stock=1, stock_date=day, observed_from=day), db, DEFAULT_STORE_ID)
        assert json.loads(db.query(SystemSettings).filter_by(key=key).one().value)['ever_buyable_skus'] == ['b']
        assert not eligible(db, 'other-store', db.get(Product, 'never'))


def test_multi_sku_counts_deduplicate_and_never_guess_prior_buyability():
    with setup() as db:
        p = db.get(Product, 'never'); p.sku = 'b,c'
        db.commit()
        offers = [{**OFFERS[1], 'status': 'buyable'},
                  {'sku': 'c', 'offer_id': 3, 'status': 'not_buyable', 'stock_at_takealot_total': 0}]
        apply_snapshot(db, offers, [], NOW); db.commit()
        summary = overview(db, DEFAULT_STORE_ID)['coverage']
        assert summary == dict(products=2, skus=3, buyable=1, formerly_buyable=0, unknown=2)
        # Changing local identity must not transfer SKU b's former buyability to SKU d.
        p.sku = 'd'; db.commit()
        assert not eligible(db, DEFAULT_STORE_ID, p)
