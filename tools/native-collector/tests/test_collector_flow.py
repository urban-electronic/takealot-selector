"""采集器主流程行为测试（WB-103-R2）。

全部用替身：假 Api / 假页面 / 假解析器。不访问网站、不使用凭据、不写生产。
覆盖：成功 / 全失败 / 部分回传失败 / 取消释放 / 连续验证暂停 / 旧状态隔离。
"""
import asyncio
import os
import sys
import threading
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

    async def close(self):
        return None


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


@pytest.mark.parametrize('bad', [None, [], {}, 1, '', '  ', ['bad']])
def test_malformed_claim_releases_valid_lease_without_visiting(bad):
    api = FakeApi([*items(1), {'productId': bad, 'sku': 'bad'}])
    ctx, parser = FakeContext(), FakeParser(ok)
    with pytest.raises(RuntimeError, match='响应格式异常'):
        run(collector.round_once(ctx, api, parser))
    assert api.released == ['lease-0']
    assert not ctx.pages and not parser.visited and not api.reported


@pytest.mark.parametrize('batch', [None, [], {'items': None}, {'items': {}}, {'items': 'bad'}])
def test_non_list_claim_is_not_idle_or_success(batch):
    class MalformedApi(FakeApi):
        def call(self, path, body=None):
            if path == '/api/collector/products':
                return batch
            return super().call(path, body)
    api, ctx = MalformedApi([]), FakeContext()
    with pytest.raises(RuntimeError, match='响应格式异常'):
        run(collector.round_once(ctx, api, FakeParser(ok)))
    assert not ctx.pages and not api.released


def test_cancelled_malformed_claim_releases_identifiable_lease():
    async def scenario():
        entered, complete = threading.Event(), threading.Event()
        class LateApi(FakeApi):
            def call(self, path, body=None):
                if path == '/api/collector/products':
                    entered.set()
                    assert complete.wait(5)
                    return {'items': [None, *items(1), {'productId': ['bad']}]}
                return super().call(path, body)
        api = LateApi([])
        task = asyncio.create_task(collector.claim_batch(api))
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        complete.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert api.released == ['lease-0']
    run(scenario())


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


class BlockingApi(FakeApi):
    """products 可被门闩阻塞，用于确定性复现「超时后迟到租约」场景。"""

    def __init__(self, items):
        super().__init__(items)
        self.gate = threading.Event()
        self.release_done = threading.Event()

    def call(self, path, body=None):
        if path == '/api/collector/products':
            self.gate.wait(30)
            return {'items': self.items}
        if path == '/api/collector/release':
            self.released.extend(body['product_ids'])
            self.release_done.set()
            return {'released': len(body['product_ids'])}
        return super().call(path, body)


def test_products_failure_is_disconnect_not_idle():
    # 领取失败必须向上传播，不得吞成 {} 后返回 (0,0,0) 被误报为「空闲」
    class BoomApi(FakeApi):
        def call(self, path, body=None):
            if path == '/api/collector/products':
                raise RuntimeError('connection refused')
            return super().call(path, body)

    api = BoomApi(items(1))

    async def go():
        with pytest.raises(RuntimeError):
            await collector.round_once(FakeContext(), api, FakeParser(ok))

    run(go())


def test_late_claim_after_cancel_releases_lease():
    # 取消时服务端尚未响应、取消后租约才迟到返回：shield 必须等真实响应再释放，不得悬挂
    # （接续 WB-103-R3 / PR105 门闩语义；原 30s 截止版本已移除，不再有事件循环层超时）
    api = BlockingApi(items(2))

    async def scenario():
        task = asyncio.create_task(collector.round_once(FakeContext(), api, FakeParser(ok)))
        await asyncio.sleep(0)            # 让 round_once 进入领取（卡在 claim_batch 的 shield）
        task.cancel()                     # 取消时服务端尚未响应
        api.gate.set()                    # 模拟迟到响应（取消后服务端才返回）
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert api.release_done.wait(10), f"迟到租约必须被释放，实际 released={api.released}"
    assert sorted(api.released) == ['lease-0', 'lease-1'], "取消后的迟到租约必须释放"


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


# ---- WB-103-R6：拦截暂停跨重启保存 + GUI run_id 绑定 + 显式人工恢复 ----

import challenge_state as cs  # noqa: E402


def test_challenge_state_persists_across_restart(tmp_path):
    # 模拟重启：save 后再次 load 应读回已保存的拦截计数（跨重启保存）
    p = tmp_path / 'challenge_state.json'
    cs.save_challenge_state({'SKU0': 1, 'SKU1': 2}, p)
    assert cs.load_challenge_state(p) == {'SKU0': 1, 'SKU1': 2}, "重启后应读回已保存的拦截计数"


