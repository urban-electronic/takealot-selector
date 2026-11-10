"""per-SKU 显式变体维度：从配置落库到解析器采用，全链路钉住。

真实故障（SKU 9902416418329）：商品名
「Lady Heart & Infinity ... - FDJH4 - Blue」里 FDJH4 是型号码，不是页面上的
变体控件。variant_labels_from_name() 按 " - " 切出 ['FDJH4','Blue']，两个维度
都必须确认选中，FDJH4 永远点不中 → 每轮确定性 variant_unmatched。

修法是让用户显式指定真正的规格，而不是放宽变体确认闸门。这里最要紧的断言是：
**显式配置只改「猜哪个标签」，不改「必须确认选中」这道闸**。少一个维度依然
报失败、依然不采信默认变体的价。
"""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import buybox_routes, collector_routes, pricing_routes
from database import Base
from models import DEFAULT_STORE_ID, Product, SystemSettings
from services import automatic_pricing, buybox_monitor, collection_diagnostics, quote_browser
from services.buybox_monitor import PREFIX
from services.pricepilot_collector import scrape_product
from tests.test_pricepilot_multi_variant import (
    COLOUR, NAME, SIZE, TARGET_PRICE, URL, FakePage,
)
from tests.test_pricing_routes import database

POLLUTED = 'Lady Heart & Infinity Rhinestone Silver Plated Pendant Necklace - FDJH4 - Blue'


def parse(page, name=POLLUTED, extra=None):
    item = {'url': URL, 'name': name, 'sku': '9902416418329', **(extra or {})}
    return asyncio.run(scrape_product(page, item))


# ---------------------------------------------------------------- 解析器 ----

def test_model_code_in_title_is_deterministic_variant_failure():
    """没有覆盖时，型号码必然被当成变体维度；这是要修的那个故障本身。"""
    result = parse(FakePage())
    assert result['variantLabelsSource'] == 'name'
    assert result['variantSwitchFailed'] is True
    assert 'FDJH4' in result['variantUnmatched']
    assert not result.get('buyboxPrice')


def test_explicit_labels_replace_title_guessing_and_reach_the_price():
    """配了 ['Nude','L'] 就完全无视标题里的 FDJH4 / Blue，正常读到报价。"""
    result = parse(FakePage(), extra={'variantLabels': ['Nude', 'L']})
    assert result['variantLabelsSource'] == 'override'
    assert result['variantApplied'] == [COLOUR, SIZE]
    assert not result.get('variantSwitchFailed')
    assert result['buyboxPrice'] == TARGET_PRICE


def test_override_never_relaxes_the_strict_confirmation_gate():
    """关键安全断言：显式配错/配了页面上没有的规格，仍然不采默认变体的价。"""
    result = parse(FakePage(), extra={'variantLabels': [COLOUR, 'XXL']})
    assert result['variantLabelsSource'] == 'override'
    assert result['variantUnmatched'] == ['XXL']
    assert result['variantSwitchFailed'] is True
    assert not result.get('buyboxPrice')
    assert not result.get('offers')


@pytest.mark.parametrize('bad', [None, '', 'Blue', 7, [], ['', '   ']])
def test_invalid_or_empty_override_falls_back_to_name_parsing(bad):
    result = parse(FakePage(), name=NAME, extra={'variantLabels': bad})
    assert result['variantLabelsSource'] == 'name'
    assert result['variantApplied'] == [COLOUR, SIZE]


# ------------------------------------------------------------ 云端 collect ----

class _Locator:
    @property
    def first(self):
        return self

    def locator(self, selector):
        return self

    async def count(self):
        return 0

    async def inner_text(self, timeout=None):
        return ''

    async def evaluate(self, script, arg=None):
        return []


class _Page:
    url = 'https://www.takealot.com/x/PLID95883207'

    def on(self, *args, **kwargs):
        pass

    def locator(self, selector):
        return _Locator()

    async def title(self):
        return 'Product | Takealot.com'

    async def evaluate(self, script, arg=None):
        return None

    async def close(self):
        pass


class _Context:
    async def new_page(self):
        return _Page()


