# -*- coding: utf-8 -*-
"""多市场参考价：解析、每日同步、批量录入。

参考价是采购决策的输入，所以这里守的底线比普通字段更硬：
- 手工/淘宝价永远不被自动同步覆盖；
- 官网抓取失败只降级成「旧价 + 过期标记」，不能清空；
- 批量录入按店铺隔离，A 店的 SKU 不会被写进 B 店。

全部用例零真实出站：HTTP 由注入的 `fetch` 替代。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import market_price_routes as routes
from database import Base
from models import DEFAULT_STORE_ID, Product, SystemSettings
from services import market_price_sync as sync

SG_LD = '''<html><head><script type="application/ld+json">
{"@context":"https://schema.org/","@type":"Product","sku":"CK2-30782933-1_PEC.BR",
 "name":"Large Ginevra Tote Bag",
 "offers":{"@type":"Offer","priceCurrency":"SGD","price":115,
           "availability":"https://schema.org/InStock"}}
</script></head><body></body></html>'''

CN_PAGE = '''<ul><li>商品编号：CK1-61720233</li></ul>
<div class="product-price"><span>￥369.000</span></div>'''


def _db(tmp_path, name='mp'):
    engine = create_engine(f'sqlite:///{tmp_path}/{name}.db', connect_args={'timeout': 30})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(Session, store_id, sku, prices):
    db = Session()
    db.add(Product(id=f'{store_id}-{sku}', store_id=store_id, sku=sku, product_name=sku,
                   market_prices=json.dumps(prices, ensure_ascii=False) if prices else ''))
    db.commit()
    db.close()


# ---- 解析 ---------------------------------------------------------------

def test_parses_json_ld_offer():
    assert sync.parse_official_price(SG_LD) == {'price': 115, 'cur': 'SGD', 'in_stock': True}


def test_parses_out_of_stock_as_flag_not_error():
    html = SG_LD.replace('InStock', 'OutOfStock')
    assert sync.parse_official_price(html)['in_stock'] is False


def test_falls_back_to_raw_price_keys():
    assert sync.parse_official_price('"priceCurrency":"MYR","price":459.90')['price'] == 459.9


@pytest.mark.parametrize('html', ['', '<html>没有价格</html>', '<script type="application/ld+json">{bad json}</script>'])
def test_unparsable_page_returns_none(html):
    assert sync.parse_official_price(html) is None


def test_parses_cn_store_page():
    assert sync.parse_cn_price(CN_PAGE) == {'price': 369, 'cur': 'CNY', 'in_stock': None}


def test_cn_page_without_sku_is_not_a_product():
    assert sync.parse_cn_price('<div>￥369</div>') is None


# ---- 同步合并 -----------------------------------------------------------

def test_manual_prices_are_never_touched():
    prices = {
        'TB': {'price': 268, 'cur': 'CNY', 'src': '淘宝均价', 'n': 12},
        'CN': {'price': 469, 'cur': 'CNY', 'src': '手工'},
    }
    calls = []
    out, report = sync.sync_prices(prices, lambda url: calls.append(url) or None)
    assert out == prices and report == [] and calls == []


def test_official_entry_updates_price_and_stamp():
    prices = {'SG': {'price': 99, 'cur': 'SGD', 'src': '官网', 'at': '2026-10-01',
                     'url': 'https://www.charleskeith.com/sg/X.html'}}
    out, report = sync.sync_prices(prices, lambda url: {'price': 115, 'cur': 'SGD'}, today='2026-10-22')
    assert out['SG']['price'] == 115
    assert out['SG']['at'] == '2026-10-22'
    assert out['SG']['src'] == '官网自动'
    assert report[0]['status'] == 'updated'


def test_unchanged_price_still_refreshes_date():
    prices = {'SG': {'price': 115, 'src': '官网', 'at': '2026-10-01',
                     'url': 'https://www.charleskeith.com/sg/X.html'}}
    out, report = sync.sync_prices(prices, lambda url: {'price': 115, 'cur': 'SGD'}, today='2026-10-22')
    assert out['SG']['at'] == '2026-10-22' and report[0]['status'] == 'unchanged'


def test_fetch_failure_keeps_price_and_counts_misses():
    prices = {'SG': {'price': 115, 'src': '官网', 'url': 'https://www.charleskeith.com/sg/X.html'}}

    def boom(url):
        raise TimeoutError('读不到')

    out, report = sync.sync_prices(prices, boom)
    assert out['SG']['price'] == 115          # 价格不许丢
    assert out['SG']['miss'] == 1
    assert 'stale' not in out['SG']
    assert report[0]['status'] == 'failed'


def test_three_failures_mark_stale_but_keep_price():
    entry = {'price': 115, 'src': '官网', 'url': 'https://www.charleskeith.com/sg/X.html'}
    for _ in range(sync.STALE_AFTER):
        entry, _ = sync.sync_prices({'SG': entry}, lambda url: None)
        entry = entry['SG']
    assert entry['price'] == 115 and entry['stale'] is True and entry['miss'] == sync.STALE_AFTER


def test_success_clears_stale_flag():
    entry = {'price': 115, 'src': '官网', 'url': 'u', 'miss': 5, 'stale': True}
    out, _ = sync.sync_prices({'SG': entry}, lambda url: {'price': 120, 'cur': 'SGD'})
    assert out['SG']['price'] == 120 and 'stale' not in out['SG'] and out['SG']['miss'] == 0


def test_malformed_url_skips_that_entry_only():
    """url 不是字符串（/api/migrate 整行搬运可能带数字）不许打断整轮。"""
    prices = {
        'CN': {'price': 469, 'src': '官网', 'url': 123},
        'SG': {'price': 99, 'src': '官网', 'url': 'https://www.charleskeith.com/sg/X.html'},
    }
    out, report = sync.sync_prices(prices, lambda url: {'price': 115, 'cur': 'SGD'}, today='2026-10-22')
    assert out['SG']['price'] == 115              # 好的一条照常更新
    assert out['CN'] == prices['CN']              # 坏的一条原样保留
    assert [item['market'] for item in report] == ['SG']


def test_non_numeric_miss_still_counts_as_one_failure():
    entry = {'price': 115, 'src': '官网', 'url': 'u', 'miss': 'oops'}
    out, report = sync.sync_prices({'SG': entry}, lambda url: None)
    assert out['SG']['price'] == 115 and out['SG']['miss'] == 1
    assert report[0]['status'] == 'failed'


def test_apply_fetch_never_raises_on_garbage_entry():
    """apply_fetch 是最后一层兜底：任何字段类型异常都要变成 failed 而不是抛出。"""
    out, status = sync.apply_fetch({'price': 115, 'miss': {'n': 1}}, None, '2026-10-22', 'boom')
    assert status == 'failed' and out['price'] == 115 and out['miss'] == 1


# ---- 调度 ---------------------------------------------------------------

def test_due_without_history_runs_immediately():
    assert sync.due({}) is True


def test_due_waits_a_full_interval():
    recent = {'at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    assert sync.due(recent) is False
    old = {'at': (datetime.now(timezone.utc) - timedelta(hours=sync.INTERVAL_HOURS + 1)).isoformat()}
    assert sync.due(old) is True


def test_broken_timestamp_does_not_stall_the_schedule():
    assert sync.due({'at': '昨天'}) is True


# ---- 批量录入 -----------------------------------------------------------

def test_batch_writes_manual_prices_per_store(tmp_path):
    Session = _db(tmp_path)
    _seed(Session, DEFAULT_STORE_ID, 'CK2-90151648-1', {'SG': {'price': 115, 'src': '官网'}})
    _seed(Session, 'store-b', 'CK2-90151648-1', {'SG': {'price': 115, 'src': '官网'}})

    out = routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='CK2-90151648-1', market='TB', price=268, n=12, src='淘宝均价'),
        routes.PriceInput(sku='CK2-90151648-1', market='CN', price=469),
    ]), db=Session(), store_id=DEFAULT_STORE_ID)

    assert out['saved'] == 2
    db = Session()
    mine = json.loads(db.query(Product).filter(Product.store_id == DEFAULT_STORE_ID).first().market_prices)
    other = json.loads(db.query(Product).filter(Product.store_id == 'store-b').first().market_prices)
    db.close()
    assert mine['TB'] == {'price': 268.0, 'cur': 'CNY', 'src': '淘宝均价', 'n': 12,
                          'at': sync._today(), 'checked_at': mine['TB']['checked_at']}
    assert mine['CN']['price'] == 469.0 and mine['SG']['price'] == 115
    assert set(other) == {'SG'}          # 另一家店一个字段都没被改


def test_batch_matches_sku_with_colour_suffix():
    """用户手上是 CK2-90151648-1_STO.GR，库里存主货号，也要能对上。"""
    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [Product(id='p1', store_id=DEFAULT_STORE_ID, sku='CK2-90151648-1')]

        def first(self):
            return None

    class _Db:
        def query(self, *a, **k):
            return _Q()

    products = routes._matches(_Db(), DEFAULT_STORE_ID, routes.PriceInput(
        sku='CK2-90151648-1_STO.GR', market='TB', price=1))
    assert len(products) == 1 and products[0].id == 'p1'


def test_batch_rejects_price_for_missing_product(tmp_path):
    Session = _db(tmp_path, 'empty')
    out = routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='不存在', market='TB', price=1)]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert out['saved'] == 0 and out['results'][0]['status'] == 'not_found'


def test_batch_keeps_the_source_date_not_the_entry_date(tmp_path):
    """抄来的报价可能是一个月前的，`at` 必须记报价日期，否则会被当成实时价。"""
    Session = _db(tmp_path, 'at')
    _seed(Session, DEFAULT_STORE_ID, 'S1', {})
    routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='S1', market='TB', price=639, src='天猫到手价', at='2026-08-13')]),
        db=Session(), store_id=DEFAULT_STORE_ID)
    db = Session()
    entry = json.loads(db.query(Product).first().market_prices)['TB']
    db.close()
    assert entry['at'] == '2026-08-13'
    assert entry['checked_at'].startswith('20')       # 录入时刻单独记


def test_batch_defaults_to_today_when_no_date_given(tmp_path):
    Session = _db(tmp_path, 'at2')
    _seed(Session, DEFAULT_STORE_ID, 'S1', {})
    routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='S1', market='CN', price=699)]), db=Session(), store_id=DEFAULT_STORE_ID)
    db = Session()
    assert json.loads(db.query(Product).first().market_prices)['CN']['at'] == sync._today()
    db.close()


def test_batch_rejects_malformed_date():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        routes.PriceInput(sku='S1', market='CN', price=1, at='2026/08/13')


def test_batch_writes_every_row_sharing_the_sku(tmp_path):
    """同一个 SKU 可能有两行（CK 选品表里 Apfra 就重复了一条），价格必须两行都写，
    否则列表里两条一模一样的记录一条有价一条空白，用户会以为没保存成功。"""
    Session = _db(tmp_path, 'dup')
    db = Session()
    for pid in ('p1', 'p2'):
        db.add(Product(id=pid, store_id=DEFAULT_STORE_ID, sku='CK2-90151648-1',
                       product_name='Apfra', market_prices=''))
    db.commit()
    db.close()

    out = routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='CK2-90151648-1', market='CN', price=569)]),
        db=Session(), store_id=DEFAULT_STORE_ID)
    assert out['results'][0]['products'] == 2 and out['products'] == 2

    db = Session()
    rows = db.query(Product).all()
    assert all(json.loads(r.market_prices)['CN']['price'] == 569.0 for r in rows)
    db.close()


def test_batch_none_price_clears_the_market(tmp_path):
    Session = _db(tmp_path, 'clear')
    _seed(Session, DEFAULT_STORE_ID, 'S1', {'TB': {'price': 268, 'src': '淘宝均价'},
                                            'SG': {'price': 115, 'src': '官网'}})
    out = routes.batch(routes.BatchInput(items=[
        routes.PriceInput(sku='S1', market='TB', price=None)]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert out['results'][0]['status'] == 'cleared'
    db = Session()
    left = json.loads(db.query(Product).first().market_prices)
    db.close()
    assert 'TB' not in left and left['SG']['price'] == 115


def test_batch_rejects_negative_price():
    """负数在入参层就被拦下（HTTP 侧是 422），不进库、不落成「¥-1」这种脏数据。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        routes.PriceInput(sku='S1', market='TB', price=-1)


