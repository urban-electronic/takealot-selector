"""详情页推荐：同类 + 配套周边。

这个功能最容易出的两类错：
1. **把不相干的东西摆成「配套」**——用户会照着买，错一次就是一笔真实采购；
2. **同一件商品在两栏各出现一次**，或者库里本来就有两条重复记录还都推出来，
   看起来像三件商品。

所以下面钉的是「宁可少推，也不凑数」：低分不进榜、配件不能混进同类、
同一身份只出现一次、数量上限必须真的截断。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, ProcurementRecord, Product, Store
from services.product_recommendation import (
    DEFAULT_COMPLEMENT_LIMIT,
    DEFAULT_SIMILAR_LIMIT,
    MIN_SCORE,
    WEIGHT_ATTRIBUTE,
    WEIGHT_BEHAVIOR,
    WEIGHT_CATEGORY,
    WEIGHT_COMPLEMENT,
    category_terms,
    is_accessory_of,
    recommend,
    tokens,
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


def add(db, no, name, sku='', plid='', fee='Wigs', price=599.0, weight=0.2, archived=False):
    product = Product(
        store_id=STORE, product_no=no, product_name=name, sku=sku, plid=plid,
        fee_category=fee, actual_sale_price_zar=price, actual_weight_kg=weight,
        is_archived=archived,
    )
    db.add(product)
    db.commit()
    return product


def anchor(db):
    return add(db, 1, 'Human Brazilian Straight Full Frontal Wig 12 Inch', sku='W-S-12-02',
               plid='102479085')


# ---------- 基础：同类要排在前面 ----------

def test_same_category_and_similar_title_ranks_first(db):
    target = anchor(db)
    close = add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Brown Brazilian Hair Pixie Water Curly Wig 8 Inch', sku='W-C-8-02')
    add(db, 4, 'Wireless Charger 15W Fast Charge', fee='Electronics', price=199.0, weight=0.1)

    result = recommend(db, STORE, target)
    names = [item['name'] for item in result['similar']]
    assert names, '至少要有同类推荐'
    assert names[0] == close.product_name, '标题最接近的同类必须排第一'
    assert all('Wireless Charger' not in name for name in names), '不相干的品类不该进同类'


def test_self_is_never_recommended(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    result = recommend(db, STORE, target)
    assert all(item['product_id'] != target.id for item in result['similar'])


def test_archived_candidates_are_skipped(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Human Brazilian Straight Full Frontal Wig 16 Inch', sku='W-S-16-01', archived=True)
    result = recommend(db, STORE, target)
    assert all(item['sku'] != 'W-S-16-01' for item in result['similar'])


# ---------- 配件 ----------

def test_accessory_goes_to_complement_not_similar(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Wig Cap Net For Full Frontal Wig Breathable', sku='W-A-01', price=49.0, weight=0.02)

    result = recommend(db, STORE, target)
    complement_skus = [item['sku'] for item in result['complement']]
    similar_skus = [item['sku'] for item in result['similar']]
    assert 'W-A-01' in complement_skus, '发网是假发的配件，要进「配套周边」'
    assert 'W-A-01' not in similar_skus, '同一件商品不能在两栏各出现一次'


def test_accessory_rule_is_recorded_in_reasons(db):
    target = anchor(db)
    add(db, 3, 'Wig Cap Net For Full Frontal Wig Breathable', sku='W-A-01', price=49.0)
    result = recommend(db, STORE, target)
    assert result['complement'], '应当识别出配件'
    assert any('配套' in reason or '配件' in reason for reason in result['complement'][0]['reasons']), \
        '必须说明为什么算配套，不能只给个分数'


def test_unrelated_accessory_is_not_paired(db):
    """充电器不是假发的配件——配件词必须和本品类词同时出现才算。"""
    wig = anchor(db)
    charger = add(db, 2, 'Wireless Charger 15W Fast Charge', fee='Electronics', price=199.0)
    matched, _ = is_accessory_of(charger, wig)
    assert matched is False


# ---------- 去重 ----------

def test_same_plid_is_deduplicated(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Wig 14 Inch', sku='W-S-14-01', plid='999001')
    add(db, 3, 'Human Brazilian Straight Wig 14 Inch (dup)', sku='W-S-14-02', plid='999001')

    result = recommend(db, STORE, target)
    plids = [item['plid'] for item in result['similar']]
    assert len(plids) == len(set(plids)), '同一 PLID 只能出现一次'


def test_same_title_normalized_is_deduplicated(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'human  brazilian straight wig 14 inch!', sku='W-S-14-09')

    result = recommend(db, STORE, target)
    names = [item['name'].lower() for item in result['similar']]
    assert len(names) == len(set(names)), '标题归一化后相同视为同一件商品'


# ---------- 数量与阈值 ----------

def test_limits_are_respected(db):
    target = anchor(db)
    for index in range(12):
        add(db, 10 + index, 'Human Brazilian Straight Wig %d Inch' % (10 + index),
            sku='W-S-1%d-01' % index)
    result = recommend(db, STORE, target, similar_limit=3, complement_limit=2)
    assert len(result['similar']) <= 3
    assert len(result['complement']) <= 2


def test_default_limits_are_six_and_four(db):
    assert DEFAULT_SIMILAR_LIMIT == 6
    assert DEFAULT_COMPLEMENT_LIMIT == 4


def test_low_score_is_not_padded(db):
    """只有一个真正相关的候选时，宁可只推一条，也不拿不相干的凑够 6 条。"""
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Stainless Steel Kitchen Sink Drain', fee='Home', price=299.0)

    result = recommend(db, STORE, target)
    assert len(result['similar']) == 1
    assert all(item['score'] >= MIN_SCORE for item in result['similar'])


# ---------- 行为信号 ----------

def test_co_purchase_boosts_score(db):
    target = anchor(db)
    plain = add(db, 2, 'Human Brazilian Straight Wig 14 Inch', sku='W-S-14-01')
    boosted = add(db, 3, 'Human Brazilian Straight Wig 16 Inch', sku='W-S-16-01')

    before = {item['sku']: item['score'] for item in recommend(db, STORE, target)['similar']}

    for day in ('2026-10-20', '2026-10-21', '2026-10-22'):
        db.add(ProcurementRecord(store_id=STORE, product_id=target.id, product_no=target.product_no,
                                 product_name=target.product_name, quantity=5, recorded_at=day))
        db.add(ProcurementRecord(store_id=STORE, product_id=boosted.id, product_no=boosted.product_no,
                                 product_name=boosted.product_name, quantity=5, recorded_at=day))
        db.commit()

    after = {item['sku']: item['score'] for item in recommend(db, STORE, target)['similar']}
    assert after['W-S-16-01'] > before['W-S-16-01'], '同批采购过的商品分数要更高'
    assert after['W-S-14-01'] == before['W-S-14-01'], '没有共现的商品不该被带动'


# ---------- 元信息 ----------

def test_meta_documents_strategy_weights_and_limits(db):
    target = anchor(db)
    result = recommend(db, STORE, target)
    meta = result['meta']
    assert meta['weights'] == {
        'category': WEIGHT_CATEGORY,
        'attribute': WEIGHT_ATTRIBUTE,
        'complement': WEIGHT_COMPLEMENT,
        'behavior': WEIGHT_BEHAVIOR,
    }
    assert abs(sum(meta['weights'].values()) - 1.0) < 1e-9, '权重必须归一，否则分数不可比'
    assert meta['dedup'] and meta['strategy']
    assert meta['limits'] == {'similar': 6, 'complement': 4}


def test_every_recommendation_carries_reasons(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Wig 14 Inch', sku='W-S-14-01')
    result = recommend(db, STORE, target)
    assert result['similar']
    assert all(item['reasons'] for item in result['similar']), '每条推荐都要能说清来源'


# ---------- 词表 ----------

def test_category_terms_drop_accessory_words():
    terms = category_terms(Product(product_name='Wig Cap Net For Full Frontal Wig'))
    assert 'cap' not in terms and 'net' not in terms
    assert 'wig' in terms


def test_tokens_ignore_stopwords_and_short_words():
    assert 'the' not in tokens('The Best Wig')
    assert 'wig' in tokens('The Best Wig')


# ---------- 路由：详情页真正调用的那一层 ----------

def _route(db, product_id, similar_limit=6, complement_limit=4):
    from api.product_routes import product_recommendations
    return product_recommendations(product_id, similar_limit, complement_limit, db, STORE)


def test_route_returns_404_for_unknown_product(db):
    """商品不存在要回 404。回 500 会让前端把「链接失效」当成「推荐坏了」。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _route(db, 'not-a-real-id')
    assert exc.value.status_code == 404


