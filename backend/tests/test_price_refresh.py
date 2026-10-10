import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from database import Base
from models import Product
from api.product_routes import refresh_price


@pytest.fixture
def db():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(Product(id='p', store_id='default-store', sku='sku1', takealot_url='https://www.takealot.com/product/PLID123', product_name='Title', actual_sale_price_zar=500, purchase_cost_cny=40, manual_total_cost_zar=100, total_cost_zar=100))
        session.commit()
        yield session


def seller_setup(monkeypatch, rows):
    import services.takealot_seller_api as service
    monkeypatch.setattr(service, 'configured_key', lambda db: 'configured')
    monkeypatch.setattr(service, '_load_offers', lambda key: rows)
    monkeypatch.setattr(service, 'seller_product', lambda url, key: {'product_name': 'Official', 'actual_sale_price_zar': 1, 'product_image_url': 'https://media.takealot.com/covers_images/a.jpg', 'data_source': 'seller_api', 'warnings': ['本店报价，非官网最低价'], 'variants': []})


def test_seller_price_uses_exact_existing_sku_and_preserves_costs(db, monkeypatch):
    seller_setup(monkeypatch, [dict(sku='sku1', productline_id=123, selling_price=699), dict(sku='other', productline_id=123, selling_price=1)])
    result = asyncio.run(refresh_price('p', db, 'default-store', 'seller'))
    assert result['actual_sale_price_zar'] == 699
    assert result['data_source'] == 'seller_api' and result['price_updated']
    p = db.get(Product, 'p')
    assert p.purchase_cost_cny == 40 and p.manual_total_cost_zar == 100 and p.total_cost_zar == 100


def test_failed_official_refresh_does_not_write_or_fake_success(db, monkeypatch):
    import services.takealot_scraper as scraper
    async def failure(url): return {'product_name': 'Partial title', 'warnings': ['官网安全验证未通过。']}
    monkeypatch.setattr(scraper, 'scrape_product', failure)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(refresh_price('p', db, 'default-store', 'official'))
    assert exc.value.status_code == 502 and '原售价保留' in exc.value.detail
    assert db.get(Product, 'p').product_name == 'Title' and db.get(Product, 'p').actual_sale_price_zar == 500


def test_details_refresh_never_updates_sale_price(db, monkeypatch):
    seller_setup(monkeypatch, [])
    result = asyncio.run(refresh_price('p', db, 'default-store', 'details'))
    assert not result['price_updated'] and 'actual_sale_price_zar' not in result
    assert db.get(Product, 'p').actual_sale_price_zar == 500
    assert result['product_image_url']


def test_unmatched_and_ambiguous_seller_prices_fail_without_fallback(db, monkeypatch):
    seller_setup(monkeypatch, [dict(sku='sku1', productline_id=123, selling_price=699), dict(sku='sku2', productline_id=123, selling_price=599)])
    p = db.get(Product, 'p'); p.sku = 'sku1 sku2'; db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(refresh_price('p', db, 'default-store', 'seller'))
    assert exc.value.status_code == 409
    p.sku = 'missing'; db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(refresh_price('p', db, 'default-store', 'seller'))
    assert exc.value.status_code == 409 and p.actual_sale_price_zar == 500
