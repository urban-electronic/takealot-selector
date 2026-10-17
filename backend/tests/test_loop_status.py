"""采集循环心跳的对外读数。这些数字直接印在界面上，错一位就是误导。"""
import json
import time
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