def test_route_payload_shape_and_limits(db):
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Wig Cap Net For Full Frontal Wig Breathable', sku='W-A-01', price=49.0)

    payload = _route(db, target.id, similar_limit=1, complement_limit=1)
    assert set(payload) == {'similar', 'complement', 'meta'}
    assert payload['meta']['limits'] == {'similar': 1, 'complement': 1}, '上限要如实回显，界面直接展示'
    assert len(payload['similar']) <= 1 and len(payload['complement']) <= 1
    for item in payload['similar'] + payload['complement']:
        assert item['reasons'], '每条推荐都要带依据'
        assert item['score'] >= MIN_SCORE, '低于阈值的不能出现在结果里'


def test_route_zero_limit_still_fills_the_other_column(db):
    """只要一栏：similar_limit=0 不能把 complement 也清空。"""
    target = anchor(db)
    add(db, 2, 'Human Brazilian Straight Full Frontal Wig 14 Inch', sku='W-S-14-01')
    add(db, 3, 'Wig Cap Net For Full Frontal Wig Breathable', sku='W-A-01', price=49.0)

    payload = _route(db, target.id, similar_limit=0, complement_limit=4)
    assert payload['similar'] == []
    assert payload['complement'], '关掉同类不该连带关掉配套'


# ---------- 线上真机验证抓到的两个误判（2026-10-28） ----------
# 真实库里「Hair Clipper」被当成 hair clip 的配件，「Pet Hair Remover Gloves」
# 因为共享一个 hair 被推成假发的配套。两条都是把不相干的东西摆成「配着买」。

