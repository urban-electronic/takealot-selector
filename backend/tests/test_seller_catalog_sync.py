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
    assert plan['stats']['update_count'] == 0
    assert plan['stats']['skip_count'] == 2
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
from unittest.mock import patch
from api.product_routes import SellerSyncRequest, seller_sync


def call_route(db, offers, path='/api/products/seller-sync/preview', token=None, plids=None):
    product_routes._seller_offers = _fake_offers(offers)
    request = SimpleNamespace(url=SimpleNamespace(path=path))
    # 保留回退核实后范围规则的隔离验证；默认生产写入口另有暂停断言。
    with patch('services.seller_catalog_sync.SELLER_SYNC_WRITE_PAUSED', False):
        return asyncio.run(seller_sync(SellerSyncRequest(token=token, plids=plids), request, db=db, store_id=STORE))


def _fake_offers(offers):
    async def loader(db):
        return offers
    return loader


def test_preview_does_not_write_anything(db):
    payload = call_route(db, [offer('104410843', '104410843', 'W-S-12-02')])
    assert payload['stats']['create_count'] == 0
    assert payload['stats']['skip_count'] == 1
    assert payload['token']
    assert db.query(Product).count() == 0, '预览绝不能落库'


def test_apply_without_token_is_rejected(db):
    rows = [offer('104410843', '104410843', 'W-S-12-02')]
    with pytest.raises(HTTPException) as caught:
        call_route(db, rows, path='/api/products/seller-sync/apply')
    assert caught.value.status_code == 409
    assert db.query(Product).count() == 0


def test_apply_with_the_preview_token_writes_once(db):
    add(db, plid='104410843', product_name='existing')
    rows = [offer('104410843', '104410843', 'W-S-12-02')]
    token = call_route(db, rows)['token']
    payload = call_route(db, rows, path='/api/products/seller-sync/apply', token=token)
    assert payload['applied']['created'] == 0
    assert payload['applied']['updated'] == 1
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
    add(db, plid='111', product_name='existing')
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2')]
    token = call_route(db, rows, plids=['111'])['token']
    payload = call_route(db, rows, path='/api/products/seller-sync/apply', token=token, plids=['111'])
    assert payload['applied']['created'] == 0
    assert payload['applied']['updated'] == 1
    assert [product.plid for product in db.query(Product).all()] == ['111']


def test_scoped_token_cannot_be_used_for_a_full_sync(db):
    """拿着「只同步 111」的 token 发一个不带 plids 的 apply，必须被拒。"""
    rows = [offer('1', '111', 'A-1'), offer('2', '222', 'A-2')]
    token = call_route(db, rows, plids=['111'])['token']
    with pytest.raises(HTTPException) as caught:
        call_route(db, rows, path='/api/products/seller-sync/apply', token=token)
    assert caught.value.status_code == 409
    assert db.query(Product).count() == 0

@pytest.mark.parametrize('reverse', [False, True])
def test_conflicting_identifiers_never_choose_one_product(db, reverse):
    first = add(db, plid='800', sku='OLD-A', tsin='1')
    second = add(db, plid='801', sku='W-B', tsin='2')
    rows = [offer('2', '800', 'W-B')]
    if reverse:
        rows.append(offer('1', '800', 'OLD-A'))
        rows.reverse()
    plan, result = apply_all(db, rows)
    assert result['updated'] == result['created'] == 0
    assert plan['stats']['skip_count'] == 1
    assert db.get(Product, first.id).sku == 'OLD-A'
    assert db.get(Product, second.id).plid == '801'


def test_weak_match_cannot_replace_existing_different_plid(db):
    product = add(db, plid='801', sku='W-A', tsin='1')
    plan, result = apply_all(db, [offer('1', '800', 'W-A')])
    assert result['updated'] == result['created'] == 0
    assert plan['stats']['skip_count'] == 1
    assert db.get(Product, product.id).plid == '801'


@pytest.mark.parametrize('seller_plid', ['800', '801'])
def test_url_and_stored_plid_conflict_is_not_overwritten(db, seller_plid):
    product = add(db, plid='801', takealot_url='https://www.takealot.com/x/PLID800', sku='OLD')
    plan, result = apply_all(db, [offer('1', seller_plid, 'NEW')])
    assert result['updated'] == result['created'] == 0
    assert plan['stats']['skip_count'] == 1
    assert db.get(Product, product.id).sku == 'OLD'


