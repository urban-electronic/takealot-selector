"""
补齐 Fee 映射规则（一次性脚本，可重复运行）
============================================

为什么需要它
------------
`main.py` 里的种子规则只在 `fee_mapping_rules` 表为空时插入。已有的数据库
不会再补，所以线上一直缺着几条。

更具体的问题：种子规则里的 pattern 大多来自 **Fee 树**的名字（Homeware、
Drives & Storage、Games），而 `fee_category_matcher` 实际匹配的是
**前台类目树**（`takealot_category_path`）。两棵树不同名，于是：

  Home & Kitchen        → 找不到 "Homeware"，未命中
  Computers & Tablets   → 找不到 "Drives & Storage"，未命中
  TV, Audio & Video     → 未命中
  Cellphones & Wearables→ 未命中
  Cameras               → 有 fee_category 但从来没建过规则
  Household, Food & Beverages → 未命中
  Gaming                → "Games" 不是 "Gaming" 的子串，未命中
  Movies & Series       → 没有对应的 fee_category，只能留空

实测 23 个前台大类里 8 个未命中。本脚本补上其中 7 条，Movies & Series 故意不补。

用法
----
    cd backend
    python3 seed_fee_mapping_rules.py --verify    # 纯本地推演，不连库，看覆盖率变化
    python3 seed_fee_mapping_rules.py --dry-run   # 连库，看会改什么
    python3 seed_fee_mapping_rules.py             # 真正写入

规则可重复运行：已存在同名 pattern（忽略大小写和首尾空格）的会跳过，
不会重复插入，也不会覆盖你自己在设置页建过的规则。

`--verify` 不 import sqlalchemy / database / models，所以在没装后端依赖的
机器上也能跑，用来先确认规则改对了再上线上库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量区：不要在这里 import 任何后端模块，--verify 要能独立运行
# ---------------------------------------------------------------------------

# 与 main.py 里 `default_rules` 保持一致（种子规则，数据库为空时才会写入）
SEED_RULES: List[Tuple[str, str, int]] = [
    ("Computers", "Computers & Laptops", 100),
    ("Computer Components", "Computer Components", 100),
    ("TV & Audio", "TV & Audio", 100),
    ("Cameras", "Cameras", 100),
    ("Small Appliances", "Small Appliances", 90),
    ("Large Appliances", "Large Appliances", 90),
    ("Sport", "Sport", 90),
    ("Camping", "Camping & Outdoor", 90),
    ("Beauty", "Beauty", 90),
    ("Health", "Health", 90),
    ("Baby", "Baby", 90),
    ("Toys", "Toys", 90),
    ("Books", "Books", 90),
    ("Pets", "Pets", 90),
    ("Liquor", "Liquor", 90),
    ("Garden", "Garden, Pool & Patio", 90),
    ("Stationery", "Stationery", 90),
    ("Office", "Office", 90),
    ("Musical Instruments", "Musical Instruments", 90),
    ("Homeware", "Homeware", 80),
    ("Clothing", "Clothing & Footwear", 80),
    ("Fashion", "Clothing & Footwear", 80),
    ("Luggage", "Luggage & Travel", 80),
    ("DIY", "DIY & Automotive", 80),
    ("Automotive", "DIY & Automotive", 80),
    ("Electronic Accessories", "Electronic Accessories", 80),
    ("Music", "Music & DVD", 70),
    ("Games", "Games", 70),
    ("Smart Home", "Smart Home & Connected Living", 70),
    ("Drives & Storage", "Computer Components", 60),
    ("Data Storage", "Computer Components", 60),
]

# (前台类目 pattern, 目标 fee_category, 优先级, 备注)
#
# 优先级取 85：高于种子规则里的通用档（80，如 Homeware / Clothing / DIY），
# 低于明确档（90，如 Sport / Toys / Pets）。这样当某条前台路径同时命中
# 两个 pattern 时，更具体的那个（90）仍然优先。
#
# 这些 pattern 匹配的是「前台类目路径」，所以写的是前台的叫法，
# 不是 Fee 树的叫法——这正是原来缺的那一层。
NEW_RULES: List[Tuple[str, str, int, str]] = [
    ("Home & Kitchen", "Homeware", 85,
     "小家电可能应归 Small Appliances（履约费 R69 档），需人工确认"),
    ("Computers & Tablets", "Computers & Laptops", 85,
     "配件可能应归 Computer Components，需人工确认"),
    ("TV, Audio & Video", "TV & Audio", 85,
     "明确对应"),
    ("Cellphones & Wearables", "Wearables and GPS", 85,
     "手机本体可能应归 Electronic Accessories，需人工确认"),
    ("Cameras", "Cameras", 85,
     "原来只有 fee_category 没有规则，属于漏建"),
    ("Household, Food & Beverages", "Non-Perishable", 85,
     "明确对应"),
    ("Gaming", "Games", 85,
     "原规则写的是 Games，而前台叫 Gaming，子串匹配不到"),
]

# 故意不补的：没有对应的 fee_category，补了只能瞎猜。
# Movies & Series —— 现有 fee_category 里没有对应项，留空让用户在新建产品时选。
SKIPPED: List[Tuple[str, str]] = [
    ("Movies & Series", "现有 fee_category 里无对应项"),
]


# ---------------------------------------------------------------------------
# 纯函数：与 services/fee_category_matcher.py 同口径，供 --verify 使用
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> List[str]:
    return re.split(r"[\s&>/,]+", text.lower())


def _keyword_overlap(a: List[str], b: List[str]) -> int:
    set_a = {k.strip() for k in a if len(k.strip()) > 1}
    set_b = {k.strip() for k in b if len(k.strip()) > 1}
    return len(set_a & set_b)


def _match(
    rules: List[Tuple[str, str, int]],
    category_path: Optional[str],
    product_name: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """返回 (fee_category, confidence)。规则已按优先级降序排好。"""
    if category_path:
        low_path = category_path.lower()
        for pattern, category, _prio in rules:
            if pattern and pattern.strip().lower() in low_path:
                return category, "high"

    if category_path:
        keywords = _extract_keywords(category_path)
        for pattern, category, _prio in rules:
            if pattern and _keyword_overlap(keywords, _extract_keywords(pattern)) >= 2:
                return category, "medium"

    if product_name:
        low_name = product_name.lower()
        for pattern, category, _prio in rules:
            if pattern and pattern.strip().lower() in low_name:
                return category, "medium"

    return None, "low"


def _sorted_rules(rules: List[Tuple[str, str, int]]) -> List[Tuple[str, str, int]]:
    return sorted(rules, key=lambda r: -r[2])


def _load_sample() -> List[Tuple[str, str, int]]:
    """从本地 PLID 缓存取 (类目路径, 标题, 出现次数) 作为样本。

    缓存不存在时退化为「每个大类算 1 次」，仍然能看出覆盖变化。
    """
    cache = Path.home() / ".takealot_cache" / "plid.jsonl"
    counter: Dict[Tuple[str, str], int] = {}
    if cache.exists():
        with cache.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("ok"):
                    continue
                path = " > ".join(rec.get("path") or [])
                if not path:
                    continue
                key = (path, rec.get("title") or "")
                counter[key] = counter.get(key, 0) + 1
    if counter:
        return [(p, t, n) for (p, t), n in counter.items()]

    fallback = [
        "Home & Kitchen", "Garden, Pool & Patio", "Automotive", "Office & Stationery",
        "Computers & Tablets", "Sport", "Toys", "DIY Tools & Machinery", "Beauty",
        "Fashion", "Camping & Outdoors", "Cameras", "Luggage & Travel", "Pets",
        "TV, Audio & Video", "Baby & Toddler", "Health", "Cellphones & Wearables",
        "Music", "Gaming", "Household, Food & Beverages", "Movies & Series", "Books",
    ]
    return [(d, "", 1) for d in fallback]


def verify() -> int:
    """对比补规则前后的匹配覆盖率，不连数据库。"""
    sample = _load_sample()
    total = sum(n for _p, _t, n in sample)
    before = _sorted_rules(SEED_RULES)
    after = _sorted_rules(SEED_RULES + [(p, c, pr) for p, c, pr, _n in NEW_RULES])

    miss_before: Dict[str, int] = {}
    miss_after: Dict[str, int] = {}
    hit_before = hit_after = 0

    for path, title, n in sample:
        dept = path.split(" > ")[0]

        cat, _conf = _match(before, path, title)
        if cat:
            hit_before += n
        else:
            miss_before[dept] = miss_before.get(dept, 0) + n

        cat, _conf = _match(after, path, title)
        if cat:
            hit_after += n
        else:
            miss_after[dept] = miss_after.get(dept, 0) + n

    def pct(x: int) -> str:
        return f"{x / total * 100:5.1f}%" if total else "  n/a"

    print(f"样本：{total} 个商品 / {len(sample)} 条不同类目路径（来自本地 PLID 缓存）\n")
    print(f"  补规则前命中  {hit_before:5d}  {pct(hit_before)}")
    print(f"  补规则后命中  {hit_after:5d}  {pct(hit_after)}")
    print(f"  提升          {hit_after - hit_before:5d}  {pct(hit_after - hit_before)}\n")

    print("补规则后仍未命中的大类：")
    if not miss_after:
        print("  无")
    else:
        for dept, n in sorted(miss_after.items(), key=lambda kv: -kv[1]):
            print(f"  {n:5d}  {dept}")

    print("\n新增规则实际吃到的量（按大类）：")
    for pattern, category, _prio, _note in NEW_RULES:
        gained = miss_before.get(pattern, 0)
        flag = "" if gained else "   ← 样本里没碰到这类商品，不代表规则错"
        print(f"  {pattern:<28} → {category:<26} {gained:5d}{flag}")

    leftover = set(miss_after) - {p for p, _c, _pr, _n in NEW_RULES}
    if leftover:
        print("\n还有规则覆盖不到的大类（需要你决定归到哪个 fee_category）：")
        for dept in sorted(leftover):
            print(f"  {dept}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐 Fee 映射规则")
    parser.add_argument("--dry-run", action="store_true", help="连库，只打印计划不写库")
    parser.add_argument("--verify", action="store_true", help="纯本地推演覆盖率，不连库")
    args = parser.parse_args()

    if args.verify:
        return verify()

    # 只有真正写库时才需要后端依赖，放在这里 import 可以让 --verify 独立运行
    from database import SessionLocal
    from models import FeeMappingRule

    db = SessionLocal()
    try:
        existing = {
            (rule.takealot_category_pattern or "").strip().lower()
            for rule in db.query(FeeMappingRule).all()
        }
        print(f"现有规则 {len(existing)} 条\n")

        added = 0
        for pattern, category, priority, note in NEW_RULES:
            key = pattern.strip().lower()
            if key in existing:
                print(f"  跳过  {pattern:<30} 已存在")
                continue
            print(f"  {'计划' if args.dry_run else '新增'}  {pattern:<30} → {category:<26} (优先级 {priority})")
            print(f"        备注：{note}")
            if not args.dry_run:
                db.add(FeeMappingRule(
                    takealot_category_pattern=pattern,
                    fee_category=category,
                    priority=priority,
                    active=True,
                    # 标记为系统规则：以后重刷种子时能区分出哪些是用户自己建的
                    created_by_user=False,
                ))
            added += 1

        if not args.dry_run and added:
            db.commit()

        print(f"\n{'将新增' if args.dry_run else '已新增'} {added} 条")

        for pattern, reason in SKIPPED:
            print(f"  未补  {pattern:<30} {reason}")

        if added and not args.dry_run:
            print("\n提示：这些规则的抽成/履约费仍由类目决定，规则只负责把")
            print("      前台类目映射到你的 fee_category。有歧义的几条")
            print("      （Home & Kitchen / Computers & Tablets / Cellphones & Wearables）")
            print("      建议在设置页复核，或在新建产品时逐个确认。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