def run_collect(monkeypatch, variant_labels=None):
    captured = {}

    @asynccontextmanager
    async def fake_quote_context(**kwargs):
        yield _Context()

    async def fake_scrape(page, item):
        captured['item'] = item
        return {'buyboxPrice': 199, 'offers': []}

    monkeypatch.setattr(quote_browser, 'quote_context', fake_quote_context)
    monkeypatch.setattr('services.pricepilot_collector.scrape_product', fake_scrape)
    product = SimpleNamespace(id='p1', takealot_url='https://www.takealot.com/x/PLID95883207')
    offer = {'sku': '9902416418329', 'title': POLLUTED, 'selling_price': 199,
             'stock_at_takealot_total': 3}
    snapshot = asyncio.run(automatic_pricing.collect(product, offer, variant_labels=variant_labels))
    return captured['item'], snapshot


def test_collect_puts_explicit_labels_into_parser_item(monkeypatch):
    item, snapshot = run_collect(monkeypatch, ['Blue'])
    assert item['variantLabels'] == ['Blue']
    assert snapshot['diagnostics']['variant_labels_source'] == 'override'
    assert snapshot['diagnostics']['variant_labels'] == ['Blue']


def test_collect_without_override_keeps_original_item_shape(monkeypatch):
    item, snapshot = run_collect(monkeypatch)
    assert 'variantLabels' not in item
    assert 'variant_labels_source' not in snapshot['diagnostics']


# ---------------------------------------------------------------- 配置入口 ----

def test_variant_labels_endpoint_changes_only_labels_and_revision(monkeypatch):
    monkeypatch.setattr(buybox_routes, 'configured_key', lambda db: 'test')
    key = f'{PREFIX}{DEFAULT_STORE_ID}:p1:o1'
    with database() as db:
        db.add(SystemSettings(key=key, value=json.dumps({
            'enabled': True, 'next_due': 123.0, 'failures': 2, 'status': 'error',
            'request_once': True, 'revision': 4, 'sku': '9902416418329'})))
        db.commit()
        out = buybox_routes.variant_labels(
            'p1', 'o1', buybox_routes.VariantLabels(labels=[' Blue ', 'FDJH4', '', 'x' * 80]),
            db, DEFAULT_STORE_ID)
        assert out == {'variant_labels': ['Blue', 'FDJH4', 'x' * 40]}
        value = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        for field, expected in [('enabled', True), ('next_due', 123.0), ('failures', 2),
                                ('status', 'error'), ('request_once', True),
                                ('sku', '9902416418329'), ('revision', 5)]:
            assert value[field] == expected, field
        cleared = buybox_routes.variant_labels(
            'p1', 'o1', buybox_routes.VariantLabels(labels=[]), db, DEFAULT_STORE_ID)
        assert cleared == {'variant_labels': []}
        assert 'variant_labels' not in json.loads(db.query(SystemSettings).filter_by(key=key).first().value)


