"""产品推荐：同类商品 + 互补配件。

选品时最费时间的不是判断一件商品好不好，而是「接下来看什么」。这个模块用
库里已有的数据把候选摆到详情页上，让人从「翻列表」变成「顺着看下去」。

四个维度，权重集中在本文件顶部，改一处即可整体调平：

1. **品类关联 0.35**——费率类别相同、标题核心词重合。费率类别是运营自己确认过
   的粗品类（Wigs / Cameras / Health…），比标题更可靠；标题核心词负责把
   「同为 Wigs 但一个是发片一个是发网」分开。
2. **属性相似 0.30**——标题词 Jaccard、价格带（对数距离）、重量/体积接近。
   价格和尺寸用对数/比值，避免「R80 和 R1800 差 1720」这种绝对差值把量纲带歪。
3. **搭配互补 0.25**——只在「配件候选」上生效：候选标题里同时出现本品类词和
   配件词（wig + net），或命中静态组合表（假发→发网/护理/支架）。
4. **行为信号 0.10**——同一批采购单里一起出现过几次、同一装箱单里同箱几次。
   没有浏览埋点，行为只能用「真金白银下过单的共现」，这比点击更可信，但样本少，
   所以权重压到最低，只做排序微调。

**去重规则**（任何一条命中即视为同一件商品，只保留得分最高的那条）：
- 自身；
- 归档商品；
- 相同 PLID（同一商品线的不同规格不重复推荐）；
- SKU 交集非空；
- 标题归一化后相同（去掉标点/大小写/多余空格）；
- 封面图相同（同一张图往往就是同一件商品的不同记录）。

**同类与配件之间也去重**：配件先占位，同一件商品不会再出现在「同类」里——
两栏各出现一次会让人以为是两件不同的东西。

本模块只读，不写库。
"""

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from models import Product
from services.takealot_seller_api import plid_from_url

# ---- 权重（改这里就能整体调平，测试里也按这里断言） ----
WEIGHT_CATEGORY = 0.35
WEIGHT_ATTRIBUTE = 0.30
WEIGHT_COMPLEMENT = 0.25
WEIGHT_BEHAVIOR = 0.10

DEFAULT_SIMILAR_LIMIT = 6
DEFAULT_COMPLEMENT_LIMIT = 4
MAX_LIMIT = 12

# 低于这个分数不进推荐：宁可不给，也不要用「都差不多」凑数。
MIN_SCORE = 0.18

