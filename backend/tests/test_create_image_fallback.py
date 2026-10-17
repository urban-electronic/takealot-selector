"""New-product route: missing seller images must not short-circuit enrichment."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import pytest
from api import scraper_routes as routes

URL = 'https://www.takealot.com/test/PLID123'
IMAGE = 'https://media.takealot.com/covers_images/test.jpg'

@pytest.mark.parametrize('existing,page,expected,calls', [
    (None, {'product_image_url': IMAGE, 'actual_sale_price_zar': 1}, IMAGE, 1),
    (IMAGE, None, IMAGE, 0),
    (None, None, None, 1),
])
def test_partial_seller_image(existing, page, expected, calls):
    seller = dict(normalized_url=URL, product_name='Seller title',
                  product_image_url=existing, actual_sale_price_zar=269,
                  variants=[{'sku': 'correct'}], warnings=[], data_source='seller_api')
    with patch.object(routes, 'configured_key', return_value='fixture'), \
         patch.object(routes, 'seller_product', return_value=seller), \
         patch.object(routes, '_scrape_with_curl_cffi', return_value=(page, '')) as http, \
         patch.object(routes, 'scrape_product', new_callable=AsyncMock) as browser, \
         patch.object(routes, 'match_fee_category', return_value={'fee_category': None, 'confidence': 'low'}):
        result = asyncio.run(routes.scrape_takealot(routes.ScrapeRequest(url=URL),
                            db=object(), request=SimpleNamespace(headers={})))
    assert result['product_image_url'] == expected
    assert result['actual_sale_price_zar'] == 269
    assert result['variants'] == [{'sku': 'correct'}]
    assert result['product_name'] == 'Seller title'
    assert http.call_count == calls
    browser.assert_not_called()
