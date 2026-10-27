"""卖家后台 vs 本地库的核对接口：把「找不到」拆成三种不同的原因。

同一个「搜不到」，背后可能是三件完全不同的事，下一步动作正好相反：
商品还没导入（seller_only）、SKU 还是旧值（sku_mismatch）、已经下架（local_only）。
混成一句话用户只能猜，所以这里逐条钉住每个结论。

和 test_product_search 一样直接调路由函数：main 的中间件直连 SessionLocal，
走 TestClient 只会测到「表不存在」。
"""

import asyncio
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.product_routes import seller_coverage
from database import Base
from models import DEFAULT_STORE_ID, Product, Store
from services import takealot_seller_api

STORE = DEFAULT_STORE_ID


def offer(tsin, plid, sku, title='假发', status='Buyable'):
    return {'tsin_id': tsin, 'productline_id': plid, 'sku': sku,
            'title': title, 'status': status}


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id=STORE, name='默认店铺', status='active'))
    session.add_all([
        # PLID 与 SKU 都和卖家后台一致
        Product(id='p1', store_id=STORE, product_no=1, product_name='一致',
                sku='W-S-12-02', tsin='104410843',
                takealot_url='https://www.takealot.com/x/PLID104410843'),
        # PLID 对得上，SKU 还是旧的 13 位数字
        Product(id='p2', store_id=STORE, product_no=2, product_name='旧SKU',
                sku='9902357746192', tsin='104036518',
                takealot_url='https://www.takealot.com/x/PLID104036518'),
        # 卖家后台已经没有这个 PLID
        Product(id='p3', store_id=STORE, product_no=3, product_name='已下架',
                sku='W-C-8-02', tsin='104040546',
                takealot_url='https://www.takealot.com/x/PLID999999'),
        # 多规格：本地两段，卖家后台同样两段但顺序不同
        Product(id='p4', store_id=STORE, product_no=4, product_name='多规格',
                sku='W-S-18-01\nW-S-18-02', tsin='96097290',
                takealot_url='https://www.takealot.com/x/PLID96097290'),
    ])
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def offers(monkeypatch):
    rows = [
        offer('104410843', '104410843', 'W-S-12-02'),
        offer('104036518', '104036518', 'W-S-12-04'),
        offer('96097290', '96097290', 'W-S-18-02'),
        offer('96097290', '96097290', 'W-S-18-01'),
        # 卖家后台有、本地没有
        offer('111111111', '111111111', 'W-LACE-20', title='未导入的假发'),
    ]
    monkeypatch.setattr(takealot_seller_api, 'configured_key', lambda db: 'k' * 128)
    monkeypatch.setattr(takealot_seller_api, '_load_offers', lambda key: rows)
    return rows


def call(db, tsin=None, store_id=STORE):
    return asyncio.run(seller_coverage(tsin=tsin, db=db, store_id=store_id))


def probe(result, tsin):
    return next(item for item in result['probes'] if item['tsin'] == tsin)


# --- 逐个 TSIN 的结论 -----------------------------------------------------

def test_matched_product_is_reported_as_matched(db, offers):
    assert probe(call(db, tsin='104410843'), '104410843')['verdict'] == 'matched'


def test_stale_sku_is_reported_as_mismatch_not_missing(db, offers):
    """SKU 还是旧值时，商品其实在库里——报「找不到」会把人引到错误的方向。"""
    item = probe(call(db, tsin='104036518'), '104036518')
    assert item['verdict'] == 'sku_mismatch'
    assert item['local']['found'] is True
    assert item['seller']['skus'] == ['W-S-12-04']
    assert item['local']['skus'] == ['9902357746192']


def test_archived_in_seller_is_local_only(db, offers):
    assert probe(call(db, tsin='104040546'), '104040546')['verdict'] == 'local_only'


def test_seller_only_product_is_not_blamed_on_sku(db, offers):
    """卖家后台有、本地没有 = 还没导入，跟 SKU 命名无关。"""
    item = probe(call(db, tsin='111111111'), '111111111')
    assert item['verdict'] == 'seller_only'
    assert item['local']['found'] is False
    assert item['seller']['skus'] == ['W-LACE-20']


def test_unknown_tsin_is_reported_as_nowhere(db, offers):
    assert probe(call(db, tsin='404404404'), '404404404')['verdict'] == 'nowhere'


def test_multi_spec_sku_order_does_not_matter(db, offers):
    """卖家后台返回的行序不稳定，多规格比对不能按顺序比。"""
    assert probe(call(db, tsin='96097290'), '96097290')['verdict'] == 'matched'


def test_multiple_tsins_are_probed_separately(db, offers):
    result = call(db, tsin='104410843, 104036518')
    assert [item['tsin'] for item in result['probes']] == ['104410843', '104036518']
    assert [item['verdict'] for item in result['probes']] == ['matched', 'sku_mismatch']


def test_no_tsin_means_no_probes(db, offers):
    """不带参数时只是汇总，不要凭空造一个空的 probes。"""
    assert 'probes' not in call(db)


# --- 汇总口径 -------------------------------------------------------------

def test_aggregate_separates_the_three_causes(db, offers):
    result = call(db)
    assert result['seller_offer_count'] == 5
    assert result['local_product_count'] == 4
    assert result['matched_count'] == 2          # p1 + p4（多规格顺序无关）
    assert result['sku_mismatch_count'] == 1     # p2
    assert [item['product_id'] for item in result['sku_mismatch']] == ['p2']
    assert result['seller_only_count'] == 1      # PLID111111111
    assert [item['plid'] for item in result['seller_only']] == ['111111111']
    assert result['local_only_count'] == 1       # p3 的 PLID999999


