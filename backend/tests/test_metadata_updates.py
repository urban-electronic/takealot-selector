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

# Reproduce the single-field list PATCH without touching production data.
@pytest.mark.parametrize('payload', [
    {'link_status': '未购买'}, {'product_no': 304},
    {'competing_sellers_count': 2}, {'stock_remaining': 4},
    {'review_count': 3}, {'rating_value': 4.5},
    {'link_status': '已购买', 'note': 'updated'},
    {'manual_domestic_forwarding_cny': 2.0},
    {'purchase_cost_cny': 45},
])
def test_partial_patch_preserves_unspecified_manual_overrides(payload):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='partial', store_id='default-store', product_name='Fixture',
                    link_status='未购买', product_no=304,
                    manual_domestic_forwarding_cny=1.0, manual_total_cost_zar=123.45,
                    total_cost_zar=123.45, actual_sale_price_zar=200, purchase_cost_cny=40)
        db.add(p); db.commit()
        result = update_product('partial', ProductUpdate(**payload), db, 'default-store')
        assert result['manual_total_cost_zar'] == 123.45
        assert result['manual_domestic_forwarding_cny'] == payload.get('manual_domestic_forwarding_cny', 1.0)
        assert result['total_cost_zar'] == 123.45


def test_update_schema_requires_explicit_disjoint_cost_classification():
    from api.product_routes import PRODUCT_METADATA_FIELDS, PRODUCT_CALCULATION_FIELDS
    assert not PRODUCT_METADATA_FIELDS & PRODUCT_CALCULATION_FIELDS
    assert PRODUCT_METADATA_FIELDS | PRODUCT_CALCULATION_FIELDS == set(ProductUpdate.model_fields)


def test_explicit_null_resets_only_requested_override_and_recalculates():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='reset', store_id='default-store', product_name='Fixture',
                    manual_domestic_forwarding_cny=1, manual_total_cost_zar=123.45,
                    total_cost_zar=123.45, actual_sale_price_zar=200, purchase_cost_cny=40)
        db.add(p); db.commit()
        result = update_product('reset', ProductUpdate(manual_domestic_forwarding_cny=None), db, 'default-store')
        assert result['manual_domestic_forwarding_cny'] is None
        assert result['manual_total_cost_zar'] == 123.45
        assert result['profit_zar'] == 76.55
        result = update_product('reset', ProductUpdate(manual_total_cost_zar=0), db, 'default-store')
        assert result['manual_total_cost_zar'] == 0
        assert result['total_cost_zar'] == 0
        assert result['profit_zar'] == 200


@pytest.mark.parametrize('field,value,recalculate', [
    ('takealot_url','https://example.invalid/p',False), ('tsin','123',False),
    ('product_name','Fixture',False), ('product_image_url','https://example.invalid/i',False),
    ('product_image_path','image.png',False), ('note','note',False), ('purchase_url','',False),
    ('sku','123',False), ('chinese_product_name','品名',False), ('market_prices','{}',False),
    ('link_status','已购买',False), ('product_no',3,False), ('competing_sellers_count',2,False),
    ('stock_remaining',2,False), ('review_count',2,False), ('rating_value',4.0,False),
    ('actual_sale_price_zar',200,True), ('fee_category',None,True), ('fee_category_confirmed',False,True),
    ('purchase_cost_cny',40,True), ('purchase_shipping_cny',4,True), ('purchase_quantity',4,True),
    ('length_mm',100,True), ('width_mm',100,True), ('height_mm',100,True), ('actual_weight_kg',1,True),
    ('packaging_cost_per_unit_cny',1,True), ('shipping_method','海运普货',True),
    ('inbound_listing_fee_cny',1,True), ('outbound_operation_fee_cny',1,True),
    ('last_mile_delivery_fee_cny',1,True), ('other_fee_cny',1,True), ('fulfillment_fee_zar',42,True),
    ('manual_domestic_forwarding_cny',1,True), ('manual_international_shipping_cny',2,True),
    ('manual_overseas_op_cost_cny',3,True), ('manual_success_fee_zar',4,True),
    ('manual_fulfillment_fee_zar',5,True), ('manual_total_cost_zar',120,True),
])
def test_each_patch_field_recalculation_policy(field, value, recalculate, monkeypatch):
    import api.product_routes as routes
    calls = []
    original = routes._apply_calculated_fields
    def calculate(db, product):
        calls.append(product.id)
        original(db, product)
    monkeypatch.setattr(routes, '_apply_calculated_fields', calculate)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        p = Product(id='policy', store_id='default-store', product_name='Fixture',
                    actual_sale_price_zar=200, manual_total_cost_zar=123.45)
        db.add(p); db.commit()
        result = update_product('policy', ProductUpdate(**{field:value}), db, 'default-store')
        assert bool(calls) is recalculate
        assert result['manual_total_cost_zar'] == (value if field == 'manual_total_cost_zar' else 123.45)


MANUALS = ('manual_domestic_forwarding_cny', 'manual_international_shipping_cny',
           'manual_overseas_op_cost_cny', 'manual_success_fee_zar',
           'manual_fulfillment_fee_zar', 'manual_total_cost_zar')


@pytest.mark.parametrize('field', MANUALS)
@pytest.mark.parametrize('value', [0, None, 8.5])
def test_each_manual_override_changes_only_when_explicit(field, value):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        before = dict(zip(MANUALS, (1, 2, 3, 4, 5, 120)))
        p = Product(id='manual', store_id='default-store', actual_sale_price_zar=200, **before)
        db.add(p); db.commit()
        result = update_product('manual', ProductUpdate(**{field:value}), db, 'default-store')
        for key, original in before.items():
            assert result[key] == (value if key == field else original)


def test_cost_shipping_and_category_recalculate_without_total_override():
    from api.product_routes import _apply_calculated_fields
    from models import FeeCategory
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(FeeCategory(id='fee', name='Fixture Fee', success_fee_rate=0.2, active=True))
        p = Product(id='cost', store_id='default-store', actual_sale_price_zar=500,
                    purchase_cost_cny=40, purchase_shipping_cny=0, purchase_quantity=4,
                    length_mm=300, width_mm=200, height_mm=100, actual_weight_kg=2,
                    shipping_method='空运普货', success_fee_rate=0.1, fulfillment_fee_zar=42,
                    manual_domestic_forwarding_cny=1)
        db.add(p); db.commit(); _apply_calculated_fields(db,p); db.commit()
        old_cost = p.unit_product_cost_cny
        result = update_product('cost', ProductUpdate(purchase_cost_cny=80, note='mixed'), db, 'default-store')
        assert result['unit_product_cost_cny'] == old_cost + 10
        assert result['manual_domestic_forwarding_cny'] == 1
        result = update_product('cost', ProductUpdate(shipping_method='海运普货'), db, 'default-store')
        assert result['international_shipping_per_unit_cny'] != 158
        result = update_product('cost', ProductUpdate(fee_category='Fixture Fee'), db, 'default-store')
        assert result['success_fee_zar'] == 100
        assert result['profit_zar'] == round(500-result['total_cost_zar'],2)
