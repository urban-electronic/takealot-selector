"""英文标题 → 五字以内中文品名（chinese_short_name）。

这个模块的输出会印在标签和报关单上，所以测试的重点不是「翻得漂亮」，
而是三件事：

1. **不许超长**。`max_chars` 是硬约束，截断必须发生在函数内部。
2. **不许硬编**。匹配不上就返回空串让人工填——空着能补，填错要退运。
3. **不许退化成「什么都叫包」**。用 61 条线上真实标题锁住命中率，
   词表被人改坏会直接红。
"""

from services import chinese_short_name as names
from services.chinese_short_name import short_chinese_name

# 线上选品池（1804 条）里每隔 30 条抽一条，保留原始大小写和标点。
# 抽样方式固定，所以命中率是可复现的，不是「这次刚好」。
REAL_TITLES = [
    "Adjustable Under Desk Foot Stool Foot Rest with Massage Rollers - Black",
    "3D Wall Panels",
    "5 Tier Plant Display Stand For Indoor Outdoor, Tall Metal Flower Shelf, DIY",
    "Kids Climbing Rope Tree Swing Swing Seat Set Playground Accessories",
    "LED Light and TV Backlight Tester,LED Strip Lamp Beads Repair Testing Tool",
    "Yerba Mate Cup with Straw, Spoon, and Cleaning Brush",
    "Women's Plus Size Black Bikini Swimsuit With Drawstring Beach Skirt - 3 Piece - XL",
    "Laptop Bag & Women Tote Bag Handbag - Black",
    "Camping Heavy Duty Tent Screw Anchor Spiral Peg Stakes Set of 12 (20cm)",
    "2M X 3M woven Photography Backdrop Background by ZZT - GREEN",
    "Compatible Samsung 4+5+6 Stud Silicone Watch Strap - 20mm Classic - Black",
    "5 Pieces Gift Set Kiln Change Element colourful Egg Kungfu Tea Cup",
    "Car Home Handheld Vacuum Cleaner Mini Portable Strong Suction Power Gift - Black",
    "Premium Quality 1.5M X 3M Woven Photography Studio Backdrop cloth - White",
    "Crossbody Strap Anti Drop Leather Wallet Case For iPhone 11 - Gold",
    "Women Twist Braid Headwrap - Black",
    "Universal Foldable Motorcycle Rearview Mirrors-Adjustable Black Wing Mirror",
    "Pet sniffer pads train foraging skills and slow food for dogs - Yellow",
    "Kids Play Tent Castle For Children",
    "Stretchy Bed Cover Special for Eyelash Extension Cloth Massage Table Cover - Black",
    "Home Tool DIY Zinc Plated Thread Nut Insert Set of 300 (M3 - M10)",
    "Premium 32cm Silicone Splatter Guard with Bamboo Handle",
    "Alligator Print PU Leather Double Metal RFID ID Credit Card Holder Case - Black",
    "USB Rechargeable flashlight torch tactical flashlight",
    "Roll Pin Punch Set Gunsmithing Punch Removing Repair Tools",
    "Ultima M8 16pin Cartridges (Pack of 10)",
    "Card Holder Wallet with RFID Protection - Brown",
    "12 Pairs Teardrop Earrings Zirconia Hoops & Travel Jewelry Box - Gift for Her",
    "4 in 1 Cat Laser Pointer Interactive Cat Toy Chase Exercises Training Tool",
    "Wireless Waterproof Doorbell.",
    "Clear Silicone - MagSafe - Protective Cover - DIAMOND RANGE - iPhone 15 Pro Max - Pink",
    "Instax Brown Mini 12 Case",
    "Pocket Watch To My Husband Beautiful Pocket Watch A Special Gift",
    "Car Door Lock Cylinder Repair Kit Front Right and Left for BMW",
    "Unisex Dry and Wet Separation Travel Toiletry Bag - Gray",
    "My First Black and White Book - Animals",
    "Baby Head Protector Backpack Head Protection Pillow - Plush Dragon",
    "Multi-color Baby Muslin Burp Cloths 50 x 25 cm - Pack of 6",
    "Portable Magnetic Wireless Charger for Apple Watch Series - White",
    "Tablet Case compatible Huawei MatePad SE 11 (2024)PU leather Cover - Black - Blue",
    "Car Water Cup Holder Roller Blind Zipper Storage Box Trim for Mercedes Benz",
    "Paper Crimper Tool for Crafts - Pink",
    "T4U Neoprene Shoulder Support - Right Shoulder",
    "Home Garden Hanging Pot Plant Holder Set of 2 - Black",
    "Ankle Compression Sock for Men Women Low Cut Ankle Support Pack 4 of Pairs - Black",
    "Floxi Photography Sandbag",
    "Silicone Strap Compatible with Huawei GT3 46mm (22mm)-Black",
    "Water-resistant Travel Bag for Women and Men - Black",
    "Home Decor Retro Music Box Jack Rack Key Holder Ornament (20x7cm)",
    "Stitch Pencil Bag with double compartment",
    "Silicone Watch Band Compatible with Huawei Band 3 Pro & Band 4 Pro (Black)",
    "5X Magnifying Glass with Light Full Page Magnifier for Reading",
    "Sun Hat - Breathable Sun Shade with Adjustable Strap & Ponytail Hole - Pink + Grey",
    "4 Pack Dog Paw and Bone Silicone Molds, Non-Stick Food Grade Silicone Molds",
    "80pcs Set Oil-Based Thick Coloring Marker Pen",
    "Ladies Purse Cielo - Brown",
    "Corsair Void RGB Pro Headphone Cushion Mesh Replacement",
    "Double Water and Food Bowls for Dog Cat Stainless Steel & Non-Slip Station",
    "Family Travel Document Organizer Capacious Passport Holder Wallet - Navy",
    "Ladies Durable Portable Lunch Bag - Pink",
    "Sunscreen Gloves for Women UV Protection Gloves",
]


