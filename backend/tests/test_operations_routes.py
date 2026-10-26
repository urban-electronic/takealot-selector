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


def test_overview_and_alerts_do_not_check_eligibility_once_per_product():
    """补货列表的 SQL 条数不能随商品数增长（ELIGIBLE-N1）。

    overview/alerts 原来在商品循环里调 eligible()，一个商品一条查询（本地库 329
    个商品时分别是 318 / 317 条），而这两个接口都是补货页的首屏请求。

    这里用 3 个和 60 个商品各跑一次比条数；同时断言资格判断没被改坏——
    只有已购买/已上架、或有不含取消的有效采购记录的商品才算可补货。
    """
    from sqlalchemy import event
    from api.operations_routes import overview

    def run(products):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        # 这批商品故意不设 link_status：只有这种商品才会让 eligible() 去查采购记录，
        # 全设成「已购买」的话旧实现根本不查库，条数对比就成了假绿（本用例第一版
        # 正是这样通过的，反向探针一跑才发现）。
        for i in range(products):
            pid = f'p{i}'
            db.add(Product(id=pid, store_id='a', product_name=pid))
            db.add(ProcurementRecord(id=f'rec{i}', store_id='a', product_id=pid, quantity=5, status='in_transit'))
        # 三种边界：靠链接状态（应算）、已取消采购（不算）、数量 0（不算）
        db.add(Product(id='bought', store_id='a', product_name='bought', link_status='已购买'))
        db.add(Product(id='cancelled', store_id='a', product_name='cancelled'))
        db.add(ProcurementRecord(id='no', store_id='a', product_id='cancelled', quantity=5, status='cancelled'))
        db.add(Product(id='zero', store_id='a', product_name='zero'))
        db.add(ProcurementRecord(id='z', store_id='a', product_id='zero', quantity=0, status='in_transit'))
        db.commit()
        statements = []

        @event.listens_for(engine, 'before_cursor_execute')
        def record(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        rows = overview(db, 'a')['rows']
        todo = alerts(db, 'a')
        db.close()
        return len(statements), {r['id'] for r in rows}, todo

    few, few_ids, _ = run(3)
    many, many_ids, many_alerts = run(60)
    assert len(few_ids) == 4 and len(many_ids) == 61, (len(few_ids), len(many_ids))
    assert 'p0' in many_ids and 'bought' in many_ids
    assert 'cancelled' not in many_ids and 'zero' not in many_ids
    assert many_alerts == [] and many <= few + 2, f'SQL 条数随商品数增长：3 个商品 {few} 条，60 个商品 {many} 条'
