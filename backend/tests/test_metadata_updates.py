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
