"""普货 / 带电的判定（battery_guess）。

判定直接影响运输方式和运费，所以测试重点在**误报的方向**：
把带电报成普货会退运，把普货报成带电只是多算运费。所以宁可 `maybe`。

这些用例全部取自线上选品池的真实标题。
"""

import pytest

from services.battery_guess import (
    SEA_BATTERY,
    SEA_GENERAL,
    guess_battery,
    suggested_shipping,
)


@pytest.mark.parametrize("title", [
    "USB Rechargeable flashlight torch tactical flashlight",
    "Rechargeable Electric Toothbrush For Kids - Blue",
    "Cordless Split End Hair Trimmer Pro",
    "Power Bank 20000mAh Fast Charge",
    "Wireless Earbuds,BQ40 Bluetooth Headphones Playback Ear Buds Earphones",
    "Battery Powered Motion Activated Night LED Stick - Set of 2",
    "1400mAh Rechargeable Battery Pack + USB-C Cable-Compatible With Xbox S",
])
def test_battery_included_is_yes(title):
    verdict, reason = guess_battery(title)
    assert verdict == "yes", (title, verdict, reason)
    assert reason


@pytest.mark.parametrize("title", [
    "Kids Toy Car Battery Operated - Batteries Not Included",
    "Garden Light Without Battery",
    "LED Candle No Batteries Required",
])
def test_explicit_no_battery_is_general_cargo(title):
    """明写不含电池的反而要判普货——出现 battery 不等于包装里有电池。"""
    assert guess_battery(title)[0] == "no"


@pytest.mark.parametrize("title", [
    "Battery Charger for AA AAA",
    "Remote Control Holder Organizer Multi-functional Office Desk Storage",
    "USB C Hub 5 in 1 HDMI Adapter with Ethernet Port",
    "Replacement Remote Control for Samsung TV",
    "Silicone Protective Cover For TCL RC902V Remote Control",
])
def test_accessory_is_not_flagged(title):
    """充电器、收纳架、转接头、替换遥控器本身都不带电池。

    不排除这一类，「待确认」会被刷成一片，人工就没法一条条看。
    """
    assert guess_battery(title)[0] == ""


@pytest.mark.parametrize("title", [
    "LED Outdoor 30W Flood Light",
    "Outdoor Solar Powered Pathway Garden Lights",
    "Bicycle Anti Theft Alarm with Remote Vibration Sensing Wireless Bike Alarm",
])
def test_ambiguous_is_maybe(title):
    assert guess_battery(title)[0] == "maybe"


@pytest.mark.parametrize("title", [
    "Yoga Mat - Black",
    "Stainless Steel Kitchen Knife Set",
    "Cotton T-Shirt",
])
def test_plain_goods_not_flagged(title):
    assert guess_battery(title)[0] == ""


def test_empty_input():
    assert guess_battery("") == ("", "")
    assert guess_battery(None, "") == ("", "")


def test_uses_all_texts_given():
    """描述里写了内置电池、标题没写，也要认出来。"""
    verdict, reason = guess_battery("LED Desk Lamp", "Built-in battery, 8 hours runtime")
    assert verdict == "yes", (verdict, reason)


def test_suggested_shipping():
    assert suggested_shipping("yes") == SEA_BATTERY
    assert suggested_shipping("maybe") == SEA_GENERAL
    assert suggested_shipping("no") == SEA_GENERAL
    assert suggested_shipping("") == SEA_GENERAL
