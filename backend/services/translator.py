"""
自动翻译：英文产品名 -> 中文（10字符以内）
使用 Google Translate 免费接口
"""

import httpx
import re


# 常见电商产品词汇的预置中文翻译表（避免翻译API调用过频）
PRESET_TRANSLATIONS: dict[str, str] = {
    "leather": "皮革",
    "laptop": "笔记本",
    "briefcase": "公文包",
    "bag": "包",
    "backpack": "背包",
    "case": "壳",
    "cover": "盖",
    "mirror": "后视镜",
    "phone": "手机",
    "cable": "数据线",
    "charger": "充电器",
    "adapter": "转换器",
    "wireless": "无线",
    "android": "安卓",
    "auto": "汽车",
    "bluetooth": "蓝牙",
    "headphone": "耳机",
    "earphone": "耳塞",
    "speaker": "音箱",
    "keyboard": "键盘",
    "mouse": "鼠标",
    "stand": "支架",
    "holder": "支架",
    "mount": "支架",
    "light": "灯",
    "lamp": "灯",
    "led": "LED灯",
    "bulb": "灯泡",
    "fan": "风扇",
    "cooler": "散热器",
    "pad": "垫",
    "mat": "垫",
    "toy": "玩具",
    "doll": "玩偶",
    "game": "游戏",
    "controller": "手柄",
    "console": "主机",
    "tool": "工具",
    "kit": "套件",
    "set": "套装",
    "box": "盒",
    "organizer": "收纳盒",
    "shelf": "架子",
    "rack": "架子",
    "hook": "挂钩",
    "hanger": "衣架",
    "cup": "杯子",
    "mug": "马克杯",
    "bottle": "瓶",
    "water": "水",
    "stainless": "不锈钢",
    "steel": "钢",
    "glass": "玻璃",
    "plastic": "塑料",
    "silicone": "硅胶",
    "rubber": "橡胶",
    "wood": "木",
    "bamboo": "竹",
    "metal": "金属",
    "aluminum": "铝合金",
    "cotton": "棉",
    "polyester": "涤纶",
    "nylon": "尼龙",
    "fabric": "布",
    "mesh": "网",
    "black": "黑",
    "white": "白",
    "red": "红",
    "blue": "蓝",
    "green": "绿",
    "yellow": "黄",
    "pink": "粉",
    "gray": "灰",
    "grey": "灰",
    "purple": "紫",
    "orange": "橙",
    "brown": "棕",
    "gold": "金",
    "silver": "银",
    "dark": "深",
    "light": "浅",
    "large": "大",
    "small": "小",
    "medium": "中",
    "mini": "迷你",
    "portable": "便携",
    "foldable": "折叠",
    "adjustable": "可调",
    "universal": "通用",
    "heavy": "重型",
    "duty": "耐用",
    "premium": "高端",
    "luxury": "奢华",
    "basic": "基础",
    "standard": "标准",
    "pro": "专业",
    "professional": "专业",
    "smart": "智能",
    "digital": "数码",
    "electric": "电动",
    "electronic": "电子",
    "usb": "USB",
    "type-c": "Type-C",
    "hdmi": "HDMI",
    "car": "车载",
    "vehicle": "车载",
    "home": "家用",
    "office": "办公",
    "kitchen": "厨房",
    "bathroom": "浴室",
    "bedroom": "卧室",
    "outdoor": "户外",
    "indoor": "室内",
    "travel": "旅行",
    "sport": "运动",
    "gym": "健身",
    "fitness": "健身",
    "yoga": "瑜伽",
    "running": "跑步",
    "camping": "露营",
    "hiking": "登山",
    "fishing": "钓鱼",
    "cycling": "骑行",
    "swimming": "游泳",
    "golf": "高尔夫",
    "tennis": "网球",
    "football": "足球",
    "basketball": "篮球",
    "women": "女士",
    "men": "男士",
    "kids": "儿童",
    "baby": "婴儿",
    "adult": "成人",
    "pet": "宠物",
    "dog": "狗",
    "cat": "猫",
    "bird": "鸟",
    "fish": "鱼",
    "food": "食品",
    "drink": "饮品",
    "snack": "零食",
    "candy": "糖果",
    "chocolate": "巧克力",
    "coffee": "咖啡",
    "tea": "茶",
    "oil": "油",
    "cream": "霜",
    "lotion": "乳液",
    "soap": "皂",
    "shampoo": "洗发水",
    "toothbrush": "牙刷",
    "toothpaste": "牙膏",
    "towel": "毛巾",
    "blanket": "毯子",
    "pillow": "枕头",
    "sheet": "床单",
    "curtain": "窗帘",
    "carpet": "地毯",
    "rug": "地毯",
    "mat" : "垫子",
    "clock": "钟",
    "watch": "手表",
    "ring": "戒指",
    "necklace": "项链",
    "bracelet": "手链",
    "earring": "耳环",
    "chain": "链",
    "belt": "腰带",
    "wallet": "钱包",
    "purse": "钱包",
    "umbrella": "伞",
    "glasses": "眼镜",
    "sunglass": "墨镜",
    "mask": "口罩",
    "hat": "帽子",
    "cap": "帽",
    "scarf": "围巾",
    "glove": "手套",
    "sock": "袜子",
    "shoe": "鞋",
    "boot": "靴",
    "sandal": "凉鞋",
    "slipper": "拖鞋",
    "shirt": "衬衫",
    "tshirt": "T恤",
    "jacket": "夹克",
    "coat": "外套",
    "dress": "裙子",
    "skirt": "半裙",
    "pant": "裤子",
    "jeans": "牛仔裤",
    "short": "短裤",
    "sweater": "毛衣",
    "hoodie": "卫衣",
    "underwear": "内衣",
    "bra": "文胸",
    "bikini": "比基尼",
    "swimsuit": "泳衣",
    "suit": "西装",
    "uniform": "制服",
    "costume": "服装",
    "luggage": "行李箱",
    "suitcase": "行李箱",
    "baggage": "行李",
    "camera": "相机",
    "lens": "镜头",
    "tripod": "三脚架",
    "microphone": "麦克风",
    "speaker": "音箱",
    "amplifier": "放大器",
    "projector": "投影仪",
    "monitor": "显示器",
    "screen": "屏幕",
    "display": "显示屏",
    "printer": "打印机",
    "scanner": "扫描仪",
    "router": "路由器",
    "modem": "猫",
    "switch": "交换机",
    "server": "服务器",
    "drive": "硬盘",
    "ssd": "固态硬盘",
    "hdd": "机械硬盘",
    "memory": "内存",
    "ram": "内存条",
    "cpu": "处理器",
    "gpu": "显卡",
    "motherboard": "主板",
    "power": "电源",
    "supply": "电源",
    "battery": "电池",
    "solar": "太阳能",
    "panel": "面板",
    "inverter": "逆变器",
    "converter": "转换器",
    "transformer": "变压器",
    "sensor": "传感器",
    "detector": "探测器",
    "meter": "仪表",
    "gauge": "量表",
    "thermometer": "温度计",
    "scale": "秤",
    "pump": "泵",
    "motor": "电机",
    "engine": "发动机",
    "generator": "发电机",
    "compressor": "压缩机",
    "valve": "阀",
    "pipe": "管",
    "tube": "管",
    "hose": "软管",
    "fitting": "接头",
    "connector": "连接器",
    "coupling": "联轴器",
    "bearing": "轴承",
    "gear": "齿轮",
    "spring": "弹簧",
    "screw": "螺丝",
    "bolt": "螺栓",
    "nut": "螺母",
    "washer": "垫圈",
    "nail": "钉子",
    "drill": "钻",
    "saw": "锯",
    "hammer": "锤",
    "wrench": "扳手",
    "plier": "钳子",
    "cutter": "切割器",
    "knife": "刀",
    "scissor": "剪刀",
    "blade": "刀片",
    "tape": "胶带",
    "glue": "胶水",
    "adhesive": "粘合剂",
    "sealant": "密封胶",
    "paint": "漆",
    "brush": "刷子",
    "roller": "滚轮",
    "wheel": "轮子",
    "caster": "脚轮",
    "axle": "轴",
    "shaft": "轴",
    "pulley": "滑轮",
    "chain": "链条",
    "rope": "绳",
    "cord": "线",
    "wire": "线",
    "strap": "绑带",
    "buckle": "扣",
    "clip": "夹子",
    "clamp": "夹具",
    "vise": "台钳",
    "lock": "锁",
    "key": "钥匙",
    "latch": "闩",
    "hinge": "铰链",
    "handle": "拉手",
    "knob": "旋钮",
    "button": "按钮",
    "switch": "开关",
    "socket": "插座",
    "plug": "插头",
    "outlet": "插座",
    "extension": "延长线",
    "adapter": "适配器",
    "dongle": "转接头",
    "hub": "集线器",
    "splitter": "分线器",
    "repeater": "中继器",
    "extender": "扩展器",
    "booster": "增强器",
    "filter": "过滤器",
    "purifier": "净化器",
    "cleaner": "清洁器",
    "vacuum": "吸尘器",
    "mop": "拖把",
    "broom": "扫帚",
    "dustpan": "簸箕",
    "trash": "垃圾桶",
    "bin": "垃圾桶",
    "bag": "袋",
    "sack": "袋",
    "pouch": "小袋",
    "envelope": "信封",
    "folder": "文件夹",
    "binder": "活页夹",
    "notebook": "笔记本",
    "notepad": "记事本",
    "pen": "笔",
    "pencil": "铅笔",
    "marker": "记号笔",
    "highlighter": "荧光笔",
    "eraser": "橡皮",
    "ruler": "尺子",
    "calculator": "计算器",
    "calendar": "日历",
    "planner": "计划本",
    "sticker": "贴纸",
    "label": "标签",
    "tag": "标签",
    "badge": "徽章",
    "patch": "补丁",
    "pin": "别针",
    "magnet": "磁铁",
    "suction": "吸盘",
    "velcro": "魔术贴",
    "zipper": "拉链",
    "lace": "鞋带",
    "elastic": "松紧带",
    "ribbon": "丝带",
    "bow": "蝴蝶结",
}

