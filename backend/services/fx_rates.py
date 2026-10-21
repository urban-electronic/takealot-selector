"""兰特（ZAR）汇率：把参考价折算成 Takealot 用的货币，好直接比价。

为什么需要这一步：参考价是三个市场各自的本币（S$、¥），币种不同就不能和 Takealot
的售价放在一起看。用户在 Takealot 卖货、定价用兰特，所以列表里要有一个统一口径。

三条设计取舍：

1. **数据源用 open.er-api.com**（exchangerate-api 的免费端点）。不用要密钥的接口——
   这个功能是展示用的，密钥轮换一次就会静默失效，最后变成「汇率永远是旧的」。
2. **不另起定时线程**。汇率只有打开页面时才需要，「没人看就不刷新」比「每天定点抓一次」
   更省事，也不会因为容器重启丢计时。缓存写在 `SystemSettings`，超过 TTL 后
   下一次读取时才去拉。
3. **拉不到就降级，不给空值**。先退回上一次缓存（标 `stale`），再退回内置兜底值
   （标 `fallback`）。展示层永远能算出兰特；用户看到的是「R1,282」而不是「—」，
   同时 `stale`/`fallback` 会一路传到界面上的汇率说明里。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

# 折算目标货币。基准用 ZAR（Takealot 的结算货币）。
BASE = 'ZAR'
# 需要折成兰特的来源货币，和 market_prices 里的 cur 对应。
CURRENCIES = ('SGD', 'CNY')

CACHE_KEY = 'fx:zar'
TTL_HOURS = 24
SOURCE = 'open.er-api.com'
URL = f'https://open.er-api.com/v6/latest/{BASE}'

# 兜底值（2026-10-21 取自 open.er-api.com）。只在从没成功拉过、又刚好拉不到时用，
# 目的是让界面不至于整列空掉；`fallback` 标记会让界面把汇率来源写清楚。
FALLBACK_RATES = {'SGD': 12.8345, 'CNY': 2.4453}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _date_of(unix_ts) -> str:
    """上游给的是「这次汇率从什么时候开始生效」的 unix 时间戳，换算成日期。"""
    try:
        stamp = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return _now().strftime('%Y-%m-%d')
    return stamp.strftime('%Y-%m-%d')


def parse_rates(payload) -> dict:
    """把 `/v6/latest/ZAR` 的返回解析成 `{货币: 1 单位该货币值多少兰特}`。

    上游返回的是**反向**的（`rates['SGD']` = 1 兰特能换多少新元），所以这里取倒数。
    取倒数是个容易写错的地方，单独用 `test_rates_are_inverted_from_upstream` 钉住。
    """
    if not isinstance(payload, dict):
        raise ValueError('汇率接口返回的不是对象')
    if (payload.get('result') or 'success') != 'success':
        raise ValueError(f"汇率接口返回失败：{payload.get('error-type') or payload.get('result')}")
    base = (payload.get('base_code') or '').upper()
    if base != BASE:
        raise ValueError(f'基准货币不是 {BASE}：{base or "(空)"}')
    table = payload.get('rates')
    if not isinstance(table, dict):
        raise ValueError('汇率接口没有 rates')

    out: dict[str, float] = {}
    for cur in CURRENCIES:
        raw = table.get(cur)
        if isinstance(raw, bool) or raw is None:
            raise ValueError(f'{cur} 汇率缺失')
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f'{cur} 汇率不是数字：{raw!r}') from None
        if value != value or value <= 0:  # NaN / 0 / 负数都算坏数据
            raise ValueError(f'{cur} 汇率非法：{raw!r}')
        out[cur] = round(1 / value, 4)
    return out


def fetch_rates() -> dict:
    """真去拉一次。网络/解析错误一律抛出去，由 `get_rates` 决定怎么降级。"""
    import httpx

    with httpx.Client(timeout=12, follow_redirects=True) as client:
        resp = client.get(URL)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    payload = resp.json()
    return {
        'base': BASE,
        'rates': parse_rates(payload),
        'at': _date_of(payload.get('time_last_update_unix')),
        'src': SOURCE,
        'fetched_at': _now_iso(),
    }


def _load_cache(db) -> dict | None:
    from models import SystemSettings

    try:
        row = db.query(SystemSettings).filter(SystemSettings.key == CACHE_KEY).first()
        if not row or not row.value:
            return None
        value = json.loads(row.value)
    except Exception:
        return None
    if not isinstance(value, dict) or not isinstance(value.get('rates'), dict):
        return None
    return value


def _save_cache(db, data: dict) -> None:
    from models import SystemSettings

    try:
        payload = json.dumps(data, ensure_ascii=False)
        row = db.query(SystemSettings).filter(SystemSettings.key == CACHE_KEY).first()
        if row:
            row.value = payload
        else:
            db.add(SystemSettings(key=CACHE_KEY, value=payload))
        db.commit()
    except Exception:
        db.rollback()


def is_fresh(cached: dict | None, ttl_hours: float = TTL_HOURS) -> bool:
    """缓存是否还在有效期内。时间戳坏掉当成过期（宁可多拉一次）。"""
    if not cached:
        return False
    stamp = cached.get('fetched_at')
    if not stamp:
        return False
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (_now() - parsed).total_seconds() < ttl_hours * 3600


def _decorate(data: dict, *, stale: bool, error: str = '') -> dict:
    out = {
        'base': data.get('base') or BASE,
        'rates': dict(data.get('rates') or {}),
        'at': data.get('at'),
        'src': data.get('src') or SOURCE,
        'fetched_at': data.get('fetched_at'),
        'stale': stale,
        'fallback': bool(data.get('fallback')),
    }
    if error:
        out['error'] = error[:200]
    return out


def get_rates(db=None, force: bool = False, fetch=None, ttl_hours: float = TTL_HOURS) -> dict:
    """取汇率：缓存新鲜就直接用，否则拉一次；拉失败按「旧缓存 → 内置兜底」降级。

    `db` / `fetch` 都可注入，测试里零出站。
    """
    from database import SessionLocal

    own = db is None
    session = db or SessionLocal()
    fetch = fetch or fetch_rates
    try:
        cached = _load_cache(session)
        if not force and is_fresh(cached, ttl_hours):
            return _decorate(cached, stale=False)
        try:
            fresh = fetch()
        except Exception as exc:  # 网络、HTTP、解析，一律降级
            error = f'{type(exc).__name__}: {exc}'
            if cached:
                return _decorate(cached, stale=True, error=error)
            return _decorate(
                {'base': BASE, 'rates': dict(FALLBACK_RATES), 'at': None,
                 'src': f'{SOURCE}（内置兜底）', 'fallback': True},
                stale=True, error=error,
            )
        _save_cache(session, fresh)
        return _decorate(fresh, stale=False)
    finally:
        if own:
            session.close()
