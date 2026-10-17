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
    if wait:
        await asyncio.to_thread(_lock.acquire)
    elif not _lock.acquire(blocking=False):
        raise RuntimeError('采集浏览器忙，请稍后再打开')
    playwright = context = None
    try:
        profile = os.environ.get('BUYBOX_BROWSER_PROFILE', os.path.join(DB_DIR, 'buybox_browser_profile'))
        os.makedirs(profile, exist_ok=True)
        display = await asyncio.to_thread(browser_display)
        # 容器里 launch_persistent_context 偶发 TargetClosedError：上一次进程被
        # 重启杀掉后，profile 目录里会留下 SingletonLock/Socket，下一次启动直接
        # 失败；/dev/shm 只有 64MB 也会让渲染进程崩掉。两者都在这里兜住，
        # 并重试一次——采集是自动调价的上游，起不来浏览器就等于全线停摆。
        #
        # 注意：重试只包住「启动」这一段。yield 之后的异常是调用方自己的（采集逻辑
        # 出错），绝不能在这里被捕获重试——那会把整轮采集悄悄重跑一遍。
        args = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run']
        for attempt in range(2):
            await asyncio.to_thread(_clear_singleton, profile)
            try:
                playwright, context = await _launch(profile, display, args)
                break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                raise
        try:
            yield context
        finally:
            try:
                await context.close()
            finally:
                await playwright.stop()
                # 释放宽限：worker 是 2 并发、共用同一个 profile，前一个 Chromium
                # 刚 stop 就立刻启动下一个会偶发 TargetClosedError（线上实测约 7%
                # 的采集因此失败）。这里在**仍持有 _lock** 时多等一会儿，确保上一个
                # 进程真的退出、把 profile 交出来，再让下一轮进来。
                await asyncio.sleep(1.2)
    finally:
        _lock.release()


async def _launch(profile, display, args):
    """启动持久化上下文。返回 (playwright, context)，两者都由调用方负责关闭。"""
    from playwright.async_api import async_playwright
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile, headless=not bool(display), args=args,
            env={**os.environ, **({'DISPLAY': display} if display else {})},
            locale='en-ZA', timezone_id='Africa/Johannesburg',
            viewport={'width': 1366, 'height': 768})
    except Exception:
        # 启动失败要把 playwright 驱动收掉，否则每失败一次就漏一个 node 进程。
        await playwright.stop()
        raise
    return playwright, context


def _clear_singleton(profile):
    """清掉上次进程残留的 profile 单例锁。只在锁文件属于已死进程时才有害，
    但这里无法可靠判断 pid 归属（容器重启后 pid 会复用），直接删更安全：
    我们持有全局 _lock，此刻确定没有别的采集在用这个 profile。"""
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = os.path.join(profile, name)
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
