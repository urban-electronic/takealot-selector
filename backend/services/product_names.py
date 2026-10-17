"""Display names matched to an exact SKU; never translate or write product data."""
import re

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