def test_archived_local_products_are_ignored(db, offers):
    """归档商品不算「本地有」，否则下架的老商品会一直占着匹配名额。"""
    db.add(Product(id='p9', store_id=STORE, product_no=9, product_name='归档',
                   sku='W-X-1', tsin='222222222', is_archived=True,
                   takealot_url='https://www.takealot.com/x/PLID222222222'))
    db.commit()
    assert probe(call(db, tsin='222222222'), '222222222')['verdict'] == 'nowhere'


# --- 读不到的时候要能归因 -------------------------------------------------

def test_missing_key_says_not_configured(db, monkeypatch):
    monkeypatch.setattr(takealot_seller_api, 'configured_key', lambda db: '')
    with pytest.raises(HTTPException) as caught:
        call(db)
    assert caught.value.status_code == 400
    assert '尚未接入' in caught.value.detail


def test_load_failure_keeps_the_actionable_reason(db, monkeypatch):
    """401 要指向「检查密钥」，不能退化成一句「暂时无法读取」。"""
    monkeypatch.setattr(takealot_seller_api, 'configured_key', lambda db: 'k' * 128)

    def boom(key):
        raise ValueError('卖家接口暂时不可用（HTTP 401，卖家接口密钥无效或无权限），已暂停请求，请先检查卖家 API 密钥')

    monkeypatch.setattr(takealot_seller_api, '_load_offers', boom)
    with pytest.raises(HTTPException) as caught:
        call(db)
    assert caught.value.status_code == 503
    assert '检查卖家 API 密钥' in caught.value.detail


def test_other_store_is_refused(db, offers):
    with pytest.raises(HTTPException) as caught:
        call(db, store_id='other-store')
    assert caught.value.status_code == 400


def test_tsin_probe_finds_local_record_by_plid_when_tsin_missing(db, offers):
    # TSIN is a variant identifier, not the product page's PLID.
    offers.append(offer('700001', '800001', 'W-NEW'))
    db.add(Product(id='by-page', store_id=STORE, sku='9900000000001', tsin='',
                   takealot_url='https://www.takealot.com/x/PLID800001'))
    db.commit()
    item = probe(call(db, tsin='700001'), '700001')
    assert item['verdict'] == 'sku_mismatch'
    assert item['local']['found'] is True


def test_split_variant_record_is_not_compared_to_every_sku_on_page(db, offers):
    offers.extend([offer('700002', '800002', 'W-ONE'), offer('700003', '800002', 'W-TWO')])
    db.add(Product(id='one-variant', store_id=STORE, sku='W-ONE', tsin='700002',
                   takealot_url='https://www.takealot.com/x/PLID800002'))
    db.commit()
    result = call(db)
    assert not any(x['product_id'] == 'one-variant' for x in result['sku_mismatch'])


def test_coverage_is_read_only(db, offers):
    before = [(p.id,p.sku,p.tsin) for p in db.query(Product).order_by(Product.id)]
    call(db, tsin='104036518')
    assert [(p.id,p.sku,p.tsin) for p in db.query(Product).order_by(Product.id)] == before
    assert not db.new and not db.dirty and not db.deleted


def test_coverage_timeout_returns_actionable_error_without_retry(db, monkeypatch):
    import api.product_routes as routes
    monkeypatch.setattr(takealot_seller_api, 'configured_key', lambda db: 'fixture')
    calls=[]
    async def timeout(awaitable, timeout):
        calls.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr(routes.asyncio, 'wait_for', timeout)
    with pytest.raises(HTTPException) as caught:
        call(db)
    assert calls == [45]
    assert caught.value.status_code == 503
    assert '超时' in caught.value.detail


def test_summary_matches_tsin_when_local_link_is_blank(db, offers):
    offers.append(offer('700004','800004','W-LINKLESS'))
    db.add(Product(id='linkless',store_id=STORE,tsin='700004',sku='W-LINKLESS',takealot_url=''))
    db.commit()
    result=call(db,tsin='700004')
    assert not any(x['plid']=='800004' for x in result['seller_only'])
    assert probe(result,'700004')['verdict']=='matched'


def test_matching_sku_without_local_tsin_does_not_claim_tsin_is_searchable(db, offers):
    offers.append(offer('700005','800005','W-EXISTS'))
    db.add(Product(id='no-tsin',store_id=STORE,tsin='',sku='W-EXISTS',takealot_url='https://www.takealot.com/x/PLID800005'))
    db.commit()
    item=probe(call(db,tsin='700005'),'700005')
    assert item['verdict']=='matched'
    assert 'TSIN 都能搜到' not in item['advice']


def test_exact_tsin_probe_does_not_borrow_sibling_sku(db, offers):
    offers.append(offer('700006','800006','TARGET'))
    db.add_all([
        Product(id='target',store_id=STORE,tsin='700006',sku='WRONG',takealot_url='https://www.takealot.com/x/PLID800006'),
        Product(id='sibling',store_id=STORE,tsin='700007',sku='TARGET',takealot_url='https://www.takealot.com/x/PLID800006'),
    ])
    db.commit()
    item=probe(call(db,tsin='700006'),'700006')
    assert item['verdict']=='sku_mismatch'
    assert item['local']['skus']==['WRONG']


def test_empty_seller_sku_is_not_evidence_of_matching_identity(db, offers):
    offers.append(offer('700008','800008',''))
    db.add(Product(id='empty-sku',store_id=STORE,tsin='700008',sku='',takealot_url='https://www.takealot.com/x/PLID800008'))
    db.commit()
    result=call(db,tsin='700008')
    assert probe(result,'700008')['verdict'] != 'matched'
    assert any(x['product_id']=='empty-sku' for x in result['sku_mismatch'])
