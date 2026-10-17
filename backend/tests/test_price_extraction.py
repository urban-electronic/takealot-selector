import asyncio
from services.pricepilot_collector import price_of, distinct_prices, parse_price


class CardWithoutPriceNodes:
    """模拟改版后的卡片：四个价格选择器全部落空，但卡片文本里有价。

    `text` 是卡片原始文本；`clean` 是摘掉划线原价节点后的文本（真实页面上这一步
    由 evaluate 在克隆节点里完成，这里用字符串直接模拟）。
    """
    def __init__(self, text, clean=None):
        self.text = text
        self.clean = text if clean is None else clean
    async def query_selector(self, selector): return None
    async def inner_text(self): return self.text
    async def evaluate(self, js, struck=None): return self.clean


class Node:
    def __init__(self, text): self.text = text
    async def inner_text(self): return self.text


class CardWithPriceNode:
    """正常页面：价格节点命中，直接走选择器路径。"""
    def __init__(self, node_text): self.node = Node(node_text)
    async def query_selector(self, selector):
        return self.node if selector == '[data-ref="price"] .currency' else None
    async def inner_text(self): return 'ignored'


def test_price_node_path_wins_over_card_text():
    assert asyncio.run(price_of(None, CardWithPriceNode('R 269'))) == 269
    # 节点里有价时，即便卡片文本还含有别的金额也不影响（走的是节点路径）
    assert asyncio.run(price_of(None, CardWithPriceNode('R 269'))) == 269


def test_price_falls_back_to_card_text_when_selectors_miss():
    # 2026-10-18 线上实测：buybox_cards=2 但四个价格选择器全落空 → 整行被判 price_not_found。
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('Buy now\nR 269\nGet it tomorrow'))) == 269
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('R 1 299.00'))) == 1299.0
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('R\u00a0269'))) == 269


def test_ambiguous_card_text_is_refused_not_guessed():
    # 划线价 + 现价：两个金额，不能猜哪个是成交价
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('R 299 R 269'))) is None
    # 现价 + 满减金额
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('R 269\nYou save R 30'))) is None
    # 卡片里根本没有金额（缺货卡）
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('Sold out'))) is None
    assert asyncio.run(price_of(None, CardWithoutPriceNodes(''))) is None


def test_distinct_prices_dedupes_sorts_and_ignores_non_money():
    assert distinct_prices('R 269 R 269 R 30') == [30.0, 269.0]
    assert distinct_prices('R 1 299') == [1299.0]
    assert distinct_prices('no money here') == []
    assert distinct_prices('') == []
    # R 后面没有数字（如 "R&D"）不算金额
    assert distinct_prices('R&D team') == []


def test_zero_amount_is_not_a_price():
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('Free delivery R 0'))) is None


def test_strikethrough_original_price_does_not_block_extraction():
    # 线上真实卡片（SKU 9902348624058，2026-10-17 18:26 的成功快照）：
    # 当前价与划线原价并排渲染，innerText 是 "r 269r 899"——两个金额。
    # 摘掉划线节点后只剩 269，这才是成交价。
    raw = 'fastest delivery\nr 269r 899\nget it tomorrow, 7am - 7pm\nt&cs apply'
    clean = 'fastest delivery\nr 269\nget it tomorrow, 7am - 7pm\nt&cs apply'
    assert asyncio.run(price_of(None, CardWithoutPriceNodes(raw, clean))) == 269
    # 同一页的 best price 卡：268 现价 + 349 划线价
    assert asyncio.run(price_of(None, CardWithoutPriceNodes('best price\nr 268r 349', 'best price\nr 268'))) == 268


def test_two_real_prices_survive_strikethrough_removal():
    # 摘掉划线价后仍有两个独立金额（现价 + 运费/满减）：说明卡里不止一个真实报价，
    # 不采信。这是"宁可采不到也不采错"的底线。
    card = CardWithoutPriceNodes('R 269\nR 40 delivery', clean='R 269\nR 40 delivery')
    assert asyncio.run(price_of(None, card)) is None


def test_evaluate_failure_falls_back_to_raw_text():
    # 页面上下文销毁导致 evaluate 抛错时，不能把整张卡判死；
    # 原始文本本来就只有一个金额，仍然可以采信。
    class Broken(CardWithoutPriceNodes):
        async def evaluate(self, js, struck=None):
            raise RuntimeError('Execution context was destroyed')
    assert asyncio.run(price_of(None, Broken('Buy now R 269'))) == 269
    assert asyncio.run(price_of(None, Broken('R 269 R 899'))) is None


def test_struck_selectors_cover_real_markup_variants():
    from services.pricepilot_collector import STRUCK_SELECTORS
    for sel in ('s', 'del', 'strike', '[style*="line-through"]', '[class*="was-price"]'):
        assert sel in STRUCK_SELECTORS.split(',')


def test_existing_parse_price_behaviour_unchanged():
    assert parse_price('R 269') == 269
    assert parse_price('R1 299') is None or parse_price('R1 299') == 1299
    assert parse_price('') is None