def test_challenge_state_missing_is_empty(tmp_path):
    assert cs.load_challenge_state(tmp_path / 'nope.json') == {}, "缺失文件应返回空 dict"


def test_challenge_state_corrupt_raises_not_empty(tmp_path):
    # WB-103-R7 第2条：损坏文件不得静默回 {} 冒充“无暂停”，必须抛错让调用方保留内存暂停
    p = tmp_path / 'c.json'
    p.write_text('{not valid json', encoding='utf-8')
    with pytest.raises(cs.ChallengeStateError):
        cs.load_challenge_state(p)


def test_challenge_state_missing_is_empty(tmp_path):
    assert cs.load_challenge_state(tmp_path / 'nope.json') == {}, "缺失文件应返回空 dict（从未暂停，安全）"


def test_challenge_state_rejects_negative_counts(tmp_path):
    # WB-103-R7 第2条：负计数无意义，必须剔除
    p = tmp_path / 'c.json'
    p.write_text('{"SKU0": 1, "SKU1": -3, "SKU2": true}', encoding='utf-8')
    loaded = cs.load_challenge_state(p)
    assert loaded == {'SKU0': 1}, "负计数/bool 应剔除，仅保留非负 int"


def test_challenge_state_rejects_non_int_values(tmp_path):
    p = tmp_path / 'c.json'
    p.write_text('{"SKU0": 1, "SKU1": "x", "SKU2": true}', encoding='utf-8')
    loaded = cs.load_challenge_state(p)
    assert loaded == {'SKU0': 1}, "只接受 int 计数，bool/字符串应剔除"


def test_challenge_state_clear_recovery(tmp_path):
    # 显式人工恢复（删除文件）后应为空
    p = tmp_path / 'c.json'
    cs.save_challenge_state({'SKU0': 3}, p)
    cs.clear_challenge_state(p)
    assert cs.load_challenge_state(p) == {}, "显式恢复后应为空"


def test_consume_recovery_flag_clears_state(tmp_path):
    p = tmp_path / 'c.json'
    cs.save_challenge_state({'SKU0': 3}, p)
    flag = tmp_path / cs.RECOVERY_FLAG
    flag.touch()
    assert cs.consume_recovery_flag(p) is True, "存在恢复标志应返回 True"
    assert cs.load_challenge_state(p) == {}, "恢复标志应清空持久化暂停"
    assert not flag.exists(), "恢复标志应被消费删除"


def test_consume_recovery_flag_no_flag_is_false(tmp_path):
    assert cs.consume_recovery_flag(tmp_path / 'c.json') is False, "无标志应返回 False"


