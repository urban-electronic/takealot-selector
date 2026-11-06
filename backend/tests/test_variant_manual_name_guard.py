"""Execute the real pure merge helper without API, database, or seller writes."""
import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


def load_merge(reverse=False, reverse_substring=False):
    source = (Path(__file__).parents[1] / 'api/product_routes.py').read_text()
    tree = ast.parse(source)
    helpers = {'_split_tokens', '_load_variant_titles', '_merge_variant_identity'}
    nodes = [n for n in tree.body if
             isinstance(n, ast.FunctionDef) and n.name in helpers or
             isinstance(n, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id == '_VARIANT_PLACEHOLDERS'
                 for t in n.targets)]
    if reverse:
        # Remove only the protection in the return condition: same old trigger.
        for node in ast.walk(nodes[-1]):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == 'name_text' for t in node.targets):
                node.value.test = ast.Name(id='need_name', ctx=ast.Load())
    if reverse_substring:
        for node in ast.walk(nodes[-1]):
            if isinstance(node, ast.FunctionDef) and node.name == 'is_placeholder':
                node.body = ast.parse('return any(p in label for p in _VARIANT_PLACEHOLDERS)').body
    namespace = {'json': json, 're': re}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 '<actual-merge-helper>', 'exec'), namespace)
    return namespace['_merge_variant_identity']


class ManualNameGuardTests(unittest.TestCase):
    def setUp(self):
        self.merge = load_merge()
        self.receipt = [{'ok': True, 'sku': '9902416416912',
                         'variant_label': 'Gold', 'variant_title': 'Gold necklace'}]

    def product(self, sku, name):
        return SimpleNamespace(sku=sku, chinese_product_name=name, variant_titles='')

    def test_ambiguous_manual_names_are_preserved_verbatim(self):
        for sku, name in [
            ('9902416416912 9902416416929', '人工银项链'),
            ('9902416416912', '品牌 Model 银项链'),
            ('', '人工银项链'),
            ('9902416416912 9902416416929', '主商品银项链'),
        ]:
            with self.subTest(sku=sku, name=name):
                product = self.product(sku, name)
                merged_sku, changed_name, titles = self.merge(product, self.receipt)
                self.assertIsNone(changed_name)
                self.assertEqual(product.chinese_product_name, name)
                self.assertIn('9902416416912', merged_sku.split())
                if '9902416416929' in sku:
                    self.assertIn('9902416416929', merged_sku.split())
                    self.assertEqual(json.loads(titles), ['Gold necklace', ''])

    def test_failed_receipts_do_not_erase_ambiguous_name(self):
        product = self.product('9902416416912 9902416416929', '人工银项链')
        self.assertIsNone(self.merge(product, [{'ok': False}])[1])

    def test_empty_or_placeholder_name_can_still_be_filled(self):
        for name in ['', '规格待识别']:
            with self.subTest(name=name):
                product = self.product('9902416416912 9902416416929', name)
                self.assertEqual(self.merge(product, self.receipt)[1], 'Gold 规格待识别')

    def test_aligned_names_remain_mapped_when_appending(self):
        product = self.product('9902416416912 9902416416929', '金色 银色')
        receipt = [{'ok': True, 'sku': '9902416416936',
                    'variant_label': 'Blue', 'variant_title': 'Blue necklace'}]
        sku, name, titles = self.merge(product, receipt)
        self.assertEqual(sku, '9902416416912 9902416416929 9902416416936')
        self.assertEqual(name, '金色 银色 Blue')
        self.assertEqual(json.loads(titles), ['', '', 'Blue necklace'])

    def test_placeholder_substring_is_not_a_placeholder_when_appending(self):
        product = self.product('9902416416912', '主商品银项链')
        receipt = [{'ok': True, 'sku': '9902416416929',
                    'variant_label': 'Silver', 'variant_title': 'Silver necklace'}]
        sku, name, titles = self.merge(product, receipt)
        self.assertEqual(sku, '9902416416912 9902416416929')
        self.assertEqual(name, '主商品银项链 Silver')
        self.assertEqual(json.loads(titles), ['', 'Silver necklace'])
        self.assertEqual(load_merge(reverse_substring=True)(product, receipt)[1],
                         '规格待识别 Silver')

    def test_reverse_probe_restores_the_proven_name_loss(self):
        product = self.product('9902416416912 9902416416929', '人工银项链')
        self.assertEqual(load_merge(reverse=True)(product, self.receipt)[1],
                         'Gold 规格待识别')


if __name__ == '__main__':
    unittest.main()
