"""已保存目录接入的只读检查与重尺解析。

没有已保存、有效的连接配置时，不读取密钥或尝试端点/鉴权组合。
已有连接仅按保存的端点与方式检查一次；认证拒绝或限流立即停止。
401本身不能证明端点存在或鉴权方式，只能如实报告未接入/读取失败。
"""

from __future__ import annotations

import json
import re
import time
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE = "https://marketplace-api.takealot.com"
USER_AGENT = "takealot-selector/1.0"
SETTING_KEY = "takealot_catalogue_auth"
# 用户主动点「探测竞品重尺接口」时最多试多久。最坏 5 端点 × 4 鉴权位置 = 20 次
# 请求，不设上限会把一次点击拖成几分钟。
DISCOVERY_BUDGET_SECONDS = 30.0
CATALOGUE_BATCH_WAIT_SECONDS = 45.0
_catalogue_dispatch_lock = threading.Lock()
_catalogue_pool = ThreadPoolExecutor(max_workers=1)
_active_catalogue_read = None


class CatalogueReadPending(RuntimeError):
    def __init__(self):
        super().__init__('目录资料读取未完成或仍在忙，本轮未提交资料未保存，请稍后重试')


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


# 后台商品页上的尺寸是**字符串**，不是分开的数值字段：
#   Consolidated Packaged Dimensions: 86.0 x 50.0 x 190.0 mm
#   Merchant Packaged Dimensions:     3 x 8 x 18 cm
# 只认 `length_cm` 这种结构化字段的话，从后台抓回来的数据一条都解析不出来。
_DIM_TEXT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:x|×|\*)\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|\*)\s*(\d+(?:[.,]\d+)?)",
    re.I,
)

# 同一个商品在后台有三套打包数据，含义不同：
#   Consolidated —— Takealot 合并后用于**计费**的口径
#   Cubisan      —— Takealot 体积重扫描仪量出来的
#   Merchant     —— 卖家自己申报的打包尺寸
# 核算要跟 Takealot 账单对得上，所以按计费口径优先，卖家申报的放最后。
_DIM_KEY_PRIORITY = ("consolidated", "cubisan", "merchant")

# 这些词说明是「承重上限 / 推荐载荷」，不是商品自重。
_NOT_WEIGHT = ("maximum", "recommend", "capacity", "limit", "load", "max ")


def _key_priority(name: str) -> int:
    lowered = name.lower()
    for index, word in enumerate(_DIM_KEY_PRIORITY):
        if word in lowered:
            return index
    return len(_DIM_KEY_PRIORITY)


def _walk_dicts(node: Any):
    """深度遍历，把每个 dict 交给调用方。"""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value)


def _text_dims(payload: Any) -> Optional[Dict[str, float]]:
    """从字符串字段里抠三边，例如 "86.0 x 50.0 x 190.0 mm" / "3 x 8 x 18 cm"。"""
    found: List[Tuple[int, Dict[str, float]]] = []
    for node in _walk_dicts(payload):
        for name, value in node.items():
            if not isinstance(value, str):
                continue
            lowered = str(name).lower()
            if "dimension" not in lowered and "size" not in lowered:
                continue
            match = _DIM_TEXT_RE.search(value)
            if not match:
                continue
            scale = _scale_from_text(value, _UNIT_SCALE_MM) or 10.0
            numbers = [float(part.replace(",", ".")) for part in match.groups()]
            found.append((_key_priority(lowered), {
                "length_mm": round(numbers[0] * scale, 1),
                "width_mm": round(numbers[1] * scale, 1),
                "height_mm": round(numbers[2] * scale, 1),
            }))
    if not found:
        return None
    found.sort(key=lambda pair: pair[0])
    return found[0][1]


