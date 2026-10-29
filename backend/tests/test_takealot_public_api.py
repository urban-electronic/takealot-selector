"""`services.takealot_public_api` 的纯函数测试。

这些用例不联网：只测解析与取值逻辑。真实接口的形状由 `_fetch` 之外的
分支覆盖，网络行为不放进单测——否则 CI 里数据中心 IP 被 Cloudflare 挡住时
会出现「测试绿了但线上是坏的」，那种绿没有意义。
"""

import pytest

from services.takealot_public_api import (
    _category_path,
    _dimensions_and_weight,
    _first_price,
    _live_tsin,
    parse_plid,
)


class TestParsePlid:
    """PLID 少解析出一种写法，批量导入就会静默丢商品，所以逐个写法都要钉住。"""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://www.takealot.com/some-slug/PLID102501867", 102501867),
            ("https://www.takealot.com/some-slug/plid102501867", 102501867),
            ("https://www.takealot.com/some-slug?plid=102501867", 102501867),
            ("https://www.takealot.com/some-slug?PLID=102501867&x=1", 102501867),
            ("https://www.takealot.com/p/102501867", 102501867),
            ("PLID102501867", 102501867),
            ("102501867", 102501867),
            (102501867, 102501867),
        ],
    )
    def test_reads_plid_from_every_known_form(self, value, expected):
        assert parse_plid(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "no-id-here", "PLIDabc"])
    def test_returns_none_when_no_plid(self, value):
        assert parse_plid(value) is None


class TestDimensionsAndWeight:
    def _payload(self, name, text):
        return {"product_information": {"items": [
            {"display_name": name, "displayable_text": text}
        ]}}

    def test_converts_cm_dimensions_to_mm(self):
        # 商品属性写的是 cm，数据库存 mm，差一个数量级会让体积重算成 1/1000
        result = _dimensions_and_weight(self._payload("Assembled Dimensions", "22 x 7.2 x 17 cm"))
        assert result == {"length_mm": 220.0, "width_mm": 72.0, "height_mm": 170.0}

    def test_keeps_mm_dimensions_as_is(self):
        result = _dimensions_and_weight(self._payload("Assembled Dimensions", "220 x 72 x 170 mm"))
        assert result["length_mm"] == 220.0

    def test_converts_grams_to_kg(self):
        assert _dimensions_and_weight(self._payload("Weight", "380 g")) == {"weight_kg": 0.38}

    def test_reads_kilograms(self):
        assert _dimensions_and_weight(self._payload("Weight", "1.2 kg")) == {"weight_kg": 1.2}

    def test_accepts_comma_decimal(self):
        assert _dimensions_and_weight(self._payload("Weight", "1,2 kg")) == {"weight_kg": 1.2}

    def test_ignores_load_limit_attribute(self):
        """`maximum weight recommendation` 是承重上限，不是商品自重。

        置物架标 0.9 Kg 指的是能放多重的东西；当成自重会把运费算高、
        把利润算低，而且看起来完全合理，很难事后发现。
        """
        result = _dimensions_and_weight(
            self._payload("maximum weight recommendation", "0.9 Kg")
        )
        assert result == {}

    def test_ignores_capacity_attributes(self):
        assert _dimensions_and_weight(self._payload("Capacity", "3 ml")) == {}

    def test_returns_empty_when_no_usable_attribute(self):
        assert _dimensions_and_weight({"product_information": {"items": []}}) == {}


class TestCategoryPath:
    def test_joins_with_same_separator_as_scraper(self):
        """`fee_category_matcher` 按这个字符串做子串匹配，分隔符一变规则全失效。"""
        payload = {"breadcrumbs": {"items": [
            {"name": "Automotive"},
            {"name": "Automotive Accessories"},
            {"name": "Automotive Tools"},
        ]}}
        assert _category_path(payload) == "Automotive > Automotive Accessories > Automotive Tools"

    def test_returns_none_when_no_breadcrumbs(self):
        assert _category_path({}) is None


class TestLiveTsin:
    def test_prefers_buybox_tsin(self):
        assert _live_tsin({"buybox": {"tsin": 104438993}}) == "104438993"

    def test_falls_back_to_data_layer(self):
        assert _live_tsin({"data_layer": {"tsin": "TSIN104438993"}}) == "104438993"

    def test_returns_none_when_absent(self):
        assert _live_tsin({}) is None


class TestFirstPrice:
    def test_reads_price_from_first_buybox_item(self):
        assert _first_price({"buybox": {"items": [{"price": 3299.0}]}}) == 3299.0

    def test_returns_none_when_no_numeric_price(self):
        assert _first_price({"buybox": {"items": [{"price": None}]}}) is None
        assert _first_price({}) is None
