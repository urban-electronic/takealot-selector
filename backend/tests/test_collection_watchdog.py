"""采集看门狗：卡死的任务不能把整条循环冻住。

2026-10-20 线上事故：一个采集任务卡在浏览器里没返回，循环在
`_wait_with_heartbeat` 里无限等待，而等待期间心跳一直在刷新——界面显示
「采集循环运行中」，实际 17 分钟没开新一轮、26 个 SKU 排着队没动。
这些用例锁的就是那条路径：到点必须放弃、必须记账、必须能继续往下走。
"""
import asyncio
import json
import time
from concurrent.futures import Future

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import SystemSettings, DEFAULT_STORE_ID
from services import buybox_monitor as monitor
from services import quote_browser as qb


def make_db(values):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for key, value in values.items():
            db.add(SystemSettings(key=key, value=json.dumps(value)))
        db.commit()
    return factory


def test_stuck_job_is_abandoned_and_recorded_instead_of_waited_forever(monkeypatch):
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    key = prefix + 'p:1'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': time.time(), 'cycles': 7},
        key: {'enabled': True, 'status': 'running', 'next_due': 0, 'interval_minutes': 5, 'failures': 0},
    })
    # wait() 正常要阻塞 10 秒；这里直接返回「一个都没完成」，让看门狗自己判断超时。
    monkeypatch.setattr(monitor, 'wait', lambda pending, timeout, return_when: (set(), set(pending)))
    monkeypatch.setattr(monitor, 'JOB_TIMEOUT', 0.01)

    hung = Future()                     # 永远不会 set_result 的任务
    started = {hung: time.time() - 60}  # 已经跑了 60 秒
    done, pending = monitor._wait_with_heartbeat({hung}, factory, started, {hung: key})

    assert done == set() and pending == set()   # 放弃，不再等
    with factory() as db:
        value = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
        state = json.loads(db.query(SystemSettings).filter_by(key=monitor.WORKER_STATE).first().value)
    assert value['status'] == 'error' and value['error_code'] == 'collect_timeout'
    assert '浏览器卡住' in value['error'] and value['failures'] == 1
    assert value['next_due'] > time.time()      # 排到下一轮，不会立刻再撞一次
    assert state['stuck_jobs'] == 1 and state['stuck_at']


def test_abandoned_job_drops_the_previous_round_diagnostics(monkeypatch):
    """放弃的任务不能把上一轮的计数挂在自己名下。

    旧 diagnostics 属于上一轮（可能是成功的那一轮）。留着它，「复制诊断」就会
    带出 buybox_cards=0 / price_nodes=12 这类数字，看起来像是这一轮量到的，
    于是有人去查解析锚点——而真正的问题是浏览器没返回。
    """
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    key = prefix + 'p:1'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': time.time()},
        key: {'enabled': True, 'status': 'running', 'next_due': 0, 'interval_minutes': 5,
              'diagnostics': {'http_status': 200, 'buybox_cards': 0, 'price_nodes': 12},
              'snapshot': {'offers': []}},
    })
    monkeypatch.setattr(monitor, 'wait', lambda pending, timeout, return_when: (set(), set(pending)))
    monkeypatch.setattr(monitor, 'JOB_TIMEOUT', 0.01)

    hung = Future()
    monitor._wait_with_heartbeat({hung}, factory, {hung: time.time() - 60}, {hung: key})

    with factory() as db:
        value = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
    assert value['error_code'] == 'collect_timeout'
    assert 'diagnostics' not in value          # 旧计数不许冒充这一轮的结果
    assert 'snapshot' in value                 # 上一份报价快照仍然保留，不删数据


def test_null_interval_does_not_break_the_next_due(monkeypatch):
    """interval_minutes 是 null 时不能 `None * 60`。

    这段算 next_due 的代码在 `process()` 的 finally 里：抛出去就整轮采集中断，
    而这一行因为 next_due 没被推进会被每轮重新选中、每轮再抛一次——一条脏记录
    足以让整批 SKU 永久停采，日志里只有一句通用的「worker unavailable」。
    """
    assert monitor.interval_seconds(None) == 300
    assert monitor.interval_seconds(30) == 1800
    assert monitor.interval_seconds(True) == 300      # bool 不是间隔
    assert monitor.interval_seconds(0) == 300         # 0 会让记录每轮都到期
    assert monitor.interval_seconds('5') == 300       # 字符串不是整数


