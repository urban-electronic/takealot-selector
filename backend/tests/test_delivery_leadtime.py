"""配送时效解析。

leadtimeDays 之前在三类报价上全是 None，于是引擎分不清「时效位」和「价格位」。
这里只验证一件事：卡片上写了什么，就读出什么；没写就返回 None，不猜。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.pricepilot_collector import parse_leadtime

TODAY = datetime.now(ZoneInfo('Africa/Johannesburg')).date()


@pytest.mark.parametrize('label,expected', [
    ('Get it today', 0),
    ('Delivered Today', 0),
    ('Get it tomorrow', 1),
    ('Delivered tomorrow', 1),
    ('Fastest Delivery in 2-4 days', 4),      # 区间取上界
    ('Delivery in 3 days', 3),
    ('Ships in 3 - 5 working days', None),
    ('Ships in 2 days', None),
    ('Delivery in 3 working days', None),
    ('Delivery in 2-4 business days', None),
    ('Ships in 2 days. Get it tomorrow', 1),
    ('Delivery in 2 days. Ships in 1 day', 2),
    ('Get it in 2-4 days', 4),
    ('Delivers in 3 days', 3),
])
def test_leadtime_from_delivery_wording(label, expected):
    assert parse_leadtime(label) == expected


@pytest.mark.parametrize('label', [None, '', 'In stock', 'Add to cart', 'Free delivery on orders over R500'])
def test_unreadable_label_returns_none_instead_of_guessing(label):
    assert parse_leadtime(label) is None


def test_leadtime_from_date_range_counts_to_the_later_day():
    later = TODAY + timedelta(days=5)
    label = f"Delivery {TODAY.day} {TODAY.strftime('%b')} - {later.day} {later.strftime('%b')}"
    assert parse_leadtime(label) == 5


def test_leadtime_from_single_date():
    later = TODAY + timedelta(days=3)
    assert parse_leadtime(f"Delivery {later.day} {later.strftime('%b')}") == 3


def test_leadtime_rolls_over_the_new_year(monkeypatch):
    """12 月 30 日看到「1 月 4 日」是明年，不是今年（今年已经过去了）。"""
    from services import pricepilot_collector as collector

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 12, 30, tzinfo=ZoneInfo('Africa/Johannesburg'))

    monkeypatch.setattr(collector, 'datetime', Frozen)
    assert collector._days_until(4, 'jan') == 5
    assert collector._days_until(31, 'dec') == 1
