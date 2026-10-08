import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.sku_audit import audit_skus


class SkuAuditTests(unittest.TestCase):
    def test_distinguishes_missing_mismatch_and_unknown_without_mutation(self):
        rows = [SimpleNamespace(id=str(i), product_no=i, sku=sku,
                                takealot_url=f'https://www.takealot.com/x/PLID{plid}', tsin='')
                for i, (sku, plid) in enumerate([('a', '1'), ('a\nb', '1'),
                                               ('bad', '1'), ('', '1'), ('a', '2')])]
        original = [p.sku for p in rows]
        result = audit_skus(rows, {'1': {'variants': [{'sku': 'a'}, {'sku': 'b'}]}})
        self.assertEqual([result[k] for k in ['total', 'verified', 'missing', 'mismatch', 'unmatched']], [5, 2, 1, 1, 1])
        self.assertEqual([p.sku for p in rows], original)
        self.assertEqual(result['issues'][0]['reference_skus'], ['a', 'b'])

    def test_empty_reference_is_unmatched_not_verified(self):
        row = SimpleNamespace(id='x', product_no=1, sku='a', takealot_url='PLID1', tsin='')
        self.assertEqual(audit_skus([row], {'1': {'variants': [{'sku': ''}]}})['unmatched'], 1)
