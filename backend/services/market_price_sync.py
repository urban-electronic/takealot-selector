"""多市场参考价的每日同步。

产品上的 `market_prices` 是一段 JSON（市场 -> 价格条目），这里负责把
「有官网链接的市场」每天重读一次。三条硬规矩：

1. **只动自己抓得到的东西**。条目 `src` 是「手工」「淘宝均价」的一律跳过——
   用户手填的价格不能被一次抓取失败覆盖成空。
2. **抓不到就保留旧值**。失败只记 `miss` 次数，连续 3 次才打 `stale` 标记，
   价格本身永不删除。参考价是决策输入，宁可显示「上周的价 + 已过期」，
   也不能显示一个猜出来的数。
3. **不做平台反爬对抗**。淘宝/天猫需要登录态，抓取属于绕过访问控制，不在
   自动采集范围内；中国官网（charleskeith.cn）是已停摆的 ECStore 站点，
   在售商品只剩十几个旧款，也没有本店选品的货号。这两路只能人工录入，
   自动同步只覆盖官网直营市场（新加坡 / 马来西亚等）。

调度用「每 30 分钟检查一次，距上次成功超过 INTERVAL_HOURS 才真跑」，
而不是裸 sleep 24 小时：Railway 会不定时重启容器，裸 sleep 会把计时器
一起重置，结果就是「永远差一点到 24 小时」。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

# 官网直营市场：页面 JSON-LD 里有 priceCurrency + price，可稳定解析。
AUTO_SOURCES = ('', '官网', '官网自动')
# 连续失败到这个次数，条目打上 stale 标记（价格仍然保留）。
STALE_AFTER = 3

INTERVAL_HOURS = float(os.environ.get('MARKET_PRICE_SYNC_INTERVAL_HOURS', '24'))
TICK_SECONDS = int(os.environ.get('MARKET_PRICE_SYNC_TICK_SECONDS', '1800'))
DISABLED = os.environ.get('MARKET_PRICE_SYNC_DISABLED', '').strip() in ('1', 'true', 'yes')
STATE_KEY = 'market_price_sync:last'

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

_ld_re = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_cn_sku_re = re.compile(r'商品编号[：:]\s*([A-Z0-9][A-Z0-9_\-]{5,})')
_cn_price_re = re.compile(r'[￥¥]\s*([0-9]+(?:\.[0-9]+)?)')

_state_lock = threading.Lock()
_worker = None
_stop = threading.Event()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today() -> str:
    """采集日期按北京时间记，和用户看到的「今天」一致。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')


