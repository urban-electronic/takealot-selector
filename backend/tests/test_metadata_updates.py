import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import Product
from api.product_routes import ProductUpdate, update_product


def test_variant_label_update_preserves_manual_and_calculated_costs():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='product', store_id='default-store', product_name='Existing', sku='sku1 sku2',
                    manual_total_cost_zar=123.45, total_cost_zar=123.45, profit_zar=76.55,
                    actual_sale_price_zar=200, purchase_cost_cny=40, selection_status='合格选品')
        db.add(p); db.commit()
        result = update_product('product', ProductUpdate(chinese_product_name='棕色\n黑色'), db, 'default-store')
        assert result['chinese_product_name'] == '棕色\n黑色'
        for field, expected in [('manual_total_cost_zar', 123.45), ('total_cost_zar', 123.45),
                                ('profit_zar', 76.55), ('actual_sale_price_zar', 200),
                                ('purchase_cost_cny', 40), ('selection_status', '合格选品')]:
            assert result[field] == expected


# 补货侧栏的「保存采购链接」只发 purchase_url 一个字段。PATCH 里有一条分支：
# 请求字段不在 metadata_fields 里就清空全部 manual_* 再重算成本。
# purchase_url 一旦掉出白名单，一次保存链接就会顺手抹掉运营手工填的成本覆盖值，
# 而且界面上看不出来（价格还是那个价格，只是利润换成了重算值）。
# 所以这里逐个字段验一遍，而不是只验 purchase_url。
@pytest.mark.parametrize('payload', [
    {'purchase_url': 'https://detail.1688.com/offer/123.html'},
    {'chinese_product_name': '棕色\n黑色'},
    {'note': '备注'},
    {'product_name': 'Renamed'},
    {'tsin': 'PLID1'},
    {'takealot_url': 'https://www.takealot.com/x/PLID1'},
    {'market_prices': '{}'},
])
def test_metadata_only_update_preserves_manual_and_calculated_costs(payload):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='product', store_id='default-store', product_name='Existing', sku='sku1',
                    manual_total_cost_zar=123.45, total_cost_zar=123.45, profit_zar=76.55,
                    actual_sale_price_zar=200, purchase_cost_cny=40, selection_status='合格选品')
        db.add(p); db.commit()
        result = update_product('product', ProductUpdate(**payload), db, 'default-store')
        for field, expected in [('manual_total_cost_zar', 123.45), ('total_cost_zar', 123.45),
                                ('profit_zar', 76.55), ('actual_sale_price_zar', 200),
                                ('purchase_cost_cny', 40)]:
            assert result[field] == expected, f'{list(payload)} 不应改动 {field}'
        for field, value in payload.items():
            assert result[field] == value


def test_purchase_url_can_be_cleared_without_touching_costs():
    """清除采购链接（发空串）同样只算元数据改动。"""
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='product', store_id='default-store', product_name='Existing',
                    purchase_url='https://detail.1688.com/offer/123.html',
                    manual_total_cost_zar=123.45, total_cost_zar=123.45)
        db.add(p); db.commit()
        result = update_product('product', ProductUpdate(purchase_url=''), db, 'default-store')
        assert result['purchase_url'] == ''
        assert result['manual_total_cost_zar'] == 123.45
        assert result['total_cost_zar'] == 123.45
