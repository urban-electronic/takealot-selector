"""采集失败的后台自愈。

线上真实现场（2026-10-18，作业队列里 3 条红色徽标）暴露出三类互不相同的问题，
它们都不该靠人工点「立即采集」解决：

  ① SKU 9902348622221：`error_code` 是 `price_not_found`，可 diagnostics 里的
     `page_title` 明明白白是 `Just a moment...`（网站验证页）。这是旧版本代码
     写的结论，新版 classify() 早就能认出验证页，但没人去重判历史记录。
     更要命的是它 `enabled=False`，而 process() 第一行就 return —— 它永远不会
     被重试，那个错误徽标会一直挂着。
  ② SKU 9902391031247：浏览器 RuntimeError，但库里有 5 分钟前的有效价 450。
     把「这一轮没跑成」显示成红色故障，等于让用户以为报价没了。
  ③ SKU 9902415247821：变体没切全（真因在 pricepilot_collector，已修）。

这里只做**记录层**的修复：不伪造报价、不清空真实错误、不动任何价格。
判断一条记录该不该改，只依据它自己已有的事实（diagnostics、时间戳、开关状态）。
"""
import json
import time
from datetime import datetime, timezone

from models import SystemSettings, DEFAULT_STORE_ID
from services.buybox_monitor import PREFIX

# 单次退避的上限（与 buybox_monitor 里的指数退避封顶一致）。
# next_due 超过 last_attempt + 这个值再留 1 小时余量，就说明记录被卡住了。
MAX_BACKOFF_SECONDS = 6 * 3600
# 同一条记录两次「修复性重采」之间至少隔这么久：一个注定失败的 SKU
# （比如商品已下架）不能每 10 秒被重采一次。
REPAIR_COOLDOWN_SECONDS = 6 * 3600

# 这些错误码是「已经定位到根因」的结论，不需要再拿 diagnostics 重判。
# price_not_found 故意不在里面：它是旧版本对一堆不同原因的统一兜底，
# 恰恰最需要用 diagnostics 重新判一次。
DECISIVE_CODES = frozenset({
    'site_challenge', 'rate_limited', 'http_forbidden', 'site_unavailable',
    'product_not_found', 'invalid_url', 'variant_unmatched', 'tls_error',
    'navigation_timeout', 'collector_error', 'network_error', 'product_redirect',
    'unstable_price', 'page_structure_changed', 'page_not_recognized',
    'collector_unavailable', 'seller_read_failed', 'parse_error',
})


def _parse_time(value):
    """ISO 时间串 → epoch 秒。解析不了返回 None（当作"不知道"，不猜）。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def repair_monitor(monitor, now=None):
    """修一条采集记录。返回 (修好后的记录, 做了哪些改动)。

    没有改动时返回的 actions 是空列表，调用方据此跳过写库。
    """
    now = time.time() if now is None else now
    value = dict(monitor or {})
    actions = []
    was_error = value.get('status') == 'error'

    interval = value.get('interval_minutes')
    interval_seconds = (interval if isinstance(interval, int) and interval > 0 else 5) * 60

    # ① 错误码重判：库里的结论可能是旧版本代码写的。
    #    只在原有结论不是「已定位根因」时重判，避免把真实异常改写成别的东西
    #    （2026-10-17 浏览器 TargetClosedError 被写成「页面没有 Buybox 价」的教训）。
    if was_error:
        stored = value.get('error_code') or ''
        diagnostics = value.get('diagnostics')
        if stored not in DECISIVE_CODES and isinstance(diagnostics, dict) and diagnostics:
            from services.collection_diagnostics import classify
            code, message = classify({'diagnostics': diagnostics,
                                      'error': diagnostics.get('raw_error')})
            if code and code != stored:
                value['error_code'] = code
                value['error'] = message
                actions.append(f'错误码 {stored or "(空)"} → {code}')

    # ② next_due 卡死：一次失败把记录排到几小时甚至几天之后，等于永久停采。
    if was_error:
        due = value.get('next_due')
        attempted = _parse_time(value.get('last_attempt_at'))
        if isinstance(due, (int, float)) and attempted is not None:
            if due > attempted + MAX_BACKOFF_SECONDS + 3600:
                value['next_due'] = attempted + interval_seconds
                actions.append(f'next_due {int(due)} 拉回 {int(value["next_due"])}')

    # Disabled collection is an operator decision. Repair may classify history,
    # but cannot grant itself request_once; explicit existing requests survive.

    # ④ 已停采却留着 error：它不是故障，是用户把这个 SKU 关掉了。
    #    归一成 paused，真实原因留在 last_error_* 里，信息不丢，也不再报红。
    if (was_error and not value.get('enabled') and not value.get('request_once')
            and value.get('status') == 'error'):
        value['status'] = 'paused'
        value['last_error_code'] = value.get('error_code') or ''
        value['last_error'] = value.get('error') or ''
        value['last_error_at'] = value.get('last_attempt_at') or ''
        value['error_code'] = ''
        value['error_stage'] = ''
        value['error'] = ''
        actions.append('停采记录归一为 paused')

    return value, actions


def repair(db, store=DEFAULT_STORE_ID, now=None):
    """把本店所有采集记录过一遍自愈。返回改动汇总，供日志/接口使用。"""
    now = time.time() if now is None else now
    prefix = PREFIX + store + ':'
    changed = 0
    details = []
    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(prefix)).all():
        try:
            original = json.loads(row.value)
        except (TypeError, ValueError):
            continue
        if not isinstance(original, dict):
            continue
        fixed, actions = repair_monitor(original, now)
        if not actions:
            continue
        # 乐观锁：后台采集线程可能刚写过这一行。值变了就放弃，绝不覆盖它的结果。
        if db.query(SystemSettings).filter(
                SystemSettings.key == row.key,
                SystemSettings.value == row.value).update(
                    {'value': json.dumps(fixed, ensure_ascii=False)}) != 1:
            db.rollback()
            continue
        changed += 1
        details.append({'key': row.key, 'sku': fixed.get('sku'), 'actions': actions})
    db.commit()
    return {'changed': changed, 'details': details}
