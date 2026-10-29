"""卖家目录接口的重尺解析。

这个模块最危险的地方是**猜单位**：接口响应结构未知，字段名可能叫
`length_cm`、`length`、`consolidated_packaged_length`，单位可能在字段名里、
可能在同级 unit 字段里、也可能压根没写。猜错一位，运费和利润率就全错，
所以这里把每种写法都钉住。

不联网：只测纯函数 `extract_dims` / `_scale_from_text` / `_walk_dimension_keys`。
真实端点用 `/api/selection/diag/catalogue` 探。
"""

import pytest

from services.takealot_catalogue_api import (
    _scale_from_text,
    _walk_dimension_keys,
    extract_dims,
)


def test_unit_in_field_name():
    payload = {"productline": {"length_cm": 22, "width_cm": 7.2, "height_cm": 17,
                               "weight_kg": 0.38}}
    dims = extract_dims(payload)
    assert dims == {"length_mm": 220.0, "width_mm": 72.0,
                    "height_mm": 170.0, "weight_kg": 0.38}


def test_unit_in_sibling_field():
    payload = {"packaging": {"length": 22, "width": 7.2, "height": 17,
                             "unit": "cm"}}
    dims = extract_dims(payload)
    assert dims["length_mm"] == 220.0
    assert dims["width_mm"] == 72.0
    assert dims["height_mm"] == 170.0


def test_millimetre_is_not_scaled():
    payload = {"d": {"length_mm": 220, "width_mm": 72, "height_mm": 170}}
    dims = extract_dims(payload)
    assert dims["length_mm"] == 220.0
    assert dims["height_mm"] == 170.0


def test_no_unit_defaults_to_centimetre():
    """没写单位时按 cm——和公开页 `Assembled Dimensions` 的口径一致。"""
    payload = {"d": {"length": 22, "width": 7.2, "height": 17}}
    dims = extract_dims(payload)
    assert dims["length_mm"] == 220.0


def test_weight_grams_converted_to_kg():
    payload = {"d": {"length_cm": 22, "width_cm": 7.2, "height_cm": 17,
                     "weight_grams": 380}}
    assert extract_dims(payload)["weight_kg"] == 0.38


def test_weight_lb_converted_to_kg():
    payload = {"d": {"length_cm": 22, "width_cm": 7.2, "height_cm": 17,
                     "weight_lb": 1}}
    assert extract_dims(payload)["weight_kg"] == 0.454


def test_dimensions_in_consolidated_and_merchant_packaged():
    """后台那两个字段（Consolidated / Merchant Packaged）长这样。"""
    payload = {"catalogue": {
        "consolidated_packaged_dimensions": {
            "length_cm": 30, "width_cm": 20, "height_cm": 12, "weight_grams": 900,
        },
        "merchant_packaged_dimensions": {
            "length_cm": 32, "width_cm": 22, "height_cm": 14,
        },
    }}
    dims = extract_dims(payload)
    # 取第一组同时含长宽高的，且带上了重量
    assert dims["length_mm"] == 300.0
    assert dims["weight_kg"] == 0.9


def test_missing_height_returns_empty():
    """只有长宽没有高，算不出体积重——宁可返回空，也不要用默认值凑。"""
    payload = {"d": {"length_cm": 22, "width_cm": 7.2}}
    assert extract_dims(payload) == {}


def test_numeric_strings_are_parsed():
    payload = {"d": {"length_cm": "22.0", "width_cm": "7,2", "height_cm": "17 cm"}}
    dims = extract_dims(payload)
    assert dims["length_mm"] == 220.0
    assert dims["width_mm"] == 72.0


def test_maximum_weight_is_not_product_weight():
    """置物架的「最大承重」不是商品自重。"""
    payload = {"d": {"length_cm": 22, "width_cm": 7.2, "height_cm": 17,
                     "maximum_weight_recommendation": "0.9 kg"}}
    assert extract_dims(payload)["weight_kg"] is None


@pytest.mark.parametrize("text,expected", [
    ("length_cm", 10.0),
    ("length_mm", 1.0),
    ("length", None),
    ("weight_grams", 0.001),
    ("weight_kg", 1.0),
])
def test_scale_from_text(text, expected):
    from services.takealot_catalogue_api import _UNIT_SCALE_KG, _UNIT_SCALE_MM
    table = _UNIT_SCALE_KG if "weight" in text else _UNIT_SCALE_MM
    assert _scale_from_text(text, table) == expected


def test_walk_dimension_keys_lists_paths():
    payload = {"a": {"length_cm": 1, "b": [{"weight_kg": 2}]}}
    keys = _walk_dimension_keys(payload)
    assert "a.length_cm" in keys
    assert any("weight" in key for key in keys)
