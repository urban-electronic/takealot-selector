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
        # 桩可以给一串记录，按 Tab 次数轮转——真实页面上一次 Tab 一个元素，
        # 只有轮转才测得出「一部分量得到、一部分量不到」的混合情形。
        if isinstance(self.focus, list):
            return self.focus[self.tabs % len(self.focus)]
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
        def fill(record):
            return dict(Browser.focus, ringColor='rgb(0,0,0)', bg='rgb(255,255,255)', **record)
        if isinstance(focus, list):
            focus = [fill(record) for record in focus]
        elif focus is not None:
            focus = fill(focus)
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
        self.assertIn('"measured": 18', output)
        defaults = [(name, tabs) for name, tabs in shots if not name.endswith('_focus.png')]
        self.assertEqual(len(defaults), 18)
        self.assertTrue(all(tabs == 0 for _, tabs in defaults), 'default screenshots precede keyboard interaction')

    def test_all_navigation_errors_fail(self):
        status, output, _ = self.run_audit(navigation_error=True)
        self.assertEqual(status, 1)
        self.assertIn('"measured": 0', output)
        self.assertIn('"errors": 18', output)

    # ---- 焦点落在控件内部子部件上（日期框的日历按钮等）----
    # 宿主元素的 outline 读不到，但浏览器把圈画在图标自己身上（截图核对过）。
    # 这类记录必须单列：既不能算通过，也不能算失败——从报告里消失才是漏测。
    def summary(self, output):
        import json
        line = [l for l in output.splitlines() if l.startswith('{"environment"')]
        self.assertEqual(len(line), 1, 'audit must print exactly one machine-readable summary')
        return json.loads(line[0])

    def test_subcontrol_focus_is_counted_not_passed(self):
        # 一次量得到、一次量不到，交替出现：真实页面就是这样（日期框的日/月/年字段
        # 与日历按钮各自一个 Tab 停点）。
        status, output, _ = self.run_audit([
            {'tag':'button', 'ringSource':'outline:solid', 'ringVisible':True, 'ringRatio':3.1},
            {'tag':'input', 'hostFocus':False, 'ringSource':'none', 'ringVisible':False, 'ringRatio':None},
        ])
        self.assertEqual(status, 0, 'sub-control focus is not a missing ring')
        self.assertGreater(self.summary(output)['focus_unmeasurable_subcontrol'], 0,
                           'unmeasurable focus must stay visible in the summary')
        self.assertIn('子部件焦点(量不到)', output)

    def test_all_focus_inside_subcontrols_cannot_pass(self):
        # 全部记录都落在子部件里 = 一个能测的都没有，必须失败，而不是「没有失败就是通过」。
        status, output, _ = self.run_audit({'tag':'input', 'hostFocus':False,
                                            'ringSource':'none', 'ringVisible':False, 'ringRatio':None})
        self.assertEqual(status, 1)
        self.assertIn('未采到可测量的焦点元素', output)

    def test_subcontrol_flag_cannot_mask_a_missing_ring_on_a_real_control(self):
        # hostFocus=True 的记录必须照旧按「没有圈」判失败，不许被子部件那条口子放过。
        status, _, _ = self.run_audit([
            {'tag':'button', 'hostFocus':True, 'ringSource':'outline:solid', 'ringVisible':True, 'ringRatio':3.1},
            {'tag':'button', 'hostFocus':True, 'ringSource':'none', 'ringVisible':False, 'ringRatio':None},
        ])
        self.assertEqual(status, 1)

if __name__ == '__main__': unittest.main()
