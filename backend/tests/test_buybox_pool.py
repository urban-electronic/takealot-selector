"""常驻采集池与卡死自愈：线程数封顶、放弃卡死任务时释放浏览器、行情过期指标。

这些是 2026-11-05 纯云端单通道改造的回归测试。焦点不是「采集成功」，
而是「某一路悄悄卡住时，整条循环还能继续跑、并且界面看得见」。
"""
import json
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import SystemSettings, DEFAULT_STORE_ID
from services import buybox_monitor as monitor
from services import quote_browser


def _fresh_db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _reset_pool_state():
    monitor._pool = None
    monitor._pool_dirty = False
    monitor._pool_resets = 0


def test_pool_is_reused_across_rounds_and_hard_caps_threads():
    """每轮取到同一个池：线程数不再随轮次线性增长（can't start new thread 的根因）。"""
    _reset_pool_state()
    try:
        pool = monitor._get_pool()
        assert monitor._get_pool() is pool
        assert pool._max_workers == monitor.COLLECT_WORKERS
        assert monitor._pool_resets == 0
    finally:
        pool.shutdown(wait=False)
        _reset_pool_state()


def test_dirty_pool_is_rebuilt_without_blocking_on_stuck_threads():
    """放弃卡死任务后下一轮重建池；旧池用 wait=False，循环不阻塞。"""
    _reset_pool_state()
    old = monitor._get_pool()
    try:
        monitor._mark_pool_dirty()
        new = monitor._get_pool()
        assert new is not old
        assert monitor._pool_resets == 1
        assert monitor._pool_dirty is False
        assert monitor._get_pool() is new
    finally:
        old.shutdown(wait=False)
        new.shutdown(wait=False)
        _reset_pool_state()


def test_worker_restart_does_not_inherit_a_stuck_pool(monkeypatch):
    """worker 重启必须拿干净池：旧池里若有卡死线程，沿用会让新 worker 立刻停采。

    同时覆盖 test_loop_status 那类模拟：每次 start_worker() 都重新创建池，
    不能跨 worker 生命周期复用同一个全局实例。
    """
    _reset_pool_state()
    stale = monitor._get_pool()

    class FakeThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            pass
        def is_alive(self):
            return False

    monkeypatch.setattr(monitor.threading, 'Thread', FakeThread)
    monkeypatch.setattr(monitor, '_thread', None)
    monkeypatch.setattr(monitor, '_supervisor', None)
    monkeypatch.setattr(monitor, '_stop', threading.Event())
    try:
        monitor.start_worker()
        assert monitor._pool is None, '新 worker 不得继承旧池里可能卡死的线程'
        assert monitor._pool_resets == 0, '正常启动不计入看门狗重建次数'
        assert monitor._pool_dirty is False
    finally:
        stale.shutdown(wait=False)
        _reset_pool_state()


def test_abandoning_stuck_job_kills_browser_and_marks_pool_dirty(monkeypatch):
    """看门狗放弃卡死任务时，必须杀浏览器（否则线程永不归还 _lock）并标记池重建。"""
    import concurrent.futures

    factory = _fresh_db()
    key = monitor.PREFIX + DEFAULT_STORE_ID + ':p:1'
    with factory() as db:
        db.add(SystemSettings(key=key, value=json.dumps({
            'product_id': 'p', 'offer_id': '1', 'sku': 'SKU-1',
            'enabled': True, 'status': 'running', 'next_due': 0,
            'interval_minutes': 30, 'failures': 0,
        })))
        db.add(SystemSettings(key=monitor.WORKER_STATE, value=json.dumps({'cycles': 1})))
        db.commit()

    killed = []
    monkeypatch.setattr(quote_browser, 'kill_current_browser', lambda: killed.append(1) or True)
    # wait() 默认阻塞 10 秒；这里只关心超时分支的判定，直接返回「没有任务完成」。
    monkeypatch.setattr(monitor, 'wait', lambda pending, **kwargs: (set(), set(pending)))
    _reset_pool_state()

    # 一个永不完成的 future，started 时间远超 JOB_TIMEOUT。
    future = concurrent.futures.Future()
    started = {future: time.time() - monitor.JOB_TIMEOUT - 1}
    owners = {future: key}

    done, remaining = monitor._wait_with_heartbeat({future}, factory, started, owners)

    assert done == set() and remaining == set(), '卡死任务应从等待集合里移除'
    assert killed == [1], '必须终止浏览器进程树，卡住的线程才可能退出'
    assert monitor._pool_dirty is True, '下一轮应重建池，隔离取消不掉的工作项'

    with factory() as db:
        value = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
    assert value['status'] == 'error' and value['error_code'] == 'collect_timeout'
    assert 'diagnostics' not in value
    _reset_pool_state()


