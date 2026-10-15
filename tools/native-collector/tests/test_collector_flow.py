"""采集器主流程行为测试（WB-103-R2）。

全部用替身：假 Api / 假页面 / 假解析器。不访问网站、不使用凭据、不写生产。
覆盖：成功 / 全失败 / 部分回传失败 / 取消释放 / 连续验证暂停 / 旧状态隔离。
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collector  # noqa: E402

URL = 'https://www.takealot.com/x/PLID1'


class FakeLocator:
    async def count(self):
        return 1


class FakePage:
    def __init__(self):
        self.url = URL
        self.closed = False

    async def title(self):
        return 'test'

    def on(self, event, handler):
        pass

    def locator(self, selector):
        return FakeLocator()

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakeParser:
    """result_fn(item) -> dict。记录被访问的 SKU，用于验证「不再访问已拦截页」."""

    def __init__(self, result_fn):
        self.result_fn = result_fn
        self.visited = []

    async def scrape_product(self, page, item):
        self.visited.append(item['sku'])
        # 真实解析器（pricepilot_collector）返回值带 sku/productId，替身必须保持一致，
        # 否则回传报文字段缺失，错误注入与断言都会失真。
        return {**self.result_fn(item), 'sku': item['sku'], 'productId': item['productId']}


class FakeApi:
    """offer_error_skus 中的 SKU 回传时抛异常（模拟含敏感内容的异常）。"""

    def __init__(self, items, offer_error_skus=()):
        self.items = items
        self.offer_error_skus = set(offer_error_skus)
        self.released = []
        self.reported = []

    def call(self, path, body=None):
        if path == '/api/collector/products':
            return {'items': self.items}
        if path == '/api/collector/offers':
            for it in body['items']:
                if it.get('sku') in self.offer_error_skus:
                    raise RuntimeError('SECRET_SENTINEL')  # 故意含哨兵，验证不外泄
                self.reported.append(it.get('sku'))
            return {'processed': len(body['items']), 'successful': len(body['items']), 'skipped': 0}
        if path == '/api/collector/release':
            self.released.extend(body['product_ids'])
            return {'released': len(body['product_ids'])}
        raise AssertionError('unexpected path: ' + path)


def items(n=2):
    return [{'productId': f'lease-{i}', 'shopId': 'default-store', 'sku': f'SKU{i}',
             'name': f'商品 {i}', 'url': URL, 'myPrice': 100, 'myStock': 5} for i in range(n)]


def ok(_item):
    return {'buyboxPrice': 199, 'holdingBuybox': False, 'offers': [{'price': 199, 'status': 'in_stock'}]}


def failed(_item):
    return {'buyboxPrice': None, 'error': 'price_not_found', 'offers': []}


def challenged(_item):
    return {'buyboxPrice': None, 'error': 'site_challenge', 'offers': []}


def run(coro):
    return asyncio.run(coro)


def test_all_success_reports_every_sku():
    api, ctx, parser = FakeApi(items()), FakeContext(), FakeParser(ok)
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser))
    assert (claimed, successful, blocked) == (2, 2, 0)
    assert sorted(api.reported) == ['SKU0', 'SKU1']


def test_all_failed_is_not_counted_as_success():
    # 全部失败时 successful 必须为 0，不得按领取数当成功（Codex 实测回归）
    api, ctx, parser = FakeApi(items()), FakeContext(), FakeParser(failed)
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser))
    assert (claimed, successful, blocked) == (2, 0, 0)


def test_partial_offer_failure_keeps_others_and_releases_lease():
    # SKU0 回传抛异常：SKU1 必须继续成功；SKU0 的租约要被释放
    api = FakeApi(items(), offer_error_skus={'SKU0'})
    ctx, parser = FakeContext(), FakeParser(ok)
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser))
    assert claimed == 2 and successful == 1
    assert api.reported == ['SKU1'], "另一个 SKU 应继续回传"
    assert 'lease-0' in api.released, "失败项的租约必须释放，不能悬挂"


def test_exception_text_never_reaches_log(capsys):
    # Codex 实测：异常正文（含反射内容/敏感配置）曾原样进入日志。此处用哨兵回归。
    api = FakeApi(items(1), offer_error_skus={'SKU0'})
    ctx, parser = FakeContext(), FakeParser(ok)
    run(collector.round_once(ctx, api, parser))
    out = capsys.readouterr().out
    assert 'SECRET_SENTINEL' not in out, "异常正文不得输出到日志"
    assert '回传失败' in out, "应给出受控分类提示"


def test_cancelled_round_releases_all_leases():
    api, ctx, parser = FakeApi(items()), FakeContext(), FakeParser(ok)

    async def scenario():
        task = asyncio.create_task(collector.round_once(ctx, api, parser))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert sorted(api.released) == ['lease-0', 'lease-1'], "取消时所有在租租约都要释放"


def test_verification_block_pauses_on_first_confirmation():
    # 首次确认被拦截即暂停：不为「确认」而故意第二次访问已确认的拦页面
    ctx, api = FakeContext(), FakeApi(items(1))
    parser = FakeParser(challenged)
    state = {}
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser, state))
    assert blocked == 1, "拦截必须计入 blocked"
    assert parser.visited == ['SKU0'], "首轮访问一次"
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser, state))
    assert parser.visited == ['SKU0'], "首次确认后不得再访问第二次"
    assert claimed == 1 and blocked == 1


def test_http_403_is_counted_as_intercept_not_network_error():
    # 403 属验证拦截，不得误归为网络加载失败（WB-103-R3 第 2 条）
    ctx, api = FakeContext(), FakeApi(items(1))
    parser = FakeParser(lambda i: {'buyboxPrice': None, 'error': 'http_403', 'offers': []})
    state = {}
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser, state))
    assert blocked == 1, "403 必须计入 blocked"
    assert state.get('SKU0') == 1, "403 必须累加拦截计数"
    run(collector.round_once(ctx, api, parser, state))
    assert parser.visited == ['SKU0'], "403 首次确认后同样不得再访问"


def test_network_load_failure_is_not_treated_as_intercept():
    # 网络加载失败可重试，不应计入「验证拦截」的跨轮停止判据
    ctx, api = FakeContext(), FakeApi(items(1))
    parser = FakeParser(lambda i: {'buyboxPrice': None, 'error': 'blocked_or_load_failed', 'offers': []})
    state = {}
    for _ in range(3):
        run(collector.round_once(ctx, api, parser, state))
    assert state.get('SKU0') is None, "加载失败不得累加拦截计数"
    assert len(parser.visited) > 3, "加载失败应继续尝试（含重试）"


# ---- 陈旧状态隔离（GUI 纯函数，不依赖 Tk 实例）----

import status_view  # 不依赖 Tk，可无显示环境运行  # noqa: E402


def _now():
    return datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_stale_timestamp_is_not_shown_as_current_success():
    # Codex 实测：同 PID + 2000 年时间戳 + round1 collecting_ok 仍显示绿色成功
    data = {'pid': 123, 'ts': '2000-01-01T00:00:00+00:00', 'round': 1, 'state': 'collecting_ok',
            'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, rnd, _ = status_view.classify_state(data, 123, 0, '', now=_now())
    assert '成功' not in text and color != '#007a00', "陈旧时间戳不得继续显示绿色成功"


def test_round_regression_is_not_shown_as_current():
    data = {'pid': 123, 'ts': _now().isoformat(), 'round': 1, 'state': 'collecting_ok',
            'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, rnd, _ = status_view.classify_state(data, 123, 2, '', now=_now())
    assert '成功' not in text, "轮次倒退不得显示上一轮成功"


def test_running_stage_is_distinct_from_last_round_summary():
    data = {'pid': 123, 'ts': _now().isoformat(), 'round': 2, 'state': 'running',
            'claimed': 0, 'successful': 0, 'failed': 0}
    text, color, rnd, _ = status_view.classify_state(data, 123, 1, '', now=_now())
    assert '执行中' in text and color == '#0066cc', "在途阶段应显示执行中，而非上一轮汇总"


def test_pid_mismatch_is_never_trusted():
    data = {'pid': 999, 'ts': _now().isoformat(), 'round': 5, 'state': 'collecting_ok',
            'claimed': 3, 'successful': 3, 'failed': 0}
    text, _, _, _ = status_view.classify_state(data, 123, 4, '', now=_now())
    assert '成功' not in text, "pid 不一致（上一次运行残留）不得被信任"


def test_run_id_mismatch_is_not_trusted():
    # Codex 场景：同 PID + fresh ts，但 run_id='OLD' 与本次启动 'NEW' 不符
    data = {'pid': 123, 'ts': _now().isoformat(), 'run_id': 'OLD', 'round': 9,
            'state': 'collecting_ok', 'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, _, _ = status_view.classify_state(data, 123, 2, 'NEW', now=_now())
    assert '成功' not in text and color != '#007a00', "run_id 与本次启动不符不得显示绿色成功"


def test_invalid_round_does_not_raise_or_show_success():
    # round='oops' 曾直接 TypeError 并可能打断 GUI poll
    data = {'pid': 123, 'ts': _now().isoformat(), 'run_id': 'R', 'round': 'oops',
            'state': 'collecting_ok', 'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, _, _ = status_view.classify_state(data, 123, 0, '', now=_now())
    assert '成功' not in text and color != '#007a00'


def test_future_timestamp_is_not_trusted():
    future = (_now() + timedelta(hours=1)).isoformat()
    data = {'pid': 123, 'ts': future, 'run_id': 'R', 'round': 3,
            'state': 'collecting_ok', 'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, _, _ = status_view.classify_state(data, 123, 0, '', now=_now())
    assert '成功' not in text and color != '#007a00', "来自未来的时间戳不得被信任"