# ---- 覆盖率 -------------------------------------------------------------

def test_status_counts_coverage_and_stale(tmp_path):
    Session = _db(tmp_path, 'status')
    _seed(Session, DEFAULT_STORE_ID, 'A', {'SG': {'price': 115, 'src': '官网'}})
    _seed(Session, DEFAULT_STORE_ID, 'B', {'SG': {'price': 115, 'src': '官网', 'stale': True},
                                           'TB': {'price': 268, 'src': '淘宝均价'}})
    _seed(Session, DEFAULT_STORE_ID, 'C', {})

    out = routes.status(db=Session(), store_id=DEFAULT_STORE_ID)
    assert out['products'] == 3 and out['products_with_price'] == 2
    assert out['coverage']['SG'] == {'filled': 2, 'stale': 1}
    assert out['coverage']['TB'] == {'filled': 1, 'stale': 0}
    assert out['coverage']['CN'] == {'filled': 0, 'stale': 0}
    assert out['due'] is True


def test_auto_markets_only_lists_writable_markets(tmp_path):
    """`auto_markets` 是给客户端看的「哪些市场会自动重读」，照着它提交必须能写进去。

    曾经写成 ['SG','MY']：MY 既不在 MARKETS，也不在 PriceInput.market 的
    Literal 里，客户端按这个列表提交 MY 会直接 422，而这个字段本身从不参与
    校验，所以错的那版跑得很安静。
    """
    Session = _db(tmp_path, 'auto-markets')
    out = routes.status(db=Session(), store_id=DEFAULT_STORE_ID)
    unknown = [code for code in out['auto_markets'] if code not in routes.MARKETS]
    assert unknown == [], f'auto_markets 里有不可写入的市场：{unknown}'
    for code in out['auto_markets']:
        routes.PriceInput(sku='S1', market=code, price=1)  # 不抛异常即可


