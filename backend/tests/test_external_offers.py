"""站外货源：1688 / Takealot 上看到的东西挂到商品详情页。

这个功能的风险和库内推荐不一样——库内推荐推错了顶多是「推荐不准」，
站外货源推错了，用户会照着链接去进货或比价，错一次就是真金白银。
所以这里钉的是：
1. 关键词必须真的命中（`cap` 不能命中 `capacity`，这正是推荐模块踩过的坑）；
2. 没命中就不展示，不为了填满版面硬凑；
3. 链接域名和 scheme 必须校验——这个字段最终会变成可点链接。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.external_offer_routes import (
    ExternalOfferIn,
    ExternalOfferPatch,
    archive_offer,
    create_offer,
    list_offers,
    offers_for_product,
    update_offer,
)
from database import Base
from models import DEFAULT_STORE_ID, ExternalOffer, Product, Store
from services.external_offers import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    matched_keywords,
    parse_keywords,
    recommend_external,
)

STORE = DEFAULT_STORE_ID


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id=STORE, name='默认店铺', status='active'))
    session.commit()
    yield session
    session.close()


def add_product(db, name, chinese='', sku='W-A-01'):
    product = Product(
        store_id=STORE,
        product_name=name,
        chinese_product_name=chinese,
        sku=sku,
        fee_category='Wigs',
        actual_sale_price_zar=599.0,
        is_archived=False,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def add_offer(db, platform='1688', url=None, title='', keywords='', price=None, **kwargs):
    url = url or ('https://detail.1688.com/offer/%s.html' % abs(hash(title or keywords)) if platform == '1688'
                  else 'https://www.takealot.com/x/PLID%d' % abs(hash(title or keywords)))
    offer = ExternalOffer(
        store_id=STORE,
        platform=platform,
        url=url,
        title=title,
        keywords=keywords,
        price=price,
        currency='CNY' if platform == '1688' else 'ZAR',
        **kwargs,
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


# ---------- 关键词解析与命中 ----------

def test_parse_keywords_accepts_chinese_and_english_separators():
    assert parse_keywords('wig，发网; hair cap、wig') == ['wig', '发网', 'hair cap']
    assert parse_keywords('  ') == []
    assert parse_keywords(None) == []


def test_english_keyword_needs_whole_word(db):
    """`cap` 不能命中 `capacity`——子串匹配是推荐模块踩过的坑，这里不重犯。"""
    product = add_product(db, 'Water Bottle 2L Large Capacity')
    offer = add_offer(db, title='Wig Cap Net', keywords='cap')
    assert matched_keywords(offer, product) == []

    wig = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    assert matched_keywords(offer, wig) == ['cap']


def test_phrase_keyword_matches_across_words(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    offer = add_offer(db, title='Velvet Wig Grip Band', keywords='wig cap')
    assert matched_keywords(offer, product) == ['wig cap']


def test_chinese_keyword_matches_substring(db):
    product = add_product(db, 'Human Hair Lace Wig', chinese='人发蕾丝假发')
    offer = add_offer(db, title='尼龙发网', keywords='假发')
    assert matched_keywords(offer, product) == ['假发']

    unrelated = add_offer(db, title='汽车手机支架', keywords='支架')
    assert matched_keywords(unrelated, product) == [], '中文也要真的出现在标题里才算命中'


def test_unmatched_offer_is_not_shown(db):
    """没有命中就不出现：宁可版面空着，也不塞不相干的东西。"""
    product = add_product(db, 'Human Hair Lace Wig')
    add_offer(db, title='汽车手机支架', keywords='支架,车载')
    result = recommend_external(db, STORE, product)
    assert result['sourcing'] == [] and result['market'] == []
    assert result['meta']['total'] == 1
    assert result['meta']['unmatched'] == 1


# ---------- 分栏与排序 ----------

def test_platforms_are_split_into_two_columns(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    add_offer(db, platform='1688', title='尼龙发网', keywords='wig cap')
    add_offer(db, platform='takealot', title='Wig Cap 2 Pack', keywords='wig cap')

    result = recommend_external(db, STORE, product)
    assert [item['platform'] for item in result['sourcing']] == ['1688']
    assert [item['platform'] for item in result['market']] == ['takealot']
    assert result['sourcing'][0]['currency'] == 'CNY', '1688 是人民币批发价'
    assert result['market'][0]['currency'] == 'ZAR', 'Takealot 是兰特零售价'


def test_more_matched_keywords_rank_first(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    one = add_offer(db, title='只有 cap', keywords='cap')
    two = add_offer(db, title='cap 和 wig 都命中', keywords='cap,wig')

    result = recommend_external(db, STORE, product)
    assert [item['id'] for item in result['sourcing']] == [two.id, one.id]
    assert result['sourcing'][0]['matched'] == ['cap', 'wig'], '要能说清为什么挂在这里'


def test_limit_truncates_each_column(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    for index in range(5):
        add_offer(db, title='货源 %d' % index, keywords='wig cap')
    result = recommend_external(db, STORE, product, limit=2)
    assert len(result['sourcing']) == 2
    assert result['meta']['limit_per_platform'] == 2


def test_limit_is_capped_at_max(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    result = recommend_external(db, STORE, product, limit=999)
    assert result['meta']['limit_per_platform'] == MAX_LIMIT


def test_zero_limit_returns_nothing_but_keeps_meta(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    add_offer(db, title='货源', keywords='wig cap')
    result = recommend_external(db, STORE, product, limit=0)
    assert result['sourcing'] == [] and result['market'] == []
    assert result['meta']['total'] == 1, '关掉展示不该让统计也变成 0'


def test_archived_offer_is_skipped(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    offer = add_offer(db, title='货源', keywords='wig cap')
    archive_offer(offer.id, db, STORE)
    assert recommend_external(db, STORE, product)['sourcing'] == []


# ---------- 录入校验 ----------

def test_create_offer_rejects_wrong_domain():
    """把 Takealot 链接填成 1688，详情页点开就是错的平台。"""
    with pytest.raises(HTTPException) as exc:
        create_offer(ExternalOfferIn(platform='1688', url='https://www.takealot.com/x/PLID1'), _db_stub())
    assert exc.value.status_code == 400


def test_create_offer_rejects_javascript_scheme():
    """这个字段会渲染成可点链接，`javascript:` 混进来就是注入点。"""
    with pytest.raises(HTTPException) as exc:
        create_offer(ExternalOfferIn(platform='1688', url='javascript:alert(1)//1688.com'), _db_stub())
    assert exc.value.status_code == 400


def test_create_offer_accepts_subdomain(db):
    created = create_offer(
        ExternalOfferIn(platform='takealot', url='https://m.takealot.com/x/PLID1', title='Wig Cap'),
        db, STORE,
    )
    assert created['platform'] == 'takealot'
    assert created['currency'] == 'ZAR'


def test_duplicate_url_is_rejected(db):
    create_offer(ExternalOfferIn(platform='1688', url='https://detail.1688.com/offer/1.html'), db, STORE)
    with pytest.raises(HTTPException) as exc:
        create_offer(ExternalOfferIn(platform='1688', url='https://detail.1688.com/offer/1.html'), db, STORE)
    assert exc.value.status_code == 409


def test_zero_price_is_stored_as_empty(db):
    """1688 上「价格待议」常被填成 0，留着会在界面上显示 ¥0。"""
    created = create_offer(
        ExternalOfferIn(platform='1688', url='https://detail.1688.com/offer/2.html', price=0, moq=0),
        db, STORE,
    )
    assert created['price'] is None and created['moq'] is None


def test_update_can_change_keywords_and_price(db):
    created = create_offer(
        ExternalOfferIn(platform='1688', url='https://detail.1688.com/offer/3.html', keywords='旧词'),
        db, STORE,
    )
    updated = update_offer(
        created['id'],
        ExternalOfferPatch(keywords='发网, wig cap', price=1.1),
        db, STORE,
    )
    assert updated['keywords'] == ['发网', 'wig cap']
    assert updated['price'] == 1.1


def test_list_filters_by_platform(db):
    create_offer(ExternalOfferIn(platform='1688', url='https://detail.1688.com/offer/4.html'), db, STORE)
    create_offer(ExternalOfferIn(platform='takealot', url='https://www.takealot.com/x/PLID4'), db, STORE)
    assert list_offers(None, None, False, db, STORE)['counts'] == {'1688': 1, 'takealot': 1}
    only_ta = list_offers('takealot', None, False, db, STORE)
    assert only_ta['total'] == 1 and only_ta['items'][0]['platform'] == 'takealot'


# ---------- 路由层 ----------

def test_for_product_returns_404_for_unknown_product(db):
    with pytest.raises(HTTPException) as exc:
        offers_for_product('not-a-real-id', DEFAULT_LIMIT, db, STORE)
    assert exc.value.status_code == 404


def test_for_product_payload_shape(db):
    product = add_product(db, 'Wig Cap Net For Full Frontal Wig')
    add_offer(db, title='尼龙发网', keywords='wig cap')
    payload = offers_for_product(product.id, DEFAULT_LIMIT, db, STORE)
    assert set(payload) == {'sourcing', 'market', 'meta'}
    assert set(payload['meta']) == {'total', 'matched', 'unmatched', 'limit_per_platform', 'match_rule', 'sort'}


def _db_stub():
    """只为验证「域名/scheme 校验发生在查库之前」，不需要真会话。"""
    return None
