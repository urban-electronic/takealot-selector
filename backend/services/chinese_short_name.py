"""英文标题 → 五个字以内的中文品名（名词为主）。

为什么不复用 `translator.translate_to_chinese_sync`
--------------------------------------------------
那个函数把标题里**所有**命中的词按出现顺序拼起来（"旅行收纳包加大号可折叠防水"），
再截断加省略号。它适合「给人看的长名字」，不适合「五个字以内的品名」：拼出来
既超长又抓不到重点。而且 `PRESET_TRANSLATIONS` 里有重复键——"light" 先映射到
"灯"、后面又被 "浅" 覆盖，"bag" 同理（"包" → "袋"），灯类商品会被叫成"浅"。

这里的做法
----------
1. 先砍掉属性尾巴：`- Black` / `, Grey` / `(3 Pack)` / ` with USB Port` / ` for
   iPhone 13`。这些是定语，不是商品本体；不砍的话「最后一个词」几乎总是颜色。
2. 找**商品本体**（head noun）。英语复合名词的中心词在最后（"travel bag" 的
   本体是 bag），所以从后往前找第一个命中词表的词。
3. 如果本体前面紧挨着一个能塞进剩余字数的修饰语（travel / car / foldable…），
   就带上它凑成 ≤5 字："travel bag" → "旅行包"。
4. 一个字都匹配不上就返回空串，**不猜、不硬编**。空着让人工填，比填个错的强
   ——品名要印在标签和报关单上。

词表是按线上 1804 条真实标题的中心词频率排的，不是凭空写的通用词典；
`tests/test_chinese_short_name.py` 里锁住了命中率下限，词表退化会直接红。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# 默认品名长度上限。五个字：报关、标签、货代系统都按这个习惯叫。
MAX_CHARS = 5

# 复数尾巴，查表前先还原成单数。
_PLURAL_RULES = (
    ("ies", "y"),
    ("ves", "f"),
    ("ches", "ch"),
    ("shes", "sh"),
    ("ses", "s"),
    ("s", ""),
)

# 属性尾巴：出现这些就说明后面是定语/规格，不是本体。
_TAIL_SEPARATORS = re.compile(r"\s+[-\u2013\u2014|]\s+|[,;|()\[\]/]|_")
_TAIL_PHRASES = (" with ", " w/ ", " for ", " compatible", " suitable", " incl")

# 连字符/空格两写的复合词先捏成一个词再查表。
#
# 不捏的话 "Ultra-Light Safety Works" 会被拆成 ultra + light，而 `light` 在
# 词表里是「灯」——雨衣就被叫成「灯」了。这类词拆开必然误判，只能先合并。
_GLUED = (
    ("ultra light", "ultralight"), ("ultra-light", "ultralight"),
    ("light weight", "lightweight"), ("light-weight", "lightweight"),
    ("non slip", "nonslip"), ("non-slip", "nonslip"),
    ("anti slip", "antislip"), ("anti-slip", "antislip"),
    ("multi functional", "multifunctional"),
    ("multi-functional", "multifunctional"),
)

# 纯属性词：既不是本体，也不该当修饰语。带着 "Soft" 只会拼出「柔软烘干机」
# 这种半截话，所以查表前直接丢掉。
#
# 判定时要**先让位给 MODIFIERS**：adjustable / breathable / multi / office
# 这些既是形容词，也是正经修饰语（可调支架、透气垫、办公椅），不能一刀切。
_ATTRIBUTE_WORDS = {
    "compatible", "suitable", "universal", "replacement", "accessory",
    "accessories", "attachment", "attachments", "refill", "refills",
    "part", "parts", "spare", "gift", "combo", "quality", "premium",
    "style", "type", "model", "size", "colour", "color", "colors", "colours",
    "black", "white", "red", "blue", "green", "grey", "gray", "pink",
    "purple", "brown", "gold", "silver", "yellow", "orange", "khaki",
    "navy", "beige", "clear", "transparent", "multi", "xl", "xxl",
    "soft", "cute", "fashion", "fashionable", "classic", "original",
    "genuine", "running", "windproof", "wedding", "duty", "reusable",
    "adjustable", "breathable", "professional", "household", "outdoor",
    "indoor", "home", "office", "daily", "convenient", "practical",
    "durable", "flexible", "creative", "simple", "modern", "elegant",
    "ultralight", "lightweight", "nonslip", "antislip", "multifunctional",
}

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "from", "by", "at",
    "as", "is", "are", "be", "this", "that", "your", "you", "our", "we", "it",
    "its", "x", "mm", "cm", "inch", "inches", "kg", "g", "ml", "l", "pcs",
    "pack", "set", "piece", "pieces", "pairs", "pair", "new", "hot", "sale",
    "free", "shipping", "high", "best", "top", "diy", "3d",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
}

# 商品本体：英文（单数、小写）→ 中文。
#
# 用「元组 + dict()」而不是字面量，是为了让重复键**能被测出来**：dict 字面量
# 会静默保留最后一个，"light" 被写成"灯"又被写成"浅"这类事故看不出来。
# `test_no_duplicate_nouns` 直接比对长度。
_NOUN_PAIRS: Tuple[Tuple[str, str], ...] = (
    # ---- 包 / 箱 / 收纳（线上最高频的一类）----
    ("bag", "包"), ("backpack", "背包"), ("handbag", "手提包"),
    ("purse", "钱包"), ("wallet", "钱包"), ("pouch", "小包"),
    ("sack", "袋"), ("luggage", "行李箱"), ("suitcase", "行李箱"),
    ("case", "壳"), ("box", "盒"), ("basket", "篮"), ("bin", "垃圾桶"),
    ("bucket", "桶"), ("basin", "盆"), ("tray", "托盘"),
    ("container", "容器"), ("canister", "罐"), ("jar", "罐"),
    ("bottle", "瓶"), ("cup", "杯"), ("mug", "马克杯"), ("bowl", "碗"),
    ("plate", "盘"), ("pot", "锅"), ("pan", "锅"),
    ("organizer", "收纳盒"), ("organiser", "收纳盒"),
    ("carrier", "提篮"), ("holster", "腰包"), ("liner", "内衬"),
    ("slipcover", "套"), ("cover", "罩"), ("hanger", "衣架"),
    # ---- 架 / 支架 / 挂 ----
    ("rack", "架"), ("shelf", "置物架"), ("stand", "支架"),
    ("holder", "支架"), ("mount", "支架"), ("bracket", "支架"),
    ("hook", "挂钩"), ("post", "立柱"), ("rod", "杆"), ("pole", "杆"),
    ("bar", "杆"), ("rail", "导轨"), ("frame", "框"), ("easel", "画架"),
    ("station", "站架"),
    # ---- 垫 / 毯 / 枕 ----
    ("mat", "垫"), ("pad", "垫"), ("cushion", "靠垫"), ("pillow", "枕"),
    ("blanket", "毯"), ("towel", "毛巾"), ("rug", "地毯"),
    ("carpet", "地毯"), ("coaster", "杯垫"),
    # ---- 工具 / 五金 ----
    ("tool", "工具"), ("kit", "套装"), ("drill", "电钻"), ("saw", "锯"),
    ("hammer", "锤"), ("wrench", "扳手"), ("plier", "钳"),
    ("scissor", "剪刀"), ("knife", "刀"), ("blade", "刀片"),
    ("cutter", "切割器"), ("slicer", "切片器"), ("grinder", "研磨器"),
    ("sharpener", "磨刀器"), ("clamp", "夹具"), ("vise", "台钳"),
    ("gear", "齿轮"), ("screw", "螺丝"), ("bolt", "螺栓"), ("nut", "螺母"),
    ("washer", "垫圈"), ("latch", "门闩"), ("lock", "锁"),
    ("padlock", "挂锁"), ("knob", "旋钮"), ("handle", "拉手"),
    ("hinge", "铰链"), ("spring", "弹簧"), ("pulley", "滑轮"),
    ("wheel", "轮"), ("caster", "脚轮"), ("bit", "钻头"),
    ("tape", "胶带"), ("glue", "胶水"), ("adhesive", "胶"),
    ("sealant", "密封胶"), ("brush", "刷"), ("roller", "滚筒"),
    ("applicator", "涂抹器"), ("nozzle", "喷嘴"), ("valve", "阀"),
    ("pipe", "管"), ("tube", "管"), ("hose", "软管"),
    ("connector", "接头"), ("adapter", "转换头"), ("coupling", "接头"),
    ("pump", "泵"), ("motor", "电机"), ("generator", "发电机"),
    ("compressor", "压缩机"), ("bearing", "轴承"), ("shaft", "轴"),
    ("axle", "轴"), ("rope", "绳"), ("cord", "绳"), ("wire", "线"),
    ("cable", "线"), ("strap", "绑带"), ("belt", "腰带"),
    ("buckle", "扣"), ("clip", "夹"), ("pin", "别针"),
    ("magnet", "磁铁"), ("chain", "链"), ("guard", "护板"),
    ("protector", "保护套"), ("protection", "保护罩"),
    ("reflector", "反光片"), ("trim", "饰条"), ("screwdriver", "螺丝刀"),
    ("tester", "测试仪"), ("multimeter", "万用表"), ("jack", "千斤顶"),
    ("winch", "绞盘"), ("level", "水平仪"),
    # ---- 电子 / 数码 ----
    ("charger", "充电器"), ("earbuds", "耳机"), ("earphone", "耳机"),
    ("headphone", "耳机"), ("earpads", "耳罩"), ("speaker", "音箱"),
    ("camera", "摄像头"), ("lens", "镜头"), ("projector", "投影仪"),
    ("monitor", "显示器"), ("screen", "屏幕"), ("keyboard", "键盘"),
    ("mouse", "鼠标"), ("remote", "遥控器"), ("controller", "手柄"),
    ("console", "主机"), ("switch", "开关"), ("socket", "插座"),
    ("plug", "插头"), ("hub", "集线器"), ("thermometer", "温度计"),
    ("alarm", "报警器"), ("sensor", "传感器"), ("diffuser", "香薰机"),
    ("humidifier", "加湿器"), ("purifier", "净化器"), ("fan", "风扇"),
    ("light", "灯"), ("lamp", "灯"), ("bulb", "灯泡"), ("led", "灯"),
    ("strip", "灯带"), ("clock", "钟"), ("watch", "手表"),
    ("massager", "按摩器"), ("trimmer", "修剪器"), ("clipper", "理发器"),
    ("dryer", "烘干机"), ("antenna", "天线"), ("router", "路由器"),
    ("repeater", "中继器"), ("extender", "扩展器"),
    ("splitter", "分线器"), ("joystick", "摇杆"), ("gamepad", "手柄"),
    ("stylus", "触控笔"), ("webcam", "摄像头"), ("tuner", "调音器"),
    ("metronome", "节拍器"), ("softbox", "柔光箱"), ("tripod", "三脚架"),
    ("gimbal", "云台"), ("telescope", "望远镜"), ("binocular", "望远镜"),
    ("microscope", "显微镜"), ("lighter", "打火机"), ("soldering", "烙铁"),
    # ---- 家居 / 厨房 ----
    ("dispenser", "分装器"), ("cutlery", "餐具"), ("utensil", "厨具"),
    ("dinnerware", "餐具"), ("mold", "模具"), ("mould", "模具"),
    ("lid", "盖"), ("stopper", "塞"), ("curtain", "窗帘"),
    ("wallpaper", "墙纸"), ("decor", "装饰"), ("decoration", "装饰"),
    ("ornament", "挂饰"), ("mirror", "镜子"), ("vase", "花瓶"),
    ("flower", "花"), ("candle", "蜡烛"), ("incense", "香薰"),
    ("soap", "皂"), ("stool", "凳"), ("chair", "椅"), ("table", "桌"),
    ("desk", "桌"), ("bed", "床"), ("sofa", "沙发"), ("seat", "座椅"),
    ("pedal", "踏板"), ("step", "踏板"), ("ramp", "坡道"),
    ("tier", "层架"), ("drawer", "抽屉"), ("kettle", "水壶"),
    ("toaster", "烤面包机"), ("blender", "搅拌机"), ("juicer", "榨汁机"),
    ("grill", "烤炉"), ("cooler", "冷藏箱"), ("heater", "取暖器"),
    ("radiator", "暖气片"), ("dehumidifier", "除湿机"),
    ("mop", "拖把"), ("broom", "扫帚"), ("vacuum", "吸尘器"),
    ("cleaner", "清洁器"), ("sprayer", "喷雾器"),
    ("sprinkler", "喷头"), ("faucet", "龙头"),
    ("flask", "水壶"), ("jug", "水壶"), ("decanter", "醒酒器"),
    # ---- 宠物 / 户外 / 运动 / 服饰 ----
    ("leash", "牵引绳"), ("collar", "项圈"), ("harness", "胸背带"),
    ("feeder", "喂食器"), ("crate", "笼"), ("kennel", "狗屋"),
    ("tent", "帐篷"), ("sandbag", "沙袋"), ("swing", "秋千"),
    ("ball", "球"), ("dice", "骰子"), ("puzzle", "拼图"),
    ("board", "板"), ("toy", "玩具"), ("doll", "玩偶"), ("gun", "枪"),
    ("trainer", "训练器"), ("goggles", "护目镜"), ("glasses", "眼镜"),
    ("sunglasses", "墨镜"), ("mask", "面罩"), ("brace", "护具"),
    ("support", "护具"), ("band", "带"), ("bracelet", "手链"),
    ("necklace", "项链"), ("ring", "戒指"), ("earring", "耳环"),
    ("scarf", "围巾"), ("cap", "帽"), ("hat", "帽"), ("gloves", "手套"),
    ("socks", "袜"), ("slippers", "拖鞋"), ("shoes", "鞋"),
    ("boots", "靴"), ("bra", "文胸"), ("cape", "披风"),
    ("apron", "围裙"), ("uniform", "制服"), ("costume", "服装"),
    ("vest", "背心"), ("swimsuit", "泳衣"), ("dress", "裙"),
    ("sundress", "连衣裙"), ("panty", "内裤"), ("umbrella", "伞"),
    ("hammock", "吊床"), ("pool", "泳池"), ("racket", "球拍"),
    ("paddle", "桨"), ("lure", "鱼饵"), ("perch", "栖木"),
    # ---- 文具 / 其它长尾 ----
    ("pen", "笔"), ("pencil", "铅笔"), ("marker", "记号笔"),
    ("album", "相册"), ("book", "书"), ("notebook", "本子"),
    ("ruler", "尺"), ("stapler", "订书机"), ("badge", "徽章"),
    ("sticker", "贴纸"), ("label", "标签"), ("tag", "标签"),
    ("maker", "制作器"), ("machine", "机器"), ("equipment", "设备"),
    ("system", "系统"), ("net", "网"), ("combo", "组合"),
    ("comb", "梳子"), ("magnifier", "放大镜"), ("counter", "计数器"),
    ("scale", "秤"), ("wipes", "湿巾"), ("wrap", "贴膜"),
    ("panel", "板"), ("base", "底座"), ("can", "罐"), ("stick", "棒"),
    ("cloth", "布"), ("bookend", "书立"), ("iron", "熨斗"),
    ("punch", "拳靶"), ("earmuffs", "耳罩"), ("hatchet", "斧"),
    ("axe", "斧"), ("bear", "熊"), ("walker", "学步车"),
    ("bells", "铃"), ("drum", "鼓"), ("shower", "淋浴"),
    ("squeegee", "刮水器"), ("siphon", "虹吸管"),
    ("duster", "除尘掸"), ("backdrop", "背景布"), ("bidet", "洁身器"),
    ("bathrobe", "浴袍"), ("gown", "浴袍"), ("hosepipe", "水管"),
    ("fryer", "油炸锅"), ("strainer", "滤网"),
    ("spray", "喷雾器"), ("packaging", "包装"), ("grip", "握力器"),
    ("backgammon", "双陆棋"), ("poster", "海报"), ("mosaic", "马赛克"),
    ("tile", "瓷砖"), ("sponge", "海绵"), ("binder", "装订夹"),
    ("folder", "文件夹"), ("envelope", "信封"), ("calendar", "日历"),
    ("planner", "计划本"), ("stamp", "印章"), ("tarp", "防雨布"),
    ("awning", "遮阳篷"), ("parasol", "遮阳伞"), ("sling", "吊带"),
    ("inflator", "充气泵"), ("gauge", "压力表"), ("rose", "玫瑰"),
)

HEAD_NOUNS: Dict[str, str] = dict(_NOUN_PAIRS)

# 复合名词：整体匹配，优先于单词表（"power bank" 不能拆成 "power"+"bank"）。
HEAD_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("power bank", "充电宝"),
    ("phone case", "手机壳"),
    ("screen protector", "贴膜"),
    ("toilet seat", "马桶圈"),
    ("foot rest", "脚凳"),
    ("foot stool", "脚凳"),
    ("air fryer", "空气炸锅"),
    ("water bottle", "水瓶"),
    ("coffee cup", "咖啡杯"),
    ("storage bag", "收纳袋"),
    ("travel bag", "旅行包"),
    ("duffle bag", "旅行包"),
    ("diaper bag", "妈咪包"),
    ("laptop bag", "电脑包"),
    ("laptop backpack", "电脑包"),
    ("saddle bag", "马鞍包"),
    ("crossbody bag", "斜挎包"),
    ("shoulder bag", "单肩包"),
    ("cosmetic bag", "化妆包"),
    ("makeup bag", "化妆包"),
    ("grocery bag", "购物袋"),
    ("trash bag", "垃圾袋"),
    ("garbage bag", "垃圾袋"),
    ("cooler bag", "保温袋"),
    ("gift box", "礼盒"),
    ("storage box", "收纳盒"),
    ("jewelry box", "首饰盒"),
    ("tool box", "工具箱"),
    ("lunch box", "饭盒"),
    ("cake mold", "蛋糕模"),
    ("ice mold", "冰格"),
    ("phone holder", "手机支架"),
    ("phone stand", "手机支架"),
    ("cup holder", "杯架"),
    ("bike rack", "车顶架"),
    ("roof rack", "车顶架"),
    ("wall hook", "挂钩"),
    ("shelf bracket", "层板托"),
    ("door lock", "门锁"),
    ("pet bed", "宠物床"),
    ("dog bed", "狗床"),
    ("cat bed", "猫窝"),
    ("dog leash", "狗绳"),
    ("dog collar", "狗项圈"),
    ("cat litter", "猫砂盆"),
    ("yoga mat", "瑜伽垫"),
    ("floor mat", "地垫"),
    ("bath mat", "浴室垫"),
    ("door mat", "门垫"),
    ("mouse pad", "鼠标垫"),
    ("seat cushion", "坐垫"),
    ("neck pillow", "颈枕"),
    ("body pillow", "抱枕"),
    ("pregnancy pillow", "孕妇枕"),
    ("fitted sheet", "床单"),
    ("duvet cover", "被套"),
    ("pillow case", "枕套"),
    ("shower curtain", "浴帘"),
    ("hair dryer", "吹风机"),
    ("air pump", "气泵"),
    ("water pump", "水泵"),
    ("heat gun", "热风枪"),
    ("glue gun", "胶枪"),
    ("power strip", "排插"),
    ("extension cord", "延长线"),
    ("night light", "小夜灯"),
    ("string light", "灯串"),
    ("led strip", "灯带"),
    ("desk lamp", "台灯"),
    ("floor lamp", "落地灯"),
    ("wall clock", "挂钟"),
    ("garden hose", "水管"),
    ("water filter", "滤水器"),
    ("spice rack", "调料架"),
    ("dish rack", "沥水架"),
    ("shoe rack", "鞋架"),
    ("wine rack", "酒架"),
    ("book shelf", "书架"),
    ("wall shelf", "壁架"),
    ("desk organizer", "桌面收纳"),
    ("storage rack", "收纳架"),
    ("luggage tag", "行李牌"),
    ("key chain", "钥匙扣"),
    ("key holder", "钥匙架"),
    ("golf bag", "高尔夫包"),
    ("golf ball", "高尔夫球"),
    ("fishing rod", "钓竿"),
    ("camping chair", "露营椅"),
    ("beach towel", "沙滩巾"),
    ("car cover", "车衣"),
    ("car mat", "车垫"),
    ("car seat", "安全座椅"),
    ("bonnet guard", "引擎盖护板"),
    ("trash can", "垃圾桶"),
    ("garbage can", "垃圾桶"),
    ("shower head", "花洒"),
    ("charging station", "充电座"),
    ("charging dock", "充电座"),
    ("o ring", "密封圈"),
    ("seal ring", "密封圈"),
    ("zip lock bag", "密封袋"),
    ("ziplock bag", "密封袋"),
    ("waist pack", "腰包"),
    ("messenger bag", "邮差包"),
    ("tote bag", "手提包"),
    ("rain suit", "雨衣"),
    ("raincoat", "雨衣"),
    ("gift set", "礼盒"),
    ("punch mitt", "拳靶"),
    ("punch bag", "沙袋"),
    ("boxing glove", "拳套"),
    ("eye mask", "眼罩"),
    ("vacuum cup", "保温杯"),
    ("toilet paper holder", "纸巾架"),
    ("paper towel holder", "纸巾架"),
    ("tissue holder", "纸巾架"),
    ("flood light", "泛光灯"),
    ("display shelf", "展示架"),
    ("resistance band", "弹力带"),
    ("gear knob", "挡把"),
    ("fault locator", "寻线仪"),
    ("braiding hair", "假发"),
)

# 修饰语：能塞进剩余字数就带上，凑成 ≤5 字。
MODIFIERS: Dict[str, str] = {
    "travel": "旅行", "storage": "收纳", "car": "车载", "auto": "车载",
    "vehicle": "车载", "motorcycle": "摩托", "bike": "单车", "bicycle": "单车",
    "pet": "宠物", "dog": "狗", "cat": "猫", "baby": "婴儿", "kids": "儿童",
    "kid": "儿童", "women": "女士", "woman": "女士", "men": "男士",
    "man": "男士", "yoga": "瑜伽", "golf": "高尔夫", "camping": "露营",
    "fishing": "钓鱼", "garden": "花园", "kitchen": "厨房", "bathroom": "浴室",
    "bedroom": "卧室", "office": "办公", "desk": "桌面", "wall": "壁挂",
    "floor": "地面", "door": "门用", "window": "窗用", "sofa": "沙发",
    "bed": "床用", "toilet": "马桶", "shoe": "鞋用", "laptop": "电脑",
    "phone": "手机", "tablet": "平板", "camera": "相机",
    "foldable": "折叠", "portable": "便携", "adjustable": "可调",
    "waterproof": "防水", "wireless": "无线", "bluetooth": "蓝牙",
    "electric": "电动", "digital": "数码", "solar": "太阳能",
    "silicone": "硅胶", "stainless": "不锈钢", "leather": "皮革",
    "wooden": "木质", "plastic": "塑料", "metal": "金属", "glass": "玻璃",
    "cotton": "棉质", "memory": "记忆", "massage": "按摩",
    "security": "防盗", "safety": "安全", "heavy": "加厚", "thick": "加厚",
    "large": "大号", "small": "小号", "mini": "迷你", "led": "LED",
    "usb": "USB", "rechargeable": "充电", "multi": "多", "double": "双",
    "single": "单", "insulated": "保温", "heated": "加热", "slim": "超薄",
    "wide": "加宽", "extra": "加长", "long": "长款", "short": "短款",
    "round": "圆形", "square": "方形", "vintage": "复古", "luxury": "高档",
    "thickened": "加厚", "warm": "保暖", "breathable": "透气",
    "protective": "防护", "cooling": "制冷", "portable": "便携",
}

_CJK = re.compile(r"[\u3400-\u9fff]")


def _singular(word: str) -> str:
    if word in HEAD_NOUNS:
        return word
    for suffix, replacement in _PLURAL_RULES:
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            candidate = word[: -len(suffix)] + replacement
            if candidate in HEAD_NOUNS:
                return candidate
    return word


def _tokens(text: str) -> List[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def _keep(word: str) -> bool:
    """这个词能不能参与本体/修饰语的挑选。

    顺序很重要：先看 `MODIFIERS`。`adjustable` / `office` / `multi` 既是形容词
    也是正经修饰语（可调支架、办公椅、多功能包），不能因为它在
    `_ATTRIBUTE_WORDS` 里就被丢掉。
    """
    if word in _STOP_WORDS:
        return False
    if word in MODIFIERS:
        return True
    return word not in _ATTRIBUTE_WORDS


def _head_phrase(text: str) -> str:
    """砍掉属性尾巴，只留「商品本体」那一段。"""
    lowered = (text or "").lower().replace("&", " and ")
    for source, glued in _GLUED:
        lowered = lowered.replace(source, glued)
    trimmed = _TAIL_SEPARATORS.split(lowered)[0]
    for phrase in _TAIL_PHRASES:
        trimmed = trimmed.split(phrase)[0]
    return trimmed.strip()


def _pick_phrase(body: str) -> Optional[str]:
    """复合名词整体匹配。命中多个时取**位置最靠右**的那个。

    标题是「主体 + 附带物」的顺序（"Laptop Bag & Women Tote Bag Handbag"），
    按词表顺序取第一个会得到"电脑包"，而真正的主体在后面。位置相同再比长度，
    "shower curtain" 要压过 "curtain"。
    """
    best: Optional[Tuple[int, int, str]] = None
    for phrase, chinese in HEAD_PHRASES:
        match = re.search(rf"\b{re.escape(phrase)}s?\b", body)
        if not match:
            continue
        rank = (match.start(), len(phrase), chinese)
        if best is None or rank[:2] > best[:2]:
            best = rank
    return best[2] if best else None


def _pick_core(words: List[str]) -> Tuple[Optional[str], int]:
    """从后往前找本体。返回 (中文, 命中位置)。"""
    for index in range(len(words) - 1, -1, -1):
        word = _singular(words[index])
        if word in HEAD_NOUNS:
            return HEAD_NOUNS[word], index
    return None, -1


def _pick_modifier(words: List[str], before: int, room: int) -> Optional[str]:
    """在本体前面挑一个修饰语。

    只看紧邻的两个词：英语复合名词的修饰语贴在本体左边（"travel bag"），
    隔太远的多半是另一段定语——"Usb Port Armrest Box" 里的 USB 属于端口，
    不是箱子的属性，带上就成了"USB盒"。
    """
    for index in (before - 1, before - 2):
        if index < 0:
            continue
        candidate = MODIFIERS.get(words[index])
        if candidate and len(candidate) <= room:
            return candidate
    return None


def short_chinese_name(title: object, max_chars: int = MAX_CHARS) -> str:
    """把英文标题压成 ≤max_chars 个汉字的名词短语。匹配不上返回空串。"""
    text = str(title or "").strip()
    if not text:
        return ""
    # 标题里已经带了中文（有些来源表就是中文），直接取前面的汉字。
    if _CJK.search(text):
        han = "".join(_CJK.findall(text))
        return han[:max_chars] if han else ""

    body = _head_phrase(text)
    if not body:
        return ""

    # 1) 复合名词整体命中优先（"power bank" 不能拆成 power + bank）
    phrase = _pick_phrase(body)
    if phrase:
        return phrase[:max_chars]

    words = [w for w in _tokens(body) if _keep(w)]
    if not words:
        return ""

    # 2) 单个中心词
    core, index = _pick_core(words)
    if not core:
        return ""
    core = core[:max_chars]
    room = max_chars - len(core)
    if room <= 0:
        return core

    # 3) 能塞进去的修饰语
    modifier = _pick_modifier(words, index, room)
    if modifier:
        return (modifier + core)[:max_chars]
    return core
