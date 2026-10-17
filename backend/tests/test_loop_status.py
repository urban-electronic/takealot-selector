"""采集循环心跳的对外读数。这些数字直接印在界面上，错一位就是误导。"""
import json
import time
import threading
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import SystemSettings, DEFAULT_STORE_ID
from services import buybox_monitor as monitor


def make_db(values):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for key, value in values.items():
            db.add(SystemSettings(key=key, value=json.dumps(value)))
        db.commit()
    return factory


def test_due_zero_is_a_sentinel_not_a_1970_timestamp():
    """next_due=0 表示「已到期」，不能当成时间戳参与最早排期的计算。

    真实事故：17 条排队中的 SKU next_due=0，循环状态因此显示
    「下一轮 已延迟 2987107 分钟」——把 0 减 now 直接印到了界面上。
    """
    now = time.time()
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': now, 'cycles': 42},
        prefix + 'p:1': {'enabled': True, 'next_due': 0, 'status': 'queued', 'interval_minutes': 5},
        prefix + 'p:2': {'enabled': True, 'next_due': now + 240, 'status': 'success', 'interval_minutes': 5},
    })
    with factory() as db:
        state = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert 200 <= state['next_due_in'] <= 260, state['next_due_in']
    assert state['due_now'] == 1          # 0 仍然算「已到期」
    assert state['running'] == 1 and state['success'] == 1


def test_all_rows_unscheduled_reports_no_countdown_instead_of_huge_negative():
    now = time.time()
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    factory = make_db({
        monitor.WORKER_STATE: {'alive_at': now},
        prefix + 'p:1': {'enabled': True, 'next_due': 0, 'status': 'queued'},
    })
    with factory() as db:
        state = monitor.loop_status(db, DEFAULT_STORE_ID)
    assert state['next_due_in'] is None


@pytest.mark.parametrize('jobs', [1, 3])
def test_long_batch_keeps_heartbeat_without_inventing_cycles(monkeypatch, jobs):
    """Run the actual worker loop with controlled futures/clock, never a browser."""
    import database
    from api import collector_routes
    clock = [1000.0]
    monkeypatch.setattr(monitor.time, 'time', lambda: clock[0])
    prefix = monitor.PREFIX + DEFAULT_STORE_ID + ':'
    factory = make_db({prefix + f'p:{n}': {'enabled': True, 'next_due': 0}
                       for n in range(jobs)})
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(collector_routes, 'connected', lambda db: False)
    observations = []
    submitted = []
    stop = threading.Event()
    monkeypatch.setattr(monitor, '_stop', stop)
    monkeypatch.setattr(monitor, '_thread', None)
    monkeypatch.setattr(monitor, '_supervisor', None)
    monkeypatch.setattr(monitor, '_stopped', True)

    def observe():
        with factory() as db:
            observations.append(monitor.loop_status(db)['worker_alive'])

    class Job:
        def __init__(self):
            self.ready = clock[0] + 120
        def result(self):
            # Old code blocks here without refreshing the heartbeat.
            clock[0] = max(clock[0], self.ready)
            observe()
            if len(submitted) == jobs:
                stop.set()

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs['max_workers'] == 2
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, key):
            job = Job()
            submitted.append(job)
            return job

    def controlled_wait(pending, timeout, return_when):
        assert 0 < timeout <= 10
        clock[0] += timeout
        observe()
        done = {job for job in pending if job.ready <= clock[0]}
        return done, set(pending) - done

    class Thread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self):
            if self.target.__name__ == 'run': self.target()
        def is_alive(self): return False

    monkeypatch.setattr(monitor, 'ThreadPoolExecutor', Pool)
    monkeypatch.setattr(monitor, 'wait', controlled_wait)
    monkeypatch.setattr(monitor.threading, 'Thread', Thread)
    # The worker's short scheduling waits advance virtual time, not wall time.
    monkeypatch.setattr(stop, 'wait', lambda seconds: (clock.__setitem__(0, clock[0] + seconds), stop.is_set())[1])
    monitor.start_worker()
    assert len(submitted) == jobs
    assert observations and all(observations), observations
    with factory() as db:
        state = monitor.loop_status(db)
        assert state['cycles'] == 1
        assert state['last_jobs'] == jobs
        clock[0] += 61
        assert monitor.loop_status(db)['worker_alive'] is False
