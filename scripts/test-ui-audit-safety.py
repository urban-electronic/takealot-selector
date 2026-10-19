"""Execute audit failure and interception paths without browser/network/credentials."""
import asyncio
import contextlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

class FakeBrowser:
    def __init__(self): self.chromium = self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def launch(self, **kwargs): return self
    async def new_context(self, **kwargs): return self
    async def new_page(self): return self
    async def route(self, *args): pass
    async def add_init_script(self, *args): pass
    async def goto(self, *args, **kwargs): raise RuntimeError('fixture: session unavailable')
    async def close(self): pass

class ReadyLocator:
    def __init__(self, missing=False): self.missing = missing; self.first = self
    async def wait_for(self, **kwargs):
        if self.missing: raise RuntimeError('fixture text is not rendered')
    def get_by_role(self, role, **kwargs):
        return ReadyLocator(self.missing or role != 'cell' or kwargs.get('name') != '1')
    # 审计在测量前会点开「更多」面板和每行「详情」（AUDIT-CONTRAST-02），
    # 桩不实现这两个动作就会把整条 /auto-pricing 记成 ERROR——测的就不是审计判据了。
    def locator(self, *args): return self
    async def click(self): pass

class ReadyBrowser(FakeBrowser):
    async def goto(self, url, **kwargs): self.current = url
    def get_by_role(self, *args, **kwargs): return ReadyLocator()
    def locator(self, *args): return ReadyLocator()
    def get_by_text(self, text, **kwargs):
        # Procurement displays product_no, not SKU. Other key fixture rows display SKU.
        return ReadyLocator(self.current.endswith('/procurement') and text == 'TEST-001')
    async def wait_for_load_state(self, *args, **kwargs): pass
    async def evaluate(self, *args):
        return {'lowContrastCount':0, 'controls':{'tooSmallCount':0}}

class MissingProcurement(ReadyBrowser):
    def locator(self, selector):
        return ReadyLocator(selector == '.procurement-table-scroll tbody')

async_module = types.ModuleType('playwright.async_api')
async_module.async_playwright = FakeBrowser
sync_module = types.ModuleType('playwright.sync_api')
sync_module.sync_playwright = lambda: None
with patch.dict(sys.modules, {'playwright':types.ModuleType('playwright'),
                            'playwright.async_api':async_module, 'playwright.sync_api':sync_module}):
    audit = runpy.run_path(str(Path(__file__).with_name('ui_contrast_audit.py')))

class Route:
    def __init__(self, method, url):
        self.request = types.SimpleNamespace(method=method, url=url)
        self.action = None
    async def abort(self): self.action = 'abort'
    async def continue_(self): self.action = 'continue'
    async def fulfill(self, **kwargs): self.action = 'fixture'

class SafetyTests(unittest.TestCase):
    # 下面三个数字是「覆盖面锁」：11 条路由 × 3 档宽度 = 33 个面。
    # 写死而不是从 ROUTES 推导，是为了让「悄悄少测一条路由」也变成失败——
    # 推导出来的数字会跟着一起变小，等于没有断言。
    SURFACES = 33

    def test_procurement_without_sku_can_reach_measurement(self):
        fn = audit['main']
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.dict(fn.__globals__, {'OUT':directory, 'async_playwright':ReadyBrowser}):
                asyncio.run(fn())
            self.assertIn(f'"measured": {self.SURFACES}', output.getvalue())
            self.assertIn('"errors": 0', output.getvalue())

    def test_missing_procurement_record_still_fails(self):
        fn = audit['main']
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.dict(fn.__globals__, {'OUT':directory, 'async_playwright':MissingProcurement}):
                with self.assertRaisesRegex(RuntimeError, '视觉审计未通过'):
                    asyncio.run(fn())
            self.assertIn(f'"measured": {self.SURFACES - 3}', output.getvalue())
            self.assertIn('"errors": 3', output.getvalue())
    def test_all_navigation_errors_fail_instead_of_zero_success(self):
        fn = audit['main']
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.dict(fn.__globals__, {'OUT':directory}):
                with self.assertRaisesRegex(RuntimeError, '视觉审计未通过'):
                    asyncio.run(fn())
            self.assertIn('"measured": 0', output.getvalue())
            self.assertIn(f'"errors": {self.SURFACES}', output.getvalue())

    def test_context_interceptor_blocks_writes_and_external_requests(self):
        cases = [
            ('POST','/api/packing/sync-from-products?force=1','fixture'),
            ('POST','/api/buybox/page-open','fixture'),
            ('PATCH','/api/products/fixture-product','abort'),
            ('DELETE','/api/inventory','abort'),
            ('POST','/api/unlisted-write','abort'),
            ('GET','/api/unlisted-read','abort'),
            ('GET','/api/products?limit=1000','fixture'),
            ('GET','/assets/test.js','continue'),
            ('POST','/non-api-write','abort'),
            ('GET','https://example.invalid/api/products','abort'),
        ]
        for method, path, expected in cases:
            with self.subTest(method=method, path=path):
                route = Route(method, path if path.startswith('http') else audit['BASE']+path)
                errors = []
                asyncio.run(audit['intercept'](route, errors))
                self.assertEqual(route.action, expected)
                self.assertEqual(bool(errors), expected == 'abort')

    def test_production_base_is_rejected(self):
        with self.assertRaises(ValueError):
            audit['local_origin']('https://takealot-selector-production.up.railway.app')

if __name__ == '__main__': unittest.main()
