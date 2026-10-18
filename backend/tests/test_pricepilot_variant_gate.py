"""Variant switching must be gated on real main-product-area variant controls.

Background (2026-10-15 production failure, SKU 9902348621330, PLID97512496):
the product name "4K Thump action camera - professional package for motor,
biking, skydiving" contains " - " as a title separator, not a variant suffix.
The old parser treated the tail as a variant, tried colour_variant/size_variant
switching, could not confirm it, and reported variant_unmatched - blocking a
round on a single-offer product whose PLID uniquely identifies the quote.

Follow-up investigation on the live page showed the variant CSS selectors do
match nodes on that product page - but every one of them sits inside
[class*="product-card"] recommendation cards ("More Colours" teasers). The
main product area has zero variant controls. Therefore the gate, the
selected-state check and the click path must all exclude recommendation-card
nodes, otherwise a teaser whose text happens to equal the suffix would be
clicked and jump to a different product (wrong price).
"""
import asyncio
from types import SimpleNamespace

from services.pricepilot_collector import scrape_product

URL = 'https://www.takealot.com/4k-thump-action-camera-professional-package-for-motor-biking-sky/PLID97512496'
NAME = '4K Thump action camera - professional package for motor, biking, skydiving'
TITLE = NAME + ' | Takealot.com'

CARD_CHECK = 'product-card'


class Element:
    def __init__(self, text='', in_card=False):
        self._text = text
        self.in_card = in_card

    async def inner_text(self):
        return self._text

    async def query_selector(self, selector):
        return None

    async def evaluate(self, script):
        if CARD_CHECK in str(script):
            return self.in_card
        return None

    async def click(self):
        pass


class Price(Element):
    async def query_selector(self, selector):
        return Element(self._text) if ('price' in selector or 'currency' in selector) else None


class VariantButton(Element):
    def __init__(self, label, selected, page, in_card=False):
        super().__init__(label, in_card=in_card)
        self.selected = selected
        self.clicks = 0
        self.page = page

    async def evaluate(self, script):
        script = str(script)
        if CARD_CHECK in script:
            return self.in_card
        return {'selected': self.selected, 'label': self._text}

    async def click(self):
        self.clicks += 1
        self.selected = True
        self.page.on_variant_click(self._text)


class BuyboxCard:
    def __init__(self, price):
        self.price = price

    async def inner_text(self):
        return f'Fastest delivery R {self.price:,.0f} In stock'

    async def query_selector(self, selector):
        return Price(f'R {self.price:,.0f}') if ('price' in selector or 'currency' in selector) else None


class FakePage:
    """Minimal Takealot product page: one buybox card, optional variant buttons.

    variants=True      -> one real variant control in the main product area
    card_variants=True -> "More Colours" teasers inside recommendation cards
                          (matched by the CSS selectors, but NOT variant controls)
    """

    def __init__(self, variants=False, price=1234.0, variant_label='Black',
                 title=TITLE, card_variants=False, card_label='Red'):
        self.url = URL
        self.variants = variants
        self.price = price
        self.default_price = price
        self.variant_label = variant_label
        self.title_text = title
        self.card_variants = card_variants
        self.card_label = card_label
        self.card_clicks = []

    def on_variant_click(self, label):
        if label.lower() == self.variant_label.lower():
            self.price = 900.0

    def on_card_click(self, label):
        self.card_clicks.append(label)

    async def goto(self, url, **kwargs):
        self.url = url
        if 'colour_variant=' in url or 'size_variant=' in url:
            wanted = url.rsplit('=', 1)[1]
            if self.variants and wanted.lower() == self.variant_label.lower():
                self.price = 900.0
        elif url == URL:
            self.price = self.default_price
        return SimpleNamespace(status=200)

    async def title(self):
        return self.title_text

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_selector(self, *args, **kwargs):
        pass

    async def wait_for_function(self, *args, **kwargs):
        pass

    async def evaluate(self, script, arg=None):
        script = str(script)
        if 'scrollTo' in script:
            return None
        if 'querySelectorAll' in script:  # variant probe: main-area controls only
            return 1 if self.variants else 0
        return None

    async def query_selector_all(self, selector):
        if selector.startswith('[data-ref="buybox-offer"]'):
            return [BuyboxCard(self.price)]
        if 'variant' in selector or 'swatch' in selector:
            # The selector list cannot tell main-area controls from teasers;
            # it returns both, and main_variant_elements() filters by DOM scope.
            # Instances are cached: real DOM nodes keep their selected state across
            # queries, and the collector re-queries after every click to verify.
            if not hasattr(self, '_variant_nodes'):
                nodes = []
                if self.card_variants:
                    nodes.append(VariantButton(self.card_label, False, self, in_card=True))
                if self.variants:
                    nodes.append(VariantButton(self.variant_label, False, self))
                self._variant_nodes = nodes
            return list(self._variant_nodes)
        return []  # "other sellers" markers and everything else

    async def query_selector(self, selector):
        if 'stock' in selector or 'availability' in selector:
            return Element('In stock')
        if 'seller' in selector:
            return Element('Sold by Urban Electronics')
        return None


