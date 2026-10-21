"""采购记录的 recorded_at 必须按北京时间落库。

`recorded_at` 是给用户看的「采购日期」：前端直接取前 10 位当日期显示
（Procurement.tsx / PipelinePlan.tsx 都是 `.slice(0, 10)`），库存对账的
`start_date` 也拿它做字符串比较。原来用 `datetime.now()`——容器时区是 UTC，
北京时间 00:00–08:00 采购的记录会被写成前一天，账期整体错位一天。
"""
import inspect
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Product
from api.procurement_routes import (
    ProcurementRecordCreate,
    _now_str,
    create_procurement_record,
    list_procurement_records,
)

BEIJING = timedelta(hours=8)


def _beijing_now() -> datetime:
    """北京时间的 naive 表示（与 recorded_at 的存储形态一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) + BEIJING


def test_now_str_is_beijing_time_not_container_time():
    parsed = datetime.strptime(_now_str(), '%Y-%m-%d %H:%M:%S')
    drift = abs((parsed - _beijing_now()).total_seconds())
    # 差 8 小时 = 还在用服务器本地时间（UTC 容器）；这里只允许秒级误差。
    assert drift < 60, f'recorded_at 不是北京时间：{parsed} vs {_beijing_now()}'
    # 上面那条只在 UTC 机器上才抓得到问题，开发机多半就是东八区。这里再钉死实现：
    # 不许出现不带时区的 `datetime.now()`——它的结果取决于容器 TZ，等于把
    # 「今天是哪天」交给部署环境决定。
    source = inspect.getsource(_now_str)
    # 注释/文档里提到旧写法不算，只看真正执行的代码。
    code = re.sub(r'""".*?"""', '', source, flags=re.S)
    assert not re.search(r'datetime\.now\(\s*\)', code), \
        'recorded_at 不能依赖容器本地时区（裸 datetime.now()）'
    assert 'timedelta(hours=8)' in code, 'recorded_at 必须显式按 UTC+8 生成'


def test_created_record_uses_beijing_date_even_near_midnight():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Product(id='p', store_id='a', product_name='p', product_no=1, link_status='已购买'))
        db.commit()
        record = create_procurement_record(
            ProcurementRecordCreate(product_id='p', quantity=10, total_amount=100),
            db, 'a',
        )
        assert record.recorded_at is not None
        assert record.recorded_at[:10] == _beijing_now().date().isoformat()
        # 列表接口原样带出，前端 slice(0,10) 才拿得到同一天
        listed = list_procurement_records(db, 'a')
        assert listed[0].recorded_at[:10] == record.recorded_at[:10]
