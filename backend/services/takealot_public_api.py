"""
Takealot 公开接口读取商品资料（供批量选品使用）
================================================

为什么需要这个模块
------------------
`takealot_scraper.py` 走 Playwright 逐页打开商品页，一次一个、每页等 2 秒，
还会撞 Cloudflare 的 "Just a moment" 挑战。补 1800 个商品的图要跑很久，
而且它取的是 `s-pdpxl`（459px），做 1688 识图偏小。

公开接口 `api.takealot.com/rest/v-1-9-0/product-details/PLID{plid}` 一次就返回
标题、图库、类目树、价格、TSIN，不需要登录态、不触发挑战。实测 8 线程
88 秒读完 1814 个商品，零失败。图库里把尺寸令牌换成 `zoom` 是 1041×1033，
足够直接拖进 1688 的识图框。

不做什么
--------
- 不碰登录态、不带 Cookie、不用 Playwright 绕过站点验证。
- 不写数据库。本模块只负责「给 PLID，返回资料」，落库由调用方决定。
- 不覆盖卖家后台才有的字段（Fee 类目、库存、Offer 列表）。Fee 类目仍然
  由 `fee_category_matcher` 按前台类目路径推断，因为公开接口里没有 Fee 树。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

API_BASE = "https://api.takealot.com/rest/v-1-9-0"

# 不带浏览器 UA 会被 Cloudflare 直接 403（实测 curl 默认 UA 即被拦）
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 图库尺寸令牌。s-thumbnail 只有 40×40，s-pdpxl 459×459，s-zoom 约 1041×1033。
# 识图要用 zoom；如果下游只要小图，改这个常量即可。
IMAGE_SIZE_TOKEN = "zoom"

_PLID_RE = re.compile(r"PLID(\d+)", re.I)
_PLID_QUERY_RE = re.compile(r"[?&]plid=(\d+)", re.I)
_PATH_PLID_RE = re.compile(r"/p/(\d+)")


def parse_plid(value: Any) -> Optional[int]:
    """从链接、PLID 字符串或整数里取出 PLID。

    Takealot 链接的写法不止一种，实测遇到的三种都要兼容：
    `/PLID123`（标准）、`?plid=123`（带查询参数的变体）、`/p/123`（老写法）。
    少一种就会在批量导入时静默丢商品，所以这里逐个兜。
    """
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    for pattern in (_PLID_RE, _PLID_QUERY_RE, _PATH_PLID_RE):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _http_json(url: str, timeout: float = 25.0) -> Dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _first_price(payload: Dict[str, Any]) -> Optional[float]:
    """buybox 里的价格字段在不同商品上叫法不一，按常见顺序取第一个数值。"""
    for item in (payload.get("buybox") or {}).get("items") or []:
        for key in ("price", "selling_price", "current_price"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _live_tsin(payload: Dict[str, Any]) -> Optional[str]:
    """在线 TSIN。销售报表里只有 TSIN 时靠它反查 PLID；竞品表反过来用它补 TSIN。"""
    tsin = (payload.get("buybox") or {}).get("tsin")
    if tsin:
        return str(tsin)
    data_layer = str((payload.get("data_layer") or {}).get("tsin") or "")
    cleaned = data_layer.replace("TSIN", "").strip()
    return cleaned or None


def _category_path(payload: Dict[str, Any]) -> Optional[str]:
    """前台类目路径，格式与 `takealot_scraper` 保持一致（" > " 连接）。

    `fee_category_matcher` 是按这个字符串做子串匹配的，格式一变规则就全失效，
    所以这里必须沿用同一个分隔符。
    """
    items = (payload.get("breadcrumbs") or {}).get("items") or []
    names = [str(item.get("name")).strip() for item in items if item.get("name")]
    return " > ".join(names) if names else None


# 属性名里带这些词的是「承重上限 / 推荐载荷」，不是商品自重。
# 实测 `maximum weight recommendation = 0.9 Kg` 出现在置物架上——把它当自重
# 会让运费按 0.9kg 算，而实际商品可能只有 0.2kg，利润直接算错。
_NOT_PRODUCT_WEIGHT = ("maximum", "recommend", "capacity", "limit", "load", "max ")


def _dimensions_and_weight(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """从商品属性里取重尺。

    只有两个属性可用（其余是容量、屏幕尺寸这类单维信息）：
      - `Assembled Dimensions` 形如 "22 x 7.2 x 17 cm"
      - `Weight` / `Trail Weight` 形如 "380 g" / "1.2 kg"

    覆盖率不高（实测约 10%），拿不到时返回空字典，由调用方标记为待补，
    不要用类目默认值冒充——重尺错了利润就全错。
    """
    out: Dict[str, Optional[float]] = {}
    items = (payload.get("product_information") or {}).get("items") or []
    for item in items:
        name = str(item.get("display_name") or "").lower()
        text = str(item.get("displayable_text") or "")
        if not text:
            continue
        if "weight" in name and "weight_kg" not in out:
            if any(bad in name for bad in _NOT_PRODUCT_WEIGHT):
                continue
            number = _first_number(text)
            if number is None:
                continue
            lowered = text.lower()
            if "kg" in lowered:
                out["weight_kg"] = round(number, 3)
            elif "lb" in lowered:
                out["weight_kg"] = round(number * 0.4536, 3)
            elif "g" in lowered:
                out["weight_kg"] = round(number / 1000.0, 3)
        if ("dimension" in name or "assembled" in name) and "length_mm" not in out:
            numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
            if len(numbers) < 3:
                continue
            length, width, height = (float(x.replace(",", ".")) for x in numbers[:3])
            lowered = text.lower()
            if "mm" in lowered:
                scale = 1.0
            elif "cm" in lowered:
                scale = 10.0
            elif "m" in lowered:
                scale = 1000.0
            else:
                scale = 10.0  # 没写单位时按 cm 处理，是这类属性最常见的写法
            out["length_mm"] = round(length * scale, 1)
            out["width_mm"] = round(width * scale, 1)
            out["height_mm"] = round(height * scale, 1)
    return out


def _first_number(text: str) -> Optional[float]:
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def fetch_product(plid: Any, timeout: float = 25.0) -> Dict[str, Any]:
    """读取单个 PLID 的商品资料。

    返回结构刻意与 `takealot_scraper.scrape_product` 的常用字段同名，
    这样批量流程和现有的单产品「抓取产品信息」可以共用同一段下游逻辑。
    """
    resolved = parse_plid(plid)
    result: Dict[str, Any] = {
        "plid": resolved,
        "success": False,
        "product_name": None,
        "product_image_url": None,
        "actual_sale_price_zar": None,
        "tsin": None,
        "takealot_category_path": None,
        "brand": None,
        "rating_value": None,
        "review_count": None,
        "image_urls": [],
        "dimensions": {},
        "warnings": [],
    }
    if resolved is None:
        result["warnings"].append("PLID 无法识别")
        return result

    try:
        payload = _http_json(f"{API_BASE}/product-details/PLID{resolved}", timeout=timeout)
    except urllib.error.HTTPError as error:
        result["warnings"].append(f"公开接口返回 {error.code}")
        return result
    except Exception as error:  # 网络超时、DNS、JSON 解析失败都归到这里
        result["warnings"].append(f"公开接口读取失败：{type(error).__name__}")
        return result

    core = payload.get("core") or {}
    result["product_name"] = core.get("title")
    result["brand"] = core.get("brand")
    result["rating_value"] = core.get("star_rating")
    result["review_count"] = core.get("reviews")
    result["actual_sale_price_zar"] = _first_price(payload)
    result["tsin"] = _live_tsin(payload)
    result["takealot_category_path"] = _category_path(payload)

    gallery = (payload.get("gallery") or {}).get("images") or []
    images = [
        url.replace("s-{size}", f"s-{IMAGE_SIZE_TOKEN}")
        for url in gallery
        if isinstance(url, str)
    ]
    result["image_urls"] = images[:6]
    result["product_image_url"] = images[0] if images else None

    result["dimensions"] = _dimensions_and_weight(payload)

    if not result["product_image_url"]:
        result["warnings"].append("该商品没有图库图片")
    result["success"] = bool(result["product_name"])
    return result


def fetch_many(plids: Iterable[Any], workers: int = 8) -> List[Dict[str, Any]]:
    """并发读取多个 PLID。

    并发数默认 8：实测 8 线程时接口仍稳定返回，且不会触发 Cloudflare 的
    速率限制。调到 16 以上反而出现零星失败，不值得。
    """
    unique: List[int] = []
    seen = set()
    for value in plids:
        resolved = parse_plid(value)
        if resolved is not None and resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    if not unique:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as pool:
        return list(pool.map(fetch_product, unique))
