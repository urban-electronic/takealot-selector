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


def details_setup(monkeypatch, payload):
    import services.takealot_scraper as scraper
    import services.takealot_seller_api as seller
    monkeypatch.setattr(seller, 'configured_key', lambda db: None)
    async def fake(url): return payload
    monkeypatch.setattr(scraper, 'scrape_product', fake)


def test_branch_variant_image_replaces_stale_primary_image(db, monkeypatch):
    """分支 SKU 必须换成自己那张变体图，而不是继续沿用主变体的页面主图。"""
    black = 'https://media.takealot.com/covers_images/black.jpg'
    white = 'https://media.takealot.com/covers_images/white.jpg'
    details_setup(monkeypatch, {'product_name': 'Wall Socket', 'product_image_url': black,
                                'variants': [{'sku': 'sku1', 'label': 'White', 'image_url': white}]})
    p = db.get(Product, 'p'); p.product_image_url = black; db.commit()
    result = asyncio.run(refresh_price('p', db, 'default-store', 'details'))
    assert result['product_image_url'] == white
    assert db.get(Product, 'p').product_image_url == white


def test_page_image_never_overwrites_an_existing_variant_image(db, monkeypatch):
    """没有本行 SKU 的变体图时，页面主图只补空位，不把分支图换回主图。"""
    black = 'https://media.takealot.com/covers_images/black.jpg'
    white = 'https://media.takealot.com/covers_images/white.jpg'
    details_setup(monkeypatch, {'product_name': 'Wall Socket', 'product_image_url': black,
                                'variants': [{'sku': 'other', 'label': 'Black', 'image_url': black}]})
    p = db.get(Product, 'p'); p.product_image_url = white; db.commit()
    result = asyncio.run(refresh_price('p', db, 'default-store', 'details'))
    assert 'product_image_url' not in result and db.get(Product, 'p').product_image_url == white


def test_manual_image_url_is_not_overwritten_by_variant_image(db, monkeypatch):
    """人工填写的图片链接优先于抓取结果，不被变体图覆盖。"""
    details_setup(monkeypatch, {'product_name': 'Wall Socket', 'product_image_url': 'https://media.takealot.com/covers_images/black.jpg',
                                'variants': [{'sku': 'sku1', 'label': 'White', 'image_url': 'https://media.takealot.com/covers_images/white.jpg'}]})
    p = db.get(Product, 'p'); p.product_image_url = 'https://shop.example.com/mine.jpg'; db.commit()
    result = asyncio.run(refresh_price('p', db, 'default-store', 'details'))
    assert 'product_image_url' not in result and db.get(Product, 'p').product_image_url == 'https://shop.example.com/mine.jpg'


def test_multi_sku_row_keeps_page_image_without_guessing_a_variant(db, monkeypatch):
    """多 SKU 汇总行无法确定用哪张变体图，只按页面主图补空位。"""
    black = 'https://media.takealot.com/covers_images/black.jpg'
    details_setup(monkeypatch, {'product_name': 'Wall Socket', 'product_image_url': black,
                                'variants': [{'sku': 'sku1', 'label': 'White', 'image_url': 'https://media.takealot.com/covers_images/white.jpg'}]})
    p = db.get(Product, 'p'); p.sku = 'sku1 sku2'; db.commit()
    result = asyncio.run(refresh_price('p', db, 'default-store', 'details'))
    assert result['product_image_url'] == black


def test_seller_refresh_says_why_it_failed_instead_of_a_generic_message(db, monkeypatch):
    """失败原因必须说出来。原来一律「暂时无法读取」，用户只能反复点重试。

    线上 2026-10-20 就卡在这里：本店报价和官网售价都失败，两条提示一模一样，
    谁都看不出是密钥失效、被限流，还是代码报错。
    """
    import services.takealot_seller_api as service
    monkeypatch.setattr(service, 'configured_key', lambda db: 'configured')
    def blocked(url, key):
        raise ValueError('卖家接口暂时不可用，已暂停请求，请稍后再试')
    monkeypatch.setattr(service, 'seller_product', blocked)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(refresh_price('p', db, 'default-store', 'seller'))
    assert exc.value.status_code == 503
    assert '暂停请求' in exc.value.detail and '原售价保留' in exc.value.detail


def test_official_refresh_names_the_failure_class(db, monkeypatch):
    """抓取层抛异常时，至少要给出异常类型；原来只有一句「暂时无法读取」。"""
    import services.takealot_scraper as scraper
    async def boom(url):
        raise RuntimeError('browser wedged')
    monkeypatch.setattr(scraper, 'scrape_product', boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(refresh_price('p', db, 'default-store', 'official'))
    assert exc.value.status_code == 502
    assert 'RuntimeError' in exc.value.detail and '原售价保留' in exc.value.detail


def test_read_reason_maps_runtime_gaps_without_leaking_details():
    from api.product_routes import _read_reason
    assert '部署' in _read_reason(ModuleNotFoundError('No module named curl_cffi'))
    assert '超时' in _read_reason(asyncio.TimeoutError())
    assert _read_reason(RuntimeError('boom')) == '内部错误 RuntimeError'