def run(page, item):
    return asyncio.run(scrape_product(page, item))


def test_title_separator_without_variant_controls_reads_the_single_offer():
    page = FakePage()
    result = run(page, {'url': URL, 'name': NAME, 'sku': '9902348621330'})
    assert result['buyboxPrice'] == 1234.0
    assert result['buyboxSeller'] == 'Urban Electronics'
    assert result['stockMapped'] == 'in_stock'
    assert result['variantControls'] == 0
    assert not result.get('variantSwitchFailed') and not result.get('error')
    # The URL must never be rewritten with a variant parameter on this page.
    assert 'variant=' not in page.url


def test_recommendation_card_teasers_are_not_variant_controls():
    """Live-page case (PLID97512496): selectors match "More Colours" teasers
    inside recommendation cards only. They must not count as controls, must
    not be clicked (a teaser text can equal the suffix and would jump to a
    different product), and the single offer must still be read."""
    page = FakePage(card_variants=True)
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result['variantControls'] == 0
    assert not result.get('variantSwitchFailed') and not result.get('error')
    assert result['buyboxPrice'] == 1234.0
    assert page.card_clicks == []
    assert 'variant=' not in page.url


def test_variant_controls_present_but_unmatched_still_fails_conservatively():
    page = FakePage(variants=True)
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result['variantControls'] == 1
    assert result['variantSwitchFailed'] is True
    # The default-variant price is still reported for diagnostics, but the
    # backend refuses to publish it (variantSwitchFailed blocks validity).
    assert result['buyboxPrice'] == 1234.0


def test_variant_controls_present_and_matched_switches_before_reading_price():
    page = FakePage(variants=True)
    result = run(page, {'url': URL, 'name': 'Camera - Black', 'sku': '9902348621330'})
    assert result['variantControls'] == 1
    assert result['buyboxPrice'] == 900.0
    assert not result.get('variantSwitchFailed') and not result.get('error')
    # 切换靠点击页面自己的变体控件完成，不再改写 URL：多维度变体下 URL 参数
    # 一次只能表达一个维度，点控件才能把每个维度都设上并逐个验证选中态。
    assert result['variantApplied'] == ['Black']
    assert result['variantMethods'] == ['Black:click']
    assert 'variant=' not in page.url


def test_probe_counts_controls_even_when_selector_list_misses_them(monkeypatch):
    import services.pricepilot_collector as core

    page = FakePage()

    async def no_selector_match(*args, **kwargs):
        return [], None

    monkeypatch.setattr(core, 'query_all_fallback', no_selector_match)

    async def probe(script):
        return 1  # page really has a variant control our selectors missed

    page.evaluate = probe
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result['variantControls'] == 1
    assert result['variantSwitchFailed'] is True


def test_probe_ignores_recommendation_card_teasers(monkeypatch):
    """Selector list matches only card teasers AND the JS probe also sees only
    card nodes: the gate must still conclude zero controls."""
    import services.pricepilot_collector as core

    page = FakePage(card_variants=True)
    original = core.query_all_fallback

    async def only_card_teasers(pg, kind):
        if kind == 'variant':
            return [VariantButton('Red', False, page, in_card=True)], None
        return await original(pg, kind)

    monkeypatch.setattr(core, 'query_all_fallback', only_card_teasers)

    async def probe(script):
        return 0  # probe excludes product-card nodes, like the real page

    page.evaluate = probe
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result['variantControls'] == 0
    assert not result.get('variantSwitchFailed')
    assert result['buyboxPrice'] == 1234.0


def test_click_fallback_switches_when_url_parameter_does_not(monkeypatch):
    page = FakePage(variants=True)
    original_goto = page.goto

    async def goto(url, **kwargs):
        # URL parameter navigation never changes anything on this page.
        if 'colour_variant=' in url or 'size_variant=' in url:
            return await original_goto(URL, **kwargs)
        return await original_goto(url, **kwargs)

    monkeypatch.setattr(page, 'goto', goto)
    clicked = []
    original_click = VariantButton.click

    async def click(self):
        await original_click(self)
        clicked.append(self._text)

    monkeypatch.setattr(VariantButton, 'click', click)
    result = run(page, {'url': URL, 'name': 'Camera - Black', 'sku': '9902348621330'})
    assert result['buyboxPrice'] == 900.0
    assert not result.get('variantSwitchFailed')
    assert clicked == ['Black']