def test_last_run_roundtrip(tmp_path):
    Session = _db(tmp_path, 'last')
    db = Session()
    sync._save_state(db, SystemSettings, {'at': '2026-10-21T20:00:00+00:00', 'updated': 3})
    db.close()
    assert sync.last_run(Session())['updated'] == 3
    assert sync.due(sync.last_run(Session())) is False


def test_sync_preserves_manual_edit_during_fetch(tmp_path, monkeypatch):
    Session = _db(tmp_path, 'concurrent')
    old = {'SG': {'price': 99, 'src': '官网', 'url': 'https://www.charleskeith.com/sg/fixture.html'}}
    _seed(Session, DEFAULT_STORE_ID, 'A', old)
    manual = {**old, 'TB': {'price': 268, 'src': '手工', 'at': '2026-08-13'}}

    def fetch(url):
        with Session() as editor:
            product = editor.query(Product).first()
            product.market_prices = json.dumps(manual, ensure_ascii=False)
            editor.commit()
        return {'price': 115, 'cur': 'SGD'}

    monkeypatch.setattr(sync, '_fetch_official', fetch)
    with Session() as db:
        stats = sync.run_once(db, store_id=DEFAULT_STORE_ID)
    with Session() as db:
        assert json.loads(db.query(Product).first().market_prices) == manual
    assert stats['conflicts'] == 1 and stats['updated'] == 0