def test_phrase_match_respects_word_boundary():
    from services.product_recommendation import contains_phrase
    assert contains_phrase('Electric 5W Hair Clipper, Hair Trimmer Kit', 'hair clip') is False, \
        'hair clipper 不是 hair clip，子串匹配会把它误判成配件'
    assert contains_phrase('Wig Cap Net For Full Frontal Wig', 'wig cap') is True
    assert contains_phrase('Hair Clips For Women', 'hair clip') is True, '复数要认'
    assert contains_phrase('真空吸尘器滤网 3 件装', '滤网') is True, '中文按子串'


def test_pet_hair_remover_is_not_a_wig_accessory(db):
    wig = anchor(db)
    remover = add(db, 2, 'Static Pet Hair Remover Gloves Black 2-Pack, Reusable', fee='Beauty', price=129.0)
    matched, _ = is_accessory_of(remover, wig)
    assert matched is False, '宠物毛清理手套和假发只共享一个 hair，不是配套'

    result = recommend(db, STORE, wig)
    assert all('Pet Hair' not in item['name'] for item in result['complement']), \
        '这类噪音不能出现在配套栏里'


def test_real_accessory_still_matches_after_tightening(db):
    """收紧判据之后，真配件不能被一起误伤。"""
    wig = anchor(db)
    cap = add(db, 2, 'Wig Cap Net For Full Frontal Wig Breathable', sku='W-A-01', price=49.0)
    matched, reason = is_accessory_of(cap, wig)
    assert matched is True and 'wig cap' in reason


# ---------- 只共享修饰词不算配套（真机验证第二轮） ----------
# 线上配套栏出现过「Dual Wireless Microphones」挂在无人机下面，理由是「同类目配件（dual）」。

def test_sharing_only_a_modifier_is_not_a_companion(db):
    drone = add(db, 1, 'Mini Drone With Remote And Dual 4K Cameras', fee='Electronics', price=1299.0)
    mic = add(db, 2, 'Dual Wireless Microphones with Type-C Receiver and Cable',
              fee='Electronics', price=499.0)
    matched, reason = is_accessory_of(mic, drone)
    assert matched is False, '只共享一个 dual，不是无人机的配件；理由是 %r' % reason


def test_accessory_word_must_sit_next_to_the_category_word(db):
    vacuum = add(db, 1, 'Robot Vacuum Replacement Parts 15-Pack', fee='Small Appliances', price=399.0)
    miele = add(db, 2, 'Miele - GN HyClean 3D Vacuum Cleaner Filter', fee='Small Appliances', price=349.0)
    matched, reason = is_accessory_of(miele, vacuum)
    assert matched is True, '滤网是吸尘器配件，vacuum 与 filter 隔一个词也算挨着'
    assert 'vacuum + filter' in reason, '理由要说清是哪两个词凑成的配套，实际是 %r' % reason


def test_modifier_words_do_not_inflate_title_overlap():
    """compatible / dual / max 这类修饰词进词表会让「标题重合 N 个品类词」虚高。"""
    from services.product_recommendation import tokens
    for word in ('compatible', 'dual', 'max', 'plus', 'mini'):
        assert word not in tokens('Compatible With Tapo RV30 Max Plus Mini Dual Drone'), \
            '%s 是修饰词，不该进品类词表' % word


def test_standard_url_plid_deduplicates_legacy_products(db):
    target = anchor(db)
    target.plid = ''
    target.takealot_url = 'https://www.takealot.com/old-title/PLID800'
    duplicate = add(db, 90, 'Human Brazilian Straight Wig Different Title', sku='DIFFERENT')
    duplicate.takealot_url = 'https://www.takealot.com/new-title/PLID800'
    db.commit()
    result = recommend(db, STORE, target)
    assert duplicate.id not in {item['product_id'] for item in result['similar'] + result['complement']}
