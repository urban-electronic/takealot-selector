"""Exercise the actual candidate audit entry point without browser/network access."""
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

class Locator:
    first = property(lambda self: self)
    async def wait_for(self, **kwargs): pass

class Browser:
    focus = {'tag':'button', 'cls':'test', 'ringSource':'outline:solid',
             'ringVisible':True, 'ringRatio':3.1}
    navigation_error = False
    shots = []
    def __init__(self): self.chromium = self; self.keyboard = self; self.tabs = 0
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def launch(self, **kwargs): return self
    async def new_context(self, **kwargs): return self
    async def new_page(self): self.tabs = 0; return self
    async def route(self, *args): pass
    async def add_init_script(self, *args): pass
    async def goto(self, *args, **kwargs):
        if self.navigation_error: raise RuntimeError('fixture navigation unavailable')
    def get_by_role(self, *args, **kwargs): return Locator()
    def get_by_text(self, *args, **kwargs): return Locator()
    def locator(self, *args, **kwargs): return Locator()
    async def wait_for_load_state(self, *args, **kwargs): pass
    async def press(self, key): self.tabs += 1
    async def evaluate(self, script):
        if script == audit['LAYOUT_JS']:
            return {'overlaps':[], 'cellBreaks':[], 'pageOverflow':0, 'longNames':[]}
        return self.focus
    async def screenshot(self, path): self.shots.append((Path(path).name, self.tabs))
    async def close(self): pass

async_api = types.ModuleType('playwright.async_api')
async_api.async_playwright = Browser
sync_api = types.ModuleType('playwright.sync_api')
sync_api.sync_playwright = lambda: None
with patch.dict(sys.modules, {'playwright':types.ModuleType('playwright'),
                             'playwright.async_api':async_api, 'playwright.sync_api':sync_api}):
    audit = runpy.run_path(str(Path(__file__).with_name('ui_longtitle_focus_audit.py')))

class AuditTests(unittest.TestCase):
    def test_reuses_main_safety_interceptor(self):
        self.assertIs(audit['intercept'], audit['MAIN'].intercept)
        self.assertIs(audit['MAIN'].DATA, audit['DATA'])

    def run_audit(self, focus=Browser.focus, navigation_error=False):
        if focus is not None:
            focus = dict(Browser.focus, ringColor='rgb(0,0,0)', bg='rgb(255,255,255)', **focus)
        fn = audit['run']
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(Browser, 'focus', focus), patch.object(Browser, 'shots', []), \
                 patch.object(Browser, 'navigation_error', navigation_error), \
                 patch.dict(fn.__globals__, {'OUT':Path(tmp)/'longtitle'}):
                status = asyncio.run(fn())
                shots = list(Browser.shots)
            return status, output.getvalue(), shots

    def test_absent_ring_is_failure(self):
        status, _, _ = self.run_audit({'tag':'button', 'ringSource':'none',
                                     'ringVisible':False, 'ringRatio':None})
        self.assertEqual(status, 1, 'missing rings must not be filtered out of failures')

    def test_zero_focus_samples_is_failure(self):
        self.assertEqual(self.run_audit(None)[0], 1)

    def test_invisible_ring_is_failure_even_with_high_color_ratio(self):
        self.assertEqual(self.run_audit({'tag':'button', 'ringSource':'outline:solid',
                                        'ringVisible':False, 'ringRatio':21})[0], 1)

    def test_low_contrast_ring_is_failure(self):
        self.assertEqual(self.run_audit({'tag':'button', 'ringSource':'outline:solid',
                                        'ringVisible':True, 'ringRatio':2.99})[0], 1)

    def test_valid_ring_and_default_screenshot_order(self):
        status, output, shots = self.run_audit()
        self.assertEqual(status, 0)
        self.assertIn('"measured": 12', output)
        defaults = [(name, tabs) for name, tabs in shots if not name.endswith('_focus.png')]
        self.assertEqual(len(defaults), 12)
        self.assertTrue(all(tabs == 0 for _, tabs in defaults), 'default screenshots precede keyboard interaction')

    def test_all_navigation_errors_fail(self):
        status, output, _ = self.run_audit(navigation_error=True)
        self.assertEqual(status, 1)
        self.assertIn('"measured": 0', output)
        self.assertIn('"errors": 12', output)

if __name__ == '__main__': unittest.main()
