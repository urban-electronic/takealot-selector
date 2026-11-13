"""卖家门户目录接口（seller-api.takealot.com /1/catalogue/mpv）

为什么需要这个模块
------------------
公开接口 `api.takealot.com/rest/v-1-9-0/product-details` 从机房 IP 批量抓会被
Cloudflare 429：实测 1804 个候选商品只有 632 个拿到主图。而且公开接口**根本
没有**长宽高和重量——原始 payload 里连 "a x b x c" 形式的字符串都搜不到。

卖家门户自己的「Search Takealot's Catalogue」页走的是另一套主机：

    GET https://seller-api.takealot.com/1/catalogue/mpv/PLID{plid}

鉴权是卖家门户登录后下发的 Bearer JWT（浏览器 localStorage 的
`usr_st_auth` → `api_key`）。实测这个通道：

  - **一个请求同时给主图、全部图库、类目、长宽高、重量**；
  - **覆盖整个 Takealot 目录**，不限于本店在卖的商品（`hasOffer: false` 也能查）；
  - 配额 **200 次 / 60 秒**（响应头 `x-ratelimit-limit` / `x-ratelimit-remaining`
    明写着），比公开接口那个「一碰就 429 还查不出原因」的 Cloudflare 桶清楚得多。

实测 1798 个候选商品：主图 1797、长宽高 1797（全部来自 Takealot 自己用
Cubiscan 量过的 `consolidated_packaged_dimensions`）、重量 1797，耗时 9.7 分钟。
同一批商品走公开接口只有 632 个拿到主图、0 个有重尺。

令牌有效期 24 小时，由门户登录时通过 URL 参数下发，没有独立的刷新接口，
过期后需要重新取一次。过期时本模块返回 `portal_access_blocked`，调用方应
退回公开接口而不是报错。

不做什么
--------
- 不模拟浏览器、不绕过站点验证、不切换出口。
- 不写数据库。本模块只负责「给 PLID，返回资料」，落库由调用方决定。
- 不猜缺失的值：拿不到就留空，绝不用类目默认值冒充。
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

import httpx

API_BASE = "https://seller-api.takealot.com"
SETTING_KEY = "_takealot_portal_token"
USER_AGENT = "takealot-selector/1.0"

# 配额（实测自响应头 `x-ratelimit-limit` / `x-ratelimit-remaining`）：
#   200 次 / 60 秒窗口，窗口每分钟滚动一次。
# 1804 个候选要跑满 9 个窗口，约 9 分钟——这是接口本身的硬限制，不是我们的
# 实现问题。所以这里不追求「一次抓完」，而是**按配额节奏推进**：预算内能抓
# 多少抓多少，剩下的留给下一轮，绝不把 429 记成永久失败。
RATE_LIMIT_LIMIT = 200
RATE_LIMIT_WINDOW_SECONDS = 60.0
# 留一点余量：浏览器里同时开着的门户页面也在吃同一个配额。
RATE_LIMIT_RESERVE = 12

REQUEST_TIMEOUT_SECONDS = 25.0
BATCH_WORKERS = 3
BATCH_BUDGET_SECONDS = 40.0
MAX_429_RETRIES = 1

# 401/403 是令牌问题——继续打只会拿回一片 401，必须整批停下。
# 429 是配额问题，等一个窗口就好，不在此列。
ACCESS_STOP_STATUS = {401, 403}
RATE_LIMITED_STATUS = {429}
NOT_FOUND_STATUS = {404}

# 长宽高属性名。按可信度排序：Takealot 自己用 Cubiscan 量过的 consolidated
# 最准，其次是仓库实测的 cbs_，最后才是卖家自己填的 merchant_。
DIM_ATTRIBUTES = (
    ("consolidated_packaged_dimensions", "consolidated"),
    ("cbs_package_dimensions", "cubiscan"),
    ("merchant_packaged_dimensions", "merchant"),
)
WEIGHT_ATTRIBUTES = (
    ("cbs_package_weight", "cubiscan"),
    ("consolidated_packaged_weight", "consolidated"),
    ("merchant_packaged_weight", "merchant"),
)

_UNIT_SCALE_MM = {"mm": 1.0, "millimeter": 1.0, "millimetre": 1.0,
                  "cm": 10.0, "centimeter": 10.0, "centimetre": 10.0,
                  "m": 1000.0, "meter": 1000.0, "metre": 1000.0}
_UNIT_SCALE_KG = {"kg": 1.0, "kilogram": 1.0,
                  "g": 0.001, "gram": 0.001,
                  "lb": 0.4536, "pound": 0.4536}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


class PortalAccessDenied(RuntimeError):
    """令牌失效或没有权限——需要重新从卖家门户取一次。"""


class PortalNotConfigured(RuntimeError):
    """没有配置卖家门户令牌。"""


def _cipher():
    from services.takealot_seller_api import _cipher as seller_cipher
    return seller_cipher()


def configured_token(db, store_id=None) -> str:
    """读取当前店铺已保存的门户令牌；没配过返回空串。

    多店铺（STORE-CRED-01）：store_id 为空时按请求作用域解析
    （见 services/store_credentials.py）。默认店铺回落旧的全局键。
    """
    from services import store_credentials
    return store_credentials.portal_token(db, store_id)


def save_token(db, token: str, store_id=None) -> None:
    """写入门户令牌。空串写入撤销标记，阻止默认店回落旧令牌。

    加密复用卖家 API 那一套 `_cipher()`，但**不能**复用它的 `encrypt_key()`：
    那个函数要求 128 位十六进制（那是 marketplace API Key 的格式），而门户令牌
    是 JWT，直接塞进去会被它挡下来。

    多店铺（STORE-CRED-01）：按 store_id 或请求作用域落到 `<键>:<store_id>`，
    两个店铺的令牌互不覆盖。
    """
    from services import store_credentials
    store_credentials.save_portal_token(db, store_id, token)


def _jwt_payload(token: str) -> Dict[str, Any]:
    """只解析 JWT 的载荷，不验签——这里只是给用户看到期时间。"""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return {}


def token_expiry(token: str) -> Optional[int]:
    """令牌过期时间（epoch 秒）。取不到返回 None。"""
    exp = _jwt_payload(token).get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def token_state(db, store_id=None) -> Dict[str, Any]:
    """给前端看的令牌状态。不返回令牌本身。"""
    token = configured_token(db, store_id)
    if not token:
        return {"configured": False, "expires_at": None, "expired": False, "days_left": None}
    expires_at = token_expiry(token)
    now = int(time.time())
    expired = bool(expires_at and expires_at <= now)
    days_left = round((expires_at - now) / 86400, 1) if expires_at else None
    return {
        "configured": True,
        "expires_at": expires_at,
        "expired": expired,
        "days_left": days_left,
    }


def _scale_from_text(text: str, table: Dict[str, float]) -> Optional[float]:
    """从 "... mm" 这种尾部单位取换算比例；没写单位返回 None。"""
    lowered = (text or "").lower()
    best: Optional[float] = None
    best_len = 0
    for unit, scale in table.items():
        index = lowered.rfind(unit)
        if index == -1:
            continue
        # "mm" 里也含 "m"，取最长的匹配，别把 mm 当 m。
        if len(unit) > best_len:
            best, best_len = scale, len(unit)
    return best


def _numbers(text: str) -> List[float]:
    """取出文本里所有数字。逗号当小数点（"0,14 kg" 在欧洲写法里很常见）。"""
    cleaned = (text or "").replace(",", ".")
    return [float(match.group(0)) for match in _NUMBER_RE.finditer(cleaned)]


def parse_dimensions(text: Any) -> Optional[Dict[str, float]]:
    """把 "88.0 x 26.0 x 190.0 mm" 解析成毫米三元组。

    顺序**原样保留**上游的写法：卖家门户页面上显示的是哪个顺序，这里就存哪个
    顺序，用户核对时不会觉得对不上。体积与顺序无关，运费计算不受影响。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    values = _numbers(text)
    if len(values) < 3:
        return None
    scale = _scale_from_text(text, _UNIT_SCALE_MM)
    if scale is None:
        scale = 10.0  # 没写单位时按 cm —— 这个字段最常见的写法
    length, width, height = values[0], values[1], values[2]
    return {
        "length_mm": round(length * scale, 1),
        "width_mm": round(width * scale, 1),
        "height_mm": round(height * scale, 1),
    }