def test_killing_browser_never_hits_application_or_unrelated_browser(monkeypatch):
    """只杀 --user-data-dir 指向当前采集 profile 的进程；无飞行浏览器时 no-op。"""
    quote_browser._set_current_browser_profile(None)
    assert quote_browser.kill_current_browser() is False

    monkeypatch.setattr(quote_browser, '_proc_entries', lambda: ['1', '2', '3'])
    monkeypatch.setattr(quote_browser, '_pids_by_profile', lambda profile: [2])
    monkeypatch.setattr(quote_browser, '_child_pids', lambda pid, entries=None: [3])
    sent = []
    monkeypatch.setattr(quote_browser.os, 'kill', lambda pid, sig: sent.append(pid))

    quote_browser._set_current_browser_profile('/tmp/buybox_browser_profile')
    assert quote_browser.kill_current_browser() is True
    assert set(sent) == {2, 3}, '不能碰应用自身进程(pid 1)或 PID 的祖先'
    quote_browser._set_current_browser_profile(None)


def test_browser_profile_cleared_when_context_exits(monkeypatch):
    """上下文退出后清 profile，避免看门狗误杀后续正常采集的浏览器。"""
    quote_browser._set_current_browser_profile('/tmp/profile')
    assert quote_browser.current_browser_profile() == '/tmp/profile'
    quote_browser._set_current_browser_profile(None)
    assert quote_browser.current_browser_profile() is None


def test_loop_status_counts_stale_skus_and_pool_resets():
    """纯云端单通道：某个启用中的 SKU 长期没行情，页头必须能看见。"""
    factory = _fresh_db()
    now = time.time()
    old = datetime.fromtimestamp(now - 10 * 3600, timezone.utc).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    with factory() as db:
        # 过期：10 小时前成功，间隔 30 分钟（阈值 3 小时）
        db.add(SystemSettings(key=prefix + 'p1:o1', value=json.dumps({
            'sku': 'STALE-1', 'enabled': True, 'status': 'success',
            'interval_minutes': 30, 'last_success_at': old, 'next_due': now + 60})))
        # 从没成功过：也要点名
        db.add(SystemSettings(key=prefix + 'p2:o2', value=json.dumps({
            'sku': 'NEVER-1', 'enabled': True, 'status': 'queued',
            'interval_minutes': 30, 'next_due': now + 60})))
        # 新鲜：不该计入
        db.add(SystemSettings(key=prefix + 'p3:o3', value=json.dumps({
            'sku': 'FRESH-1', 'enabled': True, 'status': 'success',
            'interval_minutes': 30, 'last_success_at': fresh, 'next_due': now + 60})))
        # 已暂停：不该计入
        db.add(SystemSettings(key=prefix + 'p4:o4', value=json.dumps({
            'sku': 'PAUSED-1', 'enabled': False, 'status': 'paused',
            'interval_minutes': 30, 'last_success_at': old})))
        db.commit()

    _reset_pool_state()
    with factory() as db:
        status = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert status['stale_count'] == 2
    assert set(status['stale_skus']) == {'STALE-1', 'NEVER-1'}
    assert status['pool_resets'] == 0
    assert status['enabled'] == 3