def test_known_run_id_first_read_is_trusted():
    # GUI 生成 run_id 并立即绑定：首次读取即以此 run_id 校验，不从残留文件学习
    run_id = 'GUI-GENERATED'
    data = {'pid': 123, 'ts': _now().isoformat(), 'run_id': run_id, 'round': 1,
            'state': 'collecting_ok', 'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, _, seen = status_view.classify_state(data, 123, 0, run_id, now=_now())
    assert '成功' in text and color == '#007a00', "已知 run_id 的首次读取应信任"
    assert seen == run_id, "应回写已绑定 run_id"


def test_same_pid_reused_but_old_run_id_is_stale():
    # 进程重启后 OS 可能复用相同 PID：必须用 run_id 区分，旧 run_id 的状态视为陈旧
    data = {'pid': 123, 'ts': _now().isoformat(), 'run_id': 'OLD-RUN', 'round': 9,
            'state': 'collecting_ok', 'claimed': 1, 'successful': 1, 'failed': 0}
    text, color, _, _ = status_view.classify_state(data, 123, 0, 'NEW-RUN', now=_now())
    assert '成功' not in text and color != '#007a00', "同 PID 但旧 run_id 必须判为陈旧"


def test_after_recovery_only_rechallenged_sku_pauses_others_continue():
    # 显式恢复（清空 challenged）后，再次被拦的 SKU 仅自身暂停，其余任务继续
    def mixed(item):
        return challenged(item) if item['sku'] == 'SKU0' else ok(item)
    ctx, api = FakeContext(), FakeApi(items(2))
    parser = FakeParser(mixed)
    state = {}  # 已恢复：空拦截计数
    claimed, successful, blocked = run(collector.round_once(ctx, api, parser, state))
    assert claimed == 2 and successful == 1 and blocked == 1, "恢复后仅再被拦的 SKU 暂停，其他继续"
    assert 'SKU1' in api.reported, "未被拦的 SKU 必须继续回传"
    assert parser.visited == ['SKU0', 'SKU1'], "两个 SKU 本轮各被尝试一次，被拦项不被反复 hammer"
    assert state.get('SKU0') == 1, "被拦 SKU 的计数应累加，供后续轮次跨轮暂停"


def test_persisted_pause_survives_restart_without_reclear():
    # 跨重启语义：上一次运行已持久化的拦截计数，本次运行应直接复用（不自动清空）
    ctx, api = FakeContext(), FakeApi(items(1))
    parser = FakeParser(challenged)
    # 第一轮：首次拦截，计数变 1
    state = {}
    run(collector.round_once(ctx, api, parser, state))
    assert state.get('SKU0') == 1
    # 第二轮：本轮仍被拦，但计数已 >=1，应直接暂停、不再访问页面（跨轮暂停）
    run(collector.round_once(ctx, api, parser, state))
    assert parser.visited == ['SKU0'], "已持久化的拦截计数应使本轮直接暂停，不重复访问"


# ---- WB-103-R7 第2条：save/load 失败与损坏不冒充“无暂停” ----

def test_save_challenge_state_returns_true_on_success(tmp_path):
    p = tmp_path / 'c.json'
    assert cs.save_challenge_state({'SKU0': 1}, p) is True, "成功写盘应返回 True"
    assert cs.load_challenge_state(p) == {'SKU0': 1}


def test_save_challenge_state_returns_false_when_parent_is_file(tmp_path):
    # 父路径本身是文件：无法建目录/写盘，必须返回 False（旧实现静默返回 None 冒充成功）
    p = tmp_path / 'afile' / 'c.json'
    p.parent.write_text('i am a file not a dir', encoding='utf-8')
    assert cs.save_challenge_state({'SKU0': 1}, p) is False, "父路径为文件必须返回 False"


def test_save_challenge_state_returns_false_on_write_error(tmp_path):
    # 把目标文件本身设成只读目录名冲突等：用不存在的非法父（已覆盖上条）。再补一个：目标为目录
    p = tmp_path / 'c.json'
    p.mkdir()  # 目标是目录而非文件，os.replace 会失败
    assert cs.save_challenge_state({'SKU0': 1}, p) is False, "目标为目录写盘应失败返回 False"


# ---- WB-103-R7 第3条：recover 显式成功/失败 + 按 profile 隔离 ----

def test_consume_recovery_flag_false_when_flag_unlink_fails(tmp_path):
    # WB-103-R8 第3条：恢复标志删除失败（标志是目录无法 unlink）时必须返回 False，
    # 且**保留磁盘暂停**，不得先清空状态再谎报失败导致丢暂停（旧实现：先清空后删标志，
    # 删标志失败则状态已丢、重启放行）。
    p = tmp_path / 'c.json'
    cs.save_challenge_state({'SKU0': 3}, p)
    flag = cs.recovery_flag_path(None, p)
    flag.mkdir(parents=True, exist_ok=True)  # 标志是目录：unlink 会失败
    assert cs.consume_recovery_flag(p) is False, "标志删除失败必须返回 False（保持暂停）"
    assert cs.load_challenge_state(p) == {'SKU0': 3}, "标志删除失败时不得清空持久化暂停"


def test_consume_recovery_flag_true_only_when_all_succeed(tmp_path):
    p = tmp_path / 'c.json'
    cs.save_challenge_state({'SKU0': 3}, p)
    flag = cs.recovery_flag_path(None, p)
    flag.touch()
    assert cs.consume_recovery_flag(p) is True, "标志删除+状态清空都成功才返回 True"
    assert cs.load_challenge_state(p) == {}, "消费后持久化暂停应被清空"
    assert not flag.exists(), "恢复标志应被消费删除"


def test_recovery_flag_is_profile_scoped_and_deterministic(tmp_path):
    # 不同 profile 的标志命名不同（避免一键误放开其他 profile），且对同 profile 稳定
    p = tmp_path / 'c.json'
    f_a = cs.recovery_flag_path('C:/profiles/A', p)
    f_a2 = cs.recovery_flag_path('C:/profiles/A', p)
    f_b = cs.recovery_flag_path('C:/profiles/B', p)
    assert f_a == f_a2, "同 profile 的标志路径必须稳定一致"
    assert f_a != f_b, "不同 profile 的标志必须隔离，避免误放开"
    assert f_a.name.startswith('clear_challenge.') and f_a.name.endswith('.flag')


def test_write_recovery_flag_and_consume_profile_scoped(tmp_path):
    # GUI 写入 profile 隔离标志；运行中的采集器用同 profile 消费，互不干扰其他 profile
    p = tmp_path / 'c.json'
    flag_a = cs.write_recovery_flag('C:/profiles/A', p)
    assert flag_a is not None and flag_a.exists(), "恢复标志应创建成功"
    # 用不同 profile 消费：A 的标志不应被 B 消费
    assert cs.consume_recovery_flag(p, 'C:/profiles/B') is False, "其他 profile 不得消费本 profile 标志"
    assert cs.consume_recovery_flag(p, 'C:/profiles/A') is True, "本 profile 可正常消费"


def test_write_recovery_flag_failure_returns_none(tmp_path):
    # 父路径为文件：创建失败必须返回 None（GUI 据以提示失败而非谎报成功）
    p = tmp_path / 'afile' / 'c.json'
    p.parent.write_text('file', encoding='utf-8')
    assert cs.write_recovery_flag(None, p) is None, "标志创建失败应返回 None"


# ---- WB-103-R8 第2条：state/恢复/锁统一按 profile 隔离 ----

def test_challenge_state_path_is_profile_scoped_and_deterministic(tmp_path):
    # 不同 profile 的状态文件命名不同（避免 A 误清 B），且同 profile 稳定
    p = tmp_path / 'c.json'
    a = cs.challenge_state_path('C:/profiles/A')
    a2 = cs.challenge_state_path('C:/profiles/A')
    b = cs.challenge_state_path('C:/profiles/B')
    assert a == a2, "同 profile 的状态路径必须稳定一致"
    assert a != b, "不同 profile 的状态文件必须隔离"
    assert a.name.startswith('challenge_state.') and a.name.endswith('.json')


def test_two_profiles_same_sku_independent_pause(tmp_path):
    # 两个 profile 同一 SKU 各有独立暂停计数，互不干扰
    pa = cs.challenge_state_path('C:/profiles/A')
    pb = cs.challenge_state_path('C:/profiles/B')
    cs.save_challenge_state({'SKU0': 1}, pa)
    cs.save_challenge_state({'SKU0': 5}, pb)
    assert cs.load_challenge_state(pa) == {'SKU0': 1}
    assert cs.load_challenge_state(pb) == {'SKU0': 5}


def test_profile_a_recovery_does_not_clear_profile_b(tmp_path):
    # WB-103-R8 第2条核心：profile A 的恢复不得清空 profile B 的暂停
    pa = cs.challenge_state_path('C:/profiles/A')
    pb = cs.challenge_state_path('C:/profiles/B')
    cs.save_challenge_state({'profile-B-sku': 1}, pb)
    cs.write_recovery_flag('C:/profiles/A', pa)
    assert cs.consume_recovery_flag(pa, 'C:/profiles/A') is True, "A 的恢复应成功"
    assert cs.load_challenge_state(pa) == {}, "A 的状态应被清空"
    assert cs.load_challenge_state(pb) == {'profile-B-sku': 1}, "A 的恢复不得清除 B 的暂停"


def test_profile_concurrent_save_no_lost_pause(tmp_path):
    # 不同 profile 并发保存各自独立文件，互不覆盖（旧实现共享固定 tmp/状态文件会互相覆盖）
    import threading
    pa = cs.challenge_state_path('C:/profiles/A')
    pb = cs.challenge_state_path('C:/profiles/B')

    def worker(path, key):
        for i in range(50):
            cs.save_challenge_state({key: i}, path)

    t1 = threading.Thread(target=worker, args=(pa, 'A'))
    t2 = threading.Thread(target=worker, args=(pb, 'B'))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert cs.load_challenge_state(pa) == {'A': 49}, "A 的最后写入不得被 B 覆盖"
    assert cs.load_challenge_state(pb) == {'B': 49}, "B 的最后写入不得被 A 覆盖"


def test_default_path_is_absolute_and_stable():
    # 跨平台路径一致性：default_path 返回绝对路径且对同一环境稳定
    a = cs.default_path()
    b = cs.default_path()
    assert a.is_absolute(), "状态路径必须为绝对路径"
    assert a == b, "default_path 必须稳定一致"
    assert a.name == 'challenge_state.json'


# ---- WB-103-R7 第1条：run 级确定性测试——取消/异常后拦截暂停仍持久化 ----

import sys as _sys  # noqa: E402


class _FakePlaywright:
    """替身：吃掉真实浏览器启动，提供 launch_persistent_context。"""
    def __init__(self):
        self.chromium = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def launch_persistent_context(self, *args, **kwargs):
        return FakeContext()


def _install_fake_playwright(monkeypatch):
    """让 `from playwright.async_api import async_playwright` 拿到替身（不触碰真实 playwright/网络）。

    WB-103-R20 修复：旧实现把 `_sys.modules['playwright']` 直接赋值（未经 monkeypatch 托管），
    测试结束后该假包残留在 sys.modules，导致后续 `import playwright.async_api` 拿到的是假顶层包、
    再取 `.async_api` 属性时抛 `module 'playwright' has no attribute 'async_api'`。
    现改为两个键都用 monkeypatch 托管：测试结束自动还原为进入前状态（真实模块或彻底删除键），
    不论测试以何种顺序运行都不会污染后续用例。
    """
    import types
    fake_pkg = types.ModuleType('playwright')
    fake_pkg.__path__ = []  # 声明为包，确保 `import playwright.async_api` 解析到子模块而非假顶层包
    fake_mod = types.ModuleType('playwright.async_api')
    fake_mod.async_playwright = _FakePlaywright
    monkeypatch.setitem(_sys.modules, 'playwright', fake_pkg)
    monkeypatch.setitem(_sys.modules, 'playwright.async_api', fake_mod)


class _FakeApiRun:
    """run() 启动自检用的极简 Api：只应答 status，不访问站点/凭据。"""
    def __init__(self, base, key):
        self.base = base
        self.key = key

    def call(self, path, body=None):
        if path == '/api/collector/status':
            return {'ready': True}
        raise AssertionError('unexpected path in run-level test: ' + path)


def test_run_persists_challenge_state_on_cancel(monkeypatch):
    # 确定性 run 级复现：round_once 首次确认拦截后立即被取消，run 正常收尾，
    # 暂停状态文件必须存在（旧实现漏存，重启重访已确认拦页面）。WB-103-R7 第1条。
    import tempfile
    tmp = tempfile.mkdtemp()
    tmp_challenge = os.path.join(tmp, 'challenge_state.json')
    # 状态文件按 profile 隔离：让 run() 用本测试的固定路径（而非默认 APP_DIR/<slug>）
    def _csp(profile=None, state_path=None):
        return tmp_challenge
    monkeypatch.setattr(collector, 'challenge_state_path', _csp)

    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(challenged))

    class _FakeApi:
        # 仅应答启动自检 status；不访问站点/凭据/网络
        def __init__(self, base, key):
            self.base = base
            self.key = key

        def call(self, path, body=None):
            if path == '/api/collector/status':
                return {'ready': True}
            raise AssertionError('unexpected path in run-level test: ' + path)

    monkeypatch.setattr(collector, 'Api', _FakeApi)

    async def fake_round_once(context, api, parser, challenged=None, lock=None, persist=None):
        challenged = {} if challenged is None else challenged
        challenged['blocked-sku'] = 1  # 首次确认被拦截
        raise asyncio.CancelledError()  # 模拟用户暂停/取消 或 后续异常

    orig_round = collector.round_once
    collector.round_once = fake_round_once

    class _Args:
        profile = tmp
        login = False
        once = False
        stop_file = None

    saved_env = {}
    for k in ('PP_COLLECTOR_KEY',):
        saved_env[k] = os.environ.get(k)
    os.environ['PP_COLLECTOR_KEY'] = 'TEST_KEY_NO_SITE'
    try:
        run(collector.run(_Args()))
    finally:
        collector.round_once = orig_round
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert os.path.exists(tmp_challenge), "取消/异常后拦截暂停状态文件必须已落盘"
    loaded = cs.load_challenge_state(tmp_challenge)
    assert loaded == {'blocked-sku': 1}, "取消后拦截暂停必须已持久化，避免重启重访"


