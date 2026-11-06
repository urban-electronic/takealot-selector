"""My Offers 心愿单回传 + 多变体身份回写的回归。

两件事都源于同一个事实：产品主档一行 = 一个 PLID，`product_name` 是所有变体
共享的标题，多个 SKU 靠 `sku` 空格拼接、`chinese_product_name` 按位置对应。
这里钉住：上架回执必须把 SKU / 规格名 / 各变体标题按同一顺序写回，且不能
覆盖人工起好的名字；心愿单数必须先探测字段名再同步，猜错不能写 0。
"""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Product  # noqa: F401  建表前注册
from api import product_routes
from api.product_routes import (ListOfferIn, _merge_variant_identity,
                                list_offer, sync_wishlist)
from services import takealot_seller_api as seller

KEY = 'a' * 128


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def reset_seller_cache(monkeypatch):
    monkeypatch.setenv('API_KEY', 'test-application-access-key-' + 'x' * 32)
    seller._cache.clear()
    seller._active_offer_read = None


def product(db, **overrides):
    fields = dict(
        id='p1', store_id='default-store', product_no=617,
        takealot_url='https://www.takealot.com/necklace/PLID96357594',
        actual_sale_price_zar=200,
    )
    fields.update(overrides)
    row = Product(**fields)
    db.add(row)
    db.commit()
    return row


def stub(sku='', name='', titles=''):
    return SimpleNamespace(sku=sku, chinese_product_name=name, variant_titles=titles)


def ok(sku, label, title):
    return {'ok': True, 'sku': sku, 'variant_label': label, 'variant_title': title}


# ---- 多变体身份回写 ----------------------------------------------------------


def test_merge_writes_sku_labels_and_titles_in_the_same_order():
    """三个数组必须同序：SKU、规格名、变体标题错一位，分支行就会张冠李戴。"""
    sku_text, name_text, titles_text = _merge_variant_identity(stub(), [
        ok('9902739445801', 'Blue', 'Necklace - Blue'),
        ok('9902739445802', 'Red', 'Necklace - Red'),
    ])
    assert sku_text == '9902739445801 9902739445802'
    assert name_text == 'Blue Red'
    assert json.loads(titles_text) == ['Necklace - Blue', 'Necklace - Red']


def test_merge_keeps_manual_chinese_names():
    """人工起的中文名不许被目录里的英文 "Blue / L" 覆盖。"""
    sku_text, name_text, titles_text = _merge_variant_identity(
        stub('9902739445801 9902739445802', '银项链蓝 银项链红',
             json.dumps(['旧标题蓝', '旧标题红'], ensure_ascii=False)),
        [ok('9902739445801', 'Blue', 'Necklace - Blue'),
         ok('9902739445802', 'Red', 'Necklace - Red')],
    )
    assert sku_text == '9902739445801 9902739445802'
    assert name_text is None, '人工命名完整时一个字都不该改'
    assert json.loads(titles_text) == ['旧标题蓝', '旧标题红']


def test_second_listing_appends_platform_skus_and_fills_missing_label():
    """补上第二批变体：旧 SKU 保留、新 SKU 追加、规格名只补空缺的那一格。"""
    sku_text, name_text, titles_text = _merge_variant_identity(
        stub('9902739445801 9902739445802', '蓝色 红色',
             json.dumps(['Blue title', 'Red title'], ensure_ascii=False)),
        [ok('9902739445803', 'Green', 'Necklace - Green')],
    )
    assert sku_text == '9902739445801 9902739445802 9902739445803'
    assert name_text == '蓝色 红色 Green'
    assert json.loads(titles_text) == ['Blue title', 'Red title', 'Necklace - Green']


def test_manual_sku_is_never_touched_by_platform_receipts():
    """自建 SKU（含字母/连字符）是产品身份，平台回执不许改动。"""
    sku_text, name_text, _ = _merge_variant_identity(
        stub('W-S-12-02', '银项链'),
        [ok('9902739445801', 'Blue', 'Necklace - Blue')],
    )
    assert sku_text == 'W-S-12-02'
    assert name_text is None


