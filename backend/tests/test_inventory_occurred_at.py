# -*- coding: utf-8 -*-
"""库存调整的 `occurred_at` 必须按业务时区（UTC+8）落库，不是容器日期。

`occurred_at` 就是库存调整列表里的「日期」列（Inventory.tsx 直接显示它），
也是盘点/撤销对账的依据。原来用 `dt.date.today()`：容器时区是 UTC，北京时间
00:00–08:00 的操作会被记成**前一天**，与采购记录的 `recorded_at`（PROC-TIME-01）
是同一个坑，只是当时只修了采购那边。

开发机本身就是东八区，`date.today()` 和北京时间永远相等 —— 所以除了行为断言，
还要钉死实现里不许再出现容器日期（否则在 UTC 容器上撤回修复也不会转红）。
"""

import inspect
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import InventoryAdjustment, Product
from api import inventory_routes as routes


def _beijing_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def _session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    db.add(Product(id='p1', store_id='a', sku='SKU-1', product_name='甲', product_no=1,
                   link_status='已购买'))
    db.commit()


def test_today_local_is_beijing_date():
    assert routes.today_local() == _beijing_today()


def test_inventory_never_uses_container_date():
    """实现守卫：不许用 `date.today()` 取「今天」。"""
    source = inspect.getsource(routes)
    code = re.sub(r'""".*?"""', '', source, flags=re.S)
    code = re.sub(r'#.*', '', code)
    assert not re.search(r'\bdate\.today\(\)', code), \
        'occurred_at 不能取容器日期（UTC 容器会把北京 00:00–08:00 记成前一天）'
    assert 'timedelta(hours=8)' in inspect.getsource(routes.today_local)


def test_manual_adjustment_without_date_uses_business_date():
    db = _session()
    _seed(db)
    data = routes.AdjustmentIn(sku='SKU-1', quantity_delta=5, reason='盘点调整', notes='')
    routes.create_adjustment(data, db, 'a')
    row = db.query(InventoryAdjustment).order_by(InventoryAdjustment.created_at.desc()).first()
    assert row.occurred_at == _beijing_today()


def test_reversal_uses_business_date():
    """撤销记录没有前端传日期，一直吃服务端默认值 —— 原来吃的是容器日期。"""
    db = _session()
    _seed(db)
    data = routes.AdjustmentIn(sku='SKU-1', quantity_delta=5, reason='盘点调整', notes='',
                               occurred_at='2026-10-20')
    created = routes.create_adjustment(data, db, 'a')

    routes.reverse_adjustment(created['id'], db, 'a')

    reversal = (db.query(InventoryAdjustment)
                .filter(InventoryAdjustment.quantity_delta == -5).first())
    assert reversal is not None
    assert reversal.occurred_at == _beijing_today()
    # 原始记录的日期是人工填的，不能被改动
    original = db.query(InventoryAdjustment).filter(InventoryAdjustment.id == created['id']).first()
    assert original.occurred_at == '2026-10-20'