def test_probe_failure_must_not_publish_default_quote(monkeypatch):
    """Codex review P1 (2026-10-15): a failed probe is NOT "no variants".

    Variant queries raise (detached DOM) and the JS probe raises (execution
    context destroyed). The old code swallowed both and concluded zero
    controls, cleared the suffix and published the default-variant price.
    Undeterminable must fail conservatively: keep the suffix, attempt the
    switch, and report variantSwitchFailed when it cannot be confirmed.
    """
    page = FakePage(variants=True)
    original_query, original_evaluate = page.query_selector_all, page.evaluate

    async def query(selector):
        if 'variant' in selector or 'swatch' in selector:
            raise RuntimeError('detached during probe')
        return await original_query(selector)

    async def evaluate(script, arg=None):
        if 'querySelectorAll' in str(script):
            raise RuntimeError('execution context destroyed')
        return await original_evaluate(script, arg)

    monkeypatch.setattr(page, 'query_selector_all', query)
    monkeypatch.setattr(page, 'evaluate', evaluate)
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result.get('variantSwitchFailed') or result.get('error'), result
    assert result['variantControls'] is None, "undeterminable must be reported as unknown"


def test_element_scope_undeterminable_fails_conservatively(monkeypatch):
    """Elements are found but their card-scope cannot be determined: the gate
    must not conclude "no variants" from that."""
    page = FakePage(variants=True)

    class UnscopedButton(VariantButton):
        async def evaluate(self, script):
            if 'product-card' in str(script):
                raise RuntimeError('element detached')
            return await super().evaluate(script)

    async def variant_query(selector):
        if 'variant' in selector or 'swatch' in selector:
            return [UnscopedButton('Red', False, page)]
        return []

    monkeypatch.setattr(page, 'query_selector_all', variant_query)
    result = run(page, {'url': URL, 'name': 'Camera - Red', 'sku': '9902348621330'})
    assert result.get('variantSwitchFailed') or result.get('error'), result
    assert result['variantControls'] is None


def test_real_dom_probe_excludes_recommendation_cards():
    """Offline real-DOM check with the actual JS (no network): a synthetic page
    with one main-area control plus recommendation-card teasers must count 1,
    and a teaser-only page must count 0."""
    import asyncio

    pytest = __import__('pytest')
    import services.pricepilot_collector as core

    WITH_MAIN = """
    <div class="pdp"><div data-ref="buybox-offer">buybox</div>
      <button class="variant-swatch" aria-label="Red">Red</button></div>
    <div class="product-card-module_card">
      <div class="colour-variant-icon-text">More Colours</div></div>"""
    TEASER_ONLY = """
    <div class="pdp"><div data-ref="buybox-offer">buybox</div></div>
    <div class="product-card-module_card">
      <div class="colour-variant-icon-text">More Colours</div>
      <i class="colour-variant-icon-text-module_spectrum"></i></div>"""

    async def run():
        from playwright.async_api import async_playwright
        out = {}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content(WITH_MAIN)
                out['with_main'] = {
                    'probe': await page.evaluate(core._VARIANT_PROBE_JS),
                    'core_ready': await page.evaluate(core._CORE_BLOCK_JS),
                    'controls': await core.page_variant_controls(page),
                }
                main_els, complete = await core.main_variant_elements(page)
                out['with_main']['main_els'] = len(main_els)
                out['with_main']['complete'] = complete
                await page.set_content(TEASER_ONLY)
                out['teaser_only'] = {'controls': await core.page_variant_controls(page)}
                main_els, complete = await core.main_variant_elements(page)
                out['teaser_only']['main_els'] = len(main_els)
                out['teaser_only']['complete'] = complete
                await page.close()
                await browser.close()
        except Exception:
            return None
        return out

    result = asyncio.run(run())
    if result is None:
        pytest.skip('playwright chromium not available in this environment')
    assert result['with_main']['probe'] == 1
    assert result['with_main']['core_ready'] is True
    assert result['with_main']['controls'] == 1
    assert result['with_main']['main_els'] == 1
    assert result['with_main']['complete'] is True
    assert result['teaser_only']['controls'] == 0
    assert result['teaser_only']['main_els'] == 0
    assert result['teaser_only']['complete'] is True