def test_intervening_edit_rejects_prepared_plan(db):
    product = add(db, plid='800', sku='OLD', tsin='1')
    rows = [offer('1', '800', 'SELLER')]
    plan = build_plan(db, STORE, rows)
    old_token = plan['token']
    product.sku = 'MANUAL'
    db.commit()
    assert build_plan(db, STORE, rows)['token'] != old_token
    with pytest.raises(SellerSyncConflict):
        apply_plan(db, STORE, plan)
    db.rollback()
    assert db.get(Product, product.id).sku == 'MANUAL'


@pytest.mark.parametrize('field,value', [
    ('tsin_id', '2'),
    ('image_url', 'https://media.takealot.com/covers_images/2.jpg'),
    ('length_cm', 20), ('weight_grams', 500),
])
def test_create_payload_change_requires_new_preview(db, field, value):
    add(db, plid='800', product_name='existing')
    row = offer('1', '800', 'W-A')
    token = call_route(db, [row])['token']
    with pytest.raises(HTTPException) as caught:
        call_route(db, [dict(row, **{field: value})],
                   path='/api/products/seller-sync/apply', token=token)
    assert caught.value.status_code == 409
    assert db.query(Product).count() == 1
    assert not db.query(Product).first().sku


def test_concurrent_creates_are_serialized(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'concurrent.db'),
                           connect_args={'timeout': 0.02})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as setup:
        setup.add(Store(id=STORE, name='fixture', status='active'))
        setup.commit()
    with sessions() as first, sessions() as second:
        rows = [offer('1', '800', 'W-A')]
        a, b = build_plan(first, STORE, rows), build_plan(second, STORE, rows)
        apply_plan(first, STORE, a)
        with pytest.raises(SellerSyncConflict):
            apply_plan(second, STORE, b)
        second.rollback()
        first.commit()
        with pytest.raises(SellerSyncConflict):
            apply_plan(second, STORE, b)
        second.rollback()
        assert second.query(Product).filter(Product.plid == '800').count() == 1


def test_october_existing_scope_skips_old_and_missing_products(db):
    from datetime import datetime
    cutoff = datetime(2026,9,30,16)  # 中国时间10月1日
    old = add(db, plid='111', product_name='old', created_at=datetime(2026,9,30,15,59))
    recent = add(db, plid='222', product_name='recent', created_at=cutoff)
    rows = [offer('1','111','old-sku'),offer('2','222','new-sku'),offer('3','333','absent')]
    plan = build_plan(db, STORE, rows, existing_since=cutoff)
    assert plan['creates'] == []
    assert [u['product_id'] for u in plan['updates']] == [recent.id]
    assert {s['plid'] for s in plan['skipped']} == {'111','333'}
    result = apply_plan(db, STORE, plan)
    db.commit()
    assert result['created'] == 0 and result['updated'] == 1
    assert db.query(Product).count() == 2
    db.refresh(old)
    db.refresh(recent)
    assert not old.sku and recent.sku == 'new-sku'



def test_old_unrestricted_preview_cannot_create_through_current_route(db):
    rows = [offer('1','999','unregistered')]
    old = build_plan(db, STORE, rows)['token']
    with pytest.raises(HTTPException) as error:
        call_route(db, rows, path='/api/products/seller-sync/apply', token=old)
    assert error.value.status_code == 409
    assert db.query(Product).count() == 0



def test_internal_create_price_still_invalidates_legacy_plan(db):
    row = offer('1','800','W-A')
    assert build_plan(db, STORE, [row])['token'] != build_plan(db, STORE, [dict(row, selling_price=399)])['token']



def test_seller_sync_write_pause_blocks_before_seller_read(db, monkeypatch):
    async def forbidden(db): pytest.fail('paused write must not read seller data')
    monkeypatch.setattr(product_routes, '_seller_offers', forbidden)
    request = SimpleNamespace(url=SimpleNamespace(path='/api/products/seller-sync/apply'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(seller_sync(SellerSyncRequest(token='old'), request, db=db, store_id=STORE))
    assert error.value.status_code == 409
    assert '暂停' in error.value.detail
    assert db.query(Product).count() == 0



def test_october_scope_skips_missing_local_creation_time(db):
    from datetime import datetime
    row = add(db, plid='777', product_name='unknown-date')
    db.query(Product).filter(Product.id == row.id).update({'created_at':None})
    db.commit()
    plan = build_plan(db, STORE, [offer('7','777','unsafe')], existing_since=datetime(2026,9,30,16))
    assert not plan['updates'] and not plan['creates']
    assert plan['skipped'][0]['plid'] == '777'
