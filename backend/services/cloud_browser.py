"""A short-lived, authenticated view of the SAME durable quote browser.

No cookies, storage state, HTML or credentials are exported. Frames stay in memory.
The quote-context lock prevents concurrent profile access by scheduled collectors.
"""
import asyncio
import secrets
import threading
import time
import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException

WIDTH, HEIGHT = 1366, 768
IDLE_SECONDS, MAX_SECONDS = 300, 900


def allowed_request(url, navigation=False):
    p = urlsplit(url)
    if p.scheme != 'https': return False
    host=p.hostname or ''
    if host in ('localhost',) or host.endswith(('.internal', '.local')): return False
    try:
        if not ipaddress.ip_address(host).is_global: return False
    except ValueError: pass
    if navigation and (p.scheme != 'https' or p.hostname not in ('www.takealot.com', 'takealot.com')):
        return False
    # This surface is for public quote/location setup, never purchasing/account edits.
    if p.hostname and (p.hostname == 'takealot.com' or p.hostname.endswith('.takealot.com')):
        return not any(x in p.path.lower().split('/') for x in
            ('cart', 'checkout', 'payment', 'payments', 'orders', 'wishlist', 'login', 'register', 'account'))
    return True


class CloudBrowser:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name='cloud-browser-setup')
        self.thread.start()
        self.token = None
        self.context_manager = None
        self.page = None
        self.guard = None

    def run(self, command, token=None, **kwargs):
        future = asyncio.run_coroutine_threadsafe(self.command(command, token, **kwargs), self.loop)
        try:
            return future.result(timeout=55)
        except TimeoutError:
            future.cancel()
            raise HTTPException(504, '云端浏览器等待超时，请稍后重新打开')

    async def command(self, command, token=None, **kwargs):
        if self.guard is None: self.guard = asyncio.Lock()
        async with self.guard:
            if self.token and (time.monotonic()-self.touched > IDLE_SECONDS or time.monotonic()-self.started > MAX_SECONDS):
                await self.close()
            if command == 'open':
                if self.token: raise HTTPException(409, '已有云端浏览器窗口正在使用；关闭后再打开，闲置5分钟自动释放')
                from services.quote_browser import quote_context
                self.context_manager = quote_context(wait=False)
                try:
                    context = await self.context_manager.__aenter__()
                except RuntimeError:
                    self.context_manager = None
                    raise HTTPException(409, '采集浏览器忙，请稍后再打开')
                try:
                    self.page = await context.new_page()
                    async def restrict(route):
                        req = route.request
                        main = req.is_navigation_request() and req.frame.parent_frame is None
                        if allowed_request(req.url, main): await route.continue_()
                        else: await route.abort()
                    await context.route('**/*', restrict)
                    self.token = secrets.token_urlsafe(32)
                    self.started = self.touched = time.monotonic()
                    asyncio.create_task(self.expire(self.token))
                    await self.page.goto(kwargs['url'], wait_until='domcontentloaded', timeout=45000)
                    return {'session': self.token, 'width': WIDTH, 'height': HEIGHT}
                except BaseException:
                    await self.close()
                    raise HTTPException(503, '云端商品页未能打开；采集与调价开关未改变')
            if not self.token or not token or not secrets.compare_digest(self.token, token):
                raise HTTPException(410, '云端浏览器窗口已关闭或过期，请重新打开')
            self.touched = time.monotonic()
            if command == 'close':
                await self.close()
                return {'closed': True}
            try:
                if command == 'frame':
                    return await self.page.screenshot(type='jpeg', quality=70, timeout=5000)
                if command == 'click': await self.page.mouse.click(kwargs['x'], kwargs['y'])
                elif command == 'text': await self.page.keyboard.insert_text(kwargs['text'])
                elif command == 'key': await self.page.keyboard.press(kwargs['key'])
                elif command == 'scroll': await self.page.mouse.wheel(0, kwargs['delta'])
                return {'ok': True}
            except Exception:
                raise HTTPException(503, '浏览器操作暂未完成，请刷新画面或关闭后重开')

    async def expire(self, token):
        while self.token == token:
            await asyncio.sleep(15)
            async with self.guard:
                if self.token == token and (time.monotonic()-self.touched > IDLE_SECONDS or time.monotonic()-self.started > MAX_SECONDS):
                    await self.close()

    async def close(self):
        manager = self.context_manager
        self.token = self.context_manager = self.page = None
        if manager is not None:
            await manager.__aexit__(None, None, None)


_browser = None
_init_lock = threading.Lock()

def browser():
    global _browser
    with _init_lock:
        if _browser is None: _browser = CloudBrowser()
    return _browser
