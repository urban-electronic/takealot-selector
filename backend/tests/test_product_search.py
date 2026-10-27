"""产品列表：SKU 能搜到 + 「只看自建 SKU」筛选。

守两件事：
1. 搜索必须覆盖 SKU——之前只搜品名/TSIN/链接，运营改完 SKU 想找回来只能翻页；
2. 「自建」的判据是「任一段不是纯数字」，多规格串里的分隔符不能把纯数字误判成自建。

这里直接调路由函数而不是走 HTTP：main 的中间件直连 SessionLocal（真实库），
依赖覆盖管不到它，走 TestClient 只会测到「表不存在」。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.product_routes import is_custom_sku, list_products
from database import Base
from models import DEFAULT_STORE_ID, Product, Store

BASE_PARAMS = dict(
    selection_status=None, fee_category=None, shipping_method=None, link_status=None,
    min_margin=None, max_margin=None, search=None, missing_field=None,
    multi_sku=False, duplicate_sku=False, custom_sku=False, archived=False,
    sort_by='created_at', sort_order='desc',
)


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Store(id=DEFAULT_STORE_ID, name='默认店铺', status='active'))
    session.add_all([
        Product(id='p1', store_id=DEFAULT_STORE_ID, product_no=1, product_name='数字SKU', sku='9902357746192'),
        Product(id='p2', store_id=DEFAULT_STORE_ID, product_no=2, product_name='自建SKU', sku='WG-BOB-16-BLK'),
        Product(id='p3', store_id=DEFAULT_STORE_ID, product_no=3, product_name='多规格', sku='9902363669683\n9902363669676'),
        Product(id='p4', store_id=DEFAULT_STORE_ID, product_no=4, product_name='空SKU', sku=''),
        Product(id='p5', store_id=DEFAULT_STORE_ID, product_no=5, product_name='混合', sku='9902357746192\nWG-LACE-20'),
    ])
    session.commit()
    yield session
    session.close()


def query(db, **overrides):
    params = {**BASE_PARAMS, **overrides}
    return sorted(product.id for product in
                  list_products(db=db, store_id=DEFAULT_STORE_ID, **params))


# --- 搜索覆盖 SKU ---------------------------------------------------------

def test_search_matches_full_sku(db):
    assert query(db, search='WG-BOB-16-BLK') == ['p2']


def test_search_matches_partial_sku(db):
    assert query(db, search='WG-BOB') == ['p2']


def test_search_matches_one_segment_of_multi_sku(db):
    # 多规格是一整串，按其中一段也要能搜到。
    assert query(db, search='9902363669676') == ['p3']


def test_search_still_matches_name(db):
    # 加 SKU 不能把原来的品名搜索挤掉。
    assert query(db, search='多规格') == ['p3']


# --- 只看自建 SKU ---------------------------------------------------------

def test_custom_sku_filter_returns_only_non_numeric(db):
    assert query(db, custom_sku=True) == ['p2', 'p5']


def test_custom_sku_excludes_blank_and_pure_numeric(db):
    # 空 SKU 是「没填」，不是「自己起的」；多规格纯数字不能被分隔符带成自建。
    assert 'p1' not in query(db, custom_sku=True)
    assert 'p3' not in query(db, custom_sku=True)
    assert 'p4' not in query(db, custom_sku=True)


def test_custom_sku_combines_with_search(db):
    assert query(db, custom_sku=True, search='LACE') == ['p5']


def test_custom_sku_off_returns_everything(db):
    assert query(db) == ['p1', 'p2', 'p3', 'p4', 'p5']


# --- 判据本身 -------------------------------------------------------------

@pytest.mark.parametrize('value, expected', [
    ('9902357746192', False),
    ('9902363669683\n9902363669676', False),
    ('9902363669683 9902363669676', False),
    ('9902363669683,9902363669676', False),
    ('WG-BOB-16-BLK', True),
    ('9902357746192\nWG-LACE-20', True),
    ('abc', True),
    ('', False),
    (None, False),
])
def test_is_custom_sku(value, expected):
    assert is_custom_sku(value) is expected
