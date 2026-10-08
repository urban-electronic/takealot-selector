import asyncio
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException

from services import takealot_scraper as scraper
from api import scraper_routes as routes

URL = 'https://www.takealot.com/zenty-electronic-high-voltage-mouse-trap-electric-rodent-zapper/PLID103613893'


def test_blocked_product_uses_exact_seller_catalog_without_fake_price():
    with patch.object(scraper, '_scrape_with_curl_cffi', return_value=(None, 'curl_cffi HTTP 403')):
        result = asyncio.run(scraper.scrape_product(URL))
    assert result['data_source'] == 'offer_catalog'
    assert result['product_name'] == 'Zenty Electronic High-Voltage Mouse Trap, Electric Rodent Zapper'
    assert result['tsin'] == '105708156'
    assert result['variants'][0]['sku'] == '9902591488780'
    assert result['actual_sale_price_zar'] is None
    assert result['product_image_url'] is None
    assert result['success'] is False
    assert result['warnings']


def test_site_shell_is_not_a_success():
    assert not scraper._has_product_data({'product_name': "Takealot.com: Online Shopping | SA's leading online store"})
    assert not scraper._has_product_data({'product_name': None})
    with patch.object(scraper, '_scrape_with_curl_cffi', return_value=({'product_name': 'Takealot.com: Online Shopping'}, '')):
        result = asyncio.run(scraper.scrape_product(URL))
    assert result['data_source'] == 'offer_catalog'


def test_partial_real_product_does_not_require_price_or_playwright():
    with patch.object(scraper, '_scrape_with_curl_cffi', return_value=({'product_name': 'Actual product', 'actual_sale_price_zar': None}, '')):
        result = asyncio.run(scraper.scrape_product(URL))
    assert result['success']
    assert result['data_source'] == 'takealot'
    assert result['warnings']


@pytest.mark.parametrize('url', ['https://takealot.com.evil.test/PLID1', 'https://evil-takealot.com/PLID1', 'file://takealot.com/PLID1', 'https://user:pass@takealot.com/PLID1'])
def test_invalid_hosts_are_rejected(url):
    assert not scraper.validate_takealot_url(url)


def test_structured_product_json_graph_supports_new_markup():
    soup = BeautifulSoup('''<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Mouse trap","image":["https://media.takealot.com/trap.jpg"],"offers":{"price":"1,299.00","priceCurrency":"ZAR"}}]}</script>''', 'lxml')
    fields = scraper._structured_product_fields(soup)
    assert fields['actual_sale_price_zar'] == 1299
    assert fields['product_name'] == 'Mouse trap'
    assert fields['product_image_url'].endswith('trap.jpg')


def test_foreign_currency_is_not_used_as_zar():
    soup = BeautifulSoup('<script type="application/ld+json">{"@type":"Product","name":"Trap","offers":{"price":20,"priceCurrency":"USD"}}</script>', 'lxml')
    assert 'actual_sale_price_zar' not in scraper._structured_product_fields(soup)


def test_route_returns_failure_as_editable_result_and_preserves_variants():
    async def fake_scrape(url):
        return {'normalized_url': url, 'tsin': 'PLID123', 'success': False,
                'warnings': ['blocked'], 'variants': []}
    with patch.object(routes, 'scrape_product', fake_scrape), patch.object(routes, 'match_fee_category', return_value={'fee_category': None, 'confidence': 'low'}):
        result = asyncio.run(routes.scrape_takealot(routes.ScrapeRequest(productUrl=URL), db=None))
    assert result['success'] is False
    assert result['data_source'] == 'manual'
    assert result['warnings'] == ['blocked']
    assert result['variants'] == []


def test_route_still_rejects_invalid_url():
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.scrape_takealot(routes.ScrapeRequest(url='https://evil.test'), db=None))
    assert error.value.status_code == 400
