"""Native collector task-claiming fixes (PR #94 follow-up):

1. Autonomous continuous mode: when the local native collector sends
   `X-Collector-Autonomous: 1`, `products()` may claim catalog SKUs that are
   merely "due and not paused" — including SKUs never enabled/request_once in the
   web UI. This is what makes the Windows native collector actually collect
   instead of always claiming 0 tasks. Only the `local_collector` client may
   trigger it (the browser extension must keep opt-in semantics).

2. `web_open` (网页「立即采集」) no longer drops SKUs whose
   `operations_profile.offers[].stock` is missing/non-numeric. A missing stock
   field used to make web-open skip every SKU, so the native collector never
   received tasks. Collection is read-only and stock is re-read server-side.

Run with: pytest backend/tests/test_collector_autonomous.py
"""
import json
from api import collector_routes as bridge
from models import DEFAULT_STORE_ID, SystemSettings
from tests.test_pricing_routes import database
from services.buybox_monitor import PREFIX


def test_autonomous_claims_unconfigured_catalog_skus():
    with database() as db:
        # No SKU has been enabled/request_once in the web UI.
        assert bridge.products(db, DEFAULT_STORE_ID)['items'] == []
        # Autonomous mode (local_collector only) must surface the unconfigured
        # catalog SKUs so the native collector can actually collect them.
        items = bridge.products(db, DEFAULT_STORE_ID,
                                x_collector_client='local_collector',
                                x_autonomous='1')['items']
        assert {i['sku'] for i in items} == {'one', 'two'}


def test_autonomous_header_ignored_for_non_local_client():
    with database() as db:
        # The browser extension must keep opt-in semantics; the autonomous flag
        # must not relax claiming for any client other than local_collector.
        items = bridge.products(db, DEFAULT_STORE_ID,
                                x_collector_client='browser_extension',
                                x_autonomous='1')['items']
        assert items == []


def test_web_open_queues_items_with_missing_stock():
    with database() as db:
        # Simulate operations_profile.offers[].stock missing (never synced).
        key = f'operations_profile:{DEFAULT_STORE_ID}:one'
        prof = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        prof['offers'][0].pop('stock', None)
        db.query(SystemSettings).filter_by(key=key).first().value = json.dumps(prof)
        db.commit()
        res = bridge.web_open(bridge.WebOpen(initial=True), db, DEFAULT_STORE_ID)
        assert res['queued'] >= 1
        row = json.loads(db.query(SystemSettings).filter_by(
            key=PREFIX + DEFAULT_STORE_ID + ':one:one').first().value)
        assert row['request_once'] is True and row['web_requested'] is True