def test_placeholder_labels_and_broken_titles_json_do_not_crash():
    sku_text, name_text, titles_text = _merge_variant_identity(
        stub('9902739445801 9902739445802', '主商品 规格待识别', 'not-json'),
        [ok('9902739445801', 'Blue', 'Necklace - Blue')],
    )
    assert sku_text == '9902739445801 9902739445802'
    assert name_text == 'Blue 规格待识别'
    assert json.loads(titles_text) == ['Necklace - Blue', '']


def test_failed_variant_does_not_create_a_ghost_sku():
    sku_text, _, titles_text = _merge_variant_identity(stub(), [
        ok('9902739445801', 'Blue', 'Necklace - Blue'),
        {'ok': False, 'sku': None, 'variant_label': 'Red', 'variant_title': 'x', 'reason': '拒绝'},
    ])
    assert sku_text == '9902739445801'
    assert json.loads(titles_text) == ['Necklace - Blue']


@pytest.mark.parametrize('existing_sku,existing_name,expected_name', [
    ('', '', 'Blue Red'),
    ('9902739445801 9902739445802', '人工银项链', '人工银项链'),
    ('9902739445801', '主商品银项链', '主商品银项链 Red'),
])
def test_list_offer_end_to_end_persists_identity(monkeypatch, db,
                                                existing_sku, existing_name, expected_name):
    """从目录变体到落库：results 里的 title 要真的变成 variant_titles。"""
    from services import takealot_portal_api as portal

    row = product(db, sku=existing_sku, chinese_product_name=existing_name)
    variants = [
        {'barcode': '6009512345678', 'tsin': '104036518', 'label': 'Blue',
         'title': 'Necklace - Blue', 'has_offer': False},
        {'barcode': '6009512345679', 'tsin': '104036519', 'label': 'Red',
         'title': 'Necklace - Red', 'has_offer': False},
    ]
    monkeypatch.setattr(portal, 'configured_token', lambda db: 'portal-token')
    monkeypatch.setattr(portal, 'token_state', lambda db: {'expired': False})
    monkeypatch.setattr(portal, 'fetch', lambda plid, token: {
        'success': True, 'catalogue_variants': variants,
        'catalogue_variant_count': 2,
    })
    monkeypatch.setattr(seller, 'configured_key', lambda db: KEY)
    issued = iter(('9902739445801', '9902739445802'))

    def fake_create(key, identifier, price, rrp=None, **kwargs):
        return {'ok': True, 'payload': {'offer': {'sku': next(issued), 'offer_id': 'o', 'status': 'Buyable'}}}

    monkeypatch.setattr(seller, 'create_offer', fake_create)

    result = list_offer('p1', ListOfferIn(), db, 'default-store')
    assert result['product_sku'] == '9902739445801 9902739445802'
    assert result['chinese_product_name'] == expected_name
    assert json.loads(result['variant_titles']) == ['Necklace - Blue', 'Necklace - Red']
    db.refresh(row)
    assert row.sku == result['product_sku']
    assert row.chinese_product_name == expected_name
    assert json.loads(row.variant_titles) == ['Necklace - Blue', 'Necklace - Red']


# ---- My Offers 心愿单字段探测 ------------------------------------------------


def mock_client(monkeypatch, respond):
    original = httpx.Client
    monkeypatch.setattr(seller.httpx, 'Client',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))


