"""新建商品时的身份锚点：PLID 必须落进独立列。

抓取走的是公开商品页——标题、图、价格、类目都有，但 SKU 是卖家后台的私有
字段，公开页面根本不返回。所以「抓完没有 SKU」不是抓漏了，是那条路拿不到。
能可靠拿到的只有链接里的 PLID，它必须存进 plid 列：同步卖家资料时靠它精确
对上，不用退到 TSIN/SKU 去猜。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import product_routes
from api.product_routes import ProductCreate, create_product
from database import Base
from models import DEFAULT_STORE_ID, Product, Store

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


@pytest.fixture(autouse=True)
def no_translate(monkeypatch):
    """翻译要走外部服务，这里只关心身份字段，直接置空。"""
    monkeypatch.setattr(product_routes, 'translate_to_chinese_sync', lambda name: '')


def make(db, url, sku=''):
    return create_product(ProductCreate(product_name='Test', takealot_url=url, sku=sku),
                          db=db, store_id=STORE)


def test_plid_is_filled_from_the_link(db):
    make(db, 'https://www.takealot.com/human-brazilian-wig/PLID102479085')
    product = db.query(Product).one()
    assert product.plid == '102479085'


def test_link_without_plid_leaves_plid_empty(db):
    make(db, 'https://www.takealot.com/some-page')
    assert not (db.query(Product).one().plid or '').strip()


def test_public_page_sku_stays_empty(db):
    """公开页面没有 SKU：留空是如实反映，绝不能编一个。"""
    make(db, 'https://www.takealot.com/x/PLID95227890')
    assert (db.query(Product).one().sku or '') == ''


def test_manual_sku_is_not_overwritten(db):
    """运营手填的 SKU 优先，不能被链接解析覆盖。"""
    make(db, 'https://www.takealot.com/x/PLID95227890', sku='W-S-18-01')
    assert db.query(Product).one().sku == 'W-S-18-01'