# 用于翻译特别长的或不常见的英文名称
async def translate_via_google(english_name: str) -> str:
    """调用 Google Translate 免费接口"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": "en",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": english_name,
                },
                timeout=10,
            )
            result = resp.json()
            translated = "".join([item[0] for item in result[0] if item[0]])
            return translated.strip()
    except Exception:
        return ""


def translate_with_dict(english_name: str) -> str:
    """使用预置词典翻译，提取关键词生成中文名"""
    if not english_name:
        return ""

    # 按空格/连字符/斜杠分词
    tokens = re.split(r'[\s\-/,\+]+', english_name.lower())
    
    chinese_words = []
    seen = set()
    
    for token in tokens:
        token = token.strip()
        if not token or token in seen:
            continue
        if token in PRESET_TRANSLATIONS:
            chinese_words.append(PRESET_TRANSLATIONS[token])
            seen.add(token)
    
    if not chinese_words:
        # 提取关键特征词
        key_tokens = [t for t in tokens if len(t) > 2 and t not in {
            'for', 'and', 'the', 'with', 'new', 'hot', 'inch', 'mm', 'cm', 'kg', 'pcs',
            'set', 'lot', 'pack', 'color', 'size', 'type', 'style', 'model', 'version',
            'brand', 'quality', 'high', 'best', 'top', 'free', 'sale', 'cheap', 'price'
        }]
        
        for token in key_tokens[:3]:
            if token in PRESET_TRANSLATIONS:
                chinese_words.append(PRESET_TRANSLATIONS[token])
            elif len(token) <= 4:
                # 短词尝试词典匹配
                if token in PRESET_TRANSLATIONS:
                    chinese_words.append(PRESET_TRANSLATIONS[token])
    
    return "".join(chinese_words)


def translate_to_chinese(english_name: str, max_chars: int = 10) -> str:
    """
    翻译英文产品名到中文，限制 max_chars 字符。
    优先使用预置词典翻译，失败时返回空（由调用方决定是否调用API）。
    """
    if not english_name or not english_name.strip():
        return ""

    # 先尝试词典翻译
    result = translate_with_dict(english_name.strip())
    
    # 截断
    if len(result) > max_chars:
        result = result[: max_chars - 1] + "…"
    
    return result


# 同步版本（用于非 async 上下文），内部调用 httpx 同步
def translate_to_chinese_sync(english_name: str, max_chars: int = 10) -> str:
    """同步版本，先词典翻译，失败则调用 Google API"""
    dict_result = translate_to_chinese(english_name, max_chars)
    if dict_result:
        return dict_result
    
    # 词典没命中，尝试 Google API
    try:
        import requests
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "zh-CN",
                "dt": "t",
                "q": english_name.strip(),
            },
            timeout=10,
        )
        result = resp.json()
        translated = "".join([item[0] for item in result[0] if item[0]])
        translated = translated.strip()
        
        if len(translated) > max_chars:
            translated = translated[: max_chars - 1] + "…"
        
        return translated
    except Exception:
        return ""
