"""Durable quote browser profile. Login/location state stays on the collector host."""
import asyncio
import os
import threading
import shutil
import subprocess
from contextlib import asynccontextmanager
from database import DB_DIR

_lock = threading.Lock()
_display_lock = threading.Lock()
_display = None

def browser_display():
    """Local virtual display only; no VNC server or public listening port."""
    global _display
    if os.environ.get('DISPLAY'):
        return os.environ['DISPLAY']
    if not shutil.which('Xvfb'):
        return None
    with _display_lock:
        if _display is None or _display.poll() is not None:
            _display = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1366x768x24', '-nolisten', 'tcp'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            for _ in range(30):
                if os.path.exists('/tmp/.X11-unix/X99'): break
                if _display.poll() is not None: raise RuntimeError('云端显示器启动失败')
                time.sleep(.1)
        return ':99'

@asynccontextmanager
async def quote_context(wait=True):
    from playwright.async_api import async_playwright
    if wait:
        await asyncio.to_thread(_lock.acquire)
    elif not _lock.acquire(blocking=False):
        raise RuntimeError('采集浏览器忙，请稍后再打开')
    try:
        profile = os.environ.get('BUYBOX_BROWSER_PROFILE', os.path.join(DB_DIR, 'buybox_browser_profile'))
        os.makedirs(profile, exist_ok=True)
        display = await asyncio.to_thread(browser_display)
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile, headless=not bool(display), args=['--no-sandbox'],
                env={**os.environ, **({'DISPLAY':display} if display else {})},
                locale='en-ZA', timezone_id='Africa/Johannesburg',
                viewport={'width':1366,'height':768})
            try:
                yield context
            finally:
                await context.close()
    finally:
        _lock.release()
