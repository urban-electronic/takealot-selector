import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.offer_catalog_backfill import apply_offer_catalog_backfill


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class OfferCatalogBackfillTests(unittest.TestCase):
    def test_only_updates_matching_existing_product_and_is_idempotent(self):
        matched = SimpleNamespace(
            takealot_url="https://www.takealot.com/x/PLID95531935",
            tsin="PLID95531935",
            sku="9902348623136",
            chinese_product_name="皮革黑色包",
        )
        unmatched = SimpleNamespace(
            takealot_url="https://www.takealot.com/x/PLID00000000",
            tsin="PLID00000000",
            sku="",
            chinese_product_name="不应修改",
        )
        db = FakeDb()

        first = apply_offer_catalog_backfill(db, [matched, unmatched])
        self.assertEqual(first, {"matched": 1, "sku_updated": 1, "variant_labels_updated": 1})
        self.assertEqual(matched.sku.splitlines(), ["9902348623136", "9902387531072"])
        self.assertEqual(matched.chinese_product_name.splitlines(), ["黑色", "深绿色"])
        self.assertEqual(unmatched.sku, "")

        second = apply_offer_catalog_backfill(db, [matched, unmatched])
        self.assertEqual(second, {"matched": 1, "sku_updated": 0, "variant_labels_updated": 0})
        self.assertEqual(db.commits, 1)

    def test_fills_missing_skus_with_real_size_labels(self):
        product = SimpleNamespace(
            takealot_url="https://www.takealot.com/x/PLID90448715",
            tsin="PLID90448715",
            sku="",
            chinese_product_name="束腰带",
        )
        db = FakeDb()
        result = apply_offer_catalog_backfill(db, [product])
        self.assertEqual(result["matched"], 1)
        self.assertGreaterEqual(len(product.sku.splitlines()), 2)
        self.assertEqual(len(product.sku.splitlines()), len(product.chinese_product_name.splitlines()))


if __name__ == "__main__":
    unittest.main()
