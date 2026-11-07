import json
import time
from datetime import date

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Store
from services import seller_ledger as ledger
from api.seller_ledger_routes import listing


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id='default-store', name='Fixture'))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def sale(**kw):
    return dict(order_item_id=1, order_id=10, offer_id=20, sku='SKU-BLUE',
                order_date='2026-11-01T01:00:00+02:00', quantity=2,
                sale_status='Preparing for Customer', selling_price=100,
                total_fees=None, **kw)


def shipment():
    return dict(shipment_id=77, reference='Fixture', shipped=True, cancelled=False,
                shipment_items=[dict(shipment_item_id=9, offer_id=20, quantity_sending=5,
                                     purchase_order_quantity_received=3, purchase_order_quantity_damaged=1)])


def client(monkeypatch, pages, seller_id=123):
    calls = []
    original = httpx.Client
    iterator = iter(pages)

    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.host == 'marketplace-api.takealot.com'
        if request.url.path.endswith('/seller'):
            return httpx.Response(200, json={'seller_id': seller_id})
        return httpx.Response(200, json=next(iterator))

    monkeypatch.setattr(ledger.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(ledger, 'configured_key', lambda db: 'fixture-only')
    return calls


def sync_sales(db):
    return ledger.sync(db, 'default-store', 'sales', date(2026, 11, 1), date(2026, 11, 2))


def test_complete_token_pagination_and_snapshot_readback(db, monkeypatch):
    second = sale(); second['order_item_id'] = 2
    calls = client(monkeypatch, [{'items': [sale()], 'count': 2, 'continuation_token': 'opaque/+='}, {'items': [second]}])
    result = sync_sales(db)
    assert result['count'] == 2
    assert dict(calls[-1].url.params) == {'continuation_token': 'opaque/+='}
    assert calls[1].url.params['order_date__gte'] == '2026-11-01'
    row = listing(db, 'default-store', 'sales', 1, 1, 'BLUE')
    assert row['total'] == 2 and len(row['items']) == 1
    assert row['snapshot']['complete'] and row['snapshot']['seller_id'] == '123'
    assert row['items'][0]['business_date'] == '2026-11-01'
    assert listing(db, 'other-store', 'sales', 1, 20, '')['snapshot'] is None


@pytest.mark.parametrize('pages', [
    [{'items': [sale()], 'count': 2}],
    [{'items': [sale()], 'continuation_token': 'x'}, {'items': [sale()]}],
    [{'items': [sale()], 'continuation_token': 'x'}, {'items': [], 'continuation_token': 'x'}],
    [{'unexpected': []}],
    *[[{'items': [sale()], 'continuation_token': token}] for token in (0, False, [], {})],
])
def test_partial_or_duplicate_data_never_overwrites_snapshot(db, monkeypatch, pages):
    client(monkeypatch, [{'items': [sale()], 'count': 1}]); sync_sales(db)
    old = db.query(ledger.SellerLedgerSnapshot).one().payload
    client(monkeypatch, pages)
    with pytest.raises(HTTPException) as exc:
        sync_sales(db)
    assert exc.value.status_code == 502
    assert db.query(ledger.SellerLedgerSnapshot).one().payload == old
    assert db.query(ledger.SellerLedgerSnapshot).one().version == 1


@pytest.mark.parametrize('field,value', [('sku', ''), ('quantity', True), ('quantity', 1.5),
                                       ('order_item_id', None), ('offer_id', None),
                                       ('order_date', '2026-11-01T00:00:00')])
def test_invalid_identity_or_values_are_rejected(db, monkeypatch, field, value):
    row = sale(); row[field] = value
    client(monkeypatch, [{'items': [row]}])
    with pytest.raises(HTTPException):
        sync_sales(db)
    assert db.query(ledger.SellerLedgerSnapshot).count() == 0


def test_account_switch_is_rejected_across_resources(db, monkeypatch):
    client(monkeypatch, [{'items': [sale()]}]); sync_sales(db)
    client(monkeypatch, [{'items': [shipment()]}], seller_id=456)
    with pytest.raises(HTTPException):
        ledger.sync(db, 'default-store', 'shipments')
    assert db.query(ledger.SellerLedgerSnapshot).count() == 1


def test_unconfigured_store_never_uses_default_credentials(db, monkeypatch):
    monkeypatch.setattr(ledger, 'configured_key', lambda db: pytest.fail('must not read another store key'))
    with pytest.raises(HTTPException) as exc:
        ledger.sync(db, 'another-store', 'sales')
    assert exc.value.status_code == 409


def test_receipt_evidence_does_not_change_stock_or_procurement(db, monkeypatch):
    from models import InventoryAdjustment, ProcurementRecord, Shipment
    client(monkeypatch, [{'items': [shipment()], 'count': 1}])
    ledger.sync(db, 'default-store', 'shipments')
    result = listing(db, 'default-store', 'shipments', 1, 20, '')
    assert result['items'][0]['items'][0]['quantity_received'] == 3
    assert result['items'][0]['items'][0]['quantity_damaged'] == 1
    assert db.query(InventoryAdjustment).count() == 0
    assert db.query(ProcurementRecord).count() == 0
    assert db.query(Shipment).count() == 0


def test_empty_complete_snapshot_is_distinct_from_never_synced(db, monkeypatch):
    assert listing(db, 'default-store', 'sales', 1, 20, '')['snapshot'] is None
    client(monkeypatch, [{'items': [], 'count': 0}]); sync_sales(db)
    result = listing(db, 'default-store', 'sales', 1, 20, '')
    assert result['items'] == [] and result['snapshot']['complete']


def test_bad_upstream_date_is_not_reflected_in_error(db, monkeypatch):
    row = sale(); row['order_date'] = 'private-upstream-content'
    client(monkeypatch, [{'items': [row]}])
    with pytest.raises(HTTPException) as exc:
        sync_sales(db)
    assert 'private-upstream-content' not in exc.value.detail


def test_budget_exhaustion_makes_no_request():
    class Never:
        def get(self, *args, **kwargs):
            pytest.fail('no request after deadline')
    with pytest.raises(ledger.LedgerError):
        ledger.read_complete(Never(), 'fixture', 'sales', {}, time.monotonic() - 1)
