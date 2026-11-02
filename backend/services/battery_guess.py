"""从标题判断「带电」还是「普货」。

为什么只敢给「可能带电」，不敢给「就是带电」
--------------------------------------------
标题里出现 `battery` 不等于包装里有电池：

- `Battery Operated - Batteries Not Included`（玩具常态）→ **普货**
- `USB Rechargeable`（内置锂电）→ **带电**
- `Battery Holder` / `Battery Terminal`（只是配件）→ **普货**

只看标题不可能 100% 分清。而判错的代价是不对称的：把带电报成普货，货代
查验会**退运**（整批卡在港口，还要重做报关）；把普货报成带电，只是运费算贵
一点、利润看着差一些。所以这里的策略是**宁可多报待确认**：

    "yes"    包装里确定有电池        → 自动选「海运带电」
    "maybe"  有线索但判不准          → 选「海运普货」+ 打「待确认」标记
    "no"     明写不含电池 / 纯普货    → 「海运普货」
    ""       一点线索都没有          → 「海运普货」

`maybe` 的货最后要不要改带电，是人工看一眼货源页就能定的事，比机器猜准。
"""
from __future__ import annotations

import re
from typing import Tuple

# 运输方式的名字要和前端 `src/types.ts` 的 `SHIPPING_METHODS` 完全一致。
SEA_GENERAL = "海运普货"
SEA_BATTERY = "海运带电"

# 明写不含电池：这类反而要判成普货，不能因为出现 battery 就报带电。
_NEGATIVE = re.compile(
    r"batter(?:y|ies)\s*(?:are\s*|is\s*)?not\s*included"
    r"|no\s+batter(?:y|ies)"
    r"|without\s+(?:a\s+)?batter"
    r"|excl(?:uding|\.)?\s+batter"
    r"|batter(?:y|ies)\s+excluded"
    r"|batter(?:y|ies)\s+sold\s+separately"
    r"|不含电池|电池另配|电池另购|不带电池",
    re.I,
)

# 确定带电：命中任意一条就是「包装里一定有电池」。
_YES: Tuple[Tuple[str, str], ...] = (
    (r"power\s*bank|powerbank", "充电宝"),
    (r"\b(?:li-?ion|lithium|lipo|li-?polymer)\b", "锂电池"),
    (r"\b(?:18650|21700|14500|cr2032|cr2016)\b", "纽扣/圆柱电芯"),
    (r"\b(?:aaa|aa)\b\s*(?:batter|cell)", "干电池"),
    (r"rechargeable", "可充电"),
    (r"cordless", "无线（内置电池）"),
    (r"built[-\s]?in\s+batter|internal\s+batter|integrated\s+batter", "内置电池"),
    (r"(?:bluetooth|wireless)\s+(?:earbud|earphone|headphone|headset|speaker|"
     r"mouse|keyboard|tracker|selfie|controller|gamepad|microphone)", "蓝牙/无线设备"),
    (r"batter(?:y|ies)\s*(?:included|operated|powered)", "随附或需装电池"),
)

# 有线索但判不准：可能带电，也可能只是「需要外接电源」。
_MAYBE: Tuple[Tuple[str, str], ...] = (
    (r"\bbatter", "标题提到电池"),
    (r"bluetooth", "蓝牙"),
    (r"wireless", "无线"),
    (r"\bled\b", "LED"),
    (r"electric", "电动"),
    (r"\bsolar\b", "太阳能"),
    (r"\bsmart\b", "智能"),
    (r"remote\s*control", "遥控"),
    (r"flashlight|torch|headlamp|work\s*light", "手电/工作灯"),
    (r"\bdrone|robot\s*vacuum|vacuum\s*cleaner|massager|epilator", "便携电动设备"),
)

# 「配件/容器」类名词：标题里的电池、蓝牙、USB 是在描述**它装的那个东西**，
# 不是它自己。`Remote Control Holder`（遥控器收纳架）、`USB C Hub`、
# `Battery Charger`（充电器本身不带电池）都属这类，一律不打标。
#
# 这张表只放「装着别的东西」的词。`collar` / `bag` 之类故意不放：LED 狗项圈、
# 带充电宝的背包是真的带电，不能因为叫「项圈」就放过。
_ACCESSORY = re.compile(
    r"\b(?:holder|organi[sz]er|case|cover|shell|skin|mat|pad|stand|mount|"
    r"bracket|hook|cable|cord|adapter|adaptor|hub|splitter|extension|converter|"
    r"charg(?:er|ing)|replacement|refill|sticker|decal|sleeve|strap|"
    r"backpack|pouch|tray|rack|shelf|liner|insert|pocket|wallet)\b",
    re.I,
)


def _first_match(text: str, rules) -> str:
    for pattern, reason in rules:
        if re.search(pattern, text, re.I):
            return reason
    return ""


def guess_battery(*texts: object) -> Tuple[str, str]:
    """返回 (verdict, 依据)。

    `texts` 可以给标题、卖点、`What's in the box`——多给几段命中率更高，
    但顺序无所谓，判定只看「有没有出现」。
    """
    joined = " ".join(str(t or "") for t in texts).strip()
    if not joined:
        return "", ""
    # 换行和连续空白压成一个空格，正则里的 `\s` 才好写
    joined = re.sub(r"\s+", " ", joined)

    if _NEGATIVE.search(joined):
        return "no", "明写不含电池"

    reason = _first_match(joined, _YES)
    if reason:
        return "yes", reason

    # 配件/容器先排除：`Battery Charger` 里没有电池，`Remote Control Holder`
    # 也不带电。不排除的话这两类会把「待确认」刷成一片。
    if _ACCESSORY.search(joined):
        return "", ""

    reason = _first_match(joined, _MAYBE)
    if reason:
        return "maybe", reason

    return "", ""


def suggested_shipping(verdict: str) -> str:
    """按判定结果给运输方式。判不准的一律先按普货，由人确认。"""
    return SEA_BATTERY if verdict == "yes" else SEA_GENERAL