def test_fake_playwright_install_restores_sys_modules(monkeypatch):
    # WB-103-R20 隔离回归：_install_fake_playwright 必须完全托管 sys.modules，
    # 退出作用域后 'playwright' / 'playwright.async_api' 还原为进入前状态，不得泄漏假包。
    # 旧实现直接赋值 _sys.modules['playwright'] 未托管，导致后续 `import playwright.async_api`
    # 拿到假顶层包、再取 .async_api 属性时抛 "module 'playwright' has no attribute 'async_api'"。
    snapshot = {k: _sys.modules.get(k) for k in ('playwright', 'playwright.async_api')}
    _install_fake_playwright(monkeypatch)
    # 作用域内必须是替身
    assert _sys.modules.get('playwright.async_api') is not None
    assert hasattr(_sys.modules['playwright.async_api'], 'async_playwright')
    monkeypatch.undo()  # 模拟测试结束时的自动清理
    for k in ('playwright', 'playwright.async_api'):
        assert _sys.modules.get(k) == snapshot.get(k), (
            f"sys.modules[{k!r}] 未还原（{snapshot.get(k)} -> {_sys.modules.get(k)}），"
            "会污染后续测试"
        )


# ---- WB-103-R8 第1条：损坏状态禁止领取/访问，且不覆盖损坏原文件 ----

