from datetime import date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
import pytest
from database import Base
from models import Product, ProcurementRecord, SystemSettings
from api.operations_routes import Profile, save_profile, get_profile, alerts
from api.settings_routes import get_settings, update_settings

def test_saved_plan_store_isolation_procurement_and_recommendations():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        for id, store in [('p', 'a'), ('same', 'a'), ('related', 'a'), ('foreign', 'b')]:
            db.add(Product(id=id, store_id=store, product_name=id, link_status='已购买', actual_sale_price_zar=200,
                           minimum_price_at_20_margin=150, profit_margin=.3, selection_status='合格选品'))
        db.add(ProcurementRecord(id='purchase', store_id='a', product_id='p', quantity=10000, status='in_transit'))
        db.add(ProcurementRecord(id='foreign-purchase', store_id='b', product_id='foreign', quantity=10000, status='in_transit'))
        db.commit()
        day = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        payload = dict(stock=0, stock_date=day, observed_from=day, sales=[{'date': day, 'quantity': 5}], group='cable', related_groups=['charger'])
        save_profile('same', Profile(**payload), db, 'a')
        save_profile('related', Profile(**{**payload, 'group': 'charger'}), db, 'a')
        r = save_profile('p', Profile(**payload), db, 'a')
        assert [p['id'] for p in r['recommendations']['same_category']] == ['same']
        assert [p['id'] for p in r['recommendations']['related']] == ['related']
        with pytest.raises(HTTPException): get_profile('p', db, 'b')
        with pytest.raises(HTTPException): save_profile('p', Profile(**{**payload, 'allocations': [{'procurement_id': 'foreign-purchase', 'mode': 'air', 'eta': day}]}), db, 'a')
        assert not any(k.startswith('operations_profile:') for k in get_settings(db))
        with pytest.raises(HTTPException): update_settings({'operations_profile:a:p': '{}'}, db)
        r = save_profile('p', Profile(**{**payload, 'allocations': [{'procurement_id': 'purchase', 'mode': 'air', 'eta': day}]}), db, 'a')
        assert r['plan']['air_quantity'] == r['plan']['sea_quantity'] == 0
        assert not any(a['product_id'] == 'p' for a in alerts(db, 'a'))
        purchase = db.get(ProcurementRecord, 'purchase'); purchase.status = 'cancelled'; db.commit()
        assert any(a['product_id'] == 'p' for a in alerts(db, 'a'))
        assert alerts(db, 'b') == []

def test_split_receipts_cannot_exceed_the_single_purchase_quantity():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)()as db:
        db.add(Product(id='p',store_id='a',product_name='p',link_status='已购买'))
        db.add(ProcurementRecord(id='order',store_id='a',product_id='p',quantity=100,status='in_transit'));db.commit()
        day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        payload=dict(stock=0,stock_date=day,observed_from=day,sales=[],allocations=[
          {'procurement_id':'order','mode':'air','quantity':20,'eta':day},
          {'procurement_id':'order','mode':'sea','quantity':80,'eta':day}])
        result=save_profile('p',Profile(**payload),db,'a')
        assert len(result['profile']['allocations'])==2
        payload['allocations'][1]['quantity']=81
        with pytest.raises(HTTPException):save_profile('p',Profile(**payload),db,'a')
        assert sum(a['quantity']for a in get_profile('p',db,'a')['profile']['allocations'])==100
