"""Read-only SKU verification against the dated seller export, never page guesses."""
from services.offer_catalog_backfill import load_offer_catalog, _plid, _tokens


def audit_skus(products, catalog=None):
    catalog = load_offer_catalog() if catalog is None else catalog
    result = dict(source="Takealot卖家导出表 2026-10-07", total=0, verified=0,
                  missing=0, mismatch=0, unmatched=0, issues=[])
    for product in products:
        result["total"] += 1
        actual = set(_tokens(product.sku))
        group = catalog.get(_plid(product.takealot_url, product.tsin))
        expected = {str(v.get("sku") or "").strip() for v in (group or {}).get("variants", [])}
        expected.discard("")
        if not actual:
            status = "missing"
        elif not expected:
            status = "unmatched"
        elif actual - expected:
            status = "mismatch"
        else:
            status = "verified"
        result[status] += 1
        if status != "verified":
            result["issues"].append(dict(product_id=product.id, product_no=product.product_no,
                                        status=status, current_skus=sorted(actual),
                                        reference_skus=sorted(expected)))
    return result