def test_run_refuses_collection_when_state_corrupt(monkeypatch):
    # 确定性 run 级复现：预写损坏状态文件，run 在不可信状态下必须禁止领取/访问
    # （不调用 /api/collector/products、不 scrape），且不得覆盖/擦除损坏原文件
    # （WB-103-R8 第1条）。
    import tempfile
    tmp = tempfile.mkdtemp()
    tmp_challenge = os.path.join(tmp, 'challenge_state.json')
    with open(tmp_challenge, 'w', encoding='utf-8') as f:
        f.write('{not valid json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile=None, state_path=None: tmp_challenge)

    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(ok))

    class _RecordApi:
        """仅应答启动自检 status；记录是否有人调用 products（应被禁止）。"""
        products_called = False

        def __init__(self, base, key):
            pass

        def call(self, path, body=None):
            if path == '/api/collector/products':
                _RecordApi.products_called = True
                return {'items': [{'productId': 'lease-0', 'sku': 'SKU0', 'url': URL}]}
            if path == '/api/collector/status':
                return {'ready': True}
            raise AssertionError('unexpected path in run-level test: ' + path)

    monkeypatch.setattr(collector, 'Api', _RecordApi)

    class _Args:
        profile = tmp
        login = False
        once = True  # 不可信状态 once 直接退出，避免长轮询
        stop_file = None

    saved_env = {}
    for k in ('PP_COLLECTOR_KEY',):
        saved_env[k] = os.environ.get(k)
    os.environ['PP_COLLECTOR_KEY'] = 'TEST_KEY_NO_SITE'
    try:
        run(collector.run(_Args()))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert _RecordApi.products_called is False, "损坏状态必须禁止领取/访问（不得调用 products）"
    with open(tmp_challenge, encoding='utf-8') as f:
        content = f.read()
    assert content.strip() == '{not valid json', "损坏原文件不得被覆盖/擦除"


