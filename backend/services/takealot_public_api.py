"""
Takealot 公开接口读取商品资料（供批量选品使用）
================================================

为什么需要这个模块
------------------
`takealot_scraper.py` 走 Playwright 逐页打开商品页，一次一个、每页等 2 秒，
还会撞 Cloudflare 的 "Just a moment" 挑战。补 1800 个商品的图要跑很久，
而且它取的是 `s-pdpxl`（459px），做 1688 识图偏小。

公开接口可能返回标题、图库、类目树、价格和 TSIN；可访问性取决于上游。
不模拟浏览器绕过拒绝或验证；收到访问拒绝或限流后停止待发读取。

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
from threading import Event
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Dict, Iterable, List, Optional

API_BASE = "https://api.takealot.com/rest/v-1-9-0"

REQUEST_HEADERS = {"User-Agent": "takealot-selector/1.0", "Accept": "application/json"}
ACCESS_STOP_STATUS = {401, 403, 429}
RETRY_STATUS = {500, 502, 503, 504}
RETRY_DELAYS = (0.6, 1.8)

MAX_RETRIES = 3
BATCH_BUDGET_SECONDS = 45.0
_BATCH_LEASE = threading.Lock()  # 到时返回后，未退出的读线程仍持有租约。
_LATE_ACCESS_DENIAL = Event()  # 未随上一批返回的迟到拒绝，下一批必须先报告停止。


class PublicBudgetExpired(Exception):
    """本批等待预算耗尽，不代表上游拒绝或失败。"""


def _time_left(deadline: Optional[float]) -> float:
    if deadline is None:
        return float("inf")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicBudgetExpired()
    return remaining


def _wait_with_budget(delay: float, stop_event: Optional[Event], deadline: Optional[float]) -> None:
    delay = min(delay, _time_left(deadline))
    if stop_event is not None:
        if stop_event.wait(delay):
            # 到时优先保留预算语义；不能冒充上游拒绝。
            _time_left(deadline)
            raise PublicReadStopped()
    else:
        time.sleep(delay)
    _time_left(deadline)



class _Pacer:
    """进程内的请求节流：任意两次请求之间至少隔 interval 秒。

    为什么要它：`fetch_many` 并发打过去，从机房 IP 会被 Cloudflare 判成机器人
    直接 429。光靠「失败了重试」治不好——重试的请求同样挤在窗口里。
    真正有效的是**主动放慢**：把并发压成「串行排队 + 最小间隔」。

    interval 会随 429 翻倍（只增不减），所以：
    - 网络宽松时按初始节奏跑，不白等；
    - 一旦被限流，停止本批；保留更慢节奏供接入条件改变后的人工操作使用。
    """

    def __init__(self, interval: float = 0.2, max_interval: float = 2.0):
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._initial = interval
        self.interval = interval
        self.max_interval = max_interval

    def wait(self, stop_event: Optional[Event] = None, deadline: Optional[float] = None) -> None:
        _time_left(deadline)
        if stop_event is not None and stop_event.is_set():
            raise PublicReadStopped()
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            _wait_with_budget(delay, stop_event, deadline)
        _time_left(deadline)
        if stop_event is not None and stop_event.is_set():
            raise PublicReadStopped()

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
    完全由抓取速度决定。这是按间隔估算的数量，并非强制总时限。被限流后 PACER
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


class PublicReadStopped(RuntimeError):
    """本批已被另一请求的拒绝/限流停止，本任务不再发送请求。"""


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
    """不区分大小写读取响应头，不输出其余响应内容。"""
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
    """暂时性故障的退避；按大小写不敏感的 Retry-After 等待，最多30秒。"""
    retry_after = _header_value(getattr(error, "headers", None), "Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 30.0)
        except (TypeError, ValueError):
            pass
    return RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]


def _request_json(url: str, timeout: float) -> Dict[str, Any]:
    """真实应用标识，只读JSON；不模拟浏览器或切换指纹/出口。"""
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _http_json(url: str, timeout: float = 25.0, stop_event: Optional[Event] = None, deadline: Optional[float] = None) -> Dict[str, Any]:
    """主动节流；仅暂时性故障有限重试，拒绝/限流立即停止。"""
    last_error: Optional[BaseException] = None
    for attempt in range(MAX_RETRIES + 1):
        _time_left(deadline)
        if stop_event is not None and stop_event.is_set():
            raise PublicReadStopped()
        if attempt:
            _wait_with_budget(_retry_delay(last_error, attempt - 1), stop_event, deadline)
        if deadline is None:
            PACER.wait(stop_event)
        else:
            PACER.wait(stop_event, deadline=deadline)
        remaining = _time_left(deadline)
        if stop_event is not None and stop_event.is_set():
            raise PublicReadStopped()
        try:
            return _request_json(url, min(timeout, remaining))
        except urllib.error.HTTPError as error:
            if error.code in ACCESS_STOP_STATUS and stop_event is not None:
                stop_event.set()
            if error.code == 429:
                PACER.penalize()
            error.close()
            if error.code not in RETRY_STATUS or attempt == MAX_RETRIES:
                raise
            last_error = error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == MAX_RETRIES:
                raise
            last_error = error
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


def fetch_product(plid: Any, timeout: float = 25.0, stop_event: Optional[Event] = None, deadline: Optional[float] = None) -> Dict[str, Any]:
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
        payload = _http_json(f"{API_BASE}/product-details/PLID{resolved}", timeout=timeout, stop_event=stop_event, deadline=deadline)
    except PublicBudgetExpired:
        result["budget_deferred"] = True
        result["warnings"].append("公开资料批读已到时，保留待处理")
        return result
    except PublicReadStopped:
        result["public_access_blocked"] = True
        result["warnings"].append("公开接口访问受限，已停止本批后续读取")
        return result
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        reason = {401: "认证未通过", 403: "访问被拒绝", 429: "请求受限流"}.get(error.code)
        result["warnings"].append(
            f"公开接口{reason}（HTTP {error.code}），已停止读取"
            if reason else f"公开接口返回 {error.code}"
        )
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

    首次访问拒绝或限流后，待发任务只返回明确失败；已发请求允许结束。
    本公开阶段最多等待45秒；返回已完成结果，其余保持待处理。线程不接触DB，
    已发读取只能等待socket退出；返回后不得重试或发送排队请求。
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
    stopped = Event()
    deadline = time.monotonic() + BATCH_BUDGET_SECONDS

    def deferred(resolved: int) -> Dict[str, Any]:
        return {"plid": resolved, "success": False, "budget_deferred": True,
                "warnings": ["公开资料批读已到时，保留待处理"]}

    def read_one(resolved: int) -> Dict[str, Any]:
        if time.monotonic() >= deadline:
            return deferred(resolved)
        if stopped.is_set():
            return {"plid": resolved, "success": False, "public_access_blocked": True,
                    "warnings": ["公开接口访问受限，已停止本批后续读取"]}
        record = fetch_product(resolved, stop_event=stopped, deadline=deadline)
        if record.get("http_status") in ACCESS_STOP_STATUS:
            stopped.set()
        return record

    if not _BATCH_LEASE.acquire(blocking=False):
        return [deferred(resolved) for resolved in unique]
    if _LATE_ACCESS_DENIAL.is_set():
        _LATE_ACCESS_DENIAL.clear()
        _BATCH_LEASE.release()
        return [{"plid": resolved, "success": False, "public_access_blocked": True,
                 "warnings": ["上一批公开读取收到迟到的访问拒绝，已停止后续批次"]}
                for resolved in unique]
    futures = {}
    remaining_count = [0]
    coordinator_finished = [False]
    completion_lock = threading.Lock()

    def finished(future) -> None:
        if not future.cancelled() and future.exception() is None:
            if future.result().get("http_status") in ACCESS_STOP_STATUS:
                _LATE_ACCESS_DENIAL.set()
        with completion_lock:
            remaining_count[0] -= 1
            if remaining_count[0] == 0 and coordinator_finished[0]:
                _BATCH_LEASE.release()

    def retain_lease_until_finished() -> None:
        remaining_count[0] = len(futures)
        for future in futures:
            future.add_done_callback(finished)

    def finish_coordinator() -> None:
        with completion_lock:
            coordinator_finished[0] = True
            if remaining_count[0] == 0:
                _BATCH_LEASE.release()

    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=max(1, min(workers, 4)))
        for resolved in unique:
            futures[pool.submit(read_one, resolved)] = resolved
    except BaseException:
        stopped.set()
        retain_lease_until_finished()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        finish_coordinator()
        raise
    retain_lease_until_finished()
    try:
        done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        records = {futures[future]: future.result() for future in done}
        if pending:
            stopped.set()  # 唤醒节流/退避；不能等正在读取的线程退出才返回。
            for future in pending:
                future.cancel()
        if any(record.get("http_status") in ACCESS_STOP_STATUS for record in records.values()):
            _LATE_ACCESS_DENIAL.clear()  # 本轮已经把拒绝证据交给调用方。
        return [records.get(resolved) or deferred(resolved) for resolved in unique]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        finish_coordinator()