def test_covers_most_real_titles():
    """真实标题的命中率下限。

    线上实测 89%（1607/1804）。这里留 4 个点余量：既允许小修词表，
    又不允许「删词表偷懒」——删掉一半词表会掉到 60% 以下直接红。
    """
    hits = [title for title in REAL_TITLES if short_chinese_name(title)]
    rate = len(hits) / len(REAL_TITLES)
    assert rate >= 0.85, f"命中率 {rate:.1%}，低于 85%；漏掉的样本：{set(REAL_TITLES) - set(hits)}"


def test_never_exceeds_max_chars():
    for title in REAL_TITLES:
        name = short_chinese_name(title)
        assert len(name) <= names.MAX_CHARS, f"{title!r} → {name!r} 超长"


def test_no_duplicate_nouns():
    """词表里不能有重复键。

    `_NOUN_PAIRS` / `HEAD_PHRASES` 用元组而不是 dict 字面量就是为了这条：
    dict 字面量遇到重复键会静默保留最后一个，"light" 被写成"灯"又被写成"浅"
    这种事故在代码评审里根本看不出来。
    """
    nouns = [key for key, _ in names._NOUN_PAIRS]
    assert len(nouns) == len(set(nouns)), f"名词表重复：{_dupes(nouns)}"
    assert len(names.HEAD_NOUNS) == len(nouns)

    phrases = [key for key, _ in names.HEAD_PHRASES]
    assert len(phrases) == len(set(phrases)), f"复合词重复：{_dupes(phrases)}"


def _dupes(keys):
    seen, dupes = set(), []
    for key in keys:
        if key in seen:
            dupes.append(key)
        seen.add(key)
    return dupes


def test_keeps_titles_already_in_chinese():
    assert short_chinese_name("加厚瑜伽垫 185x61cm") == "加厚瑜伽垫"
    assert short_chinese_name("收纳盒") == "收纳盒"


def test_empty_input_returns_empty():
    assert short_chinese_name("") == ""
    assert short_chinese_name("   ") == ""
    assert short_chinese_name(None) == ""


def test_unmatched_title_returns_empty_not_guess():
    """一个词都对不上就返回空串——品名要报关，宁缺勿错。"""
    assert short_chinese_name("Qwertyuiop Zxcvbn") == ""
    assert short_chinese_name("1 2 3") == ""


def test_phrase_beats_single_word():
    assert short_chinese_name("Power Bank 10000mAh") == "充电宝"
    assert short_chinese_name("Water Bottle") == "水瓶"
    assert short_chinese_name("Storage Box with Lid") == "收纳盒"


def test_picks_latest_phrase():
    """标题是「主体 + 附带物」，取最靠右的复合词才是主体。"""
    assert short_chinese_name("Laptop Bag & Women Tote Bag Handbag - Black") == "手提包"


def test_drops_colour_and_spec_tail():
    assert short_chinese_name("Yoga Mat - Black") == "瑜伽垫"
    assert short_chinese_name("Stainless Steel Kitchen Knife Set") == "厨房刀"


def test_modifier_only_from_adjacent_words():
    """"Usb Port Armrest Box" 里的 USB 属于端口，不是箱子的属性。

    隔两个词以上的修饰语一律不要，否则会拼出"USB盒"这种错名。
    """
    name = short_chinese_name("Usb Port Armrest Box")
    assert name == "盒", name


def test_respects_custom_max_chars():
    assert short_chinese_name("Stainless Steel Kitchen Knife Set", 3) == "厨房刀"
    assert len(short_chinese_name("Stainless Steel Kitchen Knife Set", 2)) <= 2
    assert short_chinese_name("Travel Bag", 8) == "旅行包"


def test_hyphenated_attribute_is_not_read_as_noun():
    """"Ultra-Light" 不先捏成一个词的话，light 会命中「灯」，雨衣变灯。"""
    assert short_chinese_name("2pcs PVC Rain Suit Waterproof Jacket & Trousers Ultra-Light Safety Works") == "雨衣"


def test_spec_prefix_does_not_swallow_the_noun():
    """`4 Pieces/Set … Tote Bag` 在 `/` 处一切断就只剩 "4 pieces"。

    线上 196 条空品名里有一大半是这种「开头是规格、本体在后面」的标题，
    所以第一段切不出名词时要退到整条标题再试一次。
    """
    assert short_chinese_name(
        "4 Pieces/Set Women's PU Messenger Tote Bag Coin Purse Card Holder - Brown"
    ) == "手提包"
    assert short_chinese_name(
        "2 Packs Of 100, 215mm x 315mm Plastic Zip Lock Bags"
    ) == "密封袋"


def test_compatible_with_prefix_still_finds_the_product():
    """"Compatible with Galaxy Tab A9 (8.7") … Case" 括号一断就全空了。"""
    assert short_chinese_name(
        'Compatible with Galaxy Tab A9 (8.7") Shockproof Rugged Case - Pink - B'
    ) == "壳"


def test_light_bar_is_not_a_bare_pole():
    """"Work Light Bar" 取最后一个名词会得到「杆」，得按复合词认成灯条。"""
    assert short_chinese_name(
        "COB LED Headlamp USB Rechargeable Headlight Torch Work Light Bar 3 Color"
    ) == "灯条"