def _text_weight(payload: Any) -> Optional[float]:
    """从字符串字段里抠重量，例如 "0.16 kg" / "165 g"。"""
    found: List[Tuple[int, float]] = []
    for node in _walk_dicts(payload):
        for name, value in node.items():
            lowered = str(name).lower()
            if "weight" not in lowered:
                continue
            if any(bad in lowered for bad in _NOT_WEIGHT):
                continue
            text = str(value)
            number = _to_number(text)
            if number is None:
                continue
            scale = _scale_from_text(lowered, _UNIT_SCALE_KG)
            if scale is None:
                scale = _scale_from_text(text, _UNIT_SCALE_KG)
            if scale is None:
                scale = 1.0   # 目录接口的重量字段一般是 kg
            found.append((_key_priority(lowered), round(number * scale, 3)))
    if not found:
        return None
    found.sort(key=lambda pair: pair[0])
    return found[0][1]


def _structured_dims(payload: Any) -> Optional[Dict[str, Optional[float]]]:
    """长宽高是分开的数值字段时（`length_cm` / `width` + `unit`）怎么取。"""
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
            if "weight" not in name or any(bad in name for bad in _NOT_WEIGHT):
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
    return None


def extract_dims(payload: Any) -> Dict[str, Optional[float]]:
    """从目录响应里抠出 mm / kg。

    响应结构未知，而且**同一个商品在后台有两种完全不同的表示**：结构化
    （`length_cm: 86`）和字符串（`"86.0 x 50.0 x 190.0 mm"`）。只认其中一种，
    就会有一半商品解析不出来——这正是「后台明明有重尺、程序一直补不上」的
    原因，探测通了也白通，因为解析结果永远是空。

    所以两种都试，缺的字段互相补。宁可返回空字典，也不要瞎猜——重尺错一位，
    运费和利润率全错。
    """
    out: Dict[str, Optional[float]] = dict(_structured_dims(payload) or {})

    if not (out.get("length_mm") and out.get("width_mm") and out.get("height_mm")):
        textual = _text_dims(payload)
        if textual:
            out.update(textual)

    if out.get("weight_kg") is None:
        weight = _text_weight(payload)
        if weight is not None:
            out["weight_kg"] = weight

    if not (out.get("length_mm") and out.get("width_mm") and out.get("height_mm")):
        return {}
    return {
        "length_mm": out.get("length_mm"),
        "width_mm": out.get("width_mm"),
        "height_mm": out.get("height_mm"),
        "weight_kg": out.get("weight_kg"),
    }


def probe(db, plid: Any, discover: bool = False) -> Dict[str, Any]:
    """检查（或在用户明确请求时接入）目录接口。

    `discover=False`（默认）：只检查已保存的连接一次，不读密钥、不探索端点。
    enrich 这类后台流程走这条，符合「不主动扫描上游」的边界。

    `discover=True`：**只有用户主动点「探测竞品重尺接口」时才传**。一次性试
    「端点 × 鉴权位置」，把能用的组合报告出来，由调用方决定是否保存。
    这是唯一会产生写入的路径——少了它 `takealot_catalogue_auth` 永远是空，
    `_fill_catalogue_dims` 直接 return 0，重尺功能整体不可用（线上就是这样）。
    """
    resolved = str(plid).strip()
    report: Dict[str, Any] = {'ok':False,'tried':[],'winner':None,'reason':''}
    if not resolved.isdigit():
        report['reason'] = 'PLID 无法识别'
        return report
    if discover:
        return _discover(db, resolved, report)
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


