import json
import time

from models import SystemSettings, DEFAULT_STORE_ID
from services.buybox_monitor import MIN_INTERVAL_MINUTES, PREFIX
from services.monitor_interval_normalize import normalize_monitor_intervals
from tests.test_pricing_routes import database


def monitor_key(sku):
    return f'{PREFIX}{DEFAULT_STORE_ID}:{sku}:{sku}'


def read(db, sku):
    return json.loads(db.query(SystemSettings).filter_by(key=monitor_key(sku)).first().value)


def seed(db, row_sku, **fields):
    db.add(SystemSettings(key=monitor_key(row_sku), value=json.dumps(fields, ensure_ascii=False)))
    db.commit()


def test_raises_historical_five_minute_rows_to_the_floor():
    """线上 39 条启用记录存的是当年选的 5 分钟，代码下限改不动它，只能归一。"""
    with database() as db:
        seed(db, 'five', interval_minutes=5, enabled=True, status='success', next_due=time.time() + 1200)
        seed(db, 'ten', interval_minutes=10, enabled=True)
        stats = normalize_monitor_intervals(db)
        assert stats['changed'] == 2
        assert read(db, 'five')['interval_minutes'] == MIN_INTERVAL_MINUTES == 30
        assert read(db, 'ten')['interval_minutes'] == 30


def test_does_not_shrink_user_chosen_slower_intervals():
    """60/120 是用户主动选的，不能被"归一"抹平回 30。"""
    with database() as db:
        seed(db, 'hour', interval_minutes=60, enabled=True)
        seed(db, 'twohour', interval_minutes=120, enabled=True)
        stats = normalize_monitor_intervals(db)
        assert stats['changed'] == 0 and stats['already'] == 2
        assert read(db, 'hour')['interval_minutes'] == 60
        assert read(db, 'twohour')['interval_minutes'] == 120


def test_leaves_scheduling_and_business_fields_untouched():
    """只改 interval_minutes：动 next_due 会让全部 SKU 启动瞬间一起到期。"""
    with database() as db:
        due = time.time() + 1200
        seed(db, 'keep', interval_minutes=5, enabled=True, status='success', next_due=due,
             revision=7, last_success_at='2026-11-06T10:00:00+00:00', last_result='成功', sku='K-1')
        normalize_monitor_intervals(db)
        value = read(db, 'keep')
        assert value['interval_minutes'] == 30
        assert value['next_due'] == due and value['status'] == 'success'
        assert value['revision'] == 7 and value['enabled'] is True
        assert value['last_success_at'] == '2026-11-06T10:00:00+00:00'
        assert value['last_result'] == '成功' and value['sku'] == 'K-1'


def test_is_idempotent_and_only_touches_monitor_records():
    """重复执行零副作用；别的 SystemSettings（调价规则/租约/配置）一条不许碰。"""
    with database() as db:
        seed(db, 'five', interval_minutes=5)
        db.add(SystemSettings(key='_pricing_rule:x:y', value=json.dumps({'interval_minutes': 5})))
        db.add(SystemSettings(key='_buybox_collector_claim:t', value=json.dumps({'interval_minutes': 5})))
        db.commit()
        first = normalize_monitor_intervals(db)
        assert first['changed'] == 1
        snapshot = {r.key: r.value for r in db.query(SystemSettings).all()}
        second = normalize_monitor_intervals(db)
        assert second['changed'] == 0
        assert {r.key: r.value for r in db.query(SystemSettings).all()} == snapshot
        assert json.loads(snapshot['_pricing_rule:x:y'])['interval_minutes'] == 5
        assert json.loads(snapshot['_buybox_collector_claim:t'])['interval_minutes'] == 5


def test_missing_or_broken_values_are_counted_not_guessed():
    """null / 缺失不写库（读路径按 30 兜底），坏 JSON 不覆盖、不抛错。"""
    with database() as db:
        seed(db, 'null', interval_minutes=None, enabled=True)
        seed(db, 'absent', enabled=True)
        db.add(SystemSettings(key=monitor_key('broken'), value='{not json'))
        db.commit()
        stats = normalize_monitor_intervals(db)
        assert stats['changed'] == 0
        assert stats['unset'] == 2 and stats['invalid'] == 1
        assert read(db, 'null')['interval_minutes'] is None
        assert 'interval_minutes' not in read(db, 'absent')
        assert db.query(SystemSettings).filter_by(key=monitor_key('broken')).first().value == '{not json'


def test_reports_rewritten_samples_for_the_startup_log():
    with database() as db:
        seed(db, 'five', interval_minutes=5)
        stats = normalize_monitor_intervals(db)
        assert stats['scanned'] >= 1
        assert stats['samples'][0]['from'] == 5 and stats['samples'][0]['to'] == 30
        assert stats['samples'][0]['key'] == monitor_key('five')
