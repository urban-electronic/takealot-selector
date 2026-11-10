"""采集浏览器内存策略的断言。

2026-11-10：Railway 内存占账单 54%（约 1.4GB 常驻基线），这里锁住收内存的
几条硬约束，避免以后有人"顺手"把并发调回 2 或把资源拦截关掉又把账单顶上去。
这些用例全是纯配置与纯函数级断言，不启动浏览器。
"""
import importlib
import inspect
import os

import pytest


@pytest.fixture()
def qb(monkeypatch):
    """按需重载 quote_browser，让模块级环境变量常量读到的都是当前 env。"""
    def _reload(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import services.quote_browser as module
        return importlib.reload(module)
    return _reload


def test_collect_workers_defaults_to_one(monkeypatch):
    monkeypatch.delenv('BUYBOX_COLLECT_WORKERS', raising=False)
    import services.buybox_monitor as monitor
    monitor = importlib.reload(monitor)
    assert monitor.COLLECT_WORKERS == 1


def test_collect_workers_can_be_raised_for_rollback(monkeypatch):
    monkeypatch.setenv('BUYBOX_COLLECT_WORKERS', '2')
    import services.buybox_monitor as monitor
    monitor = importlib.reload(monitor)
    assert monitor.COLLECT_WORKERS == 2
    monkeypatch.delenv('BUYBOX_COLLECT_WORKERS', raising=False)
    importlib.reload(monitor)


def test_collect_workers_never_below_one(monkeypatch):
    monkeypatch.setenv('BUYBOX_COLLECT_WORKERS', '0')
    import services.buybox_monitor as monitor
    monitor = importlib.reload(monitor)
    assert monitor.COLLECT_WORKERS == 1
    monkeypatch.delenv('BUYBOX_COLLECT_WORKERS', raising=False)
    importlib.reload(monitor)


def test_lean_args_keep_baseline_flags():
    from services import quote_browser
    args = quote_browser.LEAN_BROWSER_ARGS
    # 原有四条必须还在，否则沙箱/共享内存/GPU 的老问题会回来。
    for flag in ('--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run'):
        assert flag in args
    # 省内存的四项。
    assert any(a.startswith('--js-flags=') and 'max-old-space-size' in a for a in args)
    assert '--renderer-process-limit=2' in args
    assert '--mute-audio' in args
    assert '--disable-extensions' in args


def test_lean_args_do_not_disable_images_globally():
    """图片拦截走路由，不能靠 --blink-settings 关图——cloud_browser 要给用户看页面。"""
    from services import quote_browser
    joined = ' '.join(quote_browser.LEAN_BROWSER_ARGS)
    assert 'imagesEnabled' not in joined


def test_blocked_resource_types_cover_heavy_assets():
    from services import quote_browser
    assert {'image', 'media', 'font'} == set(quote_browser.BLOCKED_RESOURCE_TYPES)


def test_idle_close_seconds_default_and_override(qb, monkeypatch):
    monkeypatch.delenv('BUYBOX_BROWSER_IDLE_SECONDS', raising=False)
    module = qb()
    assert module.IDLE_CLOSE_SECONDS == 300
    module = qb(BUYBOX_BROWSER_IDLE_SECONDS='60')
    assert module.IDLE_CLOSE_SECONDS == 60
    monkeypatch.delenv('BUYBOX_BROWSER_IDLE_SECONDS', raising=False)
    importlib.reload(module)


def test_browser_headless_auto_follows_display(qb, monkeypatch):
    monkeypatch.delenv('BUYBOX_BROWSER_HEADLESS', raising=False)
    module = qb()
    # 有 Xvfb 就维持原来的有头形态，没有就无头：auto 不改线上现状。
    assert module.browser_headless(':99') is False
    assert module.browser_headless(None) is True


def test_browser_headless_override_wins_over_display(qb, monkeypatch):
    module = qb(BUYBOX_BROWSER_HEADLESS='1')
    assert module.browser_headless(':99') is True
    module = qb(BUYBOX_BROWSER_HEADLESS='0')
    assert module.browser_headless(None) is False
    monkeypatch.delenv('BUYBOX_BROWSER_HEADLESS', raising=False)
    importlib.reload(module)


def test_quote_context_accepts_block_media():
    from services import quote_browser
    params = inspect.signature(quote_browser.quote_context).parameters
    assert 'block_media' in params
    # 默认不拦：交互式看页面（cloud_browser）不传参时必须还是完整页面。
    assert params['block_media'].default is False


def test_collect_passes_block_media_switch(monkeypatch):
    import services.automatic_pricing as pricing
    pricing = importlib.reload(pricing)
    assert pricing.BLOCK_MEDIA is True
    monkeypatch.setenv('BUYBOX_BLOCK_MEDIA', '0')
    pricing = importlib.reload(pricing)
    assert pricing.BLOCK_MEDIA is False
    monkeypatch.delenv('BUYBOX_BLOCK_MEDIA', raising=False)
    importlib.reload(pricing)


def test_route_drop_heavy_aborts_only_heavy_assets():
    import asyncio
    from services.quote_browser import _route_drop_heavy

    class _Req:
        def __init__(self, resource_type):
            self.resource_type = resource_type

    class _Route:
        def __init__(self, resource_type):
            self.request = _Req(resource_type)
            self.calls = []

        async def abort(self):
            self.calls.append('abort')

        async def continue_(self):
            self.calls.append('continue')

    for resource_type in ('image', 'media', 'font'):
        route = _Route(resource_type)
        asyncio.run(_route_drop_heavy(route))
        assert route.calls == ['abort'], resource_type
    for resource_type in ('document', 'script', 'xhr', 'stylesheet'):
        route = _Route(resource_type)
        asyncio.run(_route_drop_heavy(route))
        assert route.calls == ['continue'], resource_type


def test_idle_guard_only_touches_collector_profile():
    """看门狗必须打的是采集 profile，不能误杀 cloud_browser 那份。"""
    from services import quote_browser
    source = inspect.getsource(quote_browser._idle_guard_loop)
    assert '_collector_profile()' in source
    assert '_kill_profile' in source
    assert 'CLOUD_BROWSER_PROFILE' not in source


def test_collector_profile_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv('BUYBOX_BROWSER_PROFILE', str(tmp_path / 'p'))
    import services.quote_browser as module
    module = importlib.reload(module)
    assert module._collector_profile() == str(tmp_path / 'p')
    monkeypatch.delenv('BUYBOX_BROWSER_PROFILE', raising=False)
    importlib.reload(module)