# ---- WB-103-R8 第4条：首次确认即即时持久化（不等整批 600s） ----

def test_round_once_persists_on_first_intercept():
    # 首次确认被拦截即调用 persist 落盘，不等整批 600s 结束（进程崩溃也不丢暂停）
    api, ctx, parser = FakeApi(items(1)), FakeContext(), FakeParser(challenged)
    lock = asyncio.Lock()
    saved = []
    async def persist(state):
        saved.append(dict(state))
    state = {}
    claimed, successful, blocked = run(
        collector.round_once(ctx, api, parser, state, lock=lock, persist=persist))
    assert (claimed, successful, blocked) == (1, 0, 1)
    assert state.get('SKU0') == 1, "拦截计数应累加"
    assert saved and saved[-1].get('SKU0') == 1, "首次确认拦截应立即持久化"


def test_run_persists_during_round_not_only_on_exit(monkeypatch):
    # 确定性 run 级复现：round_once 首次确认即 persist 后立即被取消（不走到 finally 的整批保存），
    # 但 worker 的即时持久化应已落盘（WB-103-R8 第4条：不等整批 600s）。
    import tempfile
    tmp = tempfile.mkdtemp()
    tmp_challenge = os.path.join(tmp, 'challenge_state.json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile=None, state_path=None: tmp_challenge)

    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(challenged))
    monkeypatch.setattr(collector, 'Api', _FakeApiRun)

    async def fake_round_once(context, api, parser, challenged=None, lock=None, persist=None):
        challenged = {} if challenged is None else challenged
        challenged['blocked-sku'] = 1  # 首次确认被拦截
        if persist is not None:
            await persist(dict(challenged))  # 即时持久化（模拟 worker 行为）
        raise asyncio.CancelledError()  # 随后被取消，不走到 finally 的整批保存

    orig_round = collector.round_once
    collector.round_once = fake_round_once

    class _Args:
        profile = tmp
        login = False
        once = False
        stop_file = None

    saved_env = {}
    for k in ('PP_COLLECTOR_KEY',):
        saved_env[k] = os.environ.get(k)
    os.environ['PP_COLLECTOR_KEY'] = 'TEST_KEY_NO_SITE'
    try:
        run(collector.run(_Args()))
    finally:
        collector.round_once = orig_round
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert os.path.exists(tmp_challenge), "即时持久化应已落盘（不依赖 finally 整批保存）"
    loaded = cs.load_challenge_state(tmp_challenge)
    assert loaded == {'blocked-sku': 1}, "取消前的即时持久化必须保留拦截暂停"


