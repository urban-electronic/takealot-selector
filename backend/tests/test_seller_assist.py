from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from database import Base
from models import Product
from services.takealot_seller_api import offer_plan
from api.product_routes import AssistRequest, seller_assist


def offer(**kwargs):
    return dict(sku='sku1', productline_id=123, tsin_id=456, title='Title', length_cm=12, width_cm=5, height_cm=3, weight_grams=200, **kwargs)


def test_plan_exact_identity_units_and_no_overwrite():
    p = Product(id='p', store_id='default-store', sku='sku1', product_name='Manual title', length_mm=99)
    plan = offer_plan(p, [offer()])
    changes = {i['field']: i['value'] for i in plan['changes']}
    assert changes['width_mm'] == 50 and changes['actual_weight_kg'] == .2
    assert 'length_mm' not in changes and 'product_name' not in changes and 'sku' not in changes
    assert len(plan['differences']) == 2
    p.sku = 'sku1 historical'
    assert offer_plan(p, [offer()])['changes'] == []


def test_ambiguous_dimensions_are_not_filled():
    p = Product(id='p', store_id='default-store', takealot_url='https://www.takealot.com/item/PLID123')
    other = offer(); other.update(sku='sku2', length_cm=15)
    changes = {i['field']: i['value'] for i in offer_plan(p, [offer(), other])['changes']}
    assert changes['sku'] == 'sku1\nsku2'
    assert 'length_mm' not in changes


def test_apply_stale_preview_store_scope_and_cost_preservation(monkeypatch):
    import services.takealot_seller_api as service
    monkeypatch.setattr(service, 'configured_key', lambda db: 'test')
    monkeypatch.setattr(service, '_load_offers', lambda key: [offer()])
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='p', store_id='default-store', sku='sku1', product_name='Manual', manual_total_cost_zar=123, total_cost_zar=123, actual_sale_price_zar=200, purchase_cost_cny=40)
        db.add(p); db.commit()
        preview = seller_assist(AssistRequest(ids=['p']), SimpleNamespace(url=SimpleNamespace(path='/preview')), db, 'default-store')
        assert p.width_mm is None
        token = preview['items'][0]['token']
        p.width_mm = 999; db.commit()
        with pytest.raises(HTTPException) as exc:
            seller_assist(AssistRequest(ids=['p'], tokens={'p': token}), SimpleNamespace(url=SimpleNamespace(path='/apply')), db, 'default-store')
        assert exc.value.status_code == 409
        preview = seller_assist(AssistRequest(ids=['p']), SimpleNamespace(url=SimpleNamespace(path='/preview')), db, 'default-store')
        seller_assist(AssistRequest(ids=['p'], tokens={'p': preview['items'][0]['token']}), SimpleNamespace(url=SimpleNamespace(path='/apply')), db, 'default-store')
        assert p.width_mm == 999 and p.height_mm == 30
        assert p.manual_total_cost_zar == 123 and p.total_cost_zar == 123
        assert p.actual_sale_price_zar == 200 and p.purchase_cost_cny == 40
        with pytest.raises(HTTPException):
            seller_assist(AssistRequest(ids=['p']), SimpleNamespace(url=SimpleNamespace(path='/preview')), db, 'other-store')
