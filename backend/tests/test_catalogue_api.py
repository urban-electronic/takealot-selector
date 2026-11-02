"""卖家目录接口的重尺解析。

这个模块最危险的地方是**猜单位**：接口响应结构未知，字段名可能叫
`length_cm`、`length`、`consolidated_packaged_length`，单位可能在字段名里、
可能在同级 unit 字段里、也可能压根没写。猜错一位，运费和利润率就全错，
所以这里把每种写法都钉住。

不联网：只测纯函数 `extract_dims` / `_scale_from_text` / `_walk_dimension_keys`。
接入检查只用隔离响应；不调用真实鉴权端点。
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


@pytest.mark.parametrize('winner',[None,{}, {'endpoint':'unknown','auth':'bearer'},
    {'endpoint':{},'auth':[]}, {'endpoint':'catalogue_by_id','auth':'unknown'}])
def test_no_discovery_or_key_read_without_valid_saved_connection(monkeypatch,winner):
    from services import takealot_catalogue_api as api
    monkeypatch.setattr(api,'load_winner',lambda _:winner)
    monkeypatch.setattr(api,'configured_key',lambda _:pytest.fail('unexpected credential read'))
    monkeypatch.setattr(api,'_request',lambda *a:pytest.fail('unexpected auth discovery'))
    result=api.probe(object(),123456)
    assert result['ok'] is False
    assert result['tried']==[]


@pytest.mark.parametrize('status',[200,401,403,429,404,503])
def test_check_only_saved_path_and_auth_no_fallback(monkeypatch,status):
    from services import takealot_catalogue_api as api
    winner={'endpoint':'catalogue_search','auth':'header_x_api_key'}
    monkeypatch.setattr(api,'load_winner',lambda _:winner)
    monkeypatch.setattr(api,'configured_key',lambda _:'fixture-only')
    calls=[]
    def request(url,key,style):
        calls.append((url,style))
        return status, {'length_cm':10,'width_cm':20,'height_cm':30}
    monkeypatch.setattr(api,'_request',request)
    result=api.probe(object(),123456)
    assert calls==[(api.BASE+'/v1/catalogue/search?q=123456','header_x_api_key')]
    assert result['ok'] is (status==200)
    assert len(result['tried'])==1
    if status!=200:assert result.get('winner') is None
    assert 'fixture-only' not in str(result)


def test_saved_connection_error_is_safe_and_does_not_fallback(monkeypatch):
    from services import takealot_catalogue_api as api
    monkeypatch.setattr(api,'load_winner',lambda _:{'endpoint':'catalogue_by_id','auth':'header_x_api_key'})
    monkeypatch.setattr(api,'configured_key',lambda _:'fixture-only')
    calls=[]
    def request(*args):
        calls.append(1)
        raise RuntimeError('synthetic-secret-in-message-and-url')
    monkeypatch.setattr(api,'_request',request)
    result=api.probe(object(),123456)
    assert len(calls)==1
    assert result['ok'] is False
    assert 'synthetic-secret' not in str(result)


@pytest.mark.parametrize('status',[401,403,429])
def test_saved_dims_denial_is_not_silent_empty_data(monkeypatch,status):
    from services import takealot_catalogue_api as api
    calls=[]
    monkeypatch.setattr(api,'_request',lambda *a: calls.append(1) or (status,None))
    with pytest.raises(api.CatalogueAccessDenied) as error:
        api.fetch_dims(123456,'fixture-only',{'endpoint':'catalogue_by_id','auth':'header_x_api_key'})
    assert len(calls)==1
    assert str(status) in str(error.value)
    assert 'fixture-only' not in str(error.value)


def test_request_uses_truthful_headers_and_never_follows_redirects(monkeypatch):
    from services import takealot_catalogue_api as api
    seen=[]
    class Client:
        def __init__(self,**kwargs):seen.append(kwargs)
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            seen.append(kwargs)
            return type('Response',(),{'status_code':403})()
    monkeypatch.setattr(api.httpx,'Client',Client)
    assert api._request(api.BASE+'/v1/catalogue/123456','fixture-only','header_x_api_key')==(403,None)
    assert seen[0]['follow_redirects'] is False
    assert seen[1]['headers']=={'User-Agent':'takealot-selector/1.0','Accept':'application/json','X-API-Key':'fixture-only'}


# --- 后台商品页那种「字符串」形态 -----------------------------------------
#
# 卖家后台（sellers.takealot.com/catalogue/mvp/PILID...）上显示的是
#   Consolidated Packaged Dimensions: 86.0 x 50.0 x 190.0 mm
#   Merchant Packaged Weight:         165 g
# 而原来的解析只认 `length_cm` 这种分开的数值字段，所以从后台抓回来的
# 数据一条都解析不出来——「重尺一直补不上」的真正原因就在这里。
# 探测通了也没用：dims 永远是空字典。


def test_string_dimensions_from_seller_backend():
    payload = {"catalogue": {
        "Consolidated Packaged Dimensions": "86.0 x 50.0 x 190.0 mm",
        "Cubisan Packaged dimensions": "86.0 x 190.0 x 50.0 mm",
        "Merchant Packaged Dimensions": "3 x 8 x 18 cm",
    }}
    dims = extract_dims(payload)
    assert dims["length_mm"] == 86.0
    assert dims["width_mm"] == 50.0
    assert dims["height_mm"] == 190.0


def test_consolidated_wins_over_merchant():
    """计费口径是 Consolidated，不是卖家申报的 Merchant。"""
    payload = {
        "Merchant Packaged Dimensions": "3 x 8 x 18 cm",
        "Consolidated Packaged Dimensions": "86.0 x 50.0 x 190.0 mm",
    }
    assert extract_dims(payload)["length_mm"] == 86.0


def test_string_dimensions_without_unit_default_cm():
    dims = extract_dims({"Merchant Packaged Dimensions": "3 x 8 x 18"})
    assert dims["length_mm"] == 30.0
    assert dims["width_mm"] == 80.0
    assert dims["height_mm"] == 180.0


def test_string_weight_units_and_priority():
    """Cubisan（Takealot 扫描）优先于 Merchant（卖家申报）。"""
    payload = {
        "Consolidated Packaged Dimensions": "86.0 x 50.0 x 190.0 mm",
        "Cubisan packaged weight": "0.16 kg",
        "Merchant Packaged Weight": "165 g",
    }
    assert extract_dims(payload)["weight_kg"] == 0.16


def test_string_weight_grams_converted():
    payload = {
        "Consolidated Packaged Dimensions": "86.0 x 50.0 x 190.0 mm",
        "Merchant Packaged Weight": "165 g",
    }
    assert extract_dims(payload)["weight_kg"] == 0.165


def test_string_maximum_weight_ignored():
    """承重上限不是商品自重。"""
    payload = {
        "Consolidated Packaged Dimensions": "86.0 x 50.0 x 190.0 mm",
        "Maximum Weight Recommendation": "30 kg",
    }
    assert extract_dims(payload)["weight_kg"] is None


def test_weight_falls_back_to_text_when_structured_missing():
    """结构化字段只给了三边，重量从字符串字段补。"""
    payload = {
        "d": {"length_cm": 22, "width_cm": 7.2, "height_cm": 17},
        "Merchant Packaged Weight": "165 g",
    }
    dims = extract_dims(payload)
    assert dims["length_mm"] == 220.0
    assert dims["weight_kg"] == 0.165


def test_string_dimensions_need_all_three_sides():
    """只有 "22 x 7 cm" 这种两维的，算不出体积重，宁可返回空。"""
    assert extract_dims({"Merchant Packaged Dimensions": "22 x 7 cm"}) == {}


class TestDiscover:
    """用户主动授权的一次性探测。

    后台 enrich 走只读路径（不扫上游）；只有前端那个按钮传 discover=True。
    没有这条路径，重尺功能整体是死的。
    """

    def test_finds_first_working_combination(self, monkeypatch):
        from services import takealot_catalogue_api as api

        def fake_request(url, key, style, timeout=20.0):
            if style == 'header_x_api_key':
                return 200, {'Consolidated Packaged Dimensions': '10 x 20 x 30 cm'}
            return 401, None

        monkeypatch.setattr(api, '_request', fake_request)
        monkeypatch.setattr(api, 'configured_key', lambda db: 'k')

        report = api.probe(None, '94399784', True)

        assert report['ok'] is True
        # 线上到底哪个端点在用是未知数（纯数字和 PLID 前缀都可能），这里只钉
        # 「必须用 X-API-Key 且命中 ENDPOINTS 里的某个端点」——顺序不该被测试锁死。
        assert report['winner']['auth'] == 'header_x_api_key'
        assert report['winner']['endpoint'] in dict(api.ENDPOINTS)
        assert report['tried'][0]['dims']['length_mm'] == 100.0

    def test_read_only_by_default(self, monkeypatch):
        """默认不发现：没保存过连接就直接说没接入，一个请求都不发。"""
        from services import takealot_catalogue_api as api

        monkeypatch.setattr(api, '_request', lambda *a, **k: pytest.fail('默认路径不该发请求'))
        monkeypatch.setattr(api, 'load_winner', lambda db: None)
        monkeypatch.setattr(api, 'configured_key', lambda db: 'k')

        report = api.probe(None, '94399784')

        assert report['ok'] is False
        assert '未接入' in report['reason']

    def test_discover_needs_a_key(self, monkeypatch):
        from services import takealot_catalogue_api as api

        monkeypatch.setattr(api, '_request', lambda *a, **k: pytest.fail('没 key 不该发请求'))
        monkeypatch.setattr(api, 'configured_key', lambda db: '')

        report = api.probe(None, '94399784', True)

        assert report['ok'] is False
        assert '没有配置卖家 API 密钥' in report['reason']