# ---- WB-103-R9 第1条：损坏状态 + GUI 恢复标志，不可信状态仍允许明确人工恢复 ----

def test_run_recovers_from_corrupt_state_via_gui_flag(monkeypatch):
    # 确定性 run 级复现：预写损坏状态文件并写入 GUI 恢复标志，run 在不可信状态下必须
    # 仍消费该标志（删除损坏文件 + 标志），清空暂停并恢复采集（WB-103-R9 第1条）。
    import tempfile
    tmp = tempfile.mkdtemp()
    tmp_challenge = os.path.join(tmp, 'challenge_state.json')
    with open(tmp_challenge, 'w', encoding='utf-8') as f:
        f.write('{not valid json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile=None, state_path=None: tmp_challenge)

    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(ok))

    # GUI「恢复被拦截商品」写入恢复标志（与 run 内 consume 同 profile/state_path 才匹配）
    flag = cs.write_recovery_flag(tmp, tmp_challenge)
    assert flag is not None and flag.exists(), "恢复标志应已写入"

    class _Api:
        products_called = False

        def __init__(self, base, key):
            pass

        def call(self, path, body=None):
            if path == '/api/collector/products':
                _Api.products_called = True
                return {'items': [{'productId': 'lease-0', 'sku': 'SKU0', 'url': URL}]}
            if path == '/api/collector/status':
                return {'ready': True}
            if path == '/api/collector/release':
                return {}
            raise AssertionError('unexpected path in run-level test: ' + path)

    monkeypatch.setattr(collector, 'Api', _Api)

    class _Args:
        profile = tmp
        login = False
        once = True  # 不可信状态 + 恢复标志：消费后应恢复采集一轮再退出
        stop_file = None

    saved_env = {}
    for k in ('PP_COLLECTOR_KEY',):
        saved_env[k] = os.environ.get(k)
    os.environ['PP_COLLECTOR_KEY'] = 'TEST_KEY_NO_SITE'
    try:
        run(collector.run(_Args()))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        # 不可信状态下恢复标志必须被消费（不再永远不消费）
    assert not flag.exists(), "不可信状态下恢复标志必须被消费（删除）"
    # 显式恢复后损坏原文件不得再以损坏内容残留：应被删后重写为干净空暂停状态（{}）
    assert cs.load_challenge_state(tmp_challenge) == {}, "显式恢复后损坏文件须不再存在/已重写为干净空暂停"
    # 恢复后必须已恢复采集（领取被调用）
    assert _Api.products_called is True, "显式恢复后必须恢复采集（调用 products）"


# ---- WB-103-R9 第2条：首次 persist 落盘失败立即进入持久化不可信，停止新增领取/访问 ----

