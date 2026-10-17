"""Display names matched to an exact SKU; never translate or write product data."""
import re

from services.offer_catalog_backfill import _common_prefix


def variant_base(titles):
    """同一商品页多条分支标题的公共前缀 = 无定语形态。

    变体之间只差定语（Pink / L / 3 Pack），把它们逐个平铺成一行，
    界面上就会出现 24 行几乎相同的标题——用户看到的「冗余」有一半来自这里。
    公共前缀就是去掉定语之后的商品本体，剩下的尾巴才是定语。

    只做字符串裁剪，不猜测、不翻译：前缀本身仍然来自真实标题。
    """
    cleaned = [str(t or '').strip() for t in titles if str(t or '').strip()]
    if len(cleaned) < 2:
        return ''
    base = _common_prefix(cleaned)
    # _common_prefix 保证不在单词中间截断，但可能停在 " -" 这种分隔符上。
    return re.sub(r'[\s\-–—,/+&]+$', '', base).strip()


def variant_view(product, offers, labels):
    """把一个商品页下的多条分支收成「本体 + 定语」两层。

    labels 是 {sku: 中文定语}，已由 chinese_name 校验过中文。
    单分支商品没有「本体/定语」之分，base 返回空串，渲染层据此退回原名。
    """
    base = variant_base([o.get('title') or product.product_name for o in offers])
    count = len(offers)
    siblings = [dict(sku=str(o.get('sku') or ''), label=labels.get(str(o.get('sku') or '')) or '',
                     price=o.get('price'), stock=o.get('stock')) for o in offers]
    return {'base': base if count > 1 else '', 'count': count, 'siblings': siblings}


def chinese_name(product, sku):
    raw = (product.chinese_product_name or '').strip()
    if not raw:
        return None, 'missing'
    if not re.search(r'[\u3400-\u9fff]', raw):
        return None, 'invalid'
    skus = [x for x in re.split(r'[\s,，;；/]+', (product.sku or '').strip()) if x]
    if skus.count(str(sku)) != 1:
        return None, 'ambiguous'
    if len(skus) == 1:
        return raw, 'ok'
    labels = [x for x in re.split(r'[\s,，;；/]+', raw) if x]
    if len(labels) != len(skus):
        return None, 'ambiguous'
    label = labels[skus.index(str(sku))]
    return (label, 'ok') if re.search(r'[\u3400-\u9fff]', label) else (None, 'invalid')