def _discover(db, resolved: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """一次性试「端点 × 鉴权位置」，只为「用户主动点按钮」这个场景服务。

    不做「key 无效就立刻收手」的短路：`_request` 只回状态码，拿不到服务端那句
    `Authentication failed`，分不出「这个头没被读」和「key 不对」，所以靠总
    时长兜底，别让它把请求拖住。
    """
    key = configured_key(db)
    if not key:
        report['reason'] = '没有配置卖家 API 密钥，无法探测'
        return report

    started = time.monotonic()
    for endpoint_id, template in ENDPOINTS:
        path = template.format(plid=resolved)
        for style in AUTH_STYLES:
            if time.monotonic() - started > DISCOVERY_BUDGET_SECONDS:
                report['reason'] = f'探测超过 {DISCOVERY_BUDGET_SECONDS:.0f} 秒未完成，请稍后再试'
                return report
            row: Dict[str, Any] = {'endpoint': endpoint_id, 'path': path,
                                   'auth': _AUTH_LABEL[style], 'status': 0}
            try:
                status, payload = _request(BASE + path, key, style)
            except Exception as error:
                row['note'] = type(error).__name__
                report['tried'].append(row)
                continue
            row['status'] = status
            if status == 200 and payload is not None:
                dims = extract_dims(payload)
                row.update(dimension_keys=_walk_dimension_keys(payload)[:12], dims=dims)
                if dims.get('length_mm') and dims.get('width_mm') and dims.get('height_mm'):
                    row['usable'] = True
                    report['tried'].append(row)
                    report.update(ok=True,
                                  winner={'endpoint': endpoint_id, 'auth': style})
                    return report
            elif status in (401, 403):
                row['note'] = '鉴权未通过'
            elif status == 404:
                row['note'] = '路径不存在'
            report['tried'].append(row)

    report['reason'] = '所有组合都没拿到完整的长宽高'
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


def fetch_dims(plid: Any, key: str, winner: Dict[str, str], timeout: Optional[float] = None) -> Dict[str, Optional[float]]:
    """用已保存连接读取单个数字PLID；拒绝或限流明确抛出停止信号。"""
    resolved = str(plid).strip()
    if not _valid_winner(winner) or not key or not resolved.isdigit():
        return {}
    template = dict(ENDPOINTS)[winner["endpoint"]]
    path = template.format(plid=resolved)
    try:
        if timeout is None:
            status, payload = _request(BASE + path, key, winner.get("auth", ""))
        else:
            status, payload = _request(BASE + path, key, winner.get("auth", ""), timeout)
    except Exception:
        return {}
    if status in (401, 403, 429):
        raise CatalogueAccessDenied(status)
    if status != 200 or payload is None:
        return {}
    return extract_dims(payload)



def _read_dims_batch(plids, key, winner, deadline):
    results = {}
    for plid in plids:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break  # 只返回已完成的逐商品结果，未读商品不伪造为空重尺。
        results[plid] = fetch_dims(plid, key, winner, timeout=min(20.0, remaining))
    return results


def fetch_dims_many(plids, key, winner):
    """只读worker不接触DB；调用方到时回滚，同一未交付批次复用在途结果。"""
    global _active_catalogue_read
    if not _valid_winner(winner) or not key:
        return {}
    unique = tuple(dict.fromkeys(str(p).strip() for p in plids if str(p).strip().isdigit()))
    if not unique:
        return {}
    identity = (hashlib.sha256(key.encode()).hexdigest(), winner['endpoint'], winner['auth'], unique)
    with _catalogue_dispatch_lock:
        active = _active_catalogue_read
        if active is not None and not active['consumed']:
            future = active['future']
            if active['identity'] != identity:
                if not future.done():
                    raise CatalogueReadPending()
                error = future.exception()
                if isinstance(error, CatalogueAccessDenied):
                    active['consumed'] = True
                    raise error  # 迟到拒绝必须先交付，不能直接开启另一批。
                active = None
        else:
            active = None
        if active is None:
            deadline = time.monotonic() + CATALOGUE_BATCH_WAIT_SECONDS
            future = _catalogue_pool.submit(_read_dims_batch, unique, key, dict(winner), deadline)
            active = {'identity':identity, 'future':future, 'consumed':False}
            _active_catalogue_read = active
        future = active['future']
    try:
        result = future.result(timeout=CATALOGUE_BATCH_WAIT_SECONDS)
    except FutureTimeout:
        if not future.done():
            raise CatalogueReadPending() from None
        # 任务自身的异常也须被交付，不当作等待超时。
        try:
            result = future.result()
        finally:
            with _catalogue_dispatch_lock:
                active['consumed'] = True
    except BaseException:
        with _catalogue_dispatch_lock:
            active['consumed'] = True
        raise
    else:
        with _catalogue_dispatch_lock:
            active['consumed'] = True
    return result
