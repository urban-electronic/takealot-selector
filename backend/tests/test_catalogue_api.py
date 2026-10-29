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