def test_sync_now_reports_busy_instead_of_starting_a_second_run(monkeypatch):
    """有一轮在跑时「立即重读」必须如实回 started=False。

    `run_once` 拿不到锁就直接返回 `skipped`，而接口原来无条件回
    「已开始重读官网价，约 1 分钟出结果」——用户等一分钟、刷新状态，
    发现 `last_sync` 一点没动，只能以为按钮坏了。锁被占住时不许再起线程。
    """
    started = []
    monkeypatch.setattr(sync, 'run_once', lambda **kw: started.append(kw))
    assert sync.is_running() is False
    assert sync._state_lock.acquire(blocking=False)
    try:
        out = routes.sync_now(store_id=DEFAULT_STORE_ID)
    finally:
        sync._state_lock.release()
    assert out['started'] is False
    assert '已有一轮同步' in out['message']
    assert started == [], '已经知道跑不起来，还是起了后台线程'
    assert sync.is_running() is False


def test_sync_writes_unchanged_version_only_in_selected_store(tmp_path, monkeypatch):
    Session = _db(tmp_path, 'no-conflict')
    old = {'SG': {'price': 99, 'src': '官网', 'url': 'https://www.charleskeith.com/sg/fixture.html'}}
    _seed(Session, DEFAULT_STORE_ID, 'A', old)
    _seed(Session, 'store-b', 'A', old)
    monkeypatch.setattr(sync, '_fetch_official', lambda url: {'price': 115, 'cur': 'SGD'})
    with Session() as db:
        stats = sync.run_once(db, store_id=DEFAULT_STORE_ID)
    with Session() as db:
        mine = db.query(Product).filter(Product.store_id == DEFAULT_STORE_ID).first()
        other = db.query(Product).filter(Product.store_id == 'store-b').first()
        assert json.loads(mine.market_prices)['SG']['price'] == 115
        assert json.loads(other.market_prices) == old
    assert stats['conflicts'] == 0 and stats['updated'] == 1