def parse_weight(text: Any) -> Optional[float]:
    """把 "0.14 kg" / "165 g" 解析成公斤。"""
    if not isinstance(text, str) or not text.strip():
        return None
    values = _numbers(text)
    if not values:
        return None
    scale = _scale_from_text(text, _UNIT_SCALE_KG)
    if scale is None:
        scale = 1.0  # 裸数字按 kg
    return round(values[0] * scale, 3)


def _attribute_map(variant: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    attrs = variant.get("attributes") or {}
    if not isinstance(attrs, dict):
        return {}
    return {k: v for k, v in attrs.items() if isinstance(v, dict)}


def extract_brand(payload: Dict[str, Any]) -> Optional[str]:
    """取品牌名——就是商品页标题下面那个蓝色品牌。

    它在 mpv 里不在顶层，藏在 `attributes.brand.value`（`{"name": "Brand",
    "value": "HUAOI", ...}`）。原来只读 `payload["brand"]`，所以永远是 None，
    选品池里 1804 条只有 22 条有品牌——那 22 条是走公开页拿到的。

    顶层也读一次做兜底：不同类目的返回结构不完全一样。
    """
    top = payload.get("brand")
    if isinstance(top, str) and top.strip():
        return top.strip()

    attrs = payload.get("attributes")
    if isinstance(attrs, dict):
        entry = attrs.get("brand")
        if isinstance(entry, dict):
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def extract_dimensions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从 mpv 详情里取长宽高和重量。

    多个变体时优先取有 offer 的那个；都没有 offer 就取第一个有完整长宽高的。
    返回 `{"length_mm", "width_mm", "height_mm", "weight_kg", "source"}`。
    """
    variants = payload.get("variants") or []
    if not isinstance(variants, list):
        return {}

    ordered = sorted(
        [v for v in variants if isinstance(v, dict)],
        key=lambda v: (0 if v.get("hasOffer") else 1),
    )

    dims: Dict[str, Any] = {}
    weight_kg: Optional[float] = None
    for variant in ordered:
        attrs = _attribute_map(variant)
        if not dims:
            for name, source in DIM_ATTRIBUTES:
                entry = attrs.get(name) or {}
                parsed = parse_dimensions(entry.get("value"))
                if parsed:
                    parsed["source"] = source
                    dims = parsed
                    break
        if weight_kg is None:
            for name, _source in WEIGHT_ATTRIBUTES:
                entry = attrs.get(name) or {}
                parsed = parse_weight(entry.get("value"))
                if parsed:
                    weight_kg = parsed
                    break
        if dims and weight_kg is not None:
            break

    out: Dict[str, Any] = dict(dims)
    if weight_kg is not None:
        out["weight_kg"] = weight_kg
    return out


def _image_url(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    return entry.get("zoomUrl") or entry.get("publicUrl") or None


def extract_images(payload: Dict[str, Any]) -> List[str]:
    """主图在前，其后是图库；去重保序。"""
    urls: List[str] = []
    seen = set()

    def add(url: Optional[str]) -> None:
        if not url or not isinstance(url, str) or url in seen:
            return
        seen.add(url)
        urls.append(url)

    add(_image_url(payload.get("coverImage")))
    for entry in payload.get("productlineImages") or []:
        add(_image_url(entry))
    for entry in payload.get("tsinImages") or []:
        add(_image_url(entry))
    return urls


def extract_category(payload: Dict[str, Any]) -> Optional[str]:
    """类目路径。

    mpv 给的是 `merchantLineages`，用 " → " 连接；而 `fee_category_matcher`
    是按 " > " 做子串匹配的（和公开接口、爬虫保持同一套分隔符），所以这里要
    转换。多个 lineage 时取层级最深的那个——它最具体。
    """
    lineages = payload.get("merchantLineages") or []
    if not isinstance(lineages, list):
        return None
    best: Optional[str] = None
    best_depth = -1
    for raw in lineages:
        if not isinstance(raw, str) or not raw.strip():
            continue
        parts = [p.strip() for p in raw.replace("→", ">").split(">") if p.strip()]
        if not parts:
            continue
        if len(parts) > best_depth:
            best_depth = len(parts)
            best = " > ".join(parts)
    return best


def extract_variants(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列出 mpv 里的变体及其 Barcode（mpv 的 `gtin` 就是创建 offer 用的 identifier）。

    创建 offer 只认变体 Barcode，不认 PLID；没有这一步，新品就只能停在
    「已选品未上架」。`hasOffer` 用来区分本店已在这个变体上有报价。
    """
    variants = payload.get("variants") or []
    if not isinstance(variants, list):
        return []
    rows: List[Dict[str, Any]] = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        gtin = item.get("gtin")
        best_price = item.get("bestPrice")
        price = None
        if isinstance(best_price, dict):
            for key in ("price", "sellingPrice", "amount"):
                value = best_price.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    price = float(value)
                    break
        elif isinstance(best_price, (int, float)) and not isinstance(best_price, bool):
            price = float(best_price)
        color = item.get("colorName")
        size = item.get("size")
        label = " / ".join(str(part) for part in (color, size) if part)
        rows.append({
            "barcode": str(gtin).strip() if gtin else None,
            "tsin": str(item.get("tsinId")) if item.get("tsinId") else None,
            "title": item.get("title"),
            "label": label or None,
            "has_offer": bool(item.get("hasOffer")),
            "competing_offers": item.get("competingOffers"),
            "best_price_zar": price,
        })
    return rows


def extract(plid: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """把 mpv 详情转成和 `takealot_public_api.fetch_product` 同名的结构。"""
    variants = payload.get("variants") or []
    first = variants[0] if isinstance(variants, list) and variants else {}
    if not isinstance(first, dict):
        first = {}

    images = extract_images(payload)
    best_price = first.get("bestPrice")
    price = None
    if isinstance(best_price, dict):
        for key in ("price", "sellingPrice", "amount"):
            value = best_price.get(key)
            if isinstance(value, (int, float)):
                price = float(value)
                break
    elif isinstance(best_price, (int, float)):
        price = float(best_price)

    return {
        "plid": str(plid),
        "success": True,
        "product_name": payload.get("title") or first.get("title"),
        # 描述和「包装里有什么」是判普货/带电的关键证据：标题只写
        # "LED Desk Lamp"，描述里才有 "built-in battery" 或 "batteries not included"。
        "description": payload.get("description") or "",
        "whats_in_the_box": payload.get("whatsInTheBox") or "",
        "product_image_url": images[0] if images else None,
        "image_urls": images[:6],
        "takealot_category_path": extract_category(payload),
        "actual_sale_price_zar": price,
        "tsin": str(first.get("tsinId")) if first.get("tsinId") else None,
        "brand": extract_brand(payload),
        "dimensions": extract_dimensions(payload),
        "catalogue_variants": extract_variants(payload),
        "catalogue_variant_count": len(variants) if isinstance(variants, list) else 0,
        "warnings": [],
        "source": "seller_portal",
    }


def _sleep(seconds: float) -> None:
    """可替换的睡眠。测试里换成记录器，免得真睡 60 秒。"""
    time.sleep(seconds)


class _Quota:
    """按服务端配额放行请求：滚动窗口 + 响应头双保险。

    为什么要这么麻烦
    ----------------
    门户接口是 200 次/分钟的硬配额。不管它直接猛打，1804 个商品会在第 201 个
    开始整片 429——上一版就是这样，结果一个重尺都没补上。光「吃到 429 再重试」
    也不行：重试同样挤在窗口里，只会把配额继续打空。

    所以这里主动**卡住节奏**：自己记着最近 60 秒发了多少，满了就等到窗口滑过去；
    同时读服务端的 `x-ratelimit-remaining`，如果它比我们算的更少（浏览器里开着
    门户页时就会这样），就按服务端的来。

    等不到就返回 False，调用方把这一条标记成「下一轮再抓」——绝不记成失败。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent: "deque[float]" = deque()
        self._pause_until = 0.0
        self._server_remaining: Optional[int] = None

    def _wait_for_slot(self, now: float) -> float:
        """返回还要等多久（秒）；0 表示可以发了。"""
        if self._pause_until > now:
            return self._pause_until - now
        while self._sent and now - self._sent[0] > RATE_LIMIT_WINDOW_SECONDS:
            self._sent.popleft()
        ceiling = RATE_LIMIT_LIMIT - RATE_LIMIT_RESERVE
        if len(self._sent) >= ceiling:
            # 最早那一条滑出窗口，就空出一个位置
            return max(0.05, RATE_LIMIT_WINDOW_SECONDS - (now - self._sent[0]) + 0.2)
        if self._server_remaining is not None and self._server_remaining <= 2:
            # 服务端说快没了（可能是浏览器那边用掉的），主动让开一个窗口
            return RATE_LIMIT_WINDOW_SECONDS + 1.0
        return 0.0

    def wait_seconds(self) -> float:
        """还要等多久才能发下一个请求。给测试和诊断用。"""
        with self._lock:
            return self._wait_for_slot(time.monotonic())

    def acquire(self, deadline: Optional[float]) -> bool:
        """占一个请求位。预算内等不到就返回 False。"""
        for _ in range(1000):  # 兜底：睡眠不推进时钟时也不会死循环
            with self._lock:
                now = time.monotonic()
                wait = self._wait_for_slot(now)
                if wait <= 0:
                    self._sent.append(now)
                    return True
            if deadline is not None and time.monotonic() + wait > deadline:
                return False
            # 分片睡，好在预算耗尽时及时退出，而不是一口气睡满 60 秒
            _sleep(min(max(wait, 0.05), 2.0))
        return False

    def observe(self, headers: Any) -> None:
        """记住服务端报的剩余量。"""
        value = None
        try:
            value = headers.get("x-ratelimit-remaining")
        except Exception:
            return
        if value is None:
            return
        try:
            remaining = int(value)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._server_remaining = remaining

    def back_off(self) -> None:
        """吃到 429：让开一整个窗口。"""
        with self._lock:
            self._pause_until = max(self._pause_until,
                                    time.monotonic() + RATE_LIMIT_WINDOW_SECONDS + 1.0)
            self._server_remaining = 0

    def reset(self) -> None:
        """测试用。"""
        with self._lock:
            self._sent.clear()
            self._pause_until = 0.0
            self._server_remaining = None


QUOTA = _Quota()


def _headers(token: str) -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://sellers.takealot.com",
        "Referer": "https://sellers.takealot.com/",
        "Authorization": f"Bearer {token}",
    }


def _deferred(plid: Any, reason: str) -> Dict[str, Any]:
    return {
        "plid": str(plid),
        "success": False,
        "budget_deferred": True,
        "portal_rate_limited": True,
        "warnings": [reason],
        "source": "seller_portal",
    }


def fetch(plid: Any, token: str, timeout: float = REQUEST_TIMEOUT_SECONDS,
          deadline: Optional[float] = None) -> Dict[str, Any]:
    """读一个 PLID 的门户详情。失败不抛异常，返回带 `http_status` 的记录。

    429 会等一个窗口后重试一次；预算内等不到就返回 `budget_deferred`，
    由调用方留到下一轮——这不是失败。
    """
    from services.takealot_public_api import parse_plid

    resolved = parse_plid(plid)
    base = {"plid": str(resolved) if resolved is not None else str(plid),
            "success": False, "warnings": [], "source": "seller_portal"}
    if resolved is None:
        base["warnings"] = ["PLID 无法识别"]
        return base
    if not token:
        base["warnings"] = ["没有配置卖家门户令牌"]
        base["portal_not_configured"] = True
        return base

    url = f"{API_BASE}/1/catalogue/mpv/PLID{resolved}"
    for attempt in range(MAX_429_RETRIES + 1):
        if not QUOTA.acquire(deadline):
            return _deferred(resolved, "门户配额这一轮用完了，留到下一轮")
        try:
            response = httpx.get(url, headers=_headers(token), timeout=timeout,
                                 follow_redirects=False)
        except Exception as error:
            base["warnings"] = [f"门户接口读取失败：{type(error).__name__}"]
            return base

        QUOTA.observe(response.headers)
        status = response.status_code
        base["http_status"] = status

        if status in RATE_LIMITED_STATUS:
            if attempt < MAX_429_RETRIES:
                QUOTA.back_off()
                continue
            return _deferred(resolved, "门户接口限流，留到下一轮")

        if status == 200:
            try:
                payload = response.json()
            except Exception:
                base["warnings"] = ["门户接口返回的不是 JSON"]
                return base
            record = extract(resolved, payload)
            record["http_status"] = 200
            return record

        if status in NOT_FOUND_STATUS:
            base["warnings"] = ["Takealot 目录里没有这个商品"]
            base["not_found"] = True
            return base

        if status in ACCESS_STOP_STATUS:
            base["portal_access_blocked"] = True
            base["warnings"] = ["卖家门户令牌已失效，请重新取一次"]
            return base

        base["warnings"] = [f"门户接口返回 {status}"]
        return base

    return _deferred(resolved, "门户接口限流，留到下一轮")


def fetch_many(plids: Iterable[Any], token: str, workers: int = BATCH_WORKERS,
               budget_seconds: float = BATCH_BUDGET_SECONDS) -> List[Dict[str, Any]]:
    """并发读多个 PLID，最多等 `budget_seconds` 秒。

    配额是 200 次/分钟，所以这里能抓多少完全由 `budget_seconds` 决定：40 秒约
    130 个。剩下的标成 `budget_deferred` 交给下一轮，前端看到 `remaining` 会
    自动接着调——1804 个商品大约 14 轮跑完，这是接口的硬限制。

    只有令牌失效（401/403）才整批收手；429 是配额问题，等窗口就行。
    """
    unique: List[str] = []
    seen = set()
    for value in plids:
        text = str(value).strip() if value is not None else ""
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    if not unique:
        return []

    deadline = time.monotonic() + max(1.0, budget_seconds)
    denied = threading.Event()

    def read_one(text: str) -> Dict[str, Any]:
        if time.monotonic() >= deadline:
            return _deferred(text, "门户资料批读已到时，保留待处理")
        if denied.is_set():
            return {"plid": text, "success": False, "portal_access_blocked": True,
                    "warnings": ["卖家门户令牌已失效，本轮剩余未读"],
                    "source": "seller_portal"}
        record = fetch(text, token, deadline=deadline)
        if record.get("portal_access_blocked"):
            denied.set()
        return record

    with ThreadPoolExecutor(max_workers=max(1, min(workers, BATCH_WORKERS))) as pool:
        return list(pool.map(read_one, unique))


def probe(db, plid: Any) -> Dict[str, Any]:
    """诊断用：用当前配置的令牌读一次，把关键结论摊开给用户看。"""
    token = configured_token(db)
    report: Dict[str, Any] = {
        "configured": bool(token),
        "ok": False,
        "plid": str(plid),
        "reason": "",
        "product_name": None,
        "product_image_url": None,
        "image_count": 0,
        "takealot_category_path": None,
        "dimensions": {},
        "token": token_state(db),
    }
    if not token:
        report["reason"] = "没有配置卖家门户令牌"
        return report
    if report["token"].get("expired"):
        report["reason"] = "卖家门户令牌已过期，请重新取一次"
        return report

    record = fetch(plid, token)
    report["http_status"] = record.get("http_status")
    report["product_name"] = record.get("product_name")
    report["product_image_url"] = record.get("product_image_url")
    report["image_count"] = len(record.get("image_urls") or [])
    report["takealot_category_path"] = record.get("takealot_category_path")
    report["dimensions"] = record.get("dimensions") or {}
    if record.get("success"):
        report["ok"] = True
        report["reason"] = "门户接口正常，主图和重尺都拿到了"
    else:
        report["reason"] = "；".join(record.get("warnings") or []) or "门户接口没有返回可用数据"
    return report
