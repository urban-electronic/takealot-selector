import asyncio
from unittest.mock import patch, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import SystemSettings
from api import settings_routes, scraper_routes
from services import takealot_seller_api as seller

KEY = 'a' * 128
MASTER = 'test-application-access-key-' + 'x' * 32
URL = 'https://www.takealot.com/test/PLID123'
ROWS = [{'title': 'Actual product', 'productline_id': 123, 'tsin_id': 987,
         'sku': 'confirmed-sku', 'selling_price': 319, 'status': 'not_buyable',
         'image_url': 'http://takealot.s3.amazonaws.com/covers_images/actual/s.file'}]


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv('API_KEY', MASTER)
    seller._cache.clear()


def test_exact_identity_image_price_and_no_false_stock_price():
    with patch.object(seller, '_load_offers', return_value=ROWS):
        r = seller.seller_product(URL, KEY)
        assert r['tsin'] == '987'
        assert r['variants'][0]['sku'] == 'confirmed-sku'
        assert r['actual_sale_price_zar'] == 319
        assert r['in_stock_price'] is None
        assert r['product_image_url'].startswith('https://takealot.s3.amazonaws.com/')
        assert r['success'] and r['data_source'] == 'seller_api'
        assert seller.seller_product(URL.replace('123', '999'), KEY) is None
        assert seller.seller_product('https://evil.test/PLID123', KEY) is None


def test_network_is_get_only_fixed_host_and_cached():
    calls = []
    def respond(request):
        assert request.method == 'GET'
        assert str(request.url).startswith('https://marketplace-api.takealot.com/v1/offers?')
        calls.append(request)
        return httpx.Response(200, json={'items': ROWS})
    original = httpx.Client
    with patch.object(seller.httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        assert seller._load_offers(KEY) == ROWS
        assert seller._load_offers(KEY) == ROWS
    assert len(calls) == 1


def test_rate_limit_cooldown_has_no_immediate_retry():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429)
    original = httpx.Client
    with patch.object(seller.httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        for _ in range(2):
            with pytest.raises(ValueError):
                seller._load_offers(KEY)
    assert len(calls) == 1


def test_secret_encrypted_hidden_immutable_and_requires_auth():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def db():
        with factory() as session:
            yield session
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_db] = db
    client = TestClient(app)
    body = {'api_key': KEY}
    assert client.post('/api/integrations/takealot-seller', json=body).status_code == 403
    headers = {'X-API-Key': MASTER}
    assert client.post('/api/integrations/takealot-seller', headers=headers, json=body).status_code == 200
    assert client.post('/api/integrations/takealot-seller', headers=headers, json=body).status_code == 409
    assert seller.SECRET_SETTING not in client.get('/api/settings').json()
    for method in ('patch', 'put'):
        assert getattr(client, method)('/api/settings', json={seller.SECRET_SETTING: 'overwrite'}).status_code == 403
    with factory() as session:
        encrypted = session.query(SystemSettings).first().value
        assert encrypted != KEY and KEY not in encrypted
        assert seller.configured_key(session) == KEY


def test_seller_source_short_circuits_browser_and_other_store_is_excluded():
    request = MagicMock()
    request.headers = {'X-Store-Id': 'default-store'}
    result = {'normalized_url': URL, 'product_name': 'Actual product', 'success': True, 'data_source': 'seller_api'}
    fee = {'fee_category': None, 'confidence': 'low'}
    with patch.object(scraper_routes, 'configured_key', return_value=KEY), patch.object(scraper_routes, 'seller_product', return_value=result) as get, patch.object(scraper_routes, 'scrape_product') as browser, patch.object(scraper_routes, 'match_fee_category', return_value=fee):
        r = asyncio.run(scraper_routes.scrape_takealot(scraper_routes.ScrapeRequest(url=URL), db=MagicMock(), request=request))
        assert r['data_source'] == 'seller_api'
        browser.assert_not_called()
        request.headers = {'X-Store-Id': 'another-store'}
        browser.return_value = result
        asyncio.run(scraper_routes.scrape_takealot(scraper_routes.ScrapeRequest(url=URL), db=MagicMock(), request=request))
        assert get.call_count == 1
