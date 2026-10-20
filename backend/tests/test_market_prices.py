"""多市场参考价字段（Product.market_prices）的写入校验。

参考价只做展示与比价，不参与利润计算，所以后端只守一条底线：
写进来的必须是一个「市场 -> 带数值 price 的对象」的 JSON，坏数据不进库。
"""
import json

import pytest
from pydantic import ValidationError

from api.product_routes import ProductCreate, ProductUpdate, _check_market_prices


def test_accepts_market_map():
    raw = json.dumps({
        'CN': {'price': 469, 'cur': 'CNY', 'src': '官网', 'at': '2026-10-21'},
        'SG': {'price': 115, 'cur': 'SGD', 'src': '官网', 'at': '2026-10-21'},
        'TB': {'price': 268, 'cur': 'CNY', 'src': '淘宝均价', 'n': 12, 'at': '2026-10-21'},
    }, ensure_ascii=False)
    assert _check_market_prices(raw) == raw


@pytest.mark.parametrize('raw', ['', None])
def test_empty_means_not_collected(raw):
    assert _check_market_prices(raw) == raw


@pytest.mark.parametrize('raw', [
    'not-json',
    '[1, 2]',                                  # 不是对象
    '{"CN": 469}',                             # 市场值不是对象
    '{"CN": {"cur": "CNY"}}',                  # 缺 price
    '{"CN": {"price": "469"}}',                # price 是字符串
    '{"CN": {"price": true}}',                 # bool 不是价格
    '{"CN": {"price": -1}}',                   # 负数
])
def test_rejects_bad_payload(raw):
    with pytest.raises(ValueError):
        _check_market_prices(raw)


def test_schema_surfaces_validation_error():
    with pytest.raises(ValidationError):
        ProductCreate(product_name='x', market_prices='{"CN": {"price": "abc"}}')
    with pytest.raises(ValidationError):
        ProductUpdate(market_prices='oops')