def test_healthy_job_is_not_mistaken_for_a_stuck_one(monkeypatch):
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    key = prefix + 'p:1'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': time.time()},
        key: {'enabled': True, 'status': 'running', 'next_due': 0, 'interval_minutes': 5},
    })
    monkeypatch.setattr(monitor, 'wait', lambda pending, timeout, return_when: (set(pending), set()))
    monkeypatch.setattr(monitor, 'JOB_TIMEOUT', 3600)

    finished = Future(); finished.set_result('ok')
    done, pending = monitor._wait_with_heartbeat({finished}, factory, {finished: time.time()}, {finished: key})
    assert done == {finished} and pending == set()
    with factory() as db:
        value = json.loads(db.query(SystemSettings).filter_by(key=key).first().value)
    assert value['status'] == 'running'          # 正常完成的任务不改写记录


def test_busy_browser_raises_immediately_instead_of_queueing_forever(monkeypatch):
    monkeypatch.setattr(qb, 'LOCK_WAIT_SECONDS', 0.05)
    assert qb._lock.acquire(blocking=False)
    try:
        async def use():
            async with qb.quote_context():
                raise AssertionError('不该拿到浏览器')
        with pytest.raises(qb.CollectorBusy) as exc:
            asyncio.run(use())
        assert '占用超过' in str(exc.value)
    finally:
        qb._lock.release()
    # 超时没拿到锁时绝不能去 release 别人的锁，否则第二个任务会并发开浏览器。
    assert qb._lock.acquire(blocking=False)
    qb._lock.release()


def test_loop_status_exposes_cycle_age_and_stuck_count():
    now = time.time()
    factory = make_db({
        monitor.WORKER_STATE: {
            'alive_at': now,
            'cycles': 1995,
            'last_cycle_at': monitor.datetime.fromtimestamp(now - 900, monitor.timezone.utc).isoformat(),
            'stuck_at': monitor.datetime.fromtimestamp(now - 600, monitor.timezone.utc).isoformat(),
            'stuck_jobs': 2,
        },
    })
    with factory() as db:
        state = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert state['worker_alive'] is True       # 心跳新鲜，但…
    assert 895 <= state['cycle_age_seconds'] <= 905   # …上一轮已经是 15 分钟前
    assert state['stuck_jobs'] == 2


def test_a_new_cycle_clears_the_previous_stuck_marker():
    """开新一轮就要把上一轮的「已放弃」清掉。

    这两个字段原来只写不清，于是第一次超时之后 stuck_jobs 永久 >0：界面上
    「卡住的采集任务已被放弃」这句话会挂到之后每一次慢轮次上——包括那种
    「一轮本来就排不下、一个任务都没超时」的正常情况。历史现场不是当前状态。
    """
    now = time.time()
    factory = make_db({
        monitor.WORKER_STATE: {
            'alive_at': now, 'cycles': 12,
            'last_cycle_at': monitor.datetime.fromtimestamp(now - 3600, monitor.timezone.utc).isoformat(),
            'stuck_at': monitor.datetime.fromtimestamp(now - 3600, monitor.timezone.utc).isoformat(),
            'stuck_jobs': 2,
        },
    })
    with factory() as db:
        monitor._record_cycle(db, 0, None)
    with factory() as db:
        state = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert state['stuck_jobs'] == 0, '新一轮开始后不得再声称「上一轮放弃过任务」'
    assert state['stuck_at'] is None
    assert state['cycles'] == 13        # 心跳照常计数，清理只针对这两个字段
    assert 0 <= state['cycle_age_seconds'] <= 5


def test_the_current_cycle_still_records_its_own_abandonment(monkeypatch):
    """清理只发生在「开新一轮」时：同一轮里刚放弃的任务必须留着，否则界面看不见。

    `_record_cycle` 在循环开头调用、`_record_stuck` 在本轮等待超时时调用，
    顺序决定了这条清理不会抹掉本轮自己的结论。
    """
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    key = prefix + 'p:1'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': time.time()},
        key: {'enabled': True, 'status': 'running', 'next_due': 0, 'interval_minutes': 5},
    })
    with factory() as db:
        monitor._record_cycle(db, 1, None)
    monkeypatch.setattr(monitor, 'wait', lambda pending, timeout, return_when: (set(), set(pending)))
    monkeypatch.setattr(monitor, 'JOB_TIMEOUT', 0.01)
    hung = Future()
    monitor._wait_with_heartbeat({hung}, factory, {hung: time.time() - 60}, {hung: key})
    with factory() as db:
        state = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert state['stuck_jobs'] == 1 and state['stuck_at']
