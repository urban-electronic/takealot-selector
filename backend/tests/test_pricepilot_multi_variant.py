"""多维变体：颜色 + 尺码都要选中，页面才会给出报价。

真实故障（2026-10-18，SKU 9902415247821，PLID91715202）：
商品名是 "... Shapewear - Nude - L"，旧代码用 `rsplit(' - ', 1)` 只取最后一段，
得到 'L'。于是它拿尺码去当颜色参数、又去找标签等于 'L' 的按钮——两处都落空，
连续 3 轮报 variant_unmatched。实测页面停在 'Select an option' 占位状态，
Buybox 卡文本只有 5 个字符，根本没有报价：只切颜色不切尺码是读不到价的。

这里钉住四件事：
  1. 名称里的每个变体段都要被当成一个维度；
  2. 按钮型维度（颜色）点控件，下拉型维度（尺码）点开再选；
  3. 少确认任何一个维度都不得采信默认变体的价格；
  4. 名称尾段如果不是变体（标题副标题），宁可报失败也不猜。
"""
import asyncio
from types import SimpleNamespace

from services.pricepilot_collector import scrape_product, variant_labels_from_name

URL = 'https://www.takealot.com/3-level-adjustable-high-waisted-tummy-compression-full-panty-sha/PLID91715202'
NAME = '3 Level Adjustable High-Waisted Tummy Compression Full Panty Shapewear - Nude - L'
TITLE = NAME + ' | Takealot.com'

COLOUR = 'Nude'
SIZE = 'L'
TARGET_PRICE = 138.0


class Node:
    def __init__(self, text='', in_card=False):
        self._text = text
        self.in_card = in_card

    async def inner_text(self):
        return self._text

    async def is_visible(self):
        return True

    async def evaluate(self, script, arg=None):
        if 'product-card' in str(script):
            return self.in_card
        return None

    async def click(self):
        pass

    async def query_selector(self, selector):
        return None


class Price(Node):
    pass


class ColourButton(Node):
    """颜色是按钮组：点了就给控件加选中态。"""

    def __init__(self, page, label):
        super().__init__(label)
        self.page = page
        self.selected = False

    async def evaluate(self, script, arg=None):
        if 'product-card' in str(script):
            return False
        return {'selected': self.selected, 'label': self._text}

    async def click(self):
        self.page.pick_colour(self._text)


class SizeTrigger(Node):
    """尺码是下拉：点开才出现选项，选完把值写回触发器文本。"""

    def __init__(self, page):
        super().__init__('Select an option')
        self.page = page

    async def evaluate(self, script, arg=None):
        if 'product-card' in str(script):
            return False
        chosen = self.page.size == SIZE and self._text == SIZE
        return {'selected': chosen, 'label': self._text}

    async def click(self):
        self.page.size_open = True


class SizeOption(Node):
    def __init__(self, page, label):
        super().__init__(label)
        self.page = page

    async def click(self):
        self.page.pick_size(self._text)


class BuyboxCard:
    def __init__(self, price):
        self.price = price

    async def inner_text(self):
        return '' if self.price is None else f'Fastest delivery R {self.price:,.0f} In stock'

    async def query_selector(self, selector):
        if self.price is None:
            return None
        return Price(f'R {self.price:,.0f}') if ('price' in selector or 'currency' in selector) else None


class FakePage:
    """变体商品页：颜色按钮组 + 尺码下拉；两个维度都选对才有报价。"""

    def __init__(self, colours=('Black', COLOUR), sizes=('S', 'M', SIZE),
                 size_dimension=True, title=TITLE):
        self.url = URL
        self.title_text = title
        self.colour = None
        self.size = None
        self.size_open = False
        self.size_dimension = size_dimension
        self._colours = [ColourButton(self, c) for c in colours]
        self._size_trigger = SizeTrigger(self) if size_dimension else None
        self._size_options = [SizeOption(self, s) for s in sizes]

    # --- 页面行为 ---
    def pick_colour(self, label):
        self.colour = label
        for node in self._colours:
            node.selected = node._text == label

    def pick_size(self, label):
        self.size = label
        self.size_open = False
        self._size_trigger._text = label

    @property
    def price(self):
        if self.colour == COLOUR and self.size == SIZE:
            return TARGET_PRICE
        return None

    # --- Playwright 接口 ---
    async def goto(self, url, **kwargs):
        self.url = url
        return SimpleNamespace(status=200)

    async def title(self):
        return self.title_text

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_selector(self, *args, **kwargs):
        pass

    async def wait_for_function(self, *args, **kwargs):
        pass

    async def keyboard_press(self, key):
        pass

    async def evaluate(self, script, arg=None):
        script = str(script)
        if 'scrollTo' in script:
            return None
        if 'querySelectorAll' in script:
            nodes = list(self._colours)
            if self._size_trigger:
                nodes.append(self._size_trigger)
            return len(nodes)
        return None

    async def query_selector_all(self, selector):
        if selector.startswith('[data-ref="buybox-offer"]'):
            return [BuyboxCard(self.price)]
        if 'role="option"' in selector:
            return list(self._size_options) if self.size_open else []
        if 'variant' in selector or 'swatch' in selector:
            nodes = list(self._colours)
            if self._size_trigger:
                nodes.append(self._size_trigger)
            return nodes
        return []

    async def query_selector(self, selector):
        if 'stock' in selector or 'availability' in selector:
            return Node('In stock')
        if 'seller' in selector:
            return Node('Sold by Urban Electronics')
        return None


def run(page, name=NAME):
    return asyncio.run(scrape_product(page, {'url': URL, 'name': name, 'sku': '9902415247821'}))


def test_labels_split_every_trailing_segment():
    assert variant_labels_from_name(NAME) == [COLOUR, SIZE]
    assert variant_labels_from_name('Camera') == []
    assert variant_labels_from_name('Camera - Black') == ['Black']


def test_colour_and_size_are_both_selected_before_reading_price():
    page = FakePage()
    result = run(page)
    assert result['variantApplied'] == [COLOUR, SIZE], result.get('variantMethods')
    assert result['variantMethods'] == [f'{COLOUR}:click', f'{SIZE}:dropdown']
    assert not result.get('variantSwitchFailed') and not result.get('error')
    assert result['buyboxPrice'] == TARGET_PRICE


def test_missing_size_is_not_published_as_default_variant_price():
    """页面上没有 L 码：不得退回默认变体的价，必须点名缺哪个维度。"""
    page = FakePage(sizes=('S', 'M'))
    result = run(page)
    assert result['variantUnmatched'] == [SIZE]
    assert result['variantSwitchFailed'] is True
    assert result['buyboxPrice'] is None


def test_colour_only_page_cannot_satisfy_a_two_dimension_sku():
    page = FakePage(size_dimension=False)
    result = run(page)
    assert result['variantUnmatched'] == [SIZE]
    assert result['variantSwitchFailed'] is True
    assert result['buyboxPrice'] is None


def test_subtitle_tail_on_a_variant_page_fails_conservatively():
    """名称尾段不是变体时宁可失败也不猜——分不清副标题和缺失变体，
    就按不安全的那一侧处理。"""
    page = FakePage()
    result = run(page, name='Shapewear - professional package for travel')
    assert result['variantSwitchFailed'] is True
    assert result['variantUnmatched'] == ['professional package for travel']
    assert result['buyboxPrice'] is None