# 英文停用词 + 电商标题里的无效词。只做轻量处理，不引第三方分词。
# compatible / dual / max / plus / mini / pro 这类修饰词放在这里：它们会让
# 「标题重合 6 个品类词」这种结论虚高，还会把不相干的东西凑成「同类目配件」
# （线上真机验证里「Dual Wireless Microphones」就是这么被推给无人机的）。
STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or that the to was
with without your you our we this these those new hot sale free shipping gift
set pack pcs piece pieces item items quality high premium original genuine
inch inches cm mm size large small medium mini big
compatible dual max plus mini pro multipack
""".split())

# 配件/周边词。出现在候选标题里，说明它可能是「配着买」的东西。
ACCESSORY_TERMS = frozenset("""
case cover sleeve pouch bag box holder stand mount bracket clip strap belt
cable cord adapter charger charging battery batteries powerbank filter filters
brush brushes comb cap net nets grip band bands wax oil serum spray lotion
shampoo conditioner cream gel foam pin pins clip clips ring rings hanger
replacement spare part parts kit tool tools pad pads mat mats liner liners
protector guard screen protector lens lenses cleaner cleaning organizer storage
rack shelf hook hanger connector plug socket switch remote controller
""".split())

# 静态组合表：命中即视为强互补（左右两边都会匹配）。
COMPLEMENT_RULES: Dict[str, Tuple[str, ...]] = {
    'wig': ('wig cap', 'wig net', 'wig grip', 'wig stand', 'wig brush', 'hair wax',
            'edge control', 'wig band', 'hair oil', 'hair serum', 'wig hanger',
            '发网', '发套', '假发支架', '假发护理', '发蜡'),
    'hair': ('wig cap', 'hair clip', 'hair band', 'hair oil', 'hair serum', '发网', '发夹', '发圈'),
    'drone': ('drone battery', 'propeller', 'propeller guard', 'carrying case',
              'landing gear', 'remote controller', '无人机电池', '桨叶'),
    'camera': ('camera case', 'camera mount', 'memory card', 'tripod', 'lens',
               'camera bag', 'sd card', '相机包', '三脚架'),
    'charger': ('charging cable', 'usb cable', 'power bank', 'charging pad',
                'wall adapter', '充电线', '充电头', '移动电源'),
    'vacuum': ('vacuum filter', 'vacuum bag', 'vacuum brush', 'replacement filter',
               'hepa filter', '吸尘器滤网', '尘袋'),
    'phone': ('phone case', 'screen protector', 'phone holder', 'charging cable',
              '手机壳', '钢化膜', '手机支架'),
    'watch': ('watch band', 'watch strap', 'watch case', '表带', '表壳'),
    'lamp': ('light bulb', 'led bulb', 'lamp shade', '灯泡', '灯罩'),
    'tent': ('tent pole', 'ground sheet', 'tent peg', '帐篷杆', '地布'),
    'mask': ('mask strap', 'mask filter', '口罩绳', '口罩滤芯'),
    'trimmer': ('trimmer blade', 'trimmer head', 'replacement blade', '刀头'),
    'telescope': ('telescope tripod', 'telescope mount', '望远镜支架', '三脚架'),
}

# 「同一个词，不是同一个东西」。候选标题里出现这些词时，即使与锚点共享 hair / mat
# 这类泛词，也不是它的配套——假发和「宠物毛清理手套」只共享一个 hair，
# 摆在一起就是噪音，用户会照着买。
#
# 只放**实测踩到或一眼能说清**的：宠物毛清理手套/滚筒（pet/dog/cat/lint）被推到
# 假发配套里，是 2026-10-28 线上真机验证抓到的。新增条目请附上触发它的真实标题。
SUBJECT_CONFLICTS: Dict[str, Tuple[str, ...]] = {
    'hair': ('pet', 'dog', 'cat', 'lint', 'carpet', 'floor'),
    'cover': ('car', 'sofa', 'pillow', 'book', 'phone'),
    'mat': ('floor', 'yoga', 'door', 'car'),
    'brush': ('paint', 'toilet', 'shoe'),
}

_WORD_SPLIT = re.compile(r'[a-z0-9]+')


def _words(text: str) -> List[str]:
    """把标题拆成小写词序列，供整词短语匹配用。"""
    return _WORD_SPLIT.findall((text or '').lower())


def contains_phrase(name: str, keyword: str) -> bool:
    """整词匹配短语：`hair clip` 不能命中 `hair clipper`。

    原来是 `keyword in name`，于是「Hair Clipper」被当成 hair clip 的配件——
    线上真机验证第一屏就抓到了。规则表里的词都是完整词组，按词对齐才安全；
    最后一个词允许复数（bag / bags），前面必须逐词相同。
    中文没有分词，直接子串判断。
    """
    if not keyword:
        return False
    if not keyword.isascii():
        return keyword in (name or '').lower()
    words = _words(name)
    target = _words(keyword)
    if not target:
        return False
    size = len(target)
    for index in range(len(words) - size + 1):
        if words[index:index + size - 1] != target[:-1]:
            continue
        last = words[index + size - 1]
        if last in (target[-1], target[-1] + 's', target[-1] + 'es'):
            return True
    return False


def _subject_conflict(shared_terms: Iterable[str], name: str) -> bool:
    """共享的品类词是泛词，而候选讲的是另一件事（宠物毛/车用/地板）。"""
    words = set(_words(name))
    for term in shared_terms:
        if words.intersection(SUBJECT_CONFLICTS.get(term, ())):
            return True
    return False


def _is_accessory_word(word: str) -> bool:
    """单词是不是配件词（认简单复数：pad / pads）。"""
    return word in ACCESSORY_TERMS or (
        word.endswith('s') and word[:-1] in ACCESSORY_TERMS
    ) or (
        word.endswith('es') and word[:-2] in ACCESSORY_TERMS
    )


def _matches_any(word: str, terms: Iterable[str]) -> bool:
    """单词是不是命中了给定的品类词（同样认简单复数）。"""
    if word in terms:
        return True
    if word.endswith('es') and word[:-2] in terms:
        return True
    return word.endswith('s') and word[:-1] in terms


def generic_companion(name: str, shared_terms: Iterable[str]) -> Tuple[bool, str]:
    """配件词必须**紧挨着**品类词出现（wig cap / vacuum filter / pet pad）。

    只看「有没有共同的词」会把不相干的东西凑成配套：线上真机验证里
    「Dual Wireless Microphones」被推给无人机，理由写着「同类目配件（dual）」——
    它和锚点只共享一个修饰词，配件词（pad）跟品类词根本不在一处。

    结构上说得通的配套，配件词总是跟着品类词出现。间隔放宽到 2 个词，
    覆盖 "vacuum cleaner filter" 这种中间夹一个词的写法。

    多个候选时取**后面最远**的那个（+2 → +1 → -1 → -2）：英文标题里真正的中心词
    一般在修饰语后面，"vacuum cleaner filter" 的配件是 filter，不是 cleaner。
    """
    terms = set(shared_terms)
    words = _words(name)
    if not words or not terms:
        return False, ''
    for index, word in enumerate(words):
        if not _matches_any(word, terms):
            continue
        for offset in (2, 1, -1, -2):
            near = index + offset
            if 0 <= near < len(words) and _is_accessory_word(words[near]):
                return True, '同类目配件（%s + %s）' % (word, words[near])
    return False, ''


def _norm(text: str) -> str:
    """标题归一化：小写、标点转空格、压缩空白。用于去重比较。"""
    return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-z\u4e00-\u9fff]+', ' ', (text or '').lower())).strip()


def tokens(text: str) -> set:
    """标题词集合。英文按词切，中文按 2-gram，过滤停用词与纯数字。"""
    out = set()
    for word in re.findall(r'[a-z][a-z0-9\-]{2,}', (text or '').lower()):
        if word not in STOPWORDS:
            out.add(word)
    for chunk in re.findall(r'[\u4e00-\u9fff]{2,}', text or ''):
        for index in range(len(chunk) - 1):
            out.add(chunk[index:index + 2])
    return out


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _price_score(a: Optional[float], b: Optional[float]) -> float:
    """价格带接近度：按对数比打分，R80 与 R120 的差距和 R800 与 R1200 一样。"""
    if not a or not b or a <= 0 or b <= 0:
        return 0.0
    ratio = max(a, b) / min(a, b)
    return max(0.0, 1.0 - math.log(ratio) / math.log(3.0))


def _size_score(a: Product, b: Product) -> float:
    """尺寸/重量接近度：两边都有值才比，缺一边就不给分（不猜）。"""
    scores = []
    for attr in ('actual_weight_kg', 'volume_cbm'):
        left, right = getattr(a, attr, None), getattr(b, attr, None)
        if left and right and left > 0 and right > 0:
            scores.append(_price_score(left, right))
    return sum(scores) / len(scores) if scores else 0.0


def category_terms(product: Product) -> set:
    """品类核心词：标题词去掉配件词。用来判断「是不是同一类东西」。"""
    return tokens(product.product_name or '') - ACCESSORY_TERMS


def is_accessory_of(candidate: Product, anchor: Product) -> Tuple[bool, str]:
    """候选是不是锚点的配件。返回 (是否, 说明)。

    两条判据：命中静态组合表，或候选标题同时包含锚点品类词与配件词。
    只看标题，不猜——宁可漏掉，也不要把两件不相干的东西摆成「配套」。

    两条判据之前都先过一道「主题冲突」：共享的品类词是 hair 这种泛词，
    而候选讲的是宠物毛/车用/地板，那就不是同一件事。
    """
    name = (candidate.product_name or '').lower()
    anchor_terms = category_terms(anchor)
    candidate_terms = tokens(candidate.product_name or '')
    shared = candidate_terms & anchor_terms
    if _subject_conflict(shared, name):
        return False, ''
    for term in anchor_terms:
        # 只查表，不设长度门槛：原来是 len(term) < 4 就跳过，于是 'wig'（3 个字母）
        # 的整张组合表从来没被查过——假发是这里最主要的品类，它的配件全靠后面的
        # 泛判据兜着，规则表等于死代码。表是白名单，短词也不会误伤。
        rules = COMPLEMENT_RULES.get(term)
        if rules:
            for keyword in rules:
                if contains_phrase(name, keyword):
                    return True, '常见配套：%s → %s' % (term, keyword)
    # 配件词是否真的挨着品类词，交给 generic_companion 判；这里不再先用 tokens
    # 做一遍「有没有配件词」的预筛——tokens 认不出复数（pads），会和下面的判据打架。
    paired, reason = generic_companion(name, shared)
    if paired:
        return True, reason
    return False, ''


def _cooccurrence(db, store_id: str) -> Dict[str, Dict[str, int]]:
    """行为信号：同一采购单里一起出现过几次。

    没有浏览埋点，只能拿「真下过单的共现」当行为。样本少，所以只做微调权重。
    """
    from models import ProcurementRecord

    grouped: Dict[str, set] = {}
    rows = (db.query(ProcurementRecord)
            .filter(ProcurementRecord.store_id == store_id,
                    ProcurementRecord.product_id.isnot(None))
            .all())
    for row in rows:
        # 采购记录没有批次号，只能用日期当批次。同一商品同一天的多笔采购视为一次，
        # 避免「一次买 5 箱」把共现次数刷高。
        day = str(row.recorded_at or '')[:10]
        if not day:
            day = row.status_updated_at.isoformat()[:10] if row.status_updated_at else ''
        grouped.setdefault('%s|%s' % (row.product_id, day), set()).add(row.product_id)

    by_batch: Dict[str, set] = {}
    for key, ids in grouped.items():
        batch = key.split('|')[1]
        by_batch.setdefault(batch, set()).update(ids)

    pairs: Dict[str, Dict[str, int]] = {}
    for ids in by_batch.values():
        if len(ids) < 2:
            continue
        for left in ids:
            for right in ids:
                if left == right:
                    continue
                pairs.setdefault(left, {})
                pairs[left][right] = pairs[left].get(right, 0) + 1
    return pairs


def _identity_keys(product: Product) -> List[str]:
    """同一件商品的判据集合。任一相同就算重复。"""
    keys = []
    plid = (getattr(product, 'plid', '') or '').strip() or plid_from_url(product.takealot_url)
    if plid:
        keys.append('plid:' + plid)
    sku = (product.sku or '').strip()
    if sku:
        for part in re.split(r'[\s,，;；]+', sku):
            if part:
                keys.append('sku:' + part)
    name = _norm(product.product_name or '')
    if name:
        keys.append('name:' + name)
    image = (product.product_image_url or '').strip()
    if image:
        keys.append('img:' + image)
    return keys


def _score(anchor: Product, candidate: Product, shared_terms: set,
           cooccur: int) -> Tuple[float, List[str], bool]:
    """给单个候选打分。返回 (总分, 命中理由, 是否配件)。"""
    reasons: List[str] = []

    category = 0.0
    if anchor.fee_category and anchor.fee_category == candidate.fee_category:
        category = 1.0
        reasons.append('同一费率类别：%s' % anchor.fee_category)
    else:
        category = 0.4 if shared_terms else 0.0

    anchor_terms = category_terms(anchor)
    candidate_terms = category_terms(candidate)
    title_overlap = _jaccard(anchor_terms, candidate_terms)
    attribute = 0.55 * title_overlap + 0.25 * _price_score(
        anchor.actual_sale_price_zar, candidate.actual_sale_price_zar) + 0.20 * _size_score(anchor, candidate)
    if title_overlap >= 0.25:
        reasons.append('标题重合 %d 个品类词' % len(anchor_terms & candidate_terms))
    if _price_score(anchor.actual_sale_price_zar, candidate.actual_sale_price_zar) >= 0.6:
        reasons.append('价格带接近')

    accessory, accessory_reason = is_accessory_of(candidate, anchor)
    complement = 1.0 if accessory else 0.0
    if accessory:
        reasons.append(accessory_reason)

    behavior = min(1.0, cooccur / 3.0)
    if cooccur:
        reasons.append('曾同批采购 %d 次' % cooccur)

    score = (WEIGHT_CATEGORY * category + WEIGHT_ATTRIBUTE * attribute +
             WEIGHT_COMPLEMENT * complement + WEIGHT_BEHAVIOR * behavior)
    return score, reasons, accessory


def recommend(db, store_id: str, anchor: Product,
              similar_limit: int = DEFAULT_SIMILAR_LIMIT,
              complement_limit: int = DEFAULT_COMPLEMENT_LIMIT) -> Dict[str, Any]:
    """算出同类与互补两组推荐。只读。"""
    similar_limit = max(0, min(int(similar_limit), MAX_LIMIT))
    complement_limit = max(0, min(int(complement_limit), MAX_LIMIT))

    candidates = (db.query(Product)
                  .filter(Product.store_id == store_id, Product.is_archived == False)  # noqa: E712
                  .all())
    cooccur_map = _cooccurrence(db, store_id).get(anchor.id, {})

    taken: set = set(_identity_keys(anchor))
    anchor_terms = category_terms(anchor)

    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        if candidate.id == anchor.id:
            continue
        keys = _identity_keys(candidate)
        if any(key in taken for key in keys):
            # 库里本来就存在同一件商品的多条记录，只留一条，不重复占位。
            continue
        shared = anchor_terms & category_terms(candidate)
        score, reasons, accessory = _score(anchor, candidate, shared, cooccur_map.get(candidate.id, 0))
        if score < MIN_SCORE:
            continue
        scored.append({
            'product': candidate,
            'score': round(score, 4),
            'reasons': reasons,
            'accessory': accessory,
        })

    scored.sort(key=lambda item: (item['score'], item['product'].product_no or 0), reverse=True)

    def to_payload(item) -> Dict[str, Any]:
        product = item['product']
        return {
            'product_id': product.id,
            'product_no': product.product_no,
            'name': product.product_name or '',
            'chinese_name': product.chinese_product_name or '',
            'sku': product.sku or '',
            'plid': (getattr(product, 'plid', '') or '').strip(),
            'image_url': product.product_image_url or '',
            'price_zar': product.actual_sale_price_zar,
            'profit_margin': product.profit_margin,
            'fee_category': product.fee_category,
            'takealot_url': product.takealot_url or '',
            'score': item['score'],
            'reasons': item['reasons'],
        }

    complements: List[Dict[str, Any]] = []
    similar: List[Dict[str, Any]] = []
    used: set = set()

    for item in scored:
        if len(complements) >= complement_limit:
            break
        if not item['accessory']:
            continue
        keys = _identity_keys(item['product'])
        if any(key in used for key in keys):
            continue
        used.update(keys)
        complements.append(to_payload(item))

    for item in scored:
        if len(similar) >= similar_limit:
            break
        if item['accessory']:
            continue
        keys = _identity_keys(item['product'])
        if any(key in used for key in keys):
            continue
        used.update(keys)
        similar.append(to_payload(item))

    return {
        'similar': similar,
        'complement': complements,
        'meta': {
            'strategy': '品类关联 + 属性相似 + 搭配互补 + 行为共现，加权求和后去重截断',
            'weights': {
                'category': WEIGHT_CATEGORY,
                'attribute': WEIGHT_ATTRIBUTE,
                'complement': WEIGHT_COMPLEMENT,
                'behavior': WEIGHT_BEHAVIOR,
            },
            'dedup': '自身/归档/同 PLID/同 SKU/同标题/同图，任一相同只留最高分；同类与配件之间也不重复',
            'min_score': MIN_SCORE,
            'limits': {'similar': similar_limit, 'complement': complement_limit},
            'candidate_count': len(candidates),
            'scored_count': len(scored),
            'anchor': {
                'product_id': anchor.id,
                'product_no': anchor.product_no,
                'name': anchor.product_name or '',
                'fee_category': anchor.fee_category,
            },
        },
    }
