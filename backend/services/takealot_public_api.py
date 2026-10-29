"""
Takealot 公开接口读取商品资料（供批量选品使用）
================================================

为什么需要这个模块
------------------
`takealot_scraper.py` 走 Playwright 逐页打开商品页，一次一个、每页等 2 秒，
还会撞 Cloudflare 的 "Just a moment" 挑战。补 1800 个商品的图要跑很久，
而且它取的是 `s-pdpxl`（459px），做 1688 识图偏小。

公开接口 `api.takealot.com/rest/v-1-9-0/product-details/PLID{plid}` 一次就返回
标题、图库、类目树、价格、TSIN，不需要登录态、不触发挑战。图库里把尺寸令牌
换成 `zoom` 是 1041×1033，足够直接拖进 1688 的识图框。

并发与节流（2026-10 补）
------------------------
原先写的是「8 线程实测 88 秒读完 1814 个商品，零失败」——那是在家庭网络上测的。
部署到 Railway 后同一批请求整片 429：机房 IP 在 Cloudflare 眼里的风险分更高，
配额更小。现在改成「并发上限 4 + 请求之间强制排队（`_Pacer`）+ 429 时整体放慢」，
吞吐约 5 个/秒，速度让位于成功率。不要为了快把 `workers` 加回去。

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
import threading
import time
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

# 真实浏览器打这个接口时带的头。只给 UA 在家庭网络够用，机房 IP 上不够。
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-ZA,en;q=0.9",
    "Referer": "https://www.takealot.com/",
    "Origin": "https://www.takealot.com",
    "sec-ch-ua": '"Chromium";v="126", "Not:A-Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

# 这几类状态码是「被当成机器人 / 被限流 / 上游抖动」的信号，等一下再试通常就过。
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
# 普通抖动：等一小会儿就过。
RETRY_DELAYS = (0.6, 1.8)
# 限流：0.6 秒完全不够。Cloudflare 的限流窗口通常是十几秒，短退避只会
# 把请求继续撞在同一个窗口上，看起来像「重试了还是 429」。
RATE_LIMIT_DELAYS = (3.0, 10.0, 20.0)
MAX_RETRIES = 3


class _Pacer:
    """进程内的请求节流：任意两次请求之间至少隔 interval 秒。

    为什么要它：`fetch_many` 并发打过去，从机房 IP 会被 Cloudflare 判成机器人
    直接 429。光靠「失败了重试」治不好——重试的请求同样挤在窗口里。
    真正有效的是**主动放慢**：把并发压成「串行排队 + 最小间隔」。

    interval 会随 429 翻倍（只增不减），所以：
    - 网络宽松时按初始节奏跑，不白等；
    - 一旦被限流，这次运行剩下的请求自动变慢，而不是继续挨 429。
    """

    def __init__(self, interval: float = 0.2, max_interval: float = 2.0):
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._initial = interval
        self.interval = interval
        self.max_interval = max_interval

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            time.sleep(delay)

    def penalize(self) -> None:
        """吃到 429 就把整体节奏放慢一档，本次运行内不再调回去。"""
        with self._lock:
            self.interval = min(self.interval * 2, self.max_interval)

    def reset(self) -> None:
        """测试用：把学到的节奏清掉。"""
        with self._lock:
            self.interval = self._initial
            self._next_at = 0.0


PACER = _Pacer()


def fetch_budget(seconds: float = 45.0) -> int:
    """按当前节奏，`seconds` 秒内大概能抓多少个。

    为什么需要它：`enrich` 是「先整批抓、最后一次 commit」，一个请求要跑多久
    完全由抓取速度决定。正常节奏下 400 个约 80 秒，没问题；但被限流后 PACER
    会把间隔翻倍到 2 秒，400 个就变成 13 分钟——代理层早就把连接掐了，
    抓到的数据随未提交的事务一起丢。所以调用方按这个数切一刀，剩下的交给
    下一轮请求，进度才不会白费。
    """
    return max(1, int(seconds / max(PACER.interval, 0.05)))

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


def _header_value(headers: Any, name: str) -> Optional[str]:
    """不区分大小写地取一个响应头。

    为什么不能直接 `.get()`：HTTP/2 里响应头是小写的（`retry-after`），而
    curl_cffi 返回的是它自己那套 Headers；只按 `Retry-After` 查会漏掉，
    于是明明服务端说了「等 12 秒」，我们还是按 3 秒硬撞。
    """
    if headers is None:
        return None
    try:
        pairs = list(headers.items())
    except Exception:
        return None
    for key, value in pairs:
        if str(key).lower() == name.lower():
            return value
    return None


def _retry_delay(error: Optional[BaseException], attempt: int) -> float:
    """退避时长。优先听服务端给的 Retry-After，没有就按 429 / 其它两套节奏。

    429 用 3s→10s→20s：0.6 秒的短退避会继续撞在同一个限流窗口上，
    表现就是「重试了还是 429」。普通抖动用 0.6s→1.8s 就够。
    """
    retry_after = _header_value(getattr(error, "headers", None), "Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 30.0)
        except (TypeError, ValueError):
            pass
    if getattr(error, "code", None) == 429:
        return RATE_LIMIT_DELAYS[min(attempt, len(RATE_LIMIT_DELAYS) - 1)]
    return RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]


def _request_json(url: str, timeout: float) -> Dict[str, Any]:
    """发一次请求并解析 JSON。

    只用标准库。**不要换成 curl_cffi 的浏览器指纹**：本机实测 `impersonate`
    传 `chrome124` / `chrome` 会被 Cloudflare 判 403，只有 `chrome110` 能过，
    而 urllib 带上面这套头反而稳定 200。也就是说这条链路的 429 不是 TLS 指纹
    问题，而是**出口 IP 的配额**问题——指纹换法只会把它变成更难查的 403。
    真要换出口，`urllib` 本身认 `HTTPS_PROXY` 环境变量，不用改代码。
    """
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _http_json(url: str, timeout: float = 25.0) -> Dict[str, Any]:
    """读一次公开接口：先按节奏排队，被限流就放慢并退避重试。

    机房 IP 在 Cloudflare 眼里的风险分比家庭 IP 高。实测 8 线程并发直接整片
    429——接口是通的，是被限流。所以这里不只做「失败重试」，而是：
    1. 每次请求先过 PACER 排队（并发不再是 8 个同时打）；
    2. 吃到 429 就把全局节奏翻倍（本次运行内不再调回去）；
    3. 429 的退避是 3s→10s→20s，而不是普通抖动那套 0.6s→1.8s。
    仍然失败就把最后一个异常抛出去，由 `fetch_product` 记成 warnings。
    """
    for attempt in range(MAX_RETRIES + 1):
        PACER.wait()
        try:
            return _request_json(url, timeout)
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUS:
                raise
            if error.code == 429:
                PACER.penalize()
            if attempt == MAX_RETRIES:
                raise
            time.sleep(_retry_delay(error, attempt))
        except Exception as error:  # 超时、DNS、连接重置都重试
            if attempt == MAX_RETRIES:
                raise
            time.sleep(_retry_delay(error, attempt))
    raise RuntimeError("公开接口读取失败")


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
        if error.code == 429:
            result["warnings"].append(
                "公开接口限流（429），已按 3s/10s/20s 退避重试仍失败——"
                "隔几分钟再点一次「重抓全部资料」即可，已抓到的不会重复抓"
            )
        else:
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


def fetch_many(plids: Iterable[Any], workers: int = 4) -> List[Dict[str, Any]]:
    """并发读取多个 PLID。

    并发数默认 4，而不是原来的 8。8 线程在家庭网络上没事，从机房 IP 打过去
    会整片 429——「本地测 40/40、线上全红」就是这么来的。真正的节流在
    `_Pacer` 里（请求之间强制排队），这里只是上限；两个一起才稳。

    注意：workers 现在是**上限**不是目标。想调快先看 PACER.interval，
    盲加线程只会更快撞限流。
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
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        return list(pool.map(fetch_product, unique))
