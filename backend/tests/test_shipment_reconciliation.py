import datetime as dt
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from models import Base, Store, Product, PackingProduct, InventoryAdjustment, Shipment, ShipmentLine, ShipmentAllocation
from services.shipment_reconciliation import ReconcileIn, preview, apply
from api.inventory_routes import inventory_balance_for_sku, list_inventory, list_shipments, void_shipment, VoidIn


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Store(id='s', name='Test'))
        for sku, count in [('a', 10), ('b', 0)]:
            db.add(Product(id=sku, store_id='s', sku=sku, is_archived=False))
            db.add(PackingProduct(sku=sku, name_zh=sku))
            db.add(InventoryAdjustment(store_id='s', sku=sku, quantity_delta=count, occurred_at='2026-10-01'))
        old = Shipment(id='old', store_id='s', draft_key='old', shipment_no='old', shipment_date='2026-10-07', status='confirmed')
        db.add(old); db.flush()
        db.add(ShipmentLine(shipment_id='old', sku='a', total_quantity=15))
        db.commit()
        yield db
    engine.dispose()


def payload(**kw):
    return ReconcileIn(lines=[dict(date='2026-10-07', shipping='海运', box='1', sku='a', quantity=14),
                              dict(date='2026-10-07', shipping='海运', box='1', sku='b', quantity=3),
                              dict(date='2026-10-15', shipping='空运', box='2', sku='a', quantity=2)], replace_ids=['old'], **kw)


def test_preview_does_not_mutate_and_apply_replaces_without_negative_stock(db):
    p = payload(); plan = preview(db, 's', p)
    assert (plan['total_quantity'],plan['domestic_quantity'],plan['legacy_quantity'],plan['boxes']) == (19,10,9,2)
    assert inventory_balance_for_sku(db, 'a', 's') == -5
    assert db.get(Shipment, 'old').status == 'confirmed'
    result = apply(db, 's', payload(token=plan['token']))
    assert result['total_quantity'] == 19
    assert db.get(Shipment, 'old').status == 'void'
    assert inventory_balance_for_sku(db, 'a', 's') == 0
    assert inventory_balance_for_sku(db, 'b', 's') == 0
    rows = {r['sku']:r for r in list_inventory(db,'s')}
    assert rows['a']['overseas_in_transit'] == 16
    assert rows['b']['overseas_in_transit'] == 3
    assert rows['a']['in_transit'] == 0
    assert rows['a']['shipped'] == 10
    count = db.query(Shipment).count()
    assert apply(db, 's', payload(token=plan['token']))['replayed']
    assert db.query(Shipment).count() == count
    target = next(s for s in list_shipments(db,'s') if s['status']=='transit' and s['shipment_date']=='2026-10-07')
    void_shipment(target['id'], VoidIn(reason='撤回补登'), db, 's')
    assert inventory_balance_for_sku(db, 'a', 's') == 10
    assert next(r for r in list_inventory(db,'s') if r['sku']=='a')['overseas_in_transit'] == 2


def test_stale_preview_and_cross_store_rejected_without_changes(db):
    plan = preview(db,'s',payload())
    db.add(InventoryAdjustment(store_id='s',sku='a',quantity_delta=1,occurred_at='2026-10-16')); db.commit()
    with pytest.raises(HTTPException) as err: apply(db,'s',payload(token=plan['token']))
    assert err.value.status_code == 409
    assert db.get(Shipment,'old').status == 'confirmed'
    assert db.query(ShipmentAllocation).count() == 0
    with pytest.raises(HTTPException): preview(db,'other',payload())


def test_future_date_and_duplicate_lines_rejected(db):
    p = payload(); p.lines[0].date = (dt.date.today()+dt.timedelta(days=2)).isoformat()
    with pytest.raises(HTTPException): preview(db,'s',p)
    p = payload(); p.lines.append(p.lines[0])
    with pytest.raises(HTTPException): preview(db,'s',p)


def test_second_manifest_cannot_reuse_consumed_stock(db):
    p=payload(); p.token=preview(db,'s',p)['token']; apply(db,'s',p)
    another=ReconcileIn(lines=[dict(date='2026-10-16',shipping='海运',box='other',sku='a',quantity=5)])
    plan=preview(db,'s',another)
    assert plan['domestic_quantity']==0 and plan['legacy_quantity']==5


def test_planning_reads_actual_transit_and_void_removes_it(db):
    from services.shipment_planning import with_shipments
    from services.operations_planner import plan
    p=payload(); p.token=preview(db,'s',p)['token']; apply(db,'s',p)
    profile=dict(stock=0,stock_date='2026-10-16',observed_from='2026-10-01',sales=[],air_days=17,sea_days=58,cycle_days=30,safety_days=7,allocations=[])
    product=db.get(Product,'a')
    enriched, purchases=with_shipments(db,'s',product,profile,[])
    assert profile['allocations']==[]
    r=plan(enriched,purchases,dt.date(2026,10,16))
    assert r['stock']==0 and r['pending']==dict(air=2,sea=14)
    for s in list_shipments(db,'s'):
        if s['status']=='transit': void_shipment(s['id'],VoidIn(reason='rollback'),db,'s')
    enriched, purchases=with_shipments(db,'s',product,profile,[])
    assert not purchases
    with pytest.raises(HTTPException): apply(db,'s',p)


def test_executable_http_preview_apply_and_readback(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from database import get_db
    from api.store_routes import get_store_id
    from api.inventory_routes import router
    engine=create_engine(f'sqlite:///{tmp_path}/runtime.db',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Store(id='s',name='runtime'))
        session.add(Product(id='runtime',store_id='s',sku='runtime',is_archived=False))
        session.add(PackingProduct(sku='runtime',name_zh='Runtime'))
        session.add(InventoryAdjustment(store_id='s',sku='runtime',quantity_delta=4,occurred_at='2026-10-01'))
        session.commit()
    app=FastAPI(); app.include_router(router)
    def session_override():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_db]=session_override
    app.dependency_overrides[get_store_id]=lambda:'s'
    client=TestClient(app)
    body=dict(lines=[dict(date='2026-10-07',shipping='海运',box='1',sku='runtime',quantity=9)])
    before=client.get('/api/inventory').json()[0]
    assert before['available']==4 and before['overseas_in_transit']==0
    response=client.post('/api/inventory/reconciliation/preview',json=body)
    assert response.status_code==200
    body['token']=response.json()['token']
    response=client.post('/api/inventory/reconciliation/apply',json=body)
    assert response.status_code==200
    assert response.json()['domestic_quantity']==4 and response.json()['legacy_quantity']==5
    after=client.get('/api/inventory').json()[0]
    assert after['available']==0 and after['overseas_in_transit']==9 and after['in_transit']==0
    assert client.post('/api/inventory/reconciliation/apply',json=body).json()['replayed']
    assert len(client.get('/api/inventory/shipments').json())==1
    engine.dispose()