def test_discover_prefers_numeric_wishlist_field(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        assert request.url.params.get('fields') is None, '探测请求不能带 fields 白名单'
        return httpx.Response(200, json={'items': [{
            'sku': '111', 'wishlist_enabled': True,
            'wishlist_label': 'hot', 'wishlist_count': 23,
        }]})

    mock_client(monkeypatch, respond)
    field, keys = seller.discover_wishlist_field(KEY)
    assert field == 'wishlist_count'
    assert 'wishlist_count' in keys
    assert len(seen) == 1


def test_discover_reports_none_when_interface_has_no_wishlist(monkeypatch):
    def respond(request):
        return httpx.Response(200, json={'items': [{'sku': '111', 'selling_price': 100}]})

    mock_client(monkeypatch, respond)
    field, keys = seller.discover_wishlist_field(KEY)
    assert field is None and keys == ['selling_price', 'sku']


def test_wishlist_value_accepts_numbers_and_digit_strings_only():
    assert seller.wishlist_value({'wishlist_count': 23}, 'wishlist_count') == 23
    assert seller.wishlist_value({'wishlist_count': '42'}, 'wishlist_count') == 42
    assert seller.wishlist_value({'wishlist_count': True}, 'wishlist_count') is None
    assert seller.wishlist_value({'wishlist_count': -1}, 'wishlist_count') is None
    assert seller.wishlist_value({'wishlist_count': 'many'}, 'wishlist_count') is None
    assert seller.wishlist_value({'wishlists': 7}) == 7, '字段名未知时模糊扫含 wish 的键'
    assert seller.wishlist_value(None) is None


# ---- 心愿单回写链路 ----------------------------------------------------------


def wire(monkeypatch, field='wishlist_count', rows=None, keys=None):
    monkeypatch.setattr(seller, 'configured_key', lambda db: KEY)
    monkeypatch.setattr(seller, 'discover_wishlist_field',
                        lambda key: (field, keys if keys is not None else ['sku', field or 'x']))
    calls = []

    def fake_load(key, extra_fields=()):
        calls.append(extra_fields)
        return rows or []

    monkeypatch.setattr(seller, '_load_offers', fake_load)
    return calls


def test_sync_matches_by_sku_then_plid_and_never_invents_zero(monkeypatch, db):
    a = product(db, id='a', sku='111 222')
    b = product(db, id='b', sku='', takealot_url='https://www.takealot.com/x/PLID777')
    c = product(db, id='c', sku='999', wishlist_count=3)
    wire(monkeypatch, rows=[
        {'sku': '111', 'productline_id': 1, 'wishlist_count': 5},
        {'sku': '222', 'productline_id': 1, 'wishlist_count': 9},
        {'sku': '888', 'productline_id': 777, 'wishlist_count': 7},
    ])
    result = sync_wishlist(db, 'default-store')
    assert result['ok'] and result['matched'] == 2
    db.refresh(a); db.refresh(b); db.refresh(c)
    assert a.wishlist_count == 9, '同一产品多 SKU 取最大值，不能相加'
    assert b.wishlist_count == 7, '没有 SKU 时用 PLID 兜底'
    assert c.wishlist_count == 3, '未匹配的产品保留旧值，不写成 0'
    assert c.wishlist_updated_at is None
    assert a.wishlist_updated_at is not None and b.wishlist_updated_at is not None


def test_sync_uses_discovered_field_as_extra_field(monkeypatch, db):
    calls = wire(monkeypatch, rows=[])
    product(db, sku='111')
    sync_wishlist(db, 'default-store')
    assert calls == [('wishlist_count',)]


def test_sync_without_field_returns_actionable_diagnostic(monkeypatch, db):
    """心愿单字段没找到不算整次失败：变体标题回填照样能用，理由要如实给全。"""
    row = product(db, sku='111')
    calls = wire(monkeypatch, field=None, keys=['sku', 'selling_price'])
    result = sync_wishlist(db, 'default-store')
    assert result['ok'] is True and result['wishlist_ok'] is False
    assert result['matched'] == 0
    assert 'My Offers' in result['reason']
    assert result['probed_keys'] == ['sku', 'selling_price']
    assert calls == [()], '仍要拉一次常规 offers 做变体标题回填，不是白拉'
    db.refresh(row)
    assert row.wishlist_count is None, '不许把「没拿到」写成 0'


def test_sync_requires_seller_key(monkeypatch, db):
    monkeypatch.setattr(seller, 'configured_key', lambda db: '')
    with pytest.raises(HTTPException) as exc:
        sync_wishlist(db, 'default-store')
    assert exc.value.status_code == 400


def test_extra_fields_do_not_disturb_existing_offer_reads(monkeypatch):
    """探测出来的新字段只能追加，不能挤掉原来 11 个字段或污染主缓存。"""
    seen = []

    def respond(request):
        seen.append(list(request.url.params.get_list('fields')))
        return httpx.Response(200, json={'items': [{'sku': '111'}]})

    mock_client(monkeypatch, respond)
    seller._load_offers(KEY)
    seller._load_offers(KEY, extra_fields=('wishlist_count',))
    assert 'wishlist_count' not in seen[0]
    assert 'wishlist_count' in seen[1]
    for field in seller.OFFER_FIELDS:
        assert field in seen[1]
    assert len(seen) == 2, '不同字段集合各缓存一次，不能误命中主缓存'


def test_default_offer_cache_key_stays_unchanged():
    """常规读取的缓存摘要是既有契约（冷却、在途去重、外部断言都按它索引）。

    探测字段只能另开分区；给默认调用换摘要会让 401 冷却状态凭空消失，
    也会让已有的卖家资料缓存在每次发版后全部失效。
    """
    import hashlib

    legacy = hashlib.sha256(KEY.encode()).hexdigest()
    assert seller._offer_digest(KEY) == legacy
    assert seller._offer_digest(KEY, ()) == legacy
    assert seller._offer_digest(KEY, ('wishlist_count',)) != legacy
    assert seller._offer_digest(KEY, ('wishlist_count',)) == \
        hashlib.sha256((KEY + '|wishlist_count').encode()).hexdigest()


# ---- 历史产品变体标题回填（不用重新上架） -----------------------------------


def test_backfill_writes_titles_in_sku_order(monkeypatch, db):
    a = product(db, id='a', sku='111 222', product_name='Necklace')
    b = product(db, id='b', sku='333', product_name='Ring')
    wire(monkeypatch, rows=[
        {'sku': '111', 'title': 'Necklace - Blue'},
        {'sku': '222', 'title': 'Necklace - Red'},
        {'sku': '333', 'title': 'Ring - L'},
    ])
    result = sync_wishlist(db, 'default-store')
    db.refresh(a); db.refresh(b)
    assert json.loads(a.variant_titles) == ['Necklace - Blue', 'Necklace - Red']
    assert not (b.variant_titles or ''), '单 SKU 没有「分不清」的问题，不写字段'
    assert result['variant_titles']['updated'] == 1


def test_backfill_never_overwrites_existing_or_manual_titles(monkeypatch, db):
    manual = json.dumps(['人工写的蓝', '人工写的红'], ensure_ascii=False)
    a = product(db, id='a', sku='111 222', product_name='Necklace', variant_titles=manual)
    wire(monkeypatch, rows=[
        {'sku': '111', 'title': 'Necklace - Blue'},
        {'sku': '222', 'title': 'Necklace - Red'},
    ])
    result = sync_wishlist(db, 'default-store')
    db.refresh(a)
    assert a.variant_titles == manual, '已补过或人工修正过的标题绝不覆盖'
    assert result['variant_titles']['updated'] == 0


def test_backfill_ignores_shared_title_and_keeps_sku_alignment(monkeypatch, db):
    a = product(db, id='a', sku='111 222 333', product_name='Necklace')
    wire(monkeypatch, rows=[
        {'sku': '111', 'title': 'Necklace'},  # 和共享主标题一样，填了也分不清
        {'sku': '222', 'title': 'Necklace - Red'},
    ])
    sync_wishlist(db, 'default-store')
    db.refresh(a)
    assert json.loads(a.variant_titles) == ['', 'Necklace - Red', ''], \
        '缺的 SKU 留空占位，不能把第三个 SKU 的标题挤到第二个位置'


def test_backfill_still_runs_when_wishlist_field_is_absent(monkeypatch, db):
    """这是关键：用户点的虽然是「同步 Wishlist」，但旧数据补齐不能依赖该字段存在。"""
    a = product(db, id='a', sku='111 222', product_name='Necklace')
    wire(monkeypatch, field=None, rows=[
        {'sku': '111', 'title': 'Necklace - Blue'},
        {'sku': '222', 'title': 'Necklace - Red'},
    ])
    result = sync_wishlist(db, 'default-store')
    assert result['wishlist_ok'] is False
    db.refresh(a)
    assert json.loads(a.variant_titles) == ['Necklace - Blue', 'Necklace - Red']
    assert result['variant_titles']['updated'] == 1
