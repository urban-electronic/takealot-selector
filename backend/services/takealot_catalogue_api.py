"""已保存目录接入的只读检查与重尺解析。

没有已保存、有效的连接配置时，不读取密钥或尝试端点/鉴权组合。
已有连接仅按保存的端点与方式检查一次；认证拒绝或限流立即停止。
401本身不能证明端点存在或鉴权方式，只能如实报告未接入/读取失败。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE = "https://marketplace-api.takealot.com"
USER_AGENT = "takealot-selector/1.0"
SETTING_KEY = "takealot_catalogue_auth"

# 历史已保存连接的允许端点；不遍历发现，{plid} 仅接受数字。
ENDPOINTS: List[Tuple[str, str]] = [
    ("catalogue_by_id", "/v1/catalogue/{plid}"),
    ("catalogue_by_plid", "/v1/catalogue/PLID{plid}"),
    ("catalogue_query", "/v1/catalogue?productline_id={plid}"),
    ("catalogue_search", "/v1/catalogue/search?q={plid}"),
]

# 仅兼容已保存的鉴权方式；不得依次尝试。
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


class CatalogueAccessDenied(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        reason = {401:'认证未通过',403:'访问被拒绝',429:'请求受限流'}[status]
        super().__init__(f'目录接口{reason}（HTTP {status}），已停止本轮读取，本轮未提交资料未保存')


def _valid_winner(winner: Any) -> bool:
    return (isinstance(winner, dict)
            and isinstance(winner.get('endpoint'), str)
            and winner['endpoint'] in dict(ENDPOINTS)
            and isinstance(winner.get('auth'), str)
            and winner['auth'] in AUTH_STYLES)


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

    只用来在检查结果里告诉人「响应里到底有什么」，不参与取值。
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
    """仅检查已保存连接一次，不探索端点或更换鉴权方式。"""
    resolved = str(plid).strip()
    report: Dict[str, Any] = {'ok':False,'tried':[],'winner':None,'reason':''}
    if not resolved.isdigit():
        report['reason'] = 'PLID 无法识别'
        return report
    winner = load_winner(db)
    if not _valid_winner(winner):
        report['reason'] = '尚未接入已确认的目录接口，已停用端点及鉴权组合探测'
        return report
    winner = {"endpoint": winner["endpoint"], "auth": winner["auth"]}
    key = configured_key(db)
    if not key:
        report['reason'] = '已保存目录连接的鉴权配置不可用，未发送请求'
        return report
    path = dict(ENDPOINTS)[winner['endpoint']].format(plid=resolved)
    row = {'endpoint':winner['endpoint'],'path':path,'auth':_AUTH_LABEL[winner['auth']],'status':0}
    report.update(plid=resolved,tried=[row])
    try:
        status,payload = _request(BASE+path,key,winner['auth'])
    except Exception as error:
        row['note'] = type(error).__name__
        report['reason'] = '目录连接读取失败，未尝试其他端点或鉴权方式'
        return report
    row['status'] = status
    if status != 200 or payload is None:
        report['reason'] = f'目录接口返回 HTTP {status}，已停止检查，未尝试其他端点或鉴权方式'
        return report
    dims = extract_dims(payload)
    row.update(dimension_keys=_walk_dimension_keys(payload)[:12],dims=dims)
    if dims.get('length_mm') and dims.get('width_mm') and dims.get('height_mm'):
        row['usable'] = True
        report.update(ok=True,winner=winner)
    else:
        report['reason'] = '已保存目录连接返回资料不完整，未尝试其他端点或鉴权方式'
    return report


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
    if not _valid_winner(data):
        return None
    return {"endpoint": data["endpoint"], "auth": data["auth"]}


def fetch_dims(plid: Any, key: str, winner: Dict[str, str]) -> Dict[str, Optional[float]]:
    """用已保存连接读取单个数字PLID；拒绝或限流明确抛出停止信号。"""
    resolved = str(plid).strip()
    if not _valid_winner(winner) or not key or not resolved.isdigit():
        return {}
    template = dict(ENDPOINTS)[winner["endpoint"]]
    path = template.format(plid=resolved)
    try:
        status, payload = _request(BASE + path, key, winner.get("auth", ""))
    except Exception:
        return {}
    if status in (401, 403, 429):
        raise CatalogueAccessDenied(status)
    if status != 200 or payload is None:
        return {}
    return extract_dims(payload)