def parse_official_price(html: str) -> dict | None:
    """从官网商品页解析出价格。

    首选 JSON-LD（结构化、不会随模板改版失效）；解析不到时退回
    `"price":123` / `"priceCurrency":"SGD"` 的原始键值对。
    """
    for block in _ld_re.findall(html or ''):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            offers = node.get('offers')
            for offer in (offers if isinstance(offers, list) else [offers]):
                if not isinstance(offer, dict):
                    continue
                price = _to_price(offer.get('price'))
                if price is None:
                    continue
                cur = (offer.get('priceCurrency') or '').strip().upper() or None
                availability = (offer.get('availability') or '').strip()
                return {
                    'price': price,
                    'cur': cur,
                    'in_stock': 'OutOfStock' not in availability if availability else None,
                }
    price = _to_price(_first(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', html))
    if price is None:
        return None
    cur = _first(r'"priceCurrency"\s*:\s*"([A-Za-z]{3})"', html)
    return {'price': price, 'cur': cur.upper() if cur else None, 'in_stock': None}


def parse_cn_price(html: str) -> dict | None:
    """中国官网（ECStore）详情页：服务端渲染，货号 + ￥ 价都在 HTML 里。"""
    text = html or ''
    if not _cn_sku_re.search(text):
        return None
    price = _to_price(_first(_cn_price_re, text))
    if price is None:
        return None
    return {'price': price, 'cur': 'CNY', 'in_stock': None}


def _first(pattern, text):
    if isinstance(pattern, str):
        match = re.search(pattern, text or '')
        return match.group(1) if match else None
    match = pattern.search(text or '')
    return match.group(1) if match else None


def _to_price(raw):
    """价格只接受正数；`True` 是 int 的子类，必须显式排除。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    if value <= 0 or value != value or value in (float('inf'), float('-inf')):
        return None
    return int(value) if value.is_integer() else round(value, 2)


def _text(value) -> str:
    """只把字符串当文本。

    `market_prices` 是一段自由 JSON，`/api/migrate` 整行搬运时不过 Pydantic，
    历史行里的 `url` / `miss` 可能是数字或 null。直接 `.strip()` 会抛
    AttributeError，而它发生在 `run_once` 的产品循环里——一条坏数据就能让
    整轮同步回滚，且 `last_run` 永不更新，于是每 30 分钟重试一次、每次都在
    同一条上炸掉，看起来像「自动同步从来没跑过」。
    """
    return value.strip() if isinstance(value, str) else ''


def is_auto_entry(entry: dict) -> bool:
    src = entry.get('src') or ''
    if not isinstance(src, str):
        return False
    return src in AUTO_SOURCES and bool(_text(entry.get('url')))


def apply_fetch(entry: dict, result: dict | None, today: str, error: str = '') -> tuple[dict, str]:
    """把一次抓取结果合并进条目。返回 (新条目, 状态)。

    状态取值：`updated` / `unchanged` / `failed`。失败时价格原样保留。
    """
    entry = dict(entry or {})
    entry['checked_at'] = _now_iso()
    if result and result.get('price'):
        price = _to_price(result['price'])
        if price is not None:
            changed = entry.get('price') != price
            entry['price'] = price
            entry['cur'] = result.get('cur') or entry.get('cur')
            entry['src'] = '官网自动'
            entry['at'] = today
            entry['miss'] = 0
            entry.pop('stale', None)
            entry.pop('last_error', None)
            return entry, 'updated' if changed else 'unchanged'
    try:
        miss = int(entry.get('miss') or 0) + 1
    except (TypeError, ValueError):
        # 历史行里的 miss 可能是字符串/对象：当它是第一次失败，别让整轮中断。
        miss = 1
    entry['miss'] = miss
    if miss >= STALE_AFTER:
        entry['stale'] = True
    if error:
        entry['last_error'] = error[:200]
    return entry, 'failed'


def sync_prices(prices: dict, fetch, today: str | None = None) -> tuple[dict, list[dict]]:
    """同步一组市场价。`fetch(url) -> dict | None` 由调用方注入，便于测试。

    返回 (新 prices, 报告列表)。手工录入的市场原样返回，不出现在报告里。
    单条条目出错只影响它自己：其余市场照常同步，坏的那条原值保留。
    """
    today = today or _today()
    out = dict(prices or {})
    report: list[dict] = []
    for code, entry in list(out.items()):
        if not isinstance(entry, dict) or not is_auto_entry(entry):
            continue
        url = entry['url'].strip()
        try:
            result = fetch(url)
        except Exception as exc:  # 网络/解析错误一律降级为「本次没抓到」
            result, error = None, f'{type(exc).__name__}: {exc}'
        else:
            error = '' if result else '页面里没有可解析的价格'
        try:
            out[code], status = apply_fetch(entry, result, today, error)
        except Exception as exc:
            # 单条条目无论如何不许打断整轮：坏的那条保持原值并报 failed，
            # 其余市场照常同步。价格是决策输入，宁可留着旧值也不能整轮不跑。
            status, error = 'failed', f'{type(exc).__name__}: {exc}'
        report.append({'market': code, 'status': status, 'price': out[code].get('price'), 'url': url, 'error': error})
    return out, report


def _fetch_official(url: str) -> dict | None:
    import httpx

    with httpx.Client(timeout=25, follow_redirects=True, headers={'User-Agent': _UA}) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    html = resp.text
    if 'charleskeith.cn' in url:
        return parse_cn_price(html)
    return parse_official_price(html)


def run_once(db=None, store_id: str | None = None) -> dict:
    """跑一轮同步，返回本轮统计。进程内串行，重入直接返回上一次的结果。

    `store_id` 只用来收窄范围（手动触发某家店），不加店铺过滤时同步全库——
    每个产品只写自己的 `market_prices`，不存在串店的可能。
    """
    from database import SessionLocal
    from models import Product, SystemSettings

    if not _state_lock.acquire(blocking=False):
        return {'skipped': 'another sync is running'}
    own_session = db is None
    session = db or SessionLocal()
    started = time.time()
    stats = {'products': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'manual': 0, 'conflicts': 0, 'errors': []}
    try:
        query = session.query(Product).filter(Product.market_prices.isnot(None))
        if store_id:
            query = query.filter(Product.store_id == store_id)
        for product in query.all():
            original = product.market_prices
            raw = (product.market_prices or '').strip()
            if not raw:
                continue
            try:
                prices = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(prices, dict) or not prices:
                continue
            stats['products'] += 1
            new_prices, report = sync_prices(prices, _fetch_official)
            stats['manual'] += len(prices) - len(report)
            if report:
                # 网络读取期间可能有人改了任一市场。整段 JSON 必须按读取版本回写，
                # 否则会把刚录入的手工价格或清除操作覆盖掉。
                values = {'market_prices': json.dumps(new_prices, ensure_ascii=False, sort_keys=True)}
                if hasattr(product, 'updated_at'):
                    values['updated_at'] = datetime.utcnow()
                written = session.query(Product).filter(
                    Product.id == product.id,
                    Product.store_id == product.store_id,
                    Product.market_prices == original,
                ).update(values, synchronize_session=False)
                if written != 1:
                    stats['conflicts'] += 1
                    continue
            for item in report:
                stats[item['status']] = stats.get(item['status'], 0) + 1
                if item['error'] and len(stats['errors']) < 5:
                    stats['errors'].append(f"{product.sku or product.id}/{item['market']}: {item['error']}")
        session.commit()
        stats['seconds'] = round(time.time() - started, 1)
        stats['at'] = _now_iso()
        _save_state(session, SystemSettings, stats)
        print(f"[market-price] 同步完成 {stats}", flush=True)
        return stats
    except Exception as exc:
        session.rollback()
        print(f"[market-price] 同步失败 {type(exc).__name__}: {exc}", flush=True)
        return {'error': f'{type(exc).__name__}: {exc}'}
    finally:
        if own_session:
            session.close()
        _state_lock.release()


def _save_state(session, SystemSettings, stats: dict) -> None:
    try:
        row = session.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
        payload = json.dumps(stats, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            session.add(SystemSettings(key=STATE_KEY, value=payload))
        session.commit()
    except Exception:
        session.rollback()


def last_run(db=None) -> dict:
    from database import SessionLocal
    from models import SystemSettings

    own = db is None
    session = db or SessionLocal()
    try:
        row = session.query(SystemSettings).filter(SystemSettings.key == STATE_KEY).first()
        return json.loads(row.value) if row else {}
    except Exception:
        return {}
    finally:
        if own:
            session.close()


def due(last: dict) -> bool:
    """距上次同步是否已经超过 INTERVAL_HOURS。没有历史记录就算到期。"""
    at = (last or {}).get('at')
    if not at:
        return True
    try:
        stamp = datetime.fromisoformat(at)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() >= INTERVAL_HOURS * 3600


def start_worker() -> None:
    """启动每日同步线程。容器重启不会丢计时——到期判断看的是库里的时间戳。"""
    global _worker
    if DISABLED:
        print('[market-price] 已通过 MARKET_PRICE_SYNC_DISABLED 关闭自动同步', flush=True)
        return
    if _worker and _worker.is_alive():
        return

    def loop():
        # 首轮等 90 秒：启动瞬间还在建表、灌数据、拉 Playwright，不要抢资源。
        if _stop.wait(90):
            return
        while not _stop.is_set():
            try:
                if due(last_run()):
                    run_once()
            except Exception as exc:
                print(f'[market-price] 循环异常 {type(exc).__name__}: {exc}', flush=True)
            if _stop.wait(TICK_SECONDS):
                return

    _worker = threading.Thread(target=loop, daemon=True, name='market-price-sync')
    _worker.start()
    print(f'[market-price] 每日同步已启动，间隔 {INTERVAL_HOURS}h', flush=True)


def stop_worker() -> None:
    _stop.set()
