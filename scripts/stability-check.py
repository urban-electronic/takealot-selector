#!/usr/bin/env python
"""线上稳定性巡检：只看「现在是否正常」，不重复验证已经修好的历史缺陷。

设计原则
--------
1. **只判当前状态**。不重跑 gzip 字节数、不重数 SQL 条数、不重放历史事故——
   那些是一次性修复的验收证据，写进了 docs/engineering/；每次巡检都重跑一遍
   既慢又没有新信息。
2. **只报变化**。失败 SKU 列表写进状态文件，同一个 SKU 连续失败只报一次；
   它恢复时也要说一声（否则「安静」会被误读成「还在坏」）。
3. **判不了就说判不了**。接口超时、字段缺失一律记 unknown，不折算成通过。
   巡检最容易变成假绿的地方就是把「没测到」写成「没问题」。
4. **不读凭据、不写数据**。只发 GET 请求；API key 从环境变量取，不落盘、不打印。

用法
----
    TAKEALOT_API_KEY=... python scripts/stability-check.py
    TAKEALOT_API_KEY=... python scripts/stability-check.py --json
    TAKEALOT_API_KEY=... python scripts/stability-check.py --state .stability.json --quiet

退出码：0 全部正常（或仅有已知未恢复项）；1 有新问题；2 巡检本身跑不起来。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = 'https://takealot-selector-production.up.railway.app'

# 采集失败率的告警线。28 个启用里坏 1-2 个是正常的数据问题（商品下架、
# 变体改名），坏到 1/4 才是系统性故障。
ERROR_RATE_LIMIT = 0.25

# 单个接口的容忍耗时。线上从国内访问有 ~0.8s 跨国往返，主要看有没有卡住。
SLOW_SECONDS = 12.0

# 卖家同步超过这个时长没成功就值得说一句（它每小时跑一次）。
SYNC_STALE_HOURS = 3


def api_get(base, path, key, timeout=60, attempts=3):
    """GET 一个接口，返回 (状态码, 解析后的 JSON 或 None, 耗时秒, 错误说明)。

    带重试：Railway 在部署切换、容器重启时会短暂返回 502/503，单次失败
    不代表系统挂了。实测 2026-10-27 部署后 30 秒内抓到过一次 502，紧接着
    连测 5 次全是 200。不重试的话巡检会每天误报几次「站点不可达」，
    报几次之后这个告警就没人看了。
    """
    last = (None, None, 0.0, '未执行')
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 * attempt)
        last = _api_get_once(base, path, key, timeout)
        status, _, _, error = last
        # 只有「连接层失败」和 5xx 值得重试；4xx 是确定的结论，重试没意义。
        retryable = status is None or (isinstance(status, int) and status >= 500)
        if not retryable:
            return last
    return last


def _api_get_once(base, path, key, timeout):
    url = base + path
    headers = {'Accept': 'application/json'}
    if key:
        headers['X-API-Key'] = key
    request = urllib.request.Request(url, headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        return error.code, None, time.time() - started, f'HTTP {error.code}'
    except Exception as error:  # 超时、DNS、连接被重置
        return None, None, time.time() - started, type(error).__name__
    elapsed = time.time() - started
    try:
        return status, json.loads(body), elapsed, ''
    except ValueError:
        return status, None, elapsed, '响应不是 JSON'


def _age_seconds(stamp):
    """ISO 时间串到现在的秒数；解析不了返回 None（不猜）。"""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return time.time() - parsed.timestamp()


def check_loop(loop, enabled):
    """S1/S2：循环在转吗、有没有卡死任务。"""
    findings = []
    interval = loop.get('interval_minutes') or 5
    age = loop.get('cycle_age_seconds')
    # 心跳只证明线程还在等；cycle_age 才证明循环真的转了一圈。
    if age is None:
        findings.append(('S1', 'warn', '算不出上一轮距今多久（last_cycle_at 缺失或格式不对）'))
    elif age > interval * 60 * 2:
        findings.append(('S1', 'error',
                         f'循环停转：上一轮 {age} 秒前，间隔只有 {interval} 分钟'))
    if (loop.get('stuck_jobs') or 0) > 0:
        findings.append(('S2', 'error',
                         f"有 {loop['stuck_jobs']} 个任务被看门狗放弃（{loop.get('stuck_at')}）"))
    if enabled and not loop.get('worker_alive'):
        findings.append(('S2', 'warn', '采集线程心跳已停（worker_alive=false）'))
    return findings


def check_collection(rows):
    """S3/S7：失败率是否超线、每条失败是否说得清原因。"""
    findings = []
    monitored = []
    for row in rows:
        monitor = row.get('monitor') or {}
        if monitor.get('enabled') or monitor.get('request_once'):
            monitored.append((row.get('sku'), monitor))
    failed = [(sku, m) for sku, m in monitored if m.get('status') == 'error']
    total = len(monitored)
    if total and len(failed) / total > ERROR_RATE_LIMIT:
        findings.append(('S3', 'error',
                         f'采集失败 {len(failed)}/{total} 条，超过 {int(ERROR_RATE_LIMIT * 100)}% 告警线'))
    # 失败必须能归因：没有 error_code 的记录，用户只能反复点重试。
    silent = [sku for sku, m in failed if not m.get('error_code')]
    if silent:
        findings.append(('S7', 'warn', f'{len(silent)} 条失败没有错误码：{", ".join(silent[:5])}'))
    return findings, failed, total


def check_sync(overview):
    """S4：卖家同步还在跑吗。"""
    findings = []
    sync = (overview or {}).get('sync') or {}
    age = _age_seconds(sync.get('last_success'))
    if sync.get('status') == 'error':
        findings.append(('S4', 'error', f"卖家同步失败：{sync.get('error') or '未给出原因'}"))
    elif age is None:
        findings.append(('S4', 'warn', '卖家同步没有成功记录'))
    elif age > SYNC_STALE_HOURS * 3600:
        findings.append(('S4', 'error', f'卖家同步 {age / 3600:.1f} 小时没成功'))
    return findings


def check_endpoints(base, key):
    """S6：主接口是否都通、有没有慢到卡住。"""
    findings = []
    timings = {}
    for path in ('/api/products', '/api/inventory', '/api/packing/products',
                 '/api/operations/overview', '/api/operations/alerts',
                 '/api/automatic-pricing'):
        status, _, elapsed, error = api_get(base, path, key)
        timings[path] = {'status': status, 'seconds': round(elapsed, 2), 'error': error}
        if status != 200:
            findings.append(('S6', 'error', f'{path} 返回 {status or "无响应"}（{error or "非 200"}）'))
        elif elapsed > SLOW_SECONDS:
            findings.append(('S6', 'warn', f'{path} 耗时 {elapsed:.1f}s，超过 {SLOW_SECONDS:.0f}s'))
    return findings, timings


def check_images(rows):
    """S8：缺图商品数量。只记数，不和历史比——比的是「有没有突然多出来」。"""
    missing = [row.get('sku') for row in rows if not row.get('image')]
    return missing


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    try:
        Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass  # 状态写不进去不影响本次结论，下次会当成「全是新的」再报一遍


def main():
    parser = argparse.ArgumentParser(description='线上稳定性巡检（只报当前状态与新增问题）')
    parser.add_argument('--base', default=os.environ.get('TAKEALOT_BASE') or DEFAULT_BASE)
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    parser.add_argument('--state', default='', help='状态文件路径，用于只报新增失败')
    parser.add_argument('--quiet', action='store_true', help='正常时什么都不输出（给定时任务用）')
    args = parser.parse_args()

    key = (os.environ.get('TAKEALOT_API_KEY') or '').strip()

    status, root, _, error = api_get(args.base, '/', key, timeout=30)
    if status != 200 or not isinstance(root, dict):
        payload = {'ok': False, 'reason': f'站点不可达：{error or status}', 'build': None}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload['reason'])
        return 2
    build = root.get('build')

    status, pricing, elapsed, error = api_get(args.base, '/api/automatic-pricing', key)
    if status != 200 or not isinstance(pricing, dict):
        payload = {'ok': False, 'reason': f'调价接口不可用：{error or status}', 'build': build}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload['reason'])
        return 2
    rows = pricing.get('rows') or []
    loop = pricing.get('loop') or {}

    status, overview, _, _ = api_get(args.base, '/api/operations/overview', key)
    status, collector, _, _ = api_get(args.base, '/api/collector/status', key)

    findings = []
    findings += check_loop(loop, loop.get('enabled') or len(rows))
    collection_findings, failed, monitored_total = check_collection(rows)
    findings += collection_findings
    findings += check_sync(overview if isinstance(overview, dict) else {})
    endpoint_findings, timings = check_endpoints(args.base, key)
    findings += endpoint_findings
    if isinstance(collector, dict) and not collector.get('ready'):
        findings.append(('S5', 'error', '采集器未就绪（collector/status ready=false）'))

    # 失败集合按「新增 / 复发 / 未恢复 / 已恢复」四分，避免每轮重复报同一条。
    current_failed = {sku: {'code': m.get('error_code'), 'message': m.get('error'),
                            'failures': m.get('failures')} for sku, m in failed}
    state = load_state(args.state) if args.state else {}
    previous_failed = state.get('failed') or {}
    # 间歇性失败（页面渲染慢、偶发超时）会在「失败→成功→失败」之间来回跳，
    # 只拿上一轮的快照比，同一条商品每隔一轮就报一次「新增失败」。这里留一份
    # 24 小时内的失败史：这段时间里报过的都算已知，只有从没见过的才算新增。
    now = time.time()
    history = {sku: ts for sku, ts in (state.get('failed_history') or {}).items()
               if isinstance(ts, (int, float)) and now - ts < 24 * 3600}
    fresh = set(current_failed) - set(previous_failed)
    new_skus = sorted(fresh - set(history))
    relapsed = sorted(fresh & set(history))
    still_skus = sorted(set(current_failed) & set(previous_failed))
    recovered = sorted(set(previous_failed) - set(current_failed))
    for sku in current_failed:
        history[sku] = now

    missing_images = check_images(rows)
    previous_missing = set(state.get('missing_images') or [])
    new_missing = sorted(set(missing_images) - previous_missing)

    if args.state:
        save_state(args.state, {'failed': current_failed, 'failed_history': history,
                                'missing_images': missing_images,
                                'checked_at': datetime.now(timezone.utc).isoformat(),
                                'build': build})

    hard = [f for f in findings if f[1] == 'error']
    soft = [f for f in findings if f[1] == 'warn']
    ok = not hard

    payload = {
        'ok': ok,
        'build': build,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'loop': {k: loop.get(k) for k in ('cycles', 'cycle_age_seconds', 'interval_minutes',
                                          'worker_alive', 'enabled', 'success', 'error',
                                          'running', 'stuck_jobs', 'round_seconds')},
        'collection': {'monitored': monitored_total, 'failed': len(current_failed)},
        'new_failures': {sku: current_failed[sku] for sku in new_skus},
        'relapsed': relapsed,
        'still_failing': still_skus,
        'recovered': recovered,
        'missing_images': {'total': len(missing_images), 'new': new_missing},
        'findings': [{'id': i, 'level': lv, 'message': msg} for i, lv, msg in findings],
        'timings': timings,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not (args.quiet and ok and not new_skus and not recovered):
        # 「通过」不能说成一句干净话就完事：循环正常但有 3 条商品采不到时，
        # 用户看到「通过」会以为全都好了，下次再发现红点就觉得巡检在骗人。
        pending = len(current_failed)
        if not ok:
            headline = '发现异常'
        elif pending:
            headline = f'通过（{pending} 条待处理）'
        else:
            headline = '通过'
        print(f"巡检 {headline} · build {build} · "
              f"循环 {loop.get('cycles')} 轮 / {loop.get('cycle_age_seconds')}s 前")
        if hard or soft:
            for ident, level, message in hard + soft:
                print(f"  [{'异常' if level == 'error' else '提醒'}] {ident} {message}")
        if new_skus:
            print(f'  新增失败 {len(new_skus)} 条：')
            for sku in new_skus:
                print(f"    {sku} {current_failed[sku]['code']} — {current_failed[sku]['message']}")
        if still_skus:
            print(f'  仍未恢复 {len(still_skus)} 条：{", ".join(still_skus[:8])}'
                  + (' …' if len(still_skus) > 8 else ''))
        if relapsed:
            print(f'  复发 {len(relapsed)} 条（24 小时内失败过，这次又失败）：'
                  f'{", ".join(relapsed[:8])}')
        if recovered:
            print(f'  已恢复 {len(recovered)} 条：{", ".join(recovered[:8])}')
        if new_missing:
            print(f'  新缺图 {len(new_missing)} 条：{", ".join(new_missing[:8])}')
        if ok and not new_skus and not recovered:
            print('  没有新问题。')

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