def test_http_route_is_registered_and_bounds_labels(monkeypatch):
    """直接调函数测不到路由注册/请求体验证；PUT 路径与 2 段配置路由只差一段。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.store_routes import get_store_id
    from database import get_db
    from sqlalchemy.pool import StaticPool
    monkeypatch.setattr(buybox_routes, 'configured_key', lambda db: 'test')
    # TestClient 在另一个线程执行请求；默认内存 SQLite 会拒绝跨线程使用连接。
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        app = FastAPI()
        app.include_router(buybox_routes.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_store_id] = lambda: DEFAULT_STORE_ID
        client = TestClient(app)
        ok = client.put('/api/buybox/p1/o1/variant-labels', json={'labels': ['Blue']})
        assert ok.status_code == 200 and ok.json() == {'variant_labels': ['Blue']}
        too_many = client.put('/api/buybox/p1/o1/variant-labels', json={'labels': ['a'] * 7})
        assert too_many.status_code == 422


def test_variant_labels_endpoint_rejects_foreign_store_and_missing_key(monkeypatch):
    monkeypatch.setattr(buybox_routes, 'configured_key', lambda db: 'test')
    with database() as db:
        with pytest.raises(HTTPException):
            buybox_routes.variant_labels('p1', 'o1', buybox_routes.VariantLabels(labels=['Blue']), db, 'foreign')
        monkeypatch.setattr(buybox_routes, 'configured_key', lambda db: '')
        with pytest.raises(HTTPException):
            buybox_routes.variant_labels('p1', 'o1', buybox_routes.VariantLabels(labels=['Blue']), db, DEFAULT_STORE_ID)
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).count()


# ------------------------------------------------------------ 任务下发/消费 ----

def test_monitor_actions_do_not_wipe_the_override(monkeypatch):
    """「自动」「立即采集」会重写整条 monitor；漏了 {**old} 就会静默丢配置。"""
    monkeypatch.setattr(pricing_routes, 'configured_key', lambda db: 'test')
    key = f'{PREFIX}{DEFAULT_STORE_ID}:one:one'
    with database() as db:
        db.add(SystemSettings(key=key, value=json.dumps({
            'enabled': False, 'variant_labels': ['Blue'], 'revision': 2})))
        db.commit()
        pricing_routes.monitor_configure(
            'one', 'one', pricing_routes.MonitorConfig(enabled=True, interval_minutes=5),
            db, DEFAULT_STORE_ID)
        assert json.loads(db.query(SystemSettings).filter_by(key=key).first().value)['variant_labels'] == ['Blue']
        pricing_routes.collect_now('one', 'one', db, DEFAULT_STORE_ID)
        assert json.loads(db.query(SystemSettings).filter_by(key=key).first().value)['variant_labels'] == ['Blue']


def test_products_leases_carry_variant_labels(monkeypatch):
    monkeypatch.setattr(pricing_routes, 'configured_key', lambda db: 'test')
    with database() as db:
        pricing_routes.monitor_configure(
            'one', 'one', pricing_routes.MonitorConfig(enabled=True, interval_minutes=5),
            db, DEFAULT_STORE_ID)
        row = db.query(SystemSettings).filter_by(key=f'{PREFIX}{DEFAULT_STORE_ID}:one:one').first()
        value = json.loads(row.value)
        row.value = json.dumps({**value, 'variant_labels': ['Blue'], 'next_due': 0})
        db.commit()
        item = collector_routes.products(db, DEFAULT_STORE_ID)['items'][0]
        assert item['variantLabels'] == ['Blue']


def test_collect_once_forwards_labels_only_when_configured(monkeypatch):
    calls = []

    async def fake_collect(*args):
        calls.append(args)
        return {'buyboxPrice': 100}

    monkeypatch.setattr(buybox_monitor, 'collect', fake_collect)
    asyncio.run(buybox_monitor._collect_once(object(), {}, 'seller', ['Blue']))
    assert calls[0][3] == ['Blue']
    asyncio.run(buybox_monitor._collect_once(object(), {}, 'seller'))
    assert len(calls[1]) == 3, '未配置时不得改变老采集器的三参数调用形状'


def test_process_reads_labels_from_monitor_config(monkeypatch):
    """整条链：monitor 配置 → process → _collect_with_retry → collect 第四参数。"""
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    key = f'{PREFIX}{DEFAULT_STORE_ID}:p:1'
    with factory() as db:
        db.add(Product(id='p', store_id=DEFAULT_STORE_ID,
                       takealot_url='https://www.takealot.com/p/PLID1'))
        db.add(SystemSettings(key=key, value=json.dumps(dict(
            product_id='p', offer_id='1', sku='a', enabled=True, interval_minutes=30,
            revision=1, variant_labels=['Blue']))))
        db.commit()
    import database as database_module
    monkeypatch.setattr(database_module, 'SessionLocal', factory)
    monkeypatch.setattr(buybox_monitor, 'configured_key', lambda db: 'test')
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = MagicMock(status_code=200, json=lambda: {
        'offer_id': '1', 'sku': 'a', 'selling_price': 100, 'stock_at_takealot_total': 5})
    monkeypatch.setattr(buybox_monitor.httpx, 'Client', lambda **kwargs: client)
    calls = []

    async def fake_collect(*args):
        calls.append(args)
        return {'buyboxPrice': 100, 'holdingBuybox': True,
                'offers': [{'price': 90, 'status': 'in_stock'}]}

    monkeypatch.setattr(buybox_monitor, 'collect', fake_collect)
    buybox_monitor.process(key)
    assert calls and calls[0][3] == ['Blue']


def test_safe_diagnostics_keeps_override_evidence_but_rejects_arbitrary_text():
    value = collection_diagnostics.safe_diagnostics({
        'variant_labels_source': 'override', 'variant_labels': ['Blue', 'x' * 80],
        'variant_unmatched': ['FDJH4'], 'injected': 'secret'})
    assert value == {'variant_labels_source': 'override',
                     'variant_labels': ['Blue', 'x' * 40],
                     'variant_unmatched': ['FDJH4']}
    assert collection_diagnostics.safe_diagnostics({'variant_labels_source': 'whatever'}) == {}
    assert collection_diagnostics.safe_diagnostics({'variant_labels': 'Blue'}) == {}
