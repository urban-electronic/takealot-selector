import asyncio
from unittest.mock import patch
import sys
from types import ModuleType

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


def _fake_playwright():
    class Runtime:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    module = ModuleType('playwright.async_api')
    module.async_playwright = Runtime
    return {'playwright': ModuleType('playwright'), 'playwright.async_api': module}


def test_catalog_must_not_skip_rendered_scrape():
    calls = []
    async def rendered(p, name, launcher, url, normalized):
        calls.append(name)
        return {'product_name': 'Live title', 'actual_sale_price_zar': 499.0,
                'product_image_url': 'https://media.takealot.com/live.jpg'}, ''
    with patch.dict(sys.modules, _fake_playwright()), patch.object(scraper, '_scrape_with_curl_cffi', return_value=(None, 'HTTP failed')), patch.object(scraper, '_try_scrape_with_browser', rendered):
        result = asyncio.run(scraper.scrape_product(URL))
    assert calls == ['Firefox']
    assert result['data_source'] == 'takealot'
    assert result['actual_sale_price_zar'] == 499.0
    assert result['tsin'] == '105708156'
    assert result['variants'][0]['sku'] == '9902591488780'


def test_partial_http_data_continues_and_is_preserved():
    calls = []
    async def rendered(p, name, launcher, url, normalized):
        calls.append(name)
        return {'product_name': 'Live title', 'actual_sale_price_zar': 499.0,
                'product_image_url': None}, ''
    partial = {'product_name': 'HTTP title', 'actual_sale_price_zar': None,
               'product_image_url': 'https://media.takealot.com/http.jpg'}
    with patch.dict(sys.modules, _fake_playwright()), patch.object(scraper, '_scrape_with_curl_cffi', return_value=(partial, '')), patch.object(scraper, '_try_scrape_with_browser', rendered):
        result = asyncio.run(scraper.scrape_product(URL))
    assert calls == ['Firefox']
    assert result['actual_sale_price_zar'] == 499.0
    assert result['product_image_url'].endswith('http.jpg')


def test_catalog_only_used_after_both_renderers_fail():
    calls = []
    async def rendered(p, name, launcher, url, normalized):
        calls.append(name)
        return None, 'blocked'
    with patch.dict(sys.modules, _fake_playwright()), patch.object(scraper, '_scrape_with_curl_cffi', return_value=(None, 'blocked')), patch.object(scraper, '_try_scrape_with_browser', rendered):
        result = asyncio.run(scraper.scrape_product(URL))
    assert calls == ['Firefox', 'Chromium']
    assert result['data_source'] == 'offer_catalog'
    assert result['success'] is False


def test_wait_for_actual_product_not_early_generic_jsonld():
    class Page:
        ready = False
        async def goto(self, *args, **kwargs):
            pass
        async def title(self):
            return 'Takealot.com: Online Shopping'
        async def wait_for_selector(self, *args, **kwargs):
            # Generic ViewAction script exists before the product is loaded.
            pass
        async def wait_for_function(self, predicate, timeout):
            assert timeout >= 30000
            assert 'h1' in predicate and 'media.takealot.com' in predicate
            self.ready = True
    class Browser:
        async def close(self):
            pass
    page = Page()
    async def launcher(p):
        return Browser(), page
    async def extract(page, url, normalized):
        return {'product_name': 'Actual loaded product' if page.ready else None}
    with patch.object(scraper, '_extract_data', extract):
        data, error = asyncio.run(scraper._try_scrape_with_browser(None, 'test', launcher, URL, URL))
    assert data['product_name'] == 'Actual loaded product'
    assert error == ''


def test_current_product_markup_ignores_ad_price_and_navigation():
    soup = BeautifulSoup('''<nav><a>Electronics</a></nav>
        <script type="application/ld+json">{"@type":"ViewAction"}</script>
        <main><h1>Zenty Electronic High-Voltage Mouse Trap, Electric Rodent Zapper</h1>
        <img src="https://media.takealot.com/covers_images/f963b6ce9d8548c482498c469b968f03/s-thumbnail.file">
        <div data-ref="price"><span class="currency whitespace-nowrap plus">R 699</span></div>
        <aside>Buy using takealot.credit R 66 p/m</aside><article>Sponsored R 11,999</article>
        <p>Supplier out of stock</p><table><tbody><tr class="product-info-row-undefined">
        <td class="title-cell">Categories</td><td><ul><li><a href="/pool-garden">Garden, Pool &amp; Patio</a> / <a href="/pool-garden/garden-25905">Garden</a> / <a href="/pool-garden/weed-and-pest-control-25938">Weed &amp; Pest Control</a></li></ul></td></tr></tbody></table></main>''', 'lxml')
    fields = scraper._page_product_fields(soup)
    assert fields['actual_sale_price_zar'] == 699
    assert fields['in_stock_price'] is None
    assert fields['product_image_url'].endswith('s-pdpxl.file')
    assert fields['takealot_category_path'] == 'Garden, Pool & Patio > Garden > Weed & Pest Control'


def test_generic_empty_shell_has_no_product_fields():
    soup = BeautifulSoup('<title>Takealot.com: Online Shopping</title><script type="application/ld+json">{"@type":"ViewAction"}</script>', 'lxml')
    assert scraper._page_product_fields(soup) == {}
