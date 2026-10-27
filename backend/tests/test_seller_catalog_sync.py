"""卖家后台 → 正式产品主档的同步。

这个功能最容易出两类错，两类都很贵：

1. **重复导入**：同一件商品被当成新的又建一条，库存和发运从此挂在两条记录上；
2. **猜错身份**：靠标题相似或卖家返回的行序去对应规格，把 A 规格的库存挂到 B 上。

所以下面每一例都在钉「宁可不做，也不猜」：
多规格对不上就跳过、命中多个本地记录就跳过、归档商品不重复新建、
新建只写卖家资料里确实有的字段（采购成本/库存/利润一律留空）。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, Product, Store
from services.seller_catalog_sync import build_plan, apply_plan, slugify, product_url, SellerSyncConflict

STORE = DEFAULT_STORE_ID


def offer(tsin, plid, sku, title='Wig', price=299.0, status='Buyable'):
    return {
        'tsin_id': tsin, 'productline_id': plid, 'sku': sku, 'title': title,
        'selling_price': price, 'status': status,
        'image_url': 'https://media.takealot.com/covers_images/%s.jpg' % tsin,
    }


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id=STORE, name='默认店铺', status='active'))
    session.commit()
    yield session
    session.close()


def add(db, **kwargs):
    product = Product(store_id=STORE, **kwargs)
    db.add(product)
    db.commit()
    return product


def apply_all(db, offers):
    plan = build_plan(db, STORE, offers)
    result = apply_plan(db, STORE, plan)
    db.commit()
    return plan, result


# --- slug 与链接 ----------------------------------------------------------

def test_slug_matches_takealot_truncation():
    """真实链接的 slug 是 64 字符截断的，标题改过也不会跟着变。"""
    assert slugify('BMW F30 M4 Mirror Covers') == 'bmw-f30-m4-mirror-covers'
    long = slugify('2.5" 500GB Ultra Slim Portable External Hard Drive USB3.0 HDD Storage')
    assert len(long) == 64
    assert not long.endswith('-')
    assert product_url('95531935', 'BMW F30 M4 Mirror Covers') == \
        'https://www.takealot.com/bmw-f30-m4-mirror-covers/PLID95531935'


# --- 本地没有：新建 -------------------------------------------------------

def test_missing_product_is_created_with_only_seller_facts(db):
    plan, result = apply_all(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert result['created'] == 1
    created = db.query(Product).filter(Product.plid == '104410843').one()
    assert created.sku == 'W-S-12-02'
    assert created.tsin == '104410843'
    assert created.product_name == 'Wig'
    assert created.takealot_url.endswith('/PLID104410843')
    assert created.actual_sale_price_zar == 299.0
    # 没有依据的字段必须留空，不能编。
    assert created.purchase_cost_cny is None
    assert created.purchase_url == ''
    assert created.profit_zar is None


def test_multi_spec_product_joins_skus_and_tsins(db):
    rows = [offer('111', '104040546', 'W-S-18-02'), offer('222', '104040546', 'W-S-18-01')]
    apply_all(db, rows)
    created = db.query(Product).filter(Product.plid == '104040546').one()
    assert created.sku.split('\n') == ['W-S-18-01', 'W-S-18-02']
    assert created.tsin.split('\n') == ['111', '222']


def test_differing_variant_prices_leave_price_empty(db):
    """多规格价格不一致时不能把一个规格的价按到整条商品上。"""
    rows = [offer('111', '900', 'W-A', price=100.0), offer('222', '900', 'W-B', price=200.0)]
    apply_all(db, rows)
    assert db.query(Product).filter(Product.plid == '900').one().actual_sale_price_zar is None


def test_same_offer_twice_does_not_create_duplicate(db):
    rows = [offer('104410843', '104410843', 'W-S-12-02')]
    apply_all(db, rows)
    plan = build_plan(db, STORE, rows)
    assert plan['stats']['create_count'] == 0
    assert plan['stats']['unchanged_count'] == 1
    assert db.query(Product).filter(Product.plid == '104410843').count() == 1


def test_archived_product_is_not_duplicated(db):
    add(db, plid='104410843', product_no=1, is_archived=True,
        takealot_url='https://www.takealot.com/x/PLID104410843')
    plan, result = apply_all(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert result['created'] == 0
    assert plan['stats']['skip_count'] == 1
    assert '归档' in plan['skipped'][0]['reason']


def test_offer_without_title_is_not_imported(db):
    plan = build_plan(db, STORE, [offer('333', '777', 'W-X', title='')])
    assert plan['stats']['create_count'] == 0
    assert '标题' in plan['skipped'][0]['reason']


def test_offer_without_plid_is_skipped(db):
    plan = build_plan(db, STORE, [offer('444', '', 'W-Y')])
    assert plan['stats']['create_count'] == 0
    assert plan['stats']['skip_count'] == 1


# --- 本地已有：更正 SKU ---------------------------------------------------

def test_single_spec_rename_migrates_and_keeps_old_value(db):
    add(db, plid='104036518', product_no=1, sku='9902357746192', tsin='104036518',
        takealot_url='https://www.takealot.com/x/PLID104036518')
    plan, result = apply_all(db, [offer('104036518', '104036518', 'W-S-12-04')])
    assert result['renamed'] == 1
    product = db.query(Product).filter(Product.plid == '104036518').one()
    assert product.sku == 'W-S-12-04'
    # 界面要能显示「旧值 → 新值」，所以变更里必须带 current。
    change = next(c for c in plan['updates'][0]['changes'] if c['field'] == 'sku')
    assert change['current'] == '9902357746192'


def test_multi_spec_mismatch_is_skipped_not_guessed(db):
    """本地两个旧 SKU、卖家两个新 SKU，对应关系不唯一——不许按行序硬配。"""
    add(db, plid='96097290', product_no=1, sku='9901\n9902', tsin='96097290',
        takealot_url='https://www.takealot.com/x/PLID96097290')
    plan, result = apply_all(db, [offer('96097290', '96097290', 'W-S-18-01'),
                                  offer('96097291', '96097290', 'W-S-18-02')])
    assert result['renamed'] == 0
    assert plan['stats']['skip_count'] == 1
    assert '多规格' in plan['skipped'][0]['reason']
    assert db.query(Product).filter(Product.plid == '96097290').one().sku == '9901\n9902'


def test_local_row_without_url_is_matched_by_tsin_and_gets_plid(db):
    """卖家后台导入的行以前没有链接，靠 TSIN 找到后要把 PLID 补上。"""
    add(db, product_no=1, sku='W-S-12-02', tsin='104410843')
    plan, result = apply_all(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert result['created'] == 0
    product = db.query(Product).filter(Product.tsin == '104410843').one()
    assert product.plid == '104410843'
    assert product.takealot_url.endswith('/PLID104410843')


def test_local_row_without_url_is_matched_by_sku(db):
    add(db, product_no=1, sku='W-S-12-02')
    plan, result = apply_all(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert result['created'] == 0
    assert db.query(Product).filter(Product.sku == 'W-S-12-02').one().plid == '104410843'


def test_two_local_rows_on_one_plid_are_left_alone(db):
    add(db, plid='104410843', product_no=1, sku='W-A',
        takealot_url='https://www.takealot.com/x/PLID104410843')
    add(db, plid='104410843', product_no=2, sku='W-B',
        takealot_url='https://www.takealot.com/y/PLID104410843')
    plan, result = apply_all(db, [offer('104410843', '104410843', 'W-C')])
    assert result['updated'] == 0
    assert '身份不明' in plan['skipped'][0]['reason']


def test_one_local_row_claimed_by_two_plids_is_skipped(db):
    """同一件本地商品被两个 PLID 同时命中，说明卖家那边有重复链接，先人工确认。"""
    add(db, product_no=1, sku='W-SHARED')
    plan = build_plan(db, STORE, [offer('1', '800', 'W-SHARED'), offer('2', '801', 'W-SHARED')])
    assert plan['stats']['update_count'] == 1
    assert plan['stats']['skip_count'] == 1
    assert '同时命中' in plan['skipped'][0]['reason']


def test_existing_manual_fields_are_not_touched(db):
    """人工填过的采购成本、图片路径、中文名不能被同步覆盖或清空。"""
    add(db, plid='104410843', product_no=1, sku='W-S-12-02', tsin='104410843',
        takealot_url='https://www.takealot.com/x/PLID104410843',
        purchase_cost_cny=38.5, chinese_product_name='假发', product_image_path='local/a.jpg',
        product_name='Wig')
    apply_all(db, [offer('104410843', '104410843', 'W-S-12-02', title='Wig Renamed')])
    product = db.query(Product).filter(Product.plid == '104410843').one()
    assert product.purchase_cost_cny == 38.5
    assert product.chinese_product_name == '假发'
    assert product.product_image_path == 'local/a.jpg'
    assert product.product_name == 'Wig'


# --- 事务与并发 -----------------------------------------------------------

def test_conflict_rolls_back_the_whole_batch(db):
    """中途发现 PLID 已被占用时，前面改好的那条也不能留下。"""
    target = add(db, plid='104036518', product_no=1, sku='9902357746192', tsin='104036518',
                 takealot_url='https://www.takealot.com/x/PLID104036518')
    plan = build_plan(db, STORE, [offer('104036518', '104036518', 'W-S-12-04'),
                                  offer('555', '555555', 'W-NEW')])
    # 预览之后有人抢先建了同一个 PLID
    add(db, plid='555555', product_no=2, sku='W-NEW')
    with pytest.raises(SellerSyncConflict):
        apply_plan(db, STORE, plan)
    db.rollback()
    assert db.query(Product).filter(Product.id == target.id).one().sku == '9902357746192'


def test_stale_preview_token_is_rejected_by_the_route(db):
    """预览之后本地变了，token 就对不上——不能拿旧计划去写库。"""
    add(db, plid='104036518', product_no=1, sku='9902357746192', tsin='104036518',
        takealot_url='https://www.takealot.com/x/PLID104036518')
    rows = [offer('104036518', '104036518', 'W-S-12-04')]
    before = build_plan(db, STORE, rows)['token']
    db.query(Product).filter(Product.plid == '104036518').update({'sku': '9902000000000'})
    db.commit()
    assert build_plan(db, STORE, rows)['token'] != before


# --- 路由层：预览不写库、apply 认 token ------------------------------------

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from api import product_routes
from api.product_routes import SellerSyncRequest, seller_sync


def call_route(db, offers, path='/api/products/seller-sync/preview', token=None, plids=None):
    product_routes._seller_offers = _fake_offers(offers)
    request = SimpleNamespace(url=SimpleNamespace(path=path))
    return asyncio.run(seller_sync(SellerSyncRequest(token=token, plids=plids), request, db=db, store_id=STORE))


def _fake_offers(offers):
    async def loader(db):
        return offers
    return loader


def test_preview_does_not_write_anything(db):
    payload = call_route(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert payload['stats']['create_count'] == 1
    assert payload['token']
    assert db.query(Product).count() == 0, '预览绝不能落库'


def test_apply_without_token_is_rejected(db):
    rows = [offer('104410843', '104410843', 'W-S-12-02')]
    with pytest.raises(HTTPException) as caught:
        call_route(db, rows, path='/api/products/seller-sync/apply')
    assert caught.value.status_code == 409
    assert db.query(Product).count() == 0


def test_apply_with_the_preview_token_writes_once(db):
    rows = [offer('104410843', '104410843', 'W-S-12-02')]
    token = call_route(db, rows)['token']
    payload = call_route(db, rows, path='/api/products/seller-sync/apply', token=token)
    assert payload['applied']['created'] == 1
    assert db.query(Product).filter(Product.plid == '104410843').count() == 1
    # 同一个 token 再放一次：本地已经有这条 PLID，计划会变成 0 新增，
    # 不会因为重复提交再建一条。
    again = call_route(db, rows)
    assert again['stats']['create_count'] == 0


# --- 收窄范围：只同步点名的 PLID -------------------------------------------

def test_scope_keeps_only_the_named_plids(db):
    """点名两条就只算这两条——界面写「新增 2 个」，写进去的也必须是 2 个。"""
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2'), offer('3', '333', 'A-3')]
    plan = build_plan(db, STORE, rows, plids=['111', ' 333 '])
    assert plan['stats']['create_count'] == 2
    assert plan['stats']['seller_offer_count'] == 2
    assert plan['stats']['scope_count'] == 2
    assert {item['plid'] for item in plan['creates']} == {'111', '333'}


def test_scope_is_part_of_the_token(db):
    """只同步 A 的预览不能拿去执行整库，否则用户以为动了 1 条、实际写了 190 多条。"""
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2')]
    full = build_plan(db, STORE, rows)
    scoped = build_plan(db, STORE, rows, plids=['111'])
    assert full['token'] != scoped['token']


def test_scoped_apply_writes_only_the_named_plids(db):
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2')]
    token = call_route(db, rows, plids=['111'])['token']
    payload = call_route(db, rows, path='/api/products/seller-sync/apply', token=token, plids=['111'])
    assert payload['applied']['created'] == 1
    assert [product.plid for product in db.query(Product).all()] == ['111']


def test_scoped_token_cannot_be_used_for_a_full_sync(db):
    """拿着「只同步 111」的 token 发一个不带 plids 的 apply，必须被拒。"""
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2')]
    token = call_route(db, rows, plids=['111'])['token']
    with pytest.raises(HTTPException) as caught:
        call_route(db, rows, path='/api/products/seller-sync/apply', token=token)
    assert caught.value.status_code == 409
    assert db.query(Product).count() == 0