def test_run_stops_claims_when_first_persist_fails(monkeypatch):
    # 确定性 run 级复现：monkeypatch save_challenge_state 返回 False（模拟写盘失败）。
    # 多商品批次首个被拦即 persist 失败 → 立即进入持久化不可信，状态置 challenge_state_unreliable，
    # 不再把「未保存」当作成功继续整轮（WB-103-R9 第2条）。
    import json as _json
    import tempfile
    from pathlib import Path as _Path
    tmp = tempfile.mkdtemp()
    tmp_challenge = os.path.join(tmp, 'challenge_state.json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile=None, state_path=None: tmp_challenge)
    monkeypatch.setattr(collector, 'STATUS_FILE', _Path(tmp) / 'collector_status.json')
    calls = {'n': 0}

    def fake_save(state, path=None, profile=None):
        calls['n'] += 1
        return False  # 模拟写盘失败

    monkeypatch.setattr(collector, 'save_challenge_state', fake_save)

    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(challenged))

    class _Api:
        def __init__(self, base, key):
            pass

        def call(self, path, body=None):
            if path == '/api/collector/status':
                return {'ready': True}
            if path == '/api/collector/products':
                return {'items': [
                    {'productId': 'l0', 'sku': 'SKU0', 'url': URL},
                    {'productId': 'l1', 'sku': 'SKU1', 'url': URL},
                    {'productId': 'l2', 'sku': 'SKU2', 'url': URL},
                ]}
            if path == '/api/collector/release':
                return {}
            raise AssertionError('unexpected path in run-level test: ' + path)

    monkeypatch.setattr(collector, 'Api', _Api)

    class _Args:
        profile = tmp
        login = False
        once = True
        stop_file = None

    saved_env = {}
    for k in ('PP_COLLECTOR_KEY',):
        saved_env[k] = os.environ.get(k)
    os.environ['PP_COLLECTOR_KEY'] = 'TEST_KEY_NO_SITE'
    try:
        run(collector.run(_Args()))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert calls['n'] >= 1, "首次确认拦截应尝试 persist（落盘）"
    # 持久化不可信：状态必须为 challenge_state_unreliable，且不可信态不得继续写盘冒充成功
    status = _json.loads((_Path(tmp) / 'collector_status.json').read_text(encoding='utf-8'))
    assert status.get('state') == 'challenge_state_unreliable', \
        "持久化失败应进入 challenge_state_unreliable，实际：" + str(status.get('state'))
    # 写盘失败不应把状态文件写成有效暂停
    assert not os.path.exists(tmp_challenge), "写盘失败不应生成有效暂停状态文件"


# ---- WB-103-R9 第3条：GUI 状态映射须识别 challenge_state_unreliable（显示需人工恢复，非启动中） ----

def test_status_view_maps_challenge_state_unreliable():
    # classify_state 必须把 challenge_state_unreliable 映射为「需人工核验」文案，
    # 而非默认的「启动中…」（WB-103-R9 第3条）。
    from status_view import classify_state, STATE_VIEW
    assert 'challenge_state_unreliable' in STATE_VIEW, "STATE_VIEW 应含 challenge_state_unreliable"
    now = datetime.now(timezone.utc)
    data = {'pid': 1234, 'run_id': '', 'ts': now.isoformat(), 'round': 3,
            'state': 'challenge_state_unreliable', 'paused': 2}
    text, color, rnd, run_id = classify_state(data, 1234, 0, '', now=now)
    assert ('人工核验' in text or '不可信' in text), f"应显示需人工核验，实际：{text}"
    assert color != '#0066cc', "不应显示为启动中的蓝色"


try:
    import tkinter as _tk  # noqa: F401
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False


import pytest as _pytest


def test_gui_refresh_state_shows_challenge_unreliable(monkeypatch, tmp_path):
    # 真实 refresh_state 调用须反映 challenge_state_unreliable（WB-103-R9 第3条）。
    import json as _json
    import gui as gui_mod
    from types import SimpleNamespace
    monkeypatch.setattr(gui_mod, 'STATE', tmp_path)
    monkeypatch.setattr(gui_mod, 'default_path', lambda: tmp_path / 'challenge_state.json')
    monkeypatch.setattr(gui_mod, 'write_recovery_flag', lambda profile=None, state_path=None: None)
    class Value:
        def set(self, value): self.value=value
        def get(self): return self.value
        def configure(self, **kwargs): pass
    app = gui_mod.App.__new__(gui_mod.App)
    app._last_run='this-run';app._last_round=0
    app.state=Value();app.dot=Value();app.state_label=Value()
    status = {'pid':999,'run_id':'this-run','ts':datetime.now(timezone.utc).isoformat(),
              'round':1,'state':'challenge_state_unreliable'}
    (tmp_path/'collector_status.json').write_text(_json.dumps(status),encoding='utf-8')
    app.process=SimpleNamespace(pid=999,poll=lambda:None)
    app.refresh_state()
    assert '不可信' in app.state.get()

def test_persist_failure_prevents_queued_scrapes(monkeypatch):
    import collector
    from challenge_state import ChallengeStateError
    visited=[]
    async def fake_scrape(context,item,parser):
        visited.append(item['sku'])
        return {'sku':item['sku'],'productId':item['productId'],'error':'site_challenge'}
    async def persist(state):
        raise ChallengeStateError('controlled failure')
    monkeypatch.setattr(collector,'scrape',fake_scrape)
    async def scenario():
        with pytest.raises(ChallengeStateError):
            await collector.round_once(None,FakeApi(items(4)),None,{},asyncio.Lock(),persist)
    asyncio.run(scenario())
    assert visited==['SKU0']




