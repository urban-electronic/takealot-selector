"""站外货源匹配：把 1688 / Takealot 上登记的外部商品挂到对应商品下面。

和 `product_recommendation` 的区别是**数据来源不同**：那边在自家库里找邻居，
这边读的是 `external_offers` 表——站外看到的、自己没上架的东西。
分词与整词匹配直接复用那边的实现，避免出现两套判定口径。
"""

from typing import Any, Dict, Iterable, List, Optional

from models import ExternalOffer, Product
from services.product_recommendation import contains_phrase, tokens

PLATFORM_1688 = '1688'
PLATFORM_TAKEALOT = 'takealot'
PLATFORMS = (PLATFORM_1688, PLATFORM_TAKEALOT)

# 每个平台一栏，各自独立上限；加起来不超过 20 条，避免详情页被外链淹没。
DEFAULT_LIMIT = 8
MAX_LIMIT = 20

CURRENCY_OF = {PLATFORM_1688: 'CNY', PLATFORM_TAKEALOT: 'ZAR'}


def parse_keywords(raw: Optional[str]) -> List[str]:
    """逗号（中英文都认）分隔的关键词，去空、去重、保序。"""
    if not raw:
        return []
    parts = str(raw).replace('，', ',').replace(';', ',').replace('、', ',').split(',')
    seen, out = set(), []
    for part in parts:
        word = part.strip().lower()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _product_text(product: Product) -> str:
    return ' '.join(
        str(value or '')
        for value in (product.product_name, product.chinese_product_name)
    ).lower()


def matched_keywords(offer: ExternalOffer, product: Product) -> List[str]:
    """这条站外货源为什么挂在这个商品下面——返回命中的关键词。

    中文按子串（中文没有词边界），英文走整词匹配：否则 `cap` 会命中
    `capacity`、`clip` 会命中 `clipper`，正是推荐模块踩过的坑。
    """
    text = _product_text(product)
    if not text.strip():
        return []
    words = set(tokens(text))
    hits = []
    for keyword in parse_keywords(offer.keywords):
        if keyword.isascii():
            if ' ' in keyword:
                if contains_phrase(text, keyword):
                    hits.append(keyword)
            elif keyword in words:
                hits.append(keyword)
        elif keyword in text:
            hits.append(keyword)
    return hits


def serialize(offer: ExternalOffer, hits: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        'id': offer.id,
        'platform': offer.platform,
        'url': offer.url,
        'title': offer.title or '',
        'title_cn': offer.title_cn or '',
        'price': offer.price,
        'currency': offer.currency or CURRENCY_OF.get(offer.platform, ''),
        'moq': offer.moq,
        'image_url': offer.image_url or '',
        'keywords': parse_keywords(offer.keywords),
        'usage': offer.usage or '',
        'scenario': offer.scenario or '',
        'reason': offer.reason or '',
        'matched': hits or [],
    }


def _sort_key(item: Dict[str, Any]):
    """排序依据：命中关键词多的先 → 有价格的先 → 新录入的先。

    命中词数是最强的相关信号；没有价格的条目信息量低，排在后面但仍展示
    （1688 的批发价经常要问客服，缺价格不代表没用）。
    """
    return (
        -len(item['matched']),
        0 if item['price'] is not None else 1,
        item['price'] if item['price'] is not None else 0.0,
        item['id'],
    )


def recommend_external(
    db,
    store_id: str,
    product: Product,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """按平台分栏返回站外货源。

    只返回**关键词命中**的条目。没有任何命中就返回空——宁可不展示，
    也不要为了填满版面把不相干的站外商品塞进来。
    """
    limit = max(0, min(int(limit), MAX_LIMIT))
    rows = (
        db.query(ExternalOffer)
        .filter(ExternalOffer.store_id == store_id, ExternalOffer.is_active.is_(True))
        .all()
    )

    buckets: Dict[str, List[Dict[str, Any]]] = {platform: [] for platform in PLATFORMS}
    unscored = 0
    for row in rows:
        hits = matched_keywords(row, product)
        if not hits:
            unscored += 1
            continue
        platform = (row.platform or '').strip().lower()
        if platform not in buckets:
            buckets.setdefault(platform, [])
        buckets[platform].append(serialize(row, hits))

    for platform in buckets:
        buckets[platform].sort(key=_sort_key)
        if limit:
            buckets[platform] = buckets[platform][:limit]
        else:
            buckets[platform] = []

    return {
        'sourcing': buckets.get(PLATFORM_1688, []),
        'market': buckets.get(PLATFORM_TAKEALOT, []),
        'meta': {
            'total': len(rows),
            'matched': sum(len(v) for v in buckets.values()),
            'unmatched': unscored,
            'limit_per_platform': limit,
            'match_rule': '关键词命中（英文整词、中文子串），不看品牌词',
            'sort': '命中关键词数 → 有价格优先 → 价格低优先 → 录入时间',
        },
    }
