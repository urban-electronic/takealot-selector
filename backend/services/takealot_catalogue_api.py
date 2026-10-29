"""
卖家后台目录接口（Search Takealot's Catalogue 背后的那套数据）
=============================================================

为什么需要它
------------
公开商品页只有约 10% 的商品带 `Assembled Dimensions`、约 2% 带 `Weight`，格式
还乱（"22 x 7.2 x 17 cm"、"M-L"、"4400 mAh"），所以**竞品重尺拿不到**。
但卖家后台的 Search Takealot's Catalogue 界面能看到每个 productline 的
Consolidated / Merchant Packaged Dimensions —— 那说明这份数据在
`marketplace-api.takealot.com` 上是有的，只是走卖家鉴权。

无 key 探测的结果（2026-10-29 实测）
-----------------------------------
  /v1/offers                    403  "Schemes not valid for any requirement: [['api_key']]"
  /v1/catalogue                 401  "API Key not provided"
  /v1/catalogue/{id}            401  "API Key not provided"
  /v1/catalogue/search?q=       401  "API Key not provided"
  /v1/products                  404  Path not found
  /v1/productlines/{id}         404  Path not found

401（而不是 404）说明**路径存在、只差鉴权**。注意 `/v1/catalogue` 的鉴权方案和
`/v1/offers` 不是同一套（一个是 401 "API Key not provided"，一个是 403
"Schemes not valid"），所以 key 到底该放 header 还是 query、用哪个字段名，光看
错误码猜不出来——必须拿真 key 试。

本模块就干这件事
----------------
- `probe()`：用已存的 key 逐个「端点 × 鉴权位置」组合试一遍，报告哪个组合返回
  200、响应里有哪些重尺字段。结果写进 SystemSettings，之后 enrich 直接复用。
- `fetch_dims()`：用探测成功的组合抓单个 PLID 的重尺。

安全边界：key 只在内存里拼请求头/查询串，**不写日志、不进返回值、不落库**。
探测结果里只留「端点 + 鉴权方式 + 状态码 + 字段名」，没有 key 本身。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE = "https://marketplace-api.takealot.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SETTING_KEY = "takealot_catalogue_auth"

# 端点候选。{plid} 会被替换成纯数字 ID。
ENDPOINTS: List[Tuple[str, str]] = [
    ("catalogue_by_id", "/v1/catalogue/{plid}"),
    ("catalogue_by_plid", "/v1/catalogue/PLID{plid}"),
    ("catalogue_query", "/v1/catalogue?productline_id={plid}"),
    ("catalogue_search", "/v1/catalogue/search?q={plid}"),
]

# 鉴权位置候选。顺序按「最可能是对的」排：卖家 API 文档用的是 X-API-Key。
AUTH_STYLES: List[str] = ["header_x_api_key", "bearer", "query_api_key", "query_apikey"]

_AUTH_LABEL = {
    "header_x_api_key": "请求头 X-API-Key",
    "bearer": "请求头 Authorization: Bearer",
    "query_api_key": "查询参数 ?api_key=",
    "query_apikey": "查询参数 ?apikey=",
}

_DIM_WORDS = ("length", "width", "height", "weight", "dimension")
_UNIT_SCALE_MM = {"mm": 1.0, "millimeter": 1.0, "cm": 10.0, "centimeter": 10.0,
                  "m": 1000.0, "meter": 1000.0, "in": 25.4, "inch": 25.4}
_UNIT_SCALE_KG = {"kg": 1.0, "kilogram": 1.0, "g": 0.001, "gram": 0.001,
                  "lb": 0.4536, "lbs": 0.4536, "pound": 0.4536}


def configured_key(db) -> str:
    """复用卖家 API 已存的那把 key（同一套 marketplace 鉴权体系）。"""
    from services.takealot_seller_api import configured_key as seller_key
    try:
        return seller_key(db) or ""
    except Exception:
        return ""


def _request(url: str, key: str, style: str, timeout: float = 20.0) -> Tuple[int, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params: Dict[str, str] = {}
    if style == "header_x_api_key":
        headers["X-API-Key"] = key
    elif style == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif style == "query_api_key":
        params["api_key"] = key
    elif style == "query_apikey":
        params["apikey"] = key

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url, headers=headers, params=params or None)
        if response.status_code != 200:
            return response.status_code, None
        try:
            return 200, response.json()
        except ValueError:
            return 200, None


def _walk_dimension_keys(payload: Any, path: str = "", found: Optional[List[str]] = None) -> List[str]:
    """收集所有「名字里带 length/width/height/weight」的字段路径。

    只用来在探测结果里告诉人「响应里到底有什么」，不参与取值。
    """
    if found is None:
        found = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            here = f"{path}.{name}" if path else str(name)
            if any(word in str(name).lower() for word in _DIM_WORDS):
                found.append(here)
            _walk_dimension_keys(value, here, found)
    elif isinstance(payload, list):
        for index, value in enumerate(payload[:3]):   # 只看前几条，够判断字段名
            _walk_dimension_keys(value, f"{path}[{index}]", found)
    return found


def _scale_from_text(text: str, table: Dict[str, float]) -> Optional[float]:
    """从 "22.0 cm" / "length_cm" 里认出单位并返回换算系数。"""
    lowered = text.lower()
    # 先匹配长的单位名，避免 "cm" 先被 "m" 命中
    for unit in sorted(table, key=len, reverse=True):
        # `s?` 兜住复数：接口里常见 `weight_grams` / `length_millimeters`，
        # 不认复数就会退到 "g" 那条，而 "grams" 里的 g 后面跟的是 r，匹配不上，
        # 最后返回 None，重量直接丢。
        if re.search(rf"(^|[^a-z]){unit}s?([^a-z]|$)", lowered):
            return table[unit]
    return None


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(",", "."))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def extract_dims(payload: Any) -> Dict[str, Optional[float]]:
    """从目录响应里抠出 mm / kg。

    响应结构未知，所以按「找一组同时含长宽高的兄弟字段」来定位，再按字段名或
    同级 unit 字段判断单位。宁可返回空字典，也不要瞎猜——重尺错一位，运费和
    利润率全错。
    """
    candidates: List[Dict[str, Any]] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            lowered = {str(k).lower(): k for k in node}
            if (any("length" in k for k in lowered)
                    and any("width" in k for k in lowered)
                    and any("height" in k for k in lowered)):
                candidates.append(node)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(payload)

    for node in candidates:
        values: Dict[str, float] = {}
        unit_hint = ""
        for raw_key, value in node.items():
            name = str(raw_key).lower()
            if name in ("unit", "dimension_unit", "measurement_unit", "units"):
                unit_hint = str(value)
                continue
            number = _to_number(value)
            if number is None:
                continue
            for axis in ("length", "width", "height"):
                if axis in name and axis not in values:
                    scale = _scale_from_text(name, _UNIT_SCALE_MM)
                    if scale is None and unit_hint:
                        scale = _scale_from_text(unit_hint, _UNIT_SCALE_MM)
                    if scale is None and isinstance(value, str):
                        scale = _scale_from_text(value, _UNIT_SCALE_MM)
                    if scale is None:
                        # 目录接口的尺寸字段通常是 cm；无单位时按 cm 处理，
                        # 与公开页 `Assembled Dimensions` 的口径一致
                        scale = 10.0
                    values[axis] = round(number * scale, 1)
                    break
        if len(values) < 3:
            continue

        weight_kg = None
        for raw_key, value in node.items():
            name = str(raw_key).lower()
            if "weight" not in name or "maximum" in name or "recommend" in name:
                continue
            number = _to_number(value)
            if number is None:
                continue
            scale = _scale_from_text(name, _UNIT_SCALE_KG)
            if scale is None and isinstance(value, str):
                scale = _scale_from_text(value, _UNIT_SCALE_KG)
            if scale is None:
                scale = 1.0   # 目录接口的重量字段一般是 kg
            weight_kg = round(number * scale, 3)
            break

        return {
            "length_mm": values.get("length"),
            "width_mm": values.get("width"),
            "height_mm": values.get("height"),
            "weight_kg": weight_kg,
        }
    return {}


def probe(db, plid: Any) -> Dict[str, Any]:
    """逐个组合试一遍，返回可读的探测报告。不修改数据库。"""
    key = configured_key(db)
    if not key:
        return {"ok": False, "reason": "没有配置卖家 API 密钥，无法探测", "tried": []}

    resolved = str(plid).strip()
    if not resolved.isdigit():
        match = re.search(r"(\d{5,})", resolved)
        resolved = match.group(1) if match else ""
    if not resolved:
        return {"ok": False, "reason": "PLID 无法识别", "tried": []}

    tried: List[Dict[str, Any]] = []
    winner: Optional[Dict[str, str]] = None
    for endpoint_id, template in ENDPOINTS:
        path = template.format(plid=resolved)
        for style in AUTH_STYLES:
            try:
                status, payload = _request(BASE + path, key, style)
            except Exception as error:
                tried.append({"endpoint": endpoint_id, "path": path,
                              "auth": _AUTH_LABEL[style], "status": 0,
                              "note": f"{type(error).__name__}"})
                continue
            record = {
                "endpoint": endpoint_id,
                "path": path,
                "auth": _AUTH_LABEL[style],
                "status": status,
            }
            if status == 200 and payload is not None:
                keys = _walk_dimension_keys(payload)
                dims = extract_dims(payload)
                record["dimension_keys"] = keys[:12]
                record["dims"] = dims
                if dims.get("length_mm") and dims.get("width_mm") and dims.get("height_mm"):
                    winner = {"endpoint": endpoint_id, "auth": style}
                    record["usable"] = True
            elif status in (401, 403):
                record["note"] = "鉴权未通过"
            elif status == 404:
                record["note"] = "路径不存在"
            tried.append(record)
            if winner:
                break
        if winner:
            break

    return {
        "ok": winner is not None,
        "plid": resolved,
        "tried": tried,
        "winner": winner,
        "reason": "" if winner else "所有组合都没拿到完整的长宽高",
    }


def save_winner(db, winner: Dict[str, str]) -> None:
    from models import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == SETTING_KEY).first()
    value = json.dumps(winner, ensure_ascii=False)
    if row:
        row.value = value
    else:
        db.add(SystemSettings(key=SETTING_KEY, value=value))
    db.commit()


def load_winner(db) -> Optional[Dict[str, str]]:
    from models import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == SETTING_KEY).first()
    if not row or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("endpoint") or not data.get("auth"):
        return None
    return data


def fetch_dims(plid: Any, key: str, winner: Dict[str, str]) -> Dict[str, Optional[float]]:
    """用探测成功的组合抓单个 PLID 的重尺。失败返回空字典，不抛异常。"""
    template = dict(ENDPOINTS).get(winner.get("endpoint", ""))
    if not template or not key:
        return {}
    path = template.format(plid=str(plid).strip())
    try:
        status, payload = _request(BASE + path, key, winner.get("auth", ""))
    except Exception:
        return {}
    if status != 200 or payload is None:
        return {}
    return extract_dims(payload)
